"""Read-only Bedrock routing discovery. Observation only; it approves nothing.

`CompetitionProfile.models[].destinations` and `destination_snapshot_sha256` must
record the complete routing set actually published for a model identifier, and
`check_model_destinations` compares a discovered set against it. Until now nothing
produced that discovered set for a real account, so the profile fields could only
be filled by hand and never rechecked.

This adapter performs that discovery through the same injected client factory and
physical-dispatch guard as the extraction preflight, and returns a canonical
snapshot whose digest is reproducible. A snapshot is evidence for an operator
review, not routing approval: it records what the account publishes, never that
the competition permits those regions, that the role may invoke the model, or that
any data may be sent.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from appraisal_review.adapters.aws.extraction_errors import provider_failure
from appraisal_review.adapters.aws.extraction_preflight import AWSClients
from appraisal_review.domain.competition_profile import ModelDestination
from appraisal_review.domain.extraction_contracts import ContractModel
from appraisal_review.ports.document_extraction import ExtractionBoundaryError
from appraisal_review.ports.model_dispatch import (
    DispatchDenied,
    DispatchGuard,
    current_dispatch_guard,
    dispatch_guard,
    inherited_dispatch_authority,
)

ModelKind = Literal["foundation", "system_profile", "application_profile"]
_PROFILE_RESOURCE: dict[str, str] = {
    "system_profile": "inference-profile",
    "application_profile": "application-inference-profile",
}
_PROFILE_TYPE: dict[str, str] = {
    "system_profile": "SYSTEM_DEFINED",
    "application_profile": "APPLICATION",
}
_FOUNDATION_ARN = re.compile(r"arn:aws:bedrock:([a-z0-9-]+)::foundation-model/([a-zA-Z0-9.:-]+)")


class RoutingSnapshot(ContractModel):
    """One observed routing set for one model identifier in one calling region.

    `digest` deliberately excludes `observed_at` so that repeating the discovery
    reproduces the same pin while the observation time stays visible. A digest
    binds the observed routing set only; it is not an approval reference.
    """

    schema_version: Literal["routing-snapshot-v1"] = "routing-snapshot-v1"
    model_id: Annotated[str, Field(min_length=1, max_length=2048)]
    kind: ModelKind
    account_id: Annotated[str, Field(pattern=r"^[0-9]{12}$")]
    calling_region: Annotated[str, Field(pattern=r"^[a-z]{2}-[a-z]+-[0-9]+$")]
    profile_arn: str | None = None
    profile_status: str | None = None
    destinations: tuple[ModelDestination, ...] = Field(min_length=1)
    observed_at: datetime

    @model_validator(mode="after")
    def coherent_observation(self) -> RoutingSnapshot:
        if self.observed_at.tzinfo is None:
            raise ValueError("Observation time requires an explicit timezone")
        arns = [destination.arn for destination in self.destinations]
        if len(set(arns)) != len(arns):
            raise ValueError("Duplicate discovered destination")
        if (self.kind == "foundation") != (self.profile_arn is None):
            raise ValueError("Profile routing requires an observed profile ARN")
        if self.kind == "foundation" and (
            len(self.destinations) != 1
            or self.destinations[0].region != self.calling_region
            or self.profile_status is not None
        ):
            raise ValueError("A direct foundation route has one same-region destination")
        return self

    def routing_identity(self) -> dict[str, object]:
        """The exact fields the digest covers."""
        return {
            "schema_version": self.schema_version,
            "model_id": self.model_id,
            "kind": self.kind,
            "account_id": self.account_id,
            "calling_region": self.calling_region,
            "profile_arn": self.profile_arn,
            "destinations": [destination.arn for destination in self.destinations],
        }

    @property
    def digest(self) -> str:
        value = json.dumps(self.routing_identity(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(value.encode()).hexdigest()

    @property
    def destination_regions(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(destination.region for destination in self.destinations))

    def routes_outside(self, permitted_regions: frozenset[str]) -> tuple[str, ...]:
        """Destination regions the caller has not declared as permitted."""
        return tuple(r for r in self.destination_regions if r not in permitted_regions)


def _foundation(arn: str) -> tuple[str, str]:
    match = _FOUNDATION_ARN.fullmatch(arn)
    if match is None:
        raise ExtractionBoundaryError("unsupported_capability")
    return match.group(1), match.group(2)


def discover_routing(
    clients: AWSClients,
    *,
    model_id: str,
    kind: ModelKind,
    region: str,
    account_id: str,
    timeout_seconds: float = 30.0,
    observed_at: datetime | None = None,
) -> RoutingSnapshot:
    """Discover the complete published destination set. No model is invoked.

    Control-plane calls are charged to the shared physical dispatcher exactly like
    the extraction preflight, because the competition throttle covers the control
    plane. An incomplete or inconsistent observation fails instead of returning a
    partial set: a subset would silently understate where a request can travel.
    """
    if (
        not model_id.strip()
        or model_id != model_id.strip()
        or timeout_seconds <= 0
        or kind not in {"foundation", *_PROFILE_RESOURCE}
        or not re.fullmatch(r"[a-z]{2}-[a-z]+-[0-9]+", region)
        or not re.fullmatch(r"[0-9]{12}", account_id)
    ):
        raise ExtractionBoundaryError("configuration_error")
    try:
        deadline = time.monotonic() + timeout_seconds
        try:
            inherited = current_dispatch_guard()
        except DispatchDenied:
            inherited = None
        if inherited is not None:
            deadline = min(deadline, inherited.deadline)

        def metadata(call: Any, **kwargs: Any) -> dict[str, Any]:
            guard = DispatchGuard(deadline, inherited_dispatch_authority())
            if inherited is not None:
                guard.authority = inherited.authority
                guard.cancelled = inherited.cancelled
            with dispatch_guard(guard):
                result = call(**kwargs)
            if not isinstance(result, dict):
                raise ExtractionBoundaryError("malformed_output")
            return result

        def control(target_region: str) -> Any:
            client = clients.client("bedrock", target_region, timeout_seconds)
            if client.meta.region_name != target_region:
                raise ExtractionBoundaryError("configuration_error")
            return client

        profile_arn: str | None = None
        profile_status: str | None = None
        if kind == "foundation":
            if model_id.startswith("arn:") and _foundation(model_id)[0] != region:
                raise ExtractionBoundaryError("configuration_error")
            name = _foundation(model_id)[1] if model_id.startswith("arn:") else model_id
            arns = [f"arn:aws:bedrock:{region}::foundation-model/{name}"]
        else:
            client = control(region)
            observation = metadata(
                client.get_inference_profile, inferenceProfileIdentifier=model_id
            )
            profile_arn = observation.get("inferenceProfileArn")
            profile_status = observation.get("status")
            identifier = observation.get("inferenceProfileId")
            expected = f"arn:aws:bedrock:{region}:{account_id}:{_PROFILE_RESOURCE[kind]}/"
            if (
                not isinstance(profile_arn, str)
                or not isinstance(identifier, str)
                or profile_status != "ACTIVE"
                or observation.get("type") != _PROFILE_TYPE[kind]
                or not profile_arn.startswith(expected)
                or profile_arn[len(expected) :] != identifier
                or model_id not in {profile_arn, identifier}
            ):
                raise ExtractionBoundaryError("configuration_error")
            routes = observation.get("models")
            if not isinstance(routes, list) or not 1 <= len(routes) <= 5:
                raise ExtractionBoundaryError("unsupported_capability")
            arns = []
            for route in routes:
                arn = route.get("modelArn") if isinstance(route, dict) else None
                if not isinstance(arn, str):
                    raise ExtractionBoundaryError("unsupported_capability")
                _foundation(arn)
                arns.append(arn)
        # Confirm every destination resolves in its own region before pinning it.
        # An unresolvable destination means the observed set is not the real one.
        for arn in arns:
            destination_region, name = _foundation(arn)
            client = control(destination_region)
            details = metadata(client.get_foundation_model, modelIdentifier=name).get(
                "modelDetails"
            )
            if not isinstance(details, dict) or details.get("modelArn") != arn:
                raise ExtractionBoundaryError("unsupported_capability")
        return RoutingSnapshot(
            model_id=model_id,
            kind=kind,
            account_id=account_id,
            calling_region=region,
            profile_arn=profile_arn,
            profile_status=profile_status,
            destinations=tuple(ModelDestination(arn=arn) for arn in arns),
            observed_at=observed_at or datetime.now().astimezone(),
        )
    except ExtractionBoundaryError:
        raise
    except Exception as error:
        code = provider_failure(error)
        raise ExtractionBoundaryError(
            "configuration_error" if code == "provider_error" else code
        ) from None
