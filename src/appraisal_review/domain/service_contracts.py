"""Service v1 wire contracts; DTO validity never grants identity or authority.

Existing review/material/PDF types remain authoritative. Durable operations are
reserved ports, not mounted HTTP endpoints. See docs/service-contracts.md.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import ConfigDict, Field, model_validator

from appraisal_review.domain.document_models import Digest, DocumentModel, SourceCitation
from appraisal_review.domain.factor_models import (
    ArtifactStatus,
    EvaluationStatus,
    NormalizedValue,
    WorkflowStatus,
)
from appraisal_review.domain.review_contracts import ComparisonContext, ReviewFinding

OpaqueID = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")]


class ServiceModel(DocumentModel):
    model_config = ConfigDict(
        extra="forbid", allow_inf_nan=False, revalidate_instances="always", frozen=True
    )
    schema_version: Literal["service-v1"] = "service-v1"


class DocumentReference(ServiceModel):
    """External identity: no caller-selected storage URI or asserted authority."""

    case_id: OpaqueID
    document_id: OpaqueID
    version: OpaqueID
    content_hash: Digest
    purpose: Literal["criteria", "forms", "reference", "brief", "template"]


class RevisionReference(ServiceModel):
    case_id: OpaqueID
    revision_id: OpaqueID
    material_digest: Digest


class RuleReference(ServiceModel):
    rule_set_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    context: ComparisonContext
    content_hash: Digest


class ActorReference(ServiceModel):
    """Server-authored record, never an external credential."""

    actor_id: str = Field(min_length=1)
    kind: Literal["human", "system", "model"]


class PublicValue(ServiceModel):
    """URI-free view of an original/proposed value; never a trusted fact input."""

    state: Literal["present", "blank", "missing", "not_present", "not_applicable"]
    value: NormalizedValue | str | Decimal | None = None
    raw_text: str
    unit: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    evidence: tuple[SourceCitation, ...] = ()

    @model_validator(mode="after")
    def state_value(self) -> PublicValue:
        if (self.state == "present") != (self.value is not None):
            raise ValueError("Only present public values carry a value")
        return self


class ValueRevision(ServiceModel):
    subject_id: str = Field(min_length=1)
    original: PublicValue
    proposed: PublicValue | None = None
    corrected: PublicValue | None = None
    corrected_by: ActorReference | None = None

    @model_validator(mode="after")
    def correction_actor(self) -> ValueRevision:
        if (self.corrected is None) != (self.corrected_by is None):
            raise ValueError("Correction and actor must occur together")
        if self.corrected is not None and (
            self.corrected_by is None or self.corrected_by.kind != "human"
        ):
            raise ValueError("A correction requires a human actor record")
        return self


class MaterialRevision(ServiceModel):
    reference: RevisionReference
    parent: RevisionReference | None = None
    documents: tuple[DocumentReference, ...] = Field(min_length=2)
    rules: tuple[RuleReference, ...] = Field(min_length=1)
    changes: tuple[ValueRevision, ...] = ()
    canonicalization: Literal["review-material-json-v1"] = "review-material-json-v1"

    @model_validator(mode="after")
    def identities(self) -> MaterialRevision:
        if any(d.case_id != self.reference.case_id for d in self.documents):
            raise ValueError("Document case does not match revision")
        if len({d.document_id for d in self.documents}) != len(self.documents):
            raise ValueError("Duplicate revision document")
        if self.parent and (
            self.parent.case_id != self.reference.case_id
            or self.parent.revision_id == self.reference.revision_id
            or self.parent.material_digest == self.reference.material_digest
        ):
            raise ValueError("A child revision requires a new exact material version")
        return self


class RunReference(ServiceModel):
    run_id: UUID
    revision: RevisionReference
    attempt_id: UUID | None = None
    runtime_session_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def separate_session(self) -> RunReference:
        if self.runtime_session_id is not None and self.attempt_id is None:
            raise ValueError("Runtime sessions belong to an attempt, not a durable run")
        return self


class Permission(StrEnum):
    REVIEW = "review"
    CONFIRM = "confirm_observation"
    CORRECT = "correct_material"
    APPROVE_RULES = "approve_rules"
    APPROVE_MATERIAL = "approve_material"
    PUBLISH = "publish_artifact"


class TaskKind(StrEnum):
    FACT = "fact_confirmation"
    CORRECTION = "material_correction"
    RULES = "rule_approval"
    MATERIAL = "material_approval"
    PUBLICATION = "publication_authorization"


class ResponseAction(StrEnum):
    CONFIRM = "confirm"
    CORRECT = "correct"
    REJECT = "reject"
    APPROVE = "approve"
    AUTHORIZE_PUBLICATION = "authorize_publication"


class FactSideReference(ServiceModel):
    context: ComparisonContext
    factor_id: str = Field(min_length=1)
    side: Literal["target", "comparable"]
    input_digest: Digest


class HumanTask(ServiceModel):
    task_id: UUID
    run: RunReference
    version: int = Field(ge=1, strict=True)
    kind: TaskKind
    required_permission: Permission
    state: Literal["open", "answered", "superseded"] = "open"
    side: FactSideReference | None = None
    result_digest: Digest | None = None
    question: str = Field(min_length=1)
    evidence: tuple[SourceCitation, ...] = ()
    finding_ids: tuple[str, ...] = Field(min_length=1)
    allowed_responses: tuple[ResponseAction, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def task_authority(self) -> HumanTask:
        permissions = {
            TaskKind.FACT: Permission.CONFIRM,
            TaskKind.CORRECTION: Permission.CORRECT,
            TaskKind.RULES: Permission.APPROVE_RULES,
            TaskKind.MATERIAL: Permission.APPROVE_MATERIAL,
            TaskKind.PUBLICATION: Permission.PUBLISH,
        }
        actions = {
            TaskKind.FACT: ResponseAction.CONFIRM,
            TaskKind.CORRECTION: ResponseAction.CORRECT,
            TaskKind.RULES: ResponseAction.APPROVE,
            TaskKind.MATERIAL: ResponseAction.APPROVE,
            TaskKind.PUBLICATION: ResponseAction.AUTHORIZE_PUBLICATION,
        }
        if self.required_permission != permissions[self.kind] or not set(
            self.allowed_responses
        ) <= {actions[self.kind], ResponseAction.REJECT}:
            raise ValueError("Task permission and actions must match its purpose")
        if (self.kind == TaskKind.PUBLICATION) != (self.result_digest is not None):
            raise ValueError("Only publication tasks bind an exact result digest")
        if self.kind == TaskKind.FACT and self.side is None:
            raise ValueError("Observation confirmation requires an exact side")
        return self


class HumanResponse(ServiceModel):
    """Untrusted command: principal/roles and approval flags are not accepted."""

    task_id: UUID
    expected_version: int = Field(ge=1, strict=True)
    revision: RevisionReference
    side_digest: Digest | None = None
    result_digest: Digest | None = None
    idempotency_key: OpaqueID
    action: ResponseAction
    correction: ValueRevision | None = None

    @model_validator(mode="after")
    def action_payload(self) -> HumanResponse:
        if (self.action == ResponseAction.CORRECT) != (self.correction is not None):
            raise ValueError("Only correction commands contain a proposed correction")
        # The external payload proposes a value; only the server attributes a correction.
        if self.correction and (
            self.correction.corrected is not None
            or self.correction.corrected_by is not None
            or self.correction.proposed is None
        ):
            raise ValueError("A caller cannot assert a corrected value or actor")
        return self


class AcceptedResponse(ServiceModel):
    command: HumanResponse
    actor: ActorReference
    """Admission record only; persistence and material mutation are separate."""


class AuthorizationRecord(ServiceModel):
    """Reserved server record; never interchangeable with a signed local receipt."""

    authorization_id: UUID
    purpose: Literal["rules", "material", "publication"]
    revision: MaterialRevision
    actor: ActorReference
    result_digest: Digest | None = None

    @model_validator(mode="after")
    def publication_binding(self) -> AuthorizationRecord:
        if self.actor.kind != "human":
            raise ValueError("Authorization requires a trusted human actor")
        if (self.purpose == "publication") != (self.result_digest is not None):
            raise ValueError("Publication additionally binds the exact result")
        return self


class ActionKind(StrEnum):
    EXTRACT = "extract_page"
    REFERENCE = "inspect_reference"
    HUMAN = "request_human_review"
    REVIEW = "deterministic_review"


class AllowedAction(ServiceModel):
    action: ActionKind
    prerequisites: tuple[str, ...] = ()


class ActionProposal(ServiceModel):
    proposal_id: UUID
    run: RunReference
    action: ActionKind
    proposer: ActorReference
    model_id: str | None = Field(default=None, min_length=1)
    prompt_version: OpaqueID | None = None
    document: DocumentReference | None = None
    page: int | None = Field(default=None, ge=1)
    evidence: tuple[SourceCitation, ...] = ()


class Budget(ServiceModel):
    steps_remaining: int = Field(ge=0, strict=True)
    model_calls_remaining: int = Field(ge=0, strict=True)
    retries_remaining: int = Field(ge=0, strict=True)


class ServiceErrorCode(StrEnum):
    VALIDATION = "invalid_request"
    UNAUTHORIZED = "unauthorized"
    NOT_FOUND = "not_found"
    CONFLICT = "version_conflict"
    CAPABILITY = "capability_unavailable"
    EXECUTION = "execution_failed"


class ServiceProblem(ServiceModel):
    code: ServiceErrorCode
    message: Literal["Service operation could not be completed."] = (
        "Service operation could not be completed."
    )


class ToolOutcome(ServiceModel):
    outcome: Literal["succeeded", "failed"]
    result_digest: Digest | None = None
    problem: ServiceProblem | None = None

    @model_validator(mode="after")
    def outcome_evidence(self) -> ToolOutcome:
        if self.outcome == "succeeded" and (self.result_digest is None or self.problem is not None):
            raise ValueError("Successful tools require a digest and no failure")
        if self.outcome == "failed" and self.problem is None:
            raise ValueError("Failed tools require a sanitized problem")
        return self


class DecisionEvent(ServiceModel):
    event_id: UUID
    parent_event_ids: tuple[UUID, ...] = ()
    proposal: ActionProposal
    executor: ActorReference | None = None
    policy_version: OpaqueID
    disposition: Literal["rejected", "executed", "failed"]
    executed_action: ActionKind | None = None
    checked_prerequisites: tuple[str, ...] = ()
    reason_code: OpaqueID
    tool_result: ToolOutcome | None = None
    remaining_blockers: tuple[str, ...] = ()
    budget: Budget | None = None

    @model_validator(mode="after")
    def actual_execution(self) -> DecisionEvent:
        if self.disposition == "rejected":
            if self.executed_action or self.executor or self.tool_result:
                raise ValueError("A rejected action cannot claim tool execution")
        elif (
            self.executed_action != self.proposal.action
            or self.executor is None
            or self.tool_result is None
        ):
            raise ValueError("Record the actual proposed action and tool result")
        if self.tool_result and self.tool_result.outcome != (
            "succeeded" if self.disposition == "executed" else "failed"
        ):
            raise ValueError("Decision disposition must match tool outcome")
        return self


class ExecutionStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ArtifactManifest(ServiceModel):
    artifact_id: UUID
    content_hash: Digest
    media_type: Literal["application/pdf"] = "application/pdf"
    scope: Literal["single_context"] = "single_context"
    context: ComparisonContext
    field_ids: tuple[str, ...] = Field(min_length=1)
    page_count: int = Field(ge=1)
    template_hash: Digest
    field_map_hash: Digest
    verification: Literal["local_writer_reopened"] = "local_writer_reopened"
    publication: Literal["local_only"] = "local_only"

    @model_validator(mode="after")
    def exact_fields(self) -> ArtifactManifest:
        if len(set(self.field_ids)) != len(self.field_ids):
            raise ValueError("Artifact field coverage must be unique")
        return self


class VerificationDiagnostic(ServiceModel):
    """Finite public reasons; never carry raw internal verification messages."""

    code: Literal[
        "source_binding", "source_registry_required", "verification_blocker", "verification_warning"
    ]
    message: Literal[
        "Requested documents must match the configured review sources.",
        "A current source registry is required to review the material.",
        "Verification could not pass; inspect review findings or request human review.",
        "Verification reported a warning; request human review before proceeding.",
    ]

    @model_validator(mode="after")
    def matching_reason(self) -> VerificationDiagnostic:
        expected = {
            "source_binding": "Requested documents must match the configured review sources.",
            "source_registry_required": (
                "A current source registry is required to review the material."
            ),
            "verification_blocker": (
                "Verification could not pass; inspect review findings or request human review."
            ),
            "verification_warning": (
                "Verification reported a warning; request human review before proceeding."
            ),
        }
        if self.message != expected[self.code]:
            raise ValueError("Verification code and public message must agree")
        return self


class ServiceVerification(ServiceModel):
    """Sanitized projection of the existing verification report, including preflight."""

    status: EvaluationStatus
    critical_errors: tuple[VerificationDiagnostic, ...] = ()
    warnings: tuple[VerificationDiagnostic, ...] = ()


class ServiceResult(ServiceModel):
    run: RunReference
    result_version: int = Field(ge=1, strict=True)
    execution_status: ExecutionStatus
    business_status: WorkflowStatus | None = None
    artifact_status: ArtifactStatus = "not_requested"
    findings: tuple[ReviewFinding, ...] = ()
    verification: ServiceVerification | None = None
    artifacts: tuple[ArtifactManifest, ...] = Field(default=(), max_length=1)
    problem: ServiceProblem | None = None
    durable: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def honest_completion(self) -> ServiceResult:
        if self.execution_status in {ExecutionStatus.QUEUED, ExecutionStatus.RUNNING} and (
            self.business_status is not None
            or self.verification is not None
            or self.problem is not None
            or self.artifacts
            or self.artifact_status != "not_requested"
        ):
            raise ValueError("Active execution cannot claim a terminal result")
        if self.execution_status == ExecutionStatus.SUCCEEDED and (
            self.business_status is None or self.problem is not None
        ):
            raise ValueError("Successful execution requires a business result and no failure")
        written = self.artifact_status == "written"
        if written != bool(self.artifacts) or written != (
            self.business_status == WorkflowStatus.COMPLETED
        ):
            raise ValueError("Completed/written requires an actual artifact manifest")
        if written and self.execution_status != ExecutionStatus.SUCCEEDED:
            raise ValueError("Only successful execution can report a completed PDF")
        if self.execution_status == ExecutionStatus.FAILED and self.problem is None:
            raise ValueError("Execution failure requires a sanitized problem")
        return self


class ReviewSubmission(ServiceModel):
    """Reserved jobs request; no jobs route or durable implementation in M0."""

    revision: RevisionReference
    documents: tuple[DocumentReference, ...] = Field(min_length=2)
    idempotency_key: OpaqueID

    @model_validator(mode="after")
    def same_case(self) -> ReviewSubmission:
        if len({d.document_id for d in self.documents}) != len(self.documents):
            raise ValueError("Duplicate submitted document")
        if any(d.case_id != self.revision.case_id for d in self.documents):
            raise ValueError("Submitted documents must belong to the same case")
        return self
