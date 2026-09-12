"""Read-only workbench projections; no new approval or rule registry authority."""

from typing import Literal

from pydantic import Field

from appraisal_review.domain.document_models import Digest, DocumentModel, SourceCitation
from appraisal_review.domain.factor_models import EvaluationStatus, RuleApplicability
from appraisal_review.domain.job_contracts import JobReference
from appraisal_review.domain.review_contracts import (
    CaseIdentity,
    ComparisonContext,
    Coverage,
    ReviewFinding,
)
from appraisal_review.domain.rule_sources import CaseConditionCandidate, CatalogRuleSource
from appraisal_review.domain.service_contracts import (
    ActorReference,
    DocumentReference,
    FactSideReference,
    PublicValue,
    RevisionReference,
    RuleReference,
    RunReference,
    ServiceModel,
    ServiceVerification,
)


class ReviewSessionView(ServiceModel):
    """The currently authenticated actor, not a registration or permission grant."""

    actor: ActorReference
    data_mode: Literal["unspecified", "synthetic", "local_original"] = "unspecified"
    configured_jobs: tuple[JobReference, ...] = ()


class PinnedRuleView(ServiceModel):
    reference: RuleReference
    applicability: RuleApplicability
    zone: str
    declared_status: Literal["candidate", "approved", "rejected"]
    evidence: tuple[SourceCitation, ...]


class RuleSelectionView(ServiceModel):
    context: ComparisonContext
    status: Literal["unique", "missing", "ambiguous"]
    matches: tuple[RuleReference, ...]


class CaseObservationView(ServiceModel):
    side: FactSideReference
    observation: PublicValue


class RuleBundleView(DocumentModel):
    """Selected, authorized sources only; the full internal catalog stays private."""

    catalog_version: str
    catalog_digest: Digest
    primary_criteria_document_id: str
    identity: CaseIdentity
    contexts: list[ComparisonContext]
    sources: list[CatalogRuleSource]
    conditions_confirmed: bool
    condition_candidates: list[CaseConditionCandidate] = Field(default_factory=list)


class CaseContextView(ServiceModel):
    """Exact current material metadata; selection is not confirmation or approval.

    The exact semantic snapshot is required. No source path, raw material payload,
    principal roster or future deployment region is exposed. Document purposes are
    preserved, not inferred as general rules, district prices or report templates.
    """

    job: JobReference
    revision: RevisionReference
    identity: CaseIdentity | None
    documents: tuple[DocumentReference, ...] = ()
    rules: tuple[PinnedRuleView, ...] = ()
    selections: tuple[RuleSelectionView, ...] = ()
    observations: tuple[CaseObservationView, ...] = ()
    rule_bundle: RuleBundleView | None = None
    rule_bundle_id: Digest | None = None


class PausedReviewView(ServiceModel):
    """An exact stored human-handoff assessment, never a committed or published result."""

    scope: Literal["paused_review"] = "paused_review"
    run: RunReference
    status: EvaluationStatus
    findings: tuple[ReviewFinding, ...]
    coverage: Coverage
    verification: ServiceVerification | None
