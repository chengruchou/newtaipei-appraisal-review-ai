"""Frozen evaluation manifests; expectations and provider predictions stay separate."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from appraisal_review.domain.document_models import Digest
from appraisal_review.domain.extraction_contracts import (
    ContractModel,
    ExecutionBudget,
    ExtractionModel,
    PositiveCount,
    ProviderConfiguration,
    SanitizedSourceReference,
)
from appraisal_review.domain.service_contracts import OpaqueID


class EvaluationInput(ExtractionModel):
    source: SanitizedSourceReference
    pages: tuple[PositiveCount, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def selected_pages(self) -> EvaluationInput:
        if len(set(self.pages)) != len(self.pages) or max(self.pages) > self.source.page_count:
            raise ValueError("Duplicate or out-of-range evaluation page")
        return self


class ScoringPolicy(ExtractionModel):
    policy_version: OpaqueID
    policy_digest: Digest
    matching: Literal["one_to_one_identity"] = "one_to_one_identity"
    denominator: Literal["all_scheduled_eligible_cases"] = "all_scheduled_eligible_cases"
    zero_denominator: Literal["null"] = "null"
    strings: Literal["exact"] = "exact"
    numeric_absolute_tolerance: Decimal = Field(ge=0)
    numeric_relative_tolerance: Decimal = Field(ge=0)
    unit_policy: Literal["exact", "versioned_conversion"]
    unit_policy_digest: Digest | None
    missing_states: Literal["distinct"] = "distinct"
    localization: Literal["not_measured", "region_iou"]
    minimum_iou: float | None = Field(ge=0, le=1)
    acceptance_thresholds_digest: Digest | None

    @model_validator(mode="after")
    def explicit_policies(self) -> ScoringPolicy:
        if (self.localization == "region_iou") != (self.minimum_iou is not None):
            raise ValueError("Localization policy and threshold differ")
        if (self.unit_policy == "versioned_conversion") != (self.unit_policy_digest is not None):
            raise ValueError("Unit conversion requires its exact policy digest")
        return self


class CostPolicy(ExtractionModel):
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    rates_date: date
    rates_source: str = Field(min_length=1, max_length=1024)
    rates_digest: Digest
    input_per_million: Decimal = Field(ge=0)
    output_per_million: Decimal = Field(ge=0)
    estimated_cost_ceiling: Decimal = Field(gt=0)


class EvaluationManifest(ContractModel):
    """Ordered input schedule; validation does not certify independent adjudication."""

    schema_version: Literal["evaluation-v1"] = "evaluation-v1"
    dataset_id: OpaqueID
    dataset_version: OpaqueID
    dataset_digest: Digest
    dataset_kind: Literal["synthetic", "sanitized"]
    execution_kind: Literal["replay", "mocked", "live"]
    split: Literal["development", "held_out"]
    split_digest: Digest
    golden_digest: Digest
    golden_schema_version: OpaqueID
    adjudication: Literal["synthetic_expectations", "independent_reviewers"]
    inputs: tuple[EvaluationInput, ...] = Field(min_length=1)
    pipeline_version: OpaqueID
    prompt_version: OpaqueID
    prompt_digest: Digest
    proposal_schema_digest: Digest
    rendering_configuration_digest: Digest
    configuration: ProviderConfiguration
    budget: ExecutionBudget
    scoring: ScoringPolicy
    repeats: PositiveCount
    timing_scope: Literal["provider_only", "parse_render_provider"]
    cost_policy: CostPolicy | None

    @model_validator(mode="after")
    def schedule(self) -> EvaluationManifest:
        identities = [
            (i.source.document.case_id, i.source.document.document_id) for i in self.inputs
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("Duplicate scheduled document")
        if self.dataset_kind == "synthetic" and self.adjudication != "synthetic_expectations":
            raise ValueError("Synthetic expectations are not independent case adjudication")
        pages = sum(len(i.pages) for i in self.inputs) * self.repeats
        if pages > self.budget.max_pages or pages > self.budget.max_calls:
            raise ValueError("Schedule exceeds page or minimum call budget")
        if self.configuration.max_output_tokens > self.budget.max_output_tokens:
            raise ValueError("Per-call token limit exceeds total output budget")
        return self
