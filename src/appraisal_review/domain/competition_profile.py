"""Versioned deployment constraints; profile data never grants permission by itself."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, model_validator

from appraisal_review.domain.document_models import Digest
from appraisal_review.domain.extraction_contracts import ContractModel, PositiveCount

PDF_SHA256 = "64bbda4d8056d3edd913ced8e96330f282621a00fe9d4152341d162fd385aec0"
WORKBOOK_SHA256 = "378eb61dba941647748f03b16ea4fb5f37ceba5610dd0f013dd759b0f5e0ccae"
PRIMARY_REGIONS = ("us-east-1", "us-west-2")
ENTRYPOINTS = frozenset({"extractor", "action_selector", "worker", "smoke", "evaluation", "cli"})
AccountID = Annotated[str, Field(pattern=r"^[0-9]{12}$")]
RoleARN = Annotated[str, Field(pattern=r"^arn:aws:iam::[0-9]{12}:role/[A-Za-z0-9+=,.@_/-]+$")]
IAMAction = Annotated[str, Field(pattern=r"^[a-z0-9-]+:[A-Za-z0-9]+$")]
Identifier = Annotated[str, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,127}$")]
ExactARN = Annotated[str, Field(pattern=r"^arn:aws:[a-z0-9-]+:[a-z0-9-]*:[0-9]*:[^\s*?]+$")]


class SheetCoverage(ContractModel):
    name: Literal["Services List", "SageMaker AI", "EC2"]
    rows_read: PositiveCount
    columns_read: PositiveCount


class SourceCoverage(ContractModel):
    """Coverage of originals, including blank, repeated header and note rows."""

    version: Literal["20260722"] = "20260722"
    pdf_sha256: Digest
    workbook_sha256: Digest
    pdf_pages_read: tuple[PositiveCount, ...]
    worksheets: tuple[SheetCoverage, ...]

    def complete(self) -> bool:
        return (
            self.pdf_sha256 == PDF_SHA256
            and self.workbook_sha256 == WORKBOOK_SHA256
            and self.pdf_pages_read == (1, 2)
            and len(self.worksheets) == 3
            and {(s.name, s.rows_read, s.columns_read) for s in self.worksheets}
            == {("Services List", 315, 4), ("SageMaker AI", 1909, 3), ("EC2", 11, 3)}
        )


class ModelDestination(ContractModel):
    arn: Annotated[
        str,
        Field(pattern=r"^arn:aws:bedrock:[a-z0-9-]+::foundation-model/[A-Za-z0-9.:-]+$"),
    ]

    @property
    def region(self) -> str:
        return self.arn.split(":", 5)[3]


class ModelUse(ContractModel):
    model_id: Annotated[str, Field(min_length=1, max_length=2048)]
    kind: Literal["foundation", "system_profile", "application_profile"]
    purpose: Literal["extractor", "action_selector"]
    destinations: tuple[ModelDestination, ...] = ()
    destination_snapshot_sha256: Digest | None = None
    context_window_tokens: PositiveCount | None = None
    review_interval_hours: PositiveCount = 24
    stop_method: Literal["deny_exact_model_invocation"] = "deny_exact_model_invocation"

    @model_validator(mode="after")
    def unique_destinations(self) -> ModelUse:
        if len({d.arn for d in self.destinations}) != len(self.destinations):
            raise ValueError("Duplicate model destinations")
        if any(c in self.model_id for c in ("*", "?", "\n", "\r")):
            raise ValueError("Model identifier must be exact")
        return self


class ThrottlePolicy(ContractModel):
    """1.1 seconds is a conservative implementation setting, not a source quote."""

    scope: Literal["team-wide"] = "team-wide"
    interval_seconds: Annotated[float, Field(ge=1.1)] = 1.1
    includes_control_plane: Annotated[bool, Field(strict=True)] = True
    includes_count_tokens: Annotated[bool, Field(strict=True)] = True
    central_store_arn: ExactARN | None = None
    connected_entrypoints: tuple[Identifier, ...] = ()
    integration_evidence_sha256: Digest | None = None
    scope_approval_reference: Identifier | None = None


class OperationReservation(ContractModel):
    operation: Identifier
    model_id: Annotated[str, Field(min_length=1, max_length=2048)]
    max_request_bytes: PositiveCount
    input_tokens: Annotated[int, Field(ge=0, strict=True)]
    output_tokens: Annotated[int, Field(ge=0, strict=True)]
    cost_usd: Annotated[Decimal, Field(ge=0, max_digits=16, decimal_places=8)]
    bound_evidence_sha256: Digest
    destination_snapshot_sha256: Digest
    input_price_per_million_usd: Annotated[Decimal, Field(ge=0)] | None = None
    output_price_per_million_usd: Annotated[Decimal, Field(ge=0)] | None = None
    maximum_request_fee_usd: Annotated[Decimal, Field(ge=0)] | None = None

    def covers_worst_case(self, models: tuple[ModelUse, ...]) -> bool:
        """Trusted price/context ceilings; never a tokenizer or measured usage claim."""
        if (
            self.input_price_per_million_usd is None
            or self.output_price_per_million_usd is None
            or self.maximum_request_fee_usd is None
        ):
            return False
        if self.operation in {"Converse", "InvokeModel", "CountTokens"} and (
            not models
            or any(
                m.context_window_tokens is None or self.input_tokens < m.context_window_tokens
                for m in models
            )
        ):
            return False
        if self.operation in {"Converse", "InvokeModel"} and (
            self.input_price_per_million_usd <= 0 or self.output_price_per_million_usd <= 0
        ):
            return False
        minimum = (
            self.input_tokens * self.input_price_per_million_usd
            + self.output_tokens * self.output_price_per_million_usd
        ) / Decimal(1000000) + self.maximum_request_fee_usd
        return self.cost_usd >= minimum


class BudgetLimits(ContractModel):
    scope: Literal["team-wide"] = "team-wide"
    window_seconds: PositiveCount
    max_calls: PositiveCount
    max_input_tokens: PositiveCount
    max_output_tokens: PositiveCount
    max_cost_usd: Annotated[Decimal, Field(gt=0, max_digits=16, decimal_places=8)]
    pricing_evidence_sha256: Digest
    window_id: Identifier | None = None
    window_start: datetime | None = None
    operation_reservations: tuple[OperationReservation, ...] = ()

    @model_validator(mode="after")
    def fixed_window(self) -> BudgetLimits:
        if self.window_start is not None and self.window_start.tzinfo is None:
            raise ValueError("Budget window must have an explicit timezone")
        keys = [(r.operation, r.model_id) for r in self.operation_reservations]
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate operation reservation")
        return self


class ResourceBinding(ContractModel):
    """Explicit ownership and reuse plan; tags alone do not stop a resource."""

    name: Identifier
    kind: Literal["runtime", "bucket", "table", "queue", "dashboard", "worker", "schedule", "model"]
    mode: Literal["reuse", "create_once"]
    arn: ExactARN | None = None
    allowed_prefixes: tuple[
        Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_./-]*/$")], ...
    ] = ()
    allowed_indexes: tuple[Identifier, ...] = ()
    owner: Identifier
    stop_method: Literal[
        "retain_evidence",
        "disable_invocation_and_stop_sessions",
        "disable_event_source_and_zero_concurrency",
        "disable_schedule",
        "deny_exact_model_invocation",
        "no_compute",
    ]
    stop_verification_reference: Identifier | None = None


class CompetitionProfile(ContractModel):
    """Load only from trusted server configuration, never an HTTP/model payload.

    A separate trusted digest pin authorizes this exact configuration. There is
    deliberately no caller-controlled `approved` flag. This is not a data export
    authorization and cannot override the competition data-admission policy.
    """

    schema_version: Literal["competition-profile-v1"] = "competition-profile-v1"
    profile_id: Identifier
    sources: SourceCoverage | None = None
    account_id: AccountID | None = None
    role_arn: RoleARN | None = None
    role_permissions_evidence_sha256: Digest | None = None
    primary_region: Literal["us-east-1", "us-west-2"] = "us-east-1"
    iam_actions: tuple[IAMAction, ...] = ()
    models: tuple[ModelUse, ...] = ()
    allow_cross_region: Annotated[bool, Field(strict=True)] = False
    allow_global: Annotated[bool, Field(strict=True)] = False
    routing_approval_reference: Identifier | None = None
    throttle: ThrottlePolicy = Field(default_factory=ThrottlePolicy)
    budget: BudgetLimits | None = None
    resources: tuple[ResourceBinding, ...] = ()
    invocation_auth: Literal["iam_sigv4", "jwt"] = "iam_sigv4"
    invocation_principal_arns: tuple[RoleARN, ...] = ()
    invocation_auth_evidence_sha256: Digest | None = None
    data_policy_digest: Digest | None = None
    # Distinct from provenance or desensitization; never inferred from financial
    # arithmetic in a synthetic demonstration.
    synthetic_financial_organizer_reference: Identifier | None = None

    @model_validator(mode="after")
    def unambiguous(self) -> CompetitionProfile:
        if len(set(self.iam_actions)) != len(self.iam_actions):
            raise ValueError("Duplicate IAM actions")
        if len({r.name for r in self.resources}) != len(self.resources):
            raise ValueError("Duplicate resource bindings")
        if len({(m.model_id, m.purpose) for m in self.models}) != len(self.models):
            raise ValueError("Duplicate model use")
        if self.account_id and self.role_arn and self.role_arn.split(":")[4] != self.account_id:
            raise ValueError("Role belongs to another account")
        return self

    @property
    def digest(self) -> str:
        value = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(value.encode()).hexdigest()


# Explicit API-to-IAM mapping. Unknown operations require a verified addition;
# guessing a permission name from an SDK operation is not permitted.
API_IAM_ACTIONS: dict[str, tuple[str, ...]] = {
    "Converse": ("bedrock:InvokeModel",),
    "ConverseStream": ("bedrock:InvokeModelWithResponseStream",),
    "InvokeModel": ("bedrock:InvokeModel",),
    "InvokeModelWithResponseStream": ("bedrock:InvokeModelWithResponseStream",),
    "CountTokens": ("bedrock:CountTokens",),
    "GetFoundationModel": ("bedrock:GetFoundationModel",),
    "GetInferenceProfile": ("bedrock:GetInferenceProfile",),
    "InvokeAgentRuntime": ("bedrock-agentcore:InvokeAgentRuntime",),
}


def iam_actions_for_api(operation: str) -> tuple[str, ...]:
    try:
        return API_IAM_ACTIONS[operation]
    except KeyError:
        raise ValueError("Unsupported API-to-IAM mapping") from None


def foundation_arn_matches(model: ModelUse, primary_region: str) -> bool:
    if model.kind != "foundation":
        return True
    identifier = model.model_id
    arn = (
        identifier
        if identifier.startswith("arn:")
        else (f"arn:aws:bedrock:{primary_region}::foundation-model/{identifier}")
    )
    return tuple(d.arn for d in model.destinations) == (arn,)


def exact_account_resource(arn: str, account: str, region: str) -> bool:
    match = re.fullmatch(r"arn:aws:([a-z0-9-]+):([a-z0-9-]*):([0-9]*):([^\s*?]+)", arn)
    if match is None:
        return False
    service, actual_region, actual_account, resource = match.groups()
    if service == "s3":
        return not actual_region and not actual_account
    if service == "bedrock" and resource.startswith("foundation-model/"):
        return actual_region == region and not actual_account
    return actual_region in (region, "") and actual_account == account


def resource_kind_matches(kind: str, arn: str) -> bool:
    patterns = {
        "runtime": r"bedrock-agentcore:[^:]+:[0-9]{12}:runtime/[A-Za-z0-9_-]+",
        "bucket": r"s3:::[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]",
        "table": r"dynamodb:[^:]+:[0-9]{12}:table/[A-Za-z0-9_.-]+",
        "queue": r"sqs:[^:]+:[0-9]{12}:[A-Za-z0-9_.-]+",
        "dashboard": r"cloudwatch::[0-9]{12}:dashboard/[A-Za-z0-9_-]+",
        "worker": r"lambda:[^:]+:[0-9]{12}:function:[A-Za-z0-9_-]+",
        "schedule": (
            r"(?:events:[^:]+:[0-9]{12}:rule/[^\s*?]+|"
            r"scheduler:[^:]+:[0-9]{12}:schedule/[^\s*?]+)"
        ),
        "model": (
            r"bedrock:[a-z0-9-]+:(?:[0-9]{12})?:"
            r"(?:foundation-model|inference-profile|application-inference-profile)/[A-Za-z0-9.:-]+"
        ),
    }
    pattern = patterns.get(kind)
    return pattern is not None and re.fullmatch("arn:aws:" + pattern, arn) is not None
