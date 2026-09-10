"""Extraction v1 envelopes. Structural validation never grants source authority."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import ConfigDict, Field, model_validator

from appraisal_review.domain.document_models import Digest, DocumentModel, SourceCitation
from appraisal_review.domain.extraction_models import PageProposal
from appraisal_review.domain.service_contracts import DocumentReference, OpaqueID, RunReference

Count = Annotated[int, Field(ge=0, strict=True)]
PositiveCount = Annotated[int, Field(ge=1, strict=True)]
Seconds = Annotated[float, Field(ge=0)]
FailureCode = Literal[
    "unauthorized_source",
    "privacy_unavailable",
    "source_changed",
    "unsupported_input",
    "unsupported_capability",
    "budget_exhausted",
    "access_denied",
    "configuration_error",
    "throttled",
    "service_unavailable",
    "timeout",
    "refused",
    "truncated_output",
    "malformed_output",
    "invalid_source_reference",
    "provider_error",
]


class ContractModel(DocumentModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, allow_inf_nan=False, revalidate_instances="always"
    )


class ExtractionModel(ContractModel):
    schema_version: Literal["extraction-v1"] = "extraction-v1"


class SanitizedSourceReference(ExtractionModel):
    """Reference to #22 evidence, not a duplicate PrivacyManifest or access token."""

    document: DocumentReference
    privacy_contract_version: Literal["privacy-v1"]
    privacy_manifest_digest: Digest
    page_count: PositiveCount

    @model_validator(mode="after")
    def extraction_purpose(self) -> SanitizedSourceReference:
        if self.document.purpose not in {"criteria", "forms", "reference", "brief"}:
            raise ValueError("Unsupported extraction purpose")
        return self


class ExtractionContext(ExtractionModel):
    """Closed context projection; no case identity strings or prior model prose."""

    language: Literal["zh-Hant", "en"]
    task: Literal["propose_rules", "propose_case", "inspect_reference"]


class PageRequest(ExtractionModel):
    run: RunReference
    source: SanitizedSourceReference
    page: PositiveCount
    context: ExtractionContext

    @model_validator(mode="after")
    def binding(self) -> PageRequest:
        if self.run.revision.case_id != self.source.document.case_id:
            raise ValueError("Request case mismatch")
        if self.page > self.source.page_count:
            raise ValueError("Request page outside source")
        allowed = {
            "criteria": "propose_rules",
            "forms": "propose_case",
            "reference": "inspect_reference",
            "brief": "inspect_reference",
        }
        if self.context.task != allowed[self.source.document.purpose]:
            raise ValueError("Request task and source purpose differ")
        return self


class ProviderConfiguration(ExtractionModel):
    """Pinned configuration metadata, not credentials or provider access proof."""

    provider: OpaqueID
    model_id: str = Field(min_length=1, max_length=512)
    region: OpaqueID
    api: OpaqueID
    routing_regions: tuple[OpaqueID, ...] = Field(min_length=1)
    temperature: float = Field(ge=0, le=1)
    max_output_tokens: PositiveCount

    @model_validator(mode="after")
    def unique_routes(self) -> ProviderConfiguration:
        if len(self.routing_regions) != len(set(self.routing_regions)):
            raise ValueError("Duplicate routing region")
        return self


class ExecutionBudget(ExtractionModel):
    max_pages: PositiveCount
    max_calls: PositiveCount
    max_attempts_per_page: PositiveCount
    max_concurrency: PositiveCount
    max_input_bytes: PositiveCount
    max_context_characters: PositiveCount
    max_output_tokens: PositiveCount
    max_elapsed_seconds: float = Field(gt=0)

    @model_validator(mode="after")
    def coherent_limits(self) -> ExecutionBudget:
        if self.max_attempts_per_page > self.max_calls or self.max_concurrency > self.max_calls:
            raise ValueError("Attempt or concurrency limit exceeds total call limit")
        return self


class AttemptTelemetry(ExtractionModel):
    attempt: PositiveCount
    completion: Literal["returned", "failed", "unknown"]
    failure: FailureCode | None
    input_tokens: Count | None
    output_tokens: Count | None
    elapsed_seconds: Seconds
    backoff_seconds: Seconds

    @model_validator(mode="after")
    def completion_evidence(self) -> AttemptTelemetry:
        if self.completion != "returned" and self.failure is None:
            raise ValueError("Failed or unknown completion needs a reason")
        if self.completion == "unknown" and self.failure != "timeout":
            raise ValueError("Unknown completion is reserved for timeout")
        return self


class ProviderTelemetry(ExtractionModel):
    configuration: ProviderConfiguration
    prompt_version: OpaqueID
    prompt_digest: Digest
    attempts: tuple[AttemptTelemetry, ...]
    elapsed_seconds: Seconds

    @model_validator(mode="after")
    def attempt_sequence(self) -> ProviderTelemetry:
        if tuple(a.attempt for a in self.attempts) != tuple(range(1, len(self.attempts) + 1)):
            raise ValueError("Attempts must be consecutive and ordered")
        if any(a.completion == "unknown" for a in self.attempts[:-1]):
            raise ValueError("Unknown completion cannot have a replacement attempt")
        if sum(a.elapsed_seconds + a.backoff_seconds for a in self.attempts) > (
            self.elapsed_seconds + 1e-9
        ):
            raise ValueError("Total elapsed time is shorter than attempt accounting")
        return self


class HandoffLocation(ExtractionModel):
    """Page-only location is valid when no trustworthy region or field is known."""

    page: PositiveCount
    region_id: OpaqueID | None = None
    field_id: OpaqueID | None = None


class HandoffRequest(ExtractionModel):
    """A request for #17/#24, never a HumanTask, confirmation or approval."""

    request: PageRequest
    reason: Literal["missing", "low_confidence", "conflicting", "unsupported", "page_failed"]
    locations: tuple[HandoffLocation, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def located(self) -> HandoffRequest:
        if any(location.page != self.request.page for location in self.locations):
            raise ValueError("Handoff location differs from requested page")
        if len(set(self.locations)) != len(self.locations):
            raise ValueError("Duplicate handoff location")
        return self


def proposal_citations(value: object) -> list[SourceCitation]:
    """Walk typed proposals without importing a provider adapter."""
    if isinstance(value, SourceCitation):
        return [value]
    if isinstance(value, DocumentModel):
        return [
            ref
            for field in type(value).model_fields
            for ref in proposal_citations(getattr(value, field))
        ]
    if isinstance(value, (list, tuple)):
        return [ref for item in value for ref in proposal_citations(item)]
    return []


class PageOutcome(ExtractionModel):
    """Success means a candidate was produced, not that facts are verified."""

    request: PageRequest
    status: Literal["candidate", "failed"]
    proposal: PageProposal | None
    failure: FailureCode | None
    telemetry: ProviderTelemetry
    handoffs: tuple[HandoffRequest, ...]

    @model_validator(mode="after")
    def honest_outcome(self) -> PageOutcome:
        if self.status == "candidate":
            if self.proposal is None or self.failure is not None:
                raise ValueError("Candidate needs a proposal without execution failure")
            if not self.telemetry.attempts or self.telemetry.attempts[-1].failure is not None:
                raise ValueError("Candidate needs a returned successful attempt")
        elif self.proposal is not None or self.failure is None:
            raise ValueError("Failed page cannot contain a successful proposal")
        if any(h.request != self.request for h in self.handoffs):
            raise ValueError("Handoff request binding differs from outcome")
        if self.status == "failed" and not any(h.reason == "page_failed" for h in self.handoffs):
            raise ValueError("Failed page requires a located handoff")
        document = self.request.source.document
        if self.proposal is not None:
            if self.proposal.rules and document.purpose != "criteria":
                raise ValueError("Rule proposals require criteria source purpose")
            if document.purpose != "forms" and any(
                (
                    self.proposal.contexts,
                    self.proposal.pairs,
                    self.proposal.observed,
                    self.proposal.slots,
                    self.proposal.empty_columns,
                )
            ):
                raise ValueError("Case proposals require forms source purpose")
        for ref in proposal_citations(self.proposal):
            if (ref.document_id, ref.version, ref.content_hash, ref.page) != (
                document.document_id,
                document.version,
                document.content_hash,
                self.request.page,
            ):
                raise ValueError("Proposal citation differs from requested source")
        return self
