"""Formal report approval: a named person approves one exact report version.

"Formal" here means system-formal: a designated report version that a person holding
the publish permission examined and approved, bound to the exact bytes that will be
delivered. It claims nothing about statutory certification, electronic seals or
official-document effect - evidence for those does not exist in this system.

What the models refuse to blur:

- Approval binds content, not intent. A request pins the revision, the calculation
  snapshot digest, the template bundle and the sha256 of every filled workbook the
  approver will preview. Publishing later must reproduce those exact hashes or refuse;
  nothing re-fills values after a person approved different bytes.
- Readiness is a policy decision, not an empty-gaps check. A snapshot with no recorded
  gaps can still be unapprovable when the required price chain is absent, and a lawful
  not_applicable never blocks by itself - but only when its justification is recorded.
- Decisions are events by authenticated actors with server timestamps. A request body
  never names its own approver, and a replayed command returns the original decision
  instead of minting a second one.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from appraisal_review.domain.document_models import Digest
from appraisal_review.domain.official_export import ExportBasis, TemplateBundleReference
from appraisal_review.domain.official_table_mapping import OfficialTable
from appraisal_review.domain.review_contracts import content_digest
from appraisal_review.domain.service_contracts import (
    ActorReference,
    OpaqueID,
    RunReference,
    ServiceModel,
)

#: One live approval request per exact content binding; these are the request states.
ApprovalStatus = Literal["submitted", "approved", "returned", "withdrawn", "superseded"]
#: Case-level readiness for submitting a formal request.
ReadinessState = Literal["pending_data", "ready_to_submit"]
DecisionKind = Literal["approve", "return", "withdraw"]

READINESS_POLICY_VERSION = "formal-readiness-v1"


class ReadinessBlocker(ServiceModel):
    """One concrete reason a formal request cannot be submitted yet."""

    code: Literal[
        "required_value_missing",
        "unjustified_not_applicable",
        "unjustified_confirmed_zero",
        "snapshot_not_registered",
        "stale_snapshot",
        "bundle_mismatch",
        "human_task_open",
        "na_not_permitted",
        "invalid_value",
        "unit_mismatch",
        "weight_sum_invalid",
        "arithmetic_mismatch",
        "calculation_policy_unconfirmed",
    ]
    message: str = Field(min_length=1, max_length=512)
    source_key: str | None = Field(default=None, max_length=256)
    table: OfficialTable | None = None
    subject_id: str | None = Field(default=None, max_length=32)
    current_state: str | None = Field(default=None, max_length=64)
    needed: str = Field(min_length=1, max_length=512)
    action: str = Field(min_length=1, max_length=512)


class ReportReadiness(ServiceModel):
    """Evaluated against the live snapshot; never cached across revisions."""

    policy_version: str = READINESS_POLICY_VERSION
    state: ReadinessState
    blockers: tuple[ReadinessBlocker, ...] = ()
    required_total: int = Field(ge=0)
    required_satisfied: int = Field(ge=0)

    @model_validator(mode="after")
    def coherent(self) -> ReportReadiness:
        if (self.state == "ready_to_submit") != (not self.blockers):
            raise ValueError("Ready means no blockers; blocked means at least one")
        if self.required_satisfied > self.required_total:
            raise ValueError("Satisfied cannot exceed required")
        return self


class ReportVersionBinding(ServiceModel):
    """The exact content a person approves: one hash per official table.

    Deliberately content-only: the snapshot digest, the template bundle and the three
    filled-workbook hashes. Which readiness policy admitted the submission is recorded
    on the approval itself, so two services computing the binding from the same bytes
    always agree on its identity.
    """

    calculation_snapshot_digest: Digest
    template_bundle: TemplateBundleReference
    workbook_hashes: dict[OfficialTable, Digest] = Field(min_length=3, max_length=3)

    def digest(self) -> str:
        return content_digest(self)


class SubmitReportApproval(ServiceModel):
    """Untrusted command; the server computes the binding it will be judged by."""

    idempotency_key: OpaqueID
    run: RunReference
    calculation_snapshot_digest: Digest
    template_bundle: TemplateBundleReference

    def payload_digest(self) -> str:
        return content_digest(self)


class ApprovalDecisionCommand(ServiceModel):
    """approve / return / withdraw, by the authenticated caller, never a named body."""

    idempotency_key: OpaqueID
    decision: DecisionKind
    reason: str = Field(default="", max_length=1024)

    @model_validator(mode="after")
    def reason_required(self) -> ApprovalDecisionCommand:
        if self.decision in {"return", "withdraw"} and not self.reason.strip():
            raise ValueError("Returning or withdrawing records why")
        return self

    def payload_digest(self) -> str:
        return content_digest(self)


class ApprovalDecision(ServiceModel):
    decision: DecisionKind
    actor: ActorReference
    decided_at: int = Field(ge=0, description="Server unix seconds; never client-supplied")
    reason: str = Field(default="", max_length=1024)


class ReportApproval(ServiceModel):
    """The durable approval request with its immutable content binding."""

    approval_id: UUID
    job_id: UUID
    run: RunReference
    binding: ReportVersionBinding
    status: ApprovalStatus
    submitted_by: ActorReference
    submitted_at: int = Field(ge=0)
    readiness_policy_version: str = READINESS_POLICY_VERSION
    payload_digest: Digest
    decision: ApprovalDecision | None = None

    @model_validator(mode="after")
    def status_decision(self) -> ReportApproval:
        decided = self.status in {"approved", "returned", "withdrawn"}
        if decided and self.decision is None:
            raise ValueError("A decided approval carries its decision event")
        if self.status == "submitted" and self.decision is not None:
            raise ValueError("An undecided approval carries no decision")
        if self.decision is not None:
            expected = {"approved": "approve", "returned": "return", "withdrawn": "withdraw"}
            if self.status in expected and self.decision.decision != expected[self.status]:
                raise ValueError("Status and decision kind must agree")
        return self

    def authorizes_publication(self) -> bool:
        """Only a currently approved request authorizes a formal publication."""
        return self.status == "approved"


class FormalExportBasis(ServiceModel):
    """The basis view plus formal readiness and the approval for the current content."""

    job_id: UUID
    run: RunReference
    calculation_snapshot_digest: Digest
    template_bundle: TemplateBundleReference
    blockers: tuple[str, ...] = ()
    readiness: ReportReadiness | None = None
    approval: ReportApproval | None = None

    @classmethod
    def from_basis(
        cls,
        basis: ExportBasis,
        readiness: ReportReadiness | None,
        approval: ReportApproval | None,
    ) -> FormalExportBasis:
        return cls(
            job_id=basis.job_id,
            run=basis.run,
            calculation_snapshot_digest=basis.calculation_snapshot_digest,
            template_bundle=basis.template_bundle,
            blockers=basis.blockers,
            readiness=readiness,
            approval=approval,
        )
