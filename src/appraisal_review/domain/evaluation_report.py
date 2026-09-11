"""Additive replay bindings and value-free reports; evaluation-v1 remains authoritative."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import Field

from appraisal_review.domain.document_models import Digest
from appraisal_review.domain.extraction_contracts import (
    ContractModel,
    Count,
    FailureCode,
    PageOutcome,
    PositiveCount,
    Seconds,
)
from appraisal_review.domain.service_contracts import OpaqueID


class EvaluationVersions(ContractModel):
    parser_version: OpaqueID
    parser_digest: Digest
    normalizer_version: OpaqueID
    normalizer_digest: Digest


class GoldenBinding(ContractModel):
    case_id: OpaqueID
    golden_case_key: str = Field(min_length=1)


class RepeatedOutcome(ContractModel):
    repeat: PositiveCount
    outcome: PageOutcome


class EvaluationExecutionError(ContractModel):
    """A callback failed without an outcome; attempts and tokens are unknown."""

    repeat: PositiveCount
    input_index: Count
    page: PositiveCount
    failure: FailureCode
    elapsed_seconds: Seconds


class TokenPriceEvidence(ContractModel):
    """Locally supplied, dated rate record; not proof of current provider pricing."""

    configuration_digest: Digest
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    rates_date: date
    rates_source: str = Field(min_length=1, max_length=1024)
    input_per_million: Decimal = Field(ge=0)
    output_per_million: Decimal = Field(ge=0)
    scope: Literal["input_output_tokens_only"] = "input_output_tokens_only"


class EvaluationReplay(ContractModel):
    """Private local input, never a public report or a second evaluation manifest."""

    schema_version: Literal["evaluation-replay-v1"] = "evaluation-replay-v1"
    manifest_digest: Digest
    versions: EvaluationVersions
    bindings: tuple[GoldenBinding, ...] = Field(min_length=1)
    outcomes: tuple[RepeatedOutcome, ...]
    execution_errors: tuple[EvaluationExecutionError, ...] = ()
    pricing_evidence: TokenPriceEvidence | None = None


class Metric(ContractModel):
    numerator: Count
    denominator: Count
    value: float | None


class Quantity(ContractModel):
    """Known subtotal is not a total when observations or usage are missing."""

    known: float
    total: float | None
    unknown_records: Count


class TokenQuantity(ContractModel):
    known: Count
    total: Count | None
    unknown_attempts: Count
    unobserved_pages: Count


class UsageReport(ContractModel):
    observed_attempts: Count
    total_attempts: Count | None
    observed_retries: Count
    total_retries: Count | None
    unknown_completion_attempts: Count
    input_tokens: TokenQuantity
    output_tokens: TokenQuantity
    retry_input_tokens: TokenQuantity
    retry_output_tokens: TokenQuantity
    page_elapsed_seconds: Quantity
    attempt_elapsed_seconds: Quantity
    backoff_seconds: Quantity
    retry_elapsed_seconds: Quantity
    callback_error_elapsed_seconds: Seconds
    mean_observed_page_seconds: float | None
    p50_observed_page_seconds: float | None
    p95_observed_page_seconds: float | None


class CostEstimate(ContractModel):
    amount: Decimal | None
    known_usage_subtotal: Decimal | None
    currency: str | None
    evidence_digest: Digest | None
    reason: Literal["estimated", "no_policy", "no_rate_evidence", "unknown_usage"]
    exceeds_ceiling: bool | None
    scope: Literal["input_output_tokens_only"] = "input_output_tokens_only"


class ScoreSummary(ContractModel):
    counts: dict[str, Count]
    metrics: dict[str, Metric]
    expected_states: dict[str, Count]
    predicted_states: dict[str, Count]
    state_confusion: dict[str, dict[str, Count]]
    field_errors: dict[str, Count]
    page_failures: dict[str, Count]
    attempt_failures: dict[str, Count]
    handoff_reasons: dict[str, Count]
    unscored_proposals: dict[str, Count]
    usage: UsageReport
    cost: CostEstimate


class RepeatDifference(ContractModel):
    left_repeat: PositiveCount
    right_repeat: PositiveCount
    scheduled_pages: Count
    observed_in_both: Count
    missing_left: Count
    missing_right: Count
    changed_pages: Count
    changes: dict[str, Count]
    metric_differences: dict[str, float | None]


class EvaluationReport(ContractModel):
    schema_version: Literal["evaluation-report-v1"] = "evaluation-report-v1"
    manifest_digest: Digest
    replay_digest: Digest
    golden_digest: Digest
    dataset_digest: Digest
    split_digest: Digest
    dataset_version: OpaqueID
    dataset_kind: Literal["synthetic", "sanitized"]
    execution_kind: Literal["replay", "mocked", "live"]
    split: Literal["development", "held_out"]
    pipeline_version: OpaqueID
    prompt_version: OpaqueID
    prompt_digest: Digest
    proposal_schema_digest: Digest
    rendering_configuration_digest: Digest
    configuration_digest: Digest
    scoring_policy_digest: Digest
    versions: EvaluationVersions
    timing_scope: Literal["provider_only", "parse_render_provider"]
    field_scope: Literal["golden_observed_slots"] = "golden_observed_slots"
    summary: ScoreSummary
    repeats: tuple[ScoreSummary, ...]
    differences: tuple[RepeatDifference, ...]
    determinism_claim: Literal["none"] = "none"
    limitations: tuple[str, ...]
