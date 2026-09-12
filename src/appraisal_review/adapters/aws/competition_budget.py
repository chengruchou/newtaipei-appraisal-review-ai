"""Atomic team budget reservations; no automatic ledger creation, reset or refund."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer

from appraisal_review.domain.competition_data import DataPart
from appraisal_review.domain.competition_profile import CompetitionProfile, OperationReservation


class CompetitionBudgetFault(RuntimeError):
    """Stable value-free denial; an uncertain reservation stays consumed."""


def _marshal(row: dict[str, Any]) -> dict[str, Any]:
    serializer = TypeSerializer()
    return {key: serializer.serialize(value) for key, value in row.items()}


def budget_seed_item(profile: CompetitionProfile) -> dict[str, Any]:
    """Prepare a reviewed seed, never send it. Operators use conditional create only.

    The runtime does not have a seed/reset code path. Reusing a window after
    restart requires the same immutable profile fingerprint and existing counters.
    """
    budget = profile.budget
    if budget is None or budget.window_start is None or budget.window_id is None:
        raise CompetitionBudgetFault("competition_budget_window_unknown")
    return _marshal(
        {
            "pk": f"competition-budget:{budget.scope}:{budget.window_id}",
            "profile_digest": profile.digest,
            "window_start": budget.window_start.isoformat(),
            "window_end": (
                budget.window_start + timedelta(seconds=budget.window_seconds)
            ).isoformat(),
            "max_calls": budget.max_calls,
            "max_input_tokens": budget.max_input_tokens,
            "max_output_tokens": budget.max_output_tokens,
            "max_cost_usd": budget.max_cost_usd,
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": Decimal(0),
            "version": 0,
        }
    )


class DynamoDBCompetitionBudget:
    """CAS across independent processes/workers using one pinned DynamoDB item.

    client_factory is the competition composition's guarded metadata-only client.
    Reservations call only GetItem/UpdateItem, never Bedrock or a price API. All
    coordination requests still traverse exact-envelope data admission.
    """

    def __init__(
        self,
        profile: CompetitionProfile,
        client_factory: Callable[[], Any],
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.profile = CompetitionProfile.model_validate_json(profile.model_dump_json())
        self.client_factory, self.clock = client_factory, clock
        arn = profile.throttle.central_store_arn
        if not arn or ":table/" not in arn:
            raise CompetitionBudgetFault("competition_budget_store_unknown")
        self.table = arn.split(":table/", 1)[1]
        self.expected = budget_seed_item(self.profile)

    def _bound(
        self, operation: str, parameters: dict[str, Any], parts: tuple[DataPart, ...]
    ) -> OperationReservation:
        budget = self.profile.budget
        if budget is None:
            raise CompetitionBudgetFault("competition_budget_bounds_unknown")
        model_id = parameters.get(
            "modelId",
            parameters.get("modelIdentifier", parameters.get("inferenceProfileIdentifier")),
        )
        matches = [
            r
            for r in budget.operation_reservations
            if r.operation == operation and r.model_id == model_id
        ]
        if len(matches) != 1:
            raise CompetitionBudgetFault("competition_budget_bounds_unknown")
        bound = matches[0]
        models = [
            m
            for m in self.profile.models
            if m.model_id == model_id
            or (
                operation == "GetFoundationModel"
                and any(model_id in (d.arn, d.arn.split("/", 1)[1]) for d in m.destinations)
            )
        ]
        if not models or any(
            m.destination_snapshot_sha256 != bound.destination_snapshot_sha256 for m in models
        ):
            raise CompetitionBudgetFault("competition_budget_routing_changed")
        if not bound.covers_worst_case(tuple(models)):
            raise CompetitionBudgetFault("competition_budget_conservative_basis_missing")
        if sum(len(part.content) for part in parts) > bound.max_request_bytes:
            raise CompetitionBudgetFault("competition_budget_request_unbounded")
        if operation in {"Converse", "InvokeModel"} and (
            bound.input_tokens < 1 or bound.output_tokens < 1 or bound.cost_usd <= 0
        ):
            raise CompetitionBudgetFault("competition_budget_pricing_unknown")
        output = None
        if operation == "Converse":
            bodies = [p.content for p in parts if p.part_id.endswith(".body")]
            try:
                if len(bodies) != 1:
                    raise ValueError("Missing exact body")
                decoded = json.loads(bodies[0])
                output = decoded.get("inferenceConfig", {}).get("maxTokens")
            except (ValueError, AttributeError):
                raise CompetitionBudgetFault("competition_budget_output_unbounded") from None
        if operation == "Converse" and (
            type(output) is not int or not 0 < output <= bound.output_tokens
        ):
            raise CompetitionBudgetFault("competition_budget_output_unbounded")
        # Provider-specific InvokeModel bodies need a separately implemented
        # verified output-limit decoder. Refuse that capability until available.
        if operation == "InvokeModel":
            raise CompetitionBudgetFault("competition_budget_output_unbounded")
        return bound

    def reserve(
        self, *, operation: str, parameters: dict[str, Any], parts: tuple[DataPart, ...]
    ) -> None:
        bound = self._bound(operation, parameters, parts)
        budget = self.profile.budget
        assert budget is not None and budget.window_start is not None
        start, end = (
            budget.window_start,
            budget.window_start + timedelta(seconds=budget.window_seconds),
        )
        if not start <= self.clock() < end:
            raise CompetitionBudgetFault("competition_budget_window_expired")
        client = self.client_factory()
        stable = {
            k: v
            for k, v in self.expected.items()
            if k not in {"calls", "input_tokens", "output_tokens", "cost_usd", "version"}
        }
        key = {"pk": self.expected["pk"]}
        decoder = TypeDeserializer()
        for _ in range(8):
            try:
                row = client.get_item(TableName=self.table, Key=key, ConsistentRead=True).get(
                    "Item"
                )
                if not isinstance(row, dict) or any(
                    row.get(k) != value for k, value in stable.items()
                ):
                    raise CompetitionBudgetFault("competition_budget_ledger_unapproved")
                values = {
                    k: decoder.deserialize(row[k])
                    for k in ("calls", "input_tokens", "output_tokens", "cost_usd", "version")
                }
                if any(
                    not isinstance(v, Decimal) or not v.is_finite() or v < 0
                    for v in values.values()
                ):
                    raise CompetitionBudgetFault("competition_budget_ledger_corrupt")
                if any(
                    values[k] != values[k].to_integral_value()
                    for k in ("calls", "input_tokens", "output_tokens", "version")
                ):
                    raise CompetitionBudgetFault("competition_budget_ledger_corrupt")
                next_values = {
                    "calls": values["calls"] + 1,
                    "input_tokens": values["input_tokens"] + bound.input_tokens,
                    "output_tokens": values["output_tokens"] + bound.output_tokens,
                    "cost_usd": values["cost_usd"] + bound.cost_usd,
                    "version": values["version"] + 1,
                }
                if any(
                    next_values[k] > limit
                    for k, limit in (
                        ("calls", budget.max_calls),
                        ("input_tokens", budget.max_input_tokens),
                        ("output_tokens", budget.max_output_tokens),
                        ("cost_usd", budget.max_cost_usd),
                    )
                ):
                    raise CompetitionBudgetFault("competition_budget_exhausted")
                if not start <= self.clock() < end:
                    raise CompetitionBudgetFault("competition_budget_window_expired")
                names = {f"#{k}": k for k in next_values}
                attributes = {f":{k}": value for k, value in next_values.items()}
                conditions = []
                # Pin the whole observed row, including caps and counters, in the
                # same atomic write. Drift between GetItem and CAS cannot pass.
                observed = {**stable, **{k: row[k] for k in values}}
                expected_values = _marshal(attributes)
                for index, (key_name, value) in enumerate(observed.items()):
                    name, placeholder = f"#old{index}", f":old{index}"
                    names[name] = key_name
                    expected_values[placeholder] = value
                    conditions.append(f"{name} = {placeholder}")
                client.update_item(
                    TableName=self.table,
                    Key=key,
                    UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in next_values),
                    ConditionExpression=" AND ".join(conditions),
                    ExpressionAttributeNames=names,
                    ExpressionAttributeValues=expected_values,
                )
                if not start <= self.clock() < end:
                    raise CompetitionBudgetFault("competition_budget_window_expired")
                return
            except CompetitionBudgetFault:
                raise
            except Exception as error:
                code = getattr(error, "response", {}).get("Error", {}).get("Code")
                if code != "ConditionalCheckFailedException":
                    raise CompetitionBudgetFault("competition_budget_reservation_unknown") from None
        raise CompetitionBudgetFault("competition_budget_contended")
