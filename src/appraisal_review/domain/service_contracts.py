"""Service v1 wire contracts; DTO validity never grants identity or authority.

Existing review/material/PDF types remain authoritative. Durable operations are
reserved ports, not mounted HTTP endpoints. See docs/service-contracts.md.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import ConfigDict, Field, RootModel, model_validator

from appraisal_review.domain.document_models import Digest, DocumentModel, SourceCitation
from appraisal_review.domain.factor_models import (
    ArtifactStatus,
    EvaluationStatus,
    NormalizedValue,
    WorkflowStatus,
)
from appraisal_review.domain.review_contracts import (
    ComparisonContext,
    ReviewFinding,
    content_digest,
)

OpaqueID = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")]


class ContractModel(DocumentModel):
    model_config = ConfigDict(
        extra="forbid", allow_inf_nan=False, revalidate_instances="always", frozen=True
    )


class ServiceModel(ContractModel):
    schema_version: Literal["service-v1"] = "service-v1"


class ControlledActionModel(ContractModel):
    """Issue #17 wire migration from the pre-execution service-v1 draft."""

    schema_version: Literal["controlled-action-v1"] = "controlled-action-v1"


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
    rules: tuple[RuleReference, ...] = ()
    changes: tuple[ValueRevision, ...] = ()
    canonicalization: Literal["review-material-json-v1", "source-documents-json-v1"] = (
        "review-material-json-v1"
    )

    @model_validator(mode="after")
    def identities(self) -> MaterialRevision:
        if self.canonicalization == "review-material-json-v1" and not self.rules:
            raise ValueError("Review material requires actual rules")
        if self.canonicalization == "source-documents-json-v1":
            ordered = tuple(sorted(self.documents, key=lambda item: item.document_id))
            if (
                self.rules
                or self.changes
                or self.documents != ordered
                or self.reference.material_digest
                != content_digest(RootModel[tuple[DocumentReference, ...]](ordered))
            ):
                raise ValueError(
                    "Preparation binds exact ordered source references, not rules or corrections"
                )
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
    EVIDENCE = "evidence_supply"


class ResponseAction(StrEnum):
    CONFIRM = "confirm"
    CORRECT = "correct"
    REJECT = "reject"
    APPROVE = "approve"
    AUTHORIZE_PUBLICATION = "authorize_publication"
    SUPPLY_EVIDENCE = "supply_evidence"


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
    reason_code: OpaqueID | None = None
    affected_subject_ids: tuple[OpaqueID, ...] = ()

    @model_validator(mode="after")
    def task_authority(self) -> HumanTask:
        permissions = {
            TaskKind.FACT: Permission.CONFIRM,
            TaskKind.CORRECTION: Permission.CORRECT,
            TaskKind.RULES: Permission.APPROVE_RULES,
            TaskKind.MATERIAL: Permission.APPROVE_MATERIAL,
            TaskKind.PUBLICATION: Permission.PUBLISH,
            TaskKind.EVIDENCE: Permission.CORRECT,
        }
        actions = {
            TaskKind.FACT: ResponseAction.CONFIRM,
            TaskKind.CORRECTION: ResponseAction.CORRECT,
            TaskKind.RULES: ResponseAction.APPROVE,
            TaskKind.MATERIAL: ResponseAction.APPROVE,
            TaskKind.PUBLICATION: ResponseAction.AUTHORIZE_PUBLICATION,
            TaskKind.EVIDENCE: ResponseAction.SUPPLY_EVIDENCE,
        }
        if self.required_permission != permissions[self.kind] or not set(
            self.allowed_responses
        ) <= {actions[self.kind], ResponseAction.REJECT}:
            raise ValueError("Task permission and actions must match its purpose")
        if (self.kind == TaskKind.PUBLICATION) != (self.result_digest is not None):
            raise ValueError("Only publication tasks bind an exact result digest")
        if self.kind == TaskKind.FACT and self.side is None:
            raise ValueError("Observation confirmation requires an exact side")
        for values in (self.finding_ids, self.allowed_responses, self.affected_subject_ids):
            if len(set(values)) != len(values):
                raise ValueError("Task findings, subjects and allowed responses must be unique")
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
        if (self.action in {ResponseAction.CORRECT, ResponseAction.SUPPLY_EVIDENCE}) != (
            self.correction is not None
        ):
            raise ValueError("Only correction or evidence commands contain a proposed value")
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


class HumanResponseResult(ServiceModel):
    """Actual atomic local response event; no execution/completion status is asserted."""

    event_id: UUID
    accepted: AcceptedResponse
    task: HumanTask
    revision: MaterialRevision
    next_run: RunReference | None = None
    authorization: AuthorizationRecord | None = None

    @model_validator(mode="after")
    def response_binding(self) -> HumanResponseResult:
        command = self.accepted.command
        if (
            self.accepted.actor.kind != "human"
            or self.task.task_id != command.task_id
            or self.task.state != "answered"
            or self.task.version != command.expected_version + 1
            or self.task.run.revision != command.revision
        ):
            raise ValueError("Response event must bind an answered version and trusted human")
        if self.next_run is not None and self.next_run.revision != self.revision.reference:
            raise ValueError("Re-entry must bind the resulting exact material revision")
        if self.authorization is not None and (
            self.authorization.actor != self.accepted.actor
            or self.authorization.revision != self.revision
        ):
            raise ValueError("Response authority must bind its actual actor and revision")
        if command.action == ResponseAction.REJECT and (
            self.authorization is not None
            or self.next_run is not None
            or self.revision.reference != command.revision
        ):
            raise ValueError("Rejection cannot create authority or re-entry or change material")
        if command.action != ResponseAction.REJECT:
            authority_task = self.task.kind in {
                TaskKind.RULES,
                TaskKind.MATERIAL,
                TaskKind.PUBLICATION,
            }
            if authority_task != (self.authorization is not None):
                raise ValueError("Only approving authority tasks produce an authorization record")
            if (self.task.kind == TaskKind.PUBLICATION) != (self.next_run is None):
                raise ValueError("Accepted non-publication responses require fresh review work")
            material_changed = self.task.kind in {
                TaskKind.FACT,
                TaskKind.CORRECTION,
                TaskKind.EVIDENCE,
                TaskKind.RULES,
            }
            if material_changed:
                if (
                    self.revision.parent != command.revision
                    or self.revision.reference == command.revision
                ):
                    raise ValueError("Material changes must append an exact child revision")
            elif self.revision.reference != command.revision:
                raise ValueError("Material and publication approval cannot edit the material")
        if self.authorization is not None:
            purposes = {
                TaskKind.RULES: "rules",
                TaskKind.MATERIAL: "material",
                TaskKind.PUBLICATION: "publication",
            }
            if self.authorization.purpose != purposes.get(self.task.kind) or command.action not in {
                ResponseAction.APPROVE,
                ResponseAction.AUTHORIZE_PUBLICATION,
            }:
                raise ValueError("Authorization must match the task's approved purpose")
            if self.authorization.result_digest != command.result_digest:
                raise ValueError("Publication authority must bind the accepted result digest")
        return self


class ActionKind(StrEnum):
    EXTRACT = "extract_page"
    REFERENCE = "inspect_reference"
    HUMAN = "request_human_review"
    REVIEW = "deterministic_review"


class WorkflowState(StrEnum):
    CRITERIA_PENDING = "criteria_pending"
    FORMS_PENDING = "forms_pending"
    EXTRACTION_RETRYABLE = "extraction_retryable"
    REFERENCE_AVAILABLE = "reference_available"
    RULES_AWAITING_APPROVAL = "rules_awaiting_approval"
    EVIDENCE_NEEDS_REVIEW = "evidence_needs_review"
    MATERIAL_READY = "material_ready"
    REVIEW_FAILED = "review_failed"
    VERIFIED = "verified"
    UNSUPPORTED_CONTEXTS = "unsupported_contexts"
    WAITING_FOR_HUMAN = "waiting_for_human"


class ActionPrerequisite(StrEnum):
    CRITERIA_DOCUMENT = "criteria_document"
    FORMS_DOCUMENT = "forms_document"
    AUTHORIZED_SOURCE = "authorized_source"
    CRITERIA_PARSED = "criteria_parsed"
    FORMS_PARSED = "forms_parsed"
    EXTRACTION_RETRY_AVAILABLE = "extraction_retry_available"
    REFERENCE_AVAILABLE = "reference_available"
    RULES_AVAILABLE = "rules_available"
    RULES_APPROVED = "rules_approved"
    CRITICAL_EVIDENCE_AVAILABLE = "critical_evidence_available"
    MATERIAL_COMPLETE = "material_complete"


class WorkflowBlocker(ControlledActionModel):
    blocker_id: OpaqueID
    reason_code: OpaqueID
    affected_subject_ids: tuple[OpaqueID, ...] = Field(min_length=1)
    evidence: tuple[SourceCitation, ...] = ()

    @model_validator(mode="after")
    def unique_subjects(self) -> WorkflowBlocker:
        if len(set(self.affected_subject_ids)) != len(self.affected_subject_ids):
            raise ValueError("A blocker cannot repeat an affected subject")
        return self


class Budget(ServiceModel):
    steps_remaining: int = Field(ge=0, strict=True)
    model_calls_remaining: int = Field(ge=0, strict=True)
    retries_remaining: int = Field(ge=0, strict=True)
    time_remaining_ms: int | None = Field(default=None, ge=0, strict=True)


class WorkflowSnapshot(ControlledActionModel):
    """Exact trusted workflow input; selectors may inspect but never author it."""

    state_version: int = Field(ge=1, strict=True)
    run: RunReference
    revision: MaterialRevision
    state: WorkflowState
    satisfied_prerequisites: tuple[ActionPrerequisite, ...] = ()
    unresolved_blockers: tuple[WorkflowBlocker, ...] = ()
    budget: Budget

    @model_validator(mode="after")
    def exact_state(self) -> WorkflowSnapshot:
        if self.revision.canonicalization == "source-documents-json-v1" and (
            self.state
            not in {
                WorkflowState.CRITERIA_PENDING,
                WorkflowState.EXTRACTION_RETRYABLE,
                WorkflowState.REFERENCE_AVAILABLE,
            }
            or not set(self.satisfied_prerequisites)
            <= {
                ActionPrerequisite.CRITERIA_DOCUMENT,
                ActionPrerequisite.FORMS_DOCUMENT,
                ActionPrerequisite.AUTHORIZED_SOURCE,
                ActionPrerequisite.EXTRACTION_RETRY_AVAILABLE,
                ActionPrerequisite.REFERENCE_AVAILABLE,
            }
        ):
            raise ValueError("Source preparation cannot assert parsed rules or review readiness")
        if self.run.revision != self.revision.reference:
            raise ValueError("Workflow run and material revision must match")
        if len(set(self.satisfied_prerequisites)) != len(self.satisfied_prerequisites):
            raise ValueError("Workflow prerequisites must be unique")
        blocker_ids = [blocker.blocker_id for blocker in self.unresolved_blockers]
        if len(set(blocker_ids)) != len(blocker_ids):
            raise ValueError("Workflow blocker IDs must be unique")
        return self


class ActionCost(ControlledActionModel):
    steps: int = Field(default=1, ge=1, strict=True)
    model_calls: int = Field(default=0, ge=0, strict=True)
    retries: int = Field(default=0, ge=0, strict=True)


class AllowedAction(ControlledActionModel):
    action_id: OpaqueID
    action: ActionKind
    permitted_states: tuple[WorkflowState, ...] = Field(min_length=1)
    permitted_document_purposes: tuple[
        Literal["criteria", "forms", "reference", "brief", "template"], ...
    ] = ()
    revision: RevisionReference
    rules: tuple[RuleReference, ...] = ()
    proposer_kinds: tuple[Literal["system", "model"], ...] = Field(min_length=1, max_length=1)
    executor_kinds: tuple[Literal["system"], ...] = Field(
        default=("system",), min_length=1, max_length=1
    )
    prerequisites: tuple[ActionPrerequisite, ...] = ()
    cost: ActionCost = ActionCost()

    @model_validator(mode="after")
    def bounded_authority(self) -> AllowedAction:
        if not self.rules and self.action not in {ActionKind.EXTRACT, ActionKind.REFERENCE}:
            raise ValueError("Only preparation source actions may omit rules")
        for values, message in (
            (self.permitted_states, "Permitted states must be unique"),
            (self.permitted_document_purposes, "Permitted document purposes must be unique"),
            (self.proposer_kinds, "Permitted proposer kinds must be unique"),
            (self.executor_kinds, "Permitted executor kinds must be unique"),
            (self.prerequisites, "Action prerequisites must be unique"),
        ):
            if len(set(values)) != len(values):
                raise ValueError(message)
        source_action = self.action in {ActionKind.EXTRACT, ActionKind.REFERENCE}
        if source_action != bool(self.permitted_document_purposes):
            raise ValueError("Only source actions declare permitted document purposes")
        if self.action == ActionKind.REFERENCE and not set(self.permitted_document_purposes) <= {
            "reference",
            "brief",
        }:
            raise ValueError("Reference inspection requires reference or brief material")
        if self.action == ActionKind.EXTRACT and "template" in self.permitted_document_purposes:
            raise ValueError("Templates are not extraction evidence")
        if ("model" in self.proposer_kinds) != (self.cost.model_calls > 0):
            raise ValueError("Action cost must account exactly for model selection")
        return self


class AllowedActionSet(ControlledActionModel):
    policy_version: OpaqueID
    snapshot_digest: Digest
    revision: RevisionReference
    documents: tuple[DocumentReference, ...] = Field(min_length=2)
    rules: tuple[RuleReference, ...] = ()
    actions: tuple[AllowedAction, ...] = ()

    @model_validator(mode="after")
    def unambiguous_registry(self) -> AllowedActionSet:
        action_ids = [action.action_id for action in self.actions]
        if len(set(action_ids)) != len(action_ids):
            raise ValueError("Allowed action IDs must be unique")
        action_kinds = [action.action for action in self.actions]
        if len(set(action_kinds)) != len(action_kinds):
            raise ValueError("An allowed-action registry cannot contain ambiguous action kinds")
        if any(document.case_id != self.revision.case_id for document in self.documents) or len(
            {document.document_id for document in self.documents}
        ) != len(self.documents):
            raise ValueError("Allowed-action documents must uniquely bind the revision case")
        document_purposes = {document.purpose for document in self.documents}
        if any(
            action.revision != self.revision
            or action.rules != self.rules
            or not set(action.permitted_document_purposes) <= document_purposes
            for action in self.actions
        ):
            raise ValueError(
                "Every allowed action must bind the current material sources and rules"
            )
        return self


class SelectorInput(ControlledActionModel):
    """Exact, URI-free selector view assembled only by trusted application code."""

    snapshot: WorkflowSnapshot
    allowed_actions: AllowedActionSet
    evidence: tuple[SourceCitation, ...] = ()
    budget: Budget

    @model_validator(mode="after")
    def exact_authority(self) -> SelectorInput:
        allowed = self.allowed_actions
        snapshot = self.snapshot
        if (
            self.budget != snapshot.budget
            or allowed.snapshot_digest != content_digest(snapshot)
            or allowed.revision != snapshot.revision.reference
            or allowed.documents != snapshot.revision.documents
            or allowed.rules != snapshot.revision.rules
        ):
            raise ValueError("Selector input must bind one exact trusted snapshot")
        if any(
            self.budget.steps_remaining < action.cost.steps
            or self.budget.model_calls_remaining < action.cost.model_calls
            or self.budget.retries_remaining < action.cost.retries
            for action in allowed.actions
        ):
            raise ValueError("Selector input cannot advertise an unaffordable action")
        documents = {
            (document.document_id, document.version, document.content_hash)
            for document in snapshot.revision.documents
        }
        if any(
            (citation.document_id, citation.version, citation.content_hash) not in documents
            for citation in self.evidence
        ):
            raise ValueError("Selector evidence must resolve in the current revision")
        return self


class ExtractPageArguments(ControlledActionModel):
    kind: Literal["extract_page"] = "extract_page"
    document: DocumentReference
    page: int = Field(ge=1, strict=True)
    region: SourceCitation | None = None

    @model_validator(mode="after")
    def located_region(self) -> ExtractPageArguments:
        if self.document.purpose == "template":
            raise ValueError("Templates are not extraction evidence")
        if self.region and (
            self.region.document_id != self.document.document_id
            or self.region.version != self.document.version
            or self.region.content_hash != self.document.content_hash
            or self.region.page != self.page
        ):
            raise ValueError("Extraction region must match the exact document page")
        return self


class InspectReferenceArguments(ControlledActionModel):
    kind: Literal["inspect_reference"] = "inspect_reference"
    document: DocumentReference
    page: int = Field(ge=1, strict=True)
    section_id: OpaqueID | None = None

    @model_validator(mode="after")
    def reference_purpose(self) -> InspectReferenceArguments:
        if self.document.purpose not in {"reference", "brief"}:
            raise ValueError("Reference inspection requires reference or brief material")
        return self


class RequestHumanReviewArguments(ControlledActionModel):
    kind: Literal["request_human_review"] = "request_human_review"
    reason_code: OpaqueID
    question: str = Field(min_length=1)
    affected_subject_ids: tuple[OpaqueID, ...] = Field(min_length=1)
    evidence: tuple[SourceCitation, ...] = ()

    @model_validator(mode="after")
    def unique_subjects(self) -> RequestHumanReviewArguments:
        if len(set(self.affected_subject_ids)) != len(self.affected_subject_ids):
            raise ValueError("A human-review request cannot repeat a subject")
        return self


class DeterministicReviewArguments(ControlledActionModel):
    kind: Literal["deterministic_review"] = "deterministic_review"
    revision: RevisionReference
    rules: tuple[RuleReference, ...] = Field(min_length=1)


ActionArguments = Annotated[
    ExtractPageArguments
    | InspectReferenceArguments
    | RequestHumanReviewArguments
    | DeterministicReviewArguments,
    Field(discriminator="kind"),
]


class ActionProposal(ControlledActionModel):
    proposal_id: UUID
    run: RunReference
    action_id: OpaqueID
    action: ActionKind
    policy_version: OpaqueID
    snapshot_digest: Digest
    proposer: ActorReference
    model_id: str | None = Field(default=None, min_length=1)
    prompt_version: OpaqueID | None = None
    input_tokens: int | None = Field(default=None, ge=0, strict=True)
    output_tokens: int | None = Field(default=None, ge=0, strict=True)
    latency_ms: int | None = Field(default=None, ge=0, strict=True)
    attempt_count: int | None = Field(default=None, ge=1, strict=True)
    arguments: ActionArguments
    proposer_rationale: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def typed_proposal(self) -> ActionProposal:
        if self.action.value != self.arguments.kind:
            raise ValueError("Proposal action and arguments must match")
        if self.proposer.kind not in {"system", "model"}:
            raise ValueError("Only trusted system or model adapters propose actions")
        model_identity = (self.model_id, self.prompt_version)
        required_telemetry = (self.latency_ms, self.attempt_count)
        token_usage = (self.input_tokens, self.output_tokens)
        if self.proposer.kind == "model" and (
            any(value is None for value in model_identity + required_telemetry)
            or (token_usage[0] is None) != (token_usage[1] is None)
        ):
            raise ValueError("Model proposals require adapter identity and complete telemetry")
        if self.proposer.kind != "model" and any(
            value is not None for value in model_identity + required_telemetry + token_usage
        ):
            raise ValueError("Only model proposals carry adapter identity and telemetry")
        if isinstance(self.arguments, DeterministicReviewArguments) and (
            self.arguments.revision != self.run.revision
        ):
            raise ValueError("Deterministic review must bind the proposal revision")
        return self


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


class ControlledToolReceipt(ControlledActionModel):
    """Sanitized actual executor result; workflow state comes from a fresh snapshot."""

    outcome: ToolOutcome
    reason_code: OpaqueID
    reviewer_summary: str = Field(min_length=1)
    affected_subject_ids: tuple[OpaqueID, ...] = ()
    evidence: tuple[SourceCitation, ...] = ()
    linked_task_id: UUID | None = None
    linked_response_key: OpaqueID | None = None

    @model_validator(mode="after")
    def exact_receipt(self) -> ControlledToolReceipt:
        if len(set(self.affected_subject_ids)) != len(self.affected_subject_ids):
            raise ValueError("Executor receipt subjects must be unique")
        if self.linked_response_key is not None and self.linked_task_id is None:
            raise ValueError("A receipt response requires its human task")
        return self


class BudgetConsumption(ControlledActionModel):
    steps: int = Field(ge=0, strict=True)
    model_calls: int = Field(ge=0, strict=True)
    retries: int = Field(ge=0, strict=True)
    elapsed_ms: int | None = Field(default=None, ge=0, strict=True)


class DecisionEvent(ControlledActionModel):
    event_id: UUID
    parent_event_ids: tuple[UUID, ...] = ()
    proposal: ActionProposal
    executor: ActorReference | None = None
    policy_version: OpaqueID
    state_before: WorkflowState
    state_after: WorkflowState
    disposition: Literal["rejected", "executed", "failed"]
    executed_action: ActionKind | None = None
    checked_prerequisites: tuple[ActionPrerequisite, ...] = ()
    reason_code: OpaqueID
    reviewer_summary: str = Field(min_length=1)
    affected_subject_ids: tuple[OpaqueID, ...] = ()
    evidence: tuple[SourceCitation, ...] = ()
    tool_result: ToolOutcome | None = None
    remaining_blockers: tuple[str, ...] = ()
    linked_task_id: UUID | None = None
    linked_response_key: OpaqueID | None = None
    budget_before: Budget
    budget_after: Budget
    budget_consumed: BudgetConsumption

    @model_validator(mode="after")
    def actual_execution(self) -> DecisionEvent:
        if self.event_id in self.parent_event_ids or len(set(self.parent_event_ids)) != len(
            self.parent_event_ids
        ):
            raise ValueError("Decision event parents must be unique and cannot include self")
        if self.policy_version != self.proposal.policy_version:
            raise ValueError("Decision event and proposal policy versions must match")
        if self.executor is not None and self.executor.kind != "system":
            raise ValueError("Only a trusted system adapter can execute an action")
        if self.disposition == "rejected":
            if self.executed_action or self.executor or self.tool_result:
                raise ValueError("A rejected action cannot claim tool execution")
            if self.state_after != self.state_before:
                raise ValueError("A rejected action cannot change workflow state")
            if self.budget_consumed.steps:
                raise ValueError("A rejected action cannot consume a tool step")
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
        if self.linked_response_key is not None and self.linked_task_id is None:
            raise ValueError("A linked response requires its human task")
        if self.linked_task_id is not None and self.proposal.action != ActionKind.HUMAN:
            raise ValueError("Only a human-review action can link a task")
        if (
            self.disposition == "executed"
            and self.proposal.action == ActionKind.HUMAN
            and (self.linked_task_id is None or self.state_after != WorkflowState.WAITING_FOR_HUMAN)
        ):
            raise ValueError("An executed human-review request must create a waiting task")
        for values, message in (
            (self.checked_prerequisites, "Checked prerequisites must be unique"),
            (self.affected_subject_ids, "Affected subjects must be unique"),
            (self.remaining_blockers, "Remaining blockers must be unique"),
        ):
            if len(set(values)) != len(values):
                raise ValueError(message)
        for field in ("steps", "model_calls", "retries"):
            before = getattr(self.budget_before, f"{field}_remaining")
            after = getattr(self.budget_after, f"{field}_remaining")
            if before - after != getattr(self.budget_consumed, field):
                raise ValueError("Decision budget consumption must match remaining budget")
        before_time = self.budget_before.time_remaining_ms
        after_time = self.budget_after.time_remaining_ms
        elapsed = self.budget_consumed.elapsed_ms
        if (before_time is None) != (after_time is None) or (before_time is None) != (
            elapsed is None
        ):
            raise ValueError("Decision time budget must be consistently available")
        if (
            before_time is not None
            and after_time is not None
            and before_time - after_time != elapsed
        ):
            raise ValueError("Decision elapsed time must match remaining time budget")
        return self


class FailureCategory(StrEnum):
    """Stable workflow failure classes; raw provider messages never cross this boundary."""

    RETRYABLE_PROVIDER_TOOL = "retryable_provider_tool_failure"
    PERMANENT_MALFORMED_PROPOSAL = "permanent_malformed_proposal"
    UNAUTHORIZED_SOURCE = "unauthorized_or_wrong_purpose_source"
    MISSING_EVIDENCE = "missing_evidence"
    UNSUPPORTED_RULE_CONTEXT = "unsupported_rule_or_context"
    DETERMINISTIC_REVIEW_BLOCKER = "deterministic_review_blocker"
    UNRESOLVED_EXECUTION = "unresolved_execution"


class SelectionFailureEvent(ControlledActionModel):
    """Observed selection failure without fabricating a valid action proposal."""

    event_id: UUID
    parent_event_ids: tuple[UUID, ...] = ()
    run: RunReference
    snapshot_digest: Digest
    policy_version: OpaqueID
    actor: ActorReference
    error_code: OpaqueID
    model_id: str | None = None
    prompt_version: OpaqueID | None = None
    attempt_count: int | None = Field(default=None, ge=0, strict=True)
    input_tokens: int | None = Field(default=None, ge=0, strict=True)
    output_tokens: int | None = Field(default=None, ge=0, strict=True)
    latency_ms: int = Field(ge=0, strict=True)
    budget_before: Budget
    budget_after: Budget
    budget_consumed: BudgetConsumption
    remaining_blockers: tuple[OpaqueID, ...] = ()

    @model_validator(mode="after")
    def failed_selection(self) -> SelectionFailureEvent:
        if self.actor.kind not in {"system", "model"}:
            raise ValueError("Selection failure requires a selector actor")
        if self.event_id in self.parent_event_ids or len(set(self.parent_event_ids)) != len(
            self.parent_event_ids
        ):
            raise ValueError("Selection parents must be unique and cannot include self")
        if (self.input_tokens is None) != (self.output_tokens is None):
            raise ValueError("Token usage must be paired")
        if self.actor.kind == "system" and any(
            value is not None
            for value in (self.model_id, self.prompt_version, self.input_tokens, self.output_tokens)
        ):
            raise ValueError("System selection cannot claim model metadata")
        if self.budget_consumed.steps != 0:
            raise ValueError("Failed selection cannot consume a tool step")
        for field in ("steps", "model_calls", "retries"):
            if getattr(self.budget_before, f"{field}_remaining") - getattr(
                self.budget_after, f"{field}_remaining"
            ) != getattr(self.budget_consumed, field):
                raise ValueError("Selection accounting must match its budgets")
        before = self.budget_before.time_remaining_ms
        after = self.budget_after.time_remaining_ms
        elapsed = self.budget_consumed.elapsed_ms
        if (before is None) != (after is None) or (before is None) != (elapsed is None):
            raise ValueError("Selection time accounting must be consistently available")
        if before is not None and after is not None and before - after != elapsed:
            raise ValueError("Selection elapsed time must match its budgets")
        return self


class WorkflowTermination(StrEnum):
    VERIFIED = "verified"
    WAITING_FOR_HUMAN = "waiting_for_human"
    BUDGET_EXHAUSTED = "budget_exhausted"
    NO_PROGRESS = "no_progress"
    PERMANENT_FAILURE = "permanent_failure"


class HumanReviewHandoff(ControlledActionModel):
    """Concrete non-persisted request; the task adapter declares its durability."""

    reason_code: OpaqueID
    category: FailureCategory
    question: str = Field(min_length=1)
    revision: RevisionReference
    blockers: tuple[WorkflowBlocker, ...] = ()
    affected_subject_ids: tuple[OpaqueID, ...] = ()
    evidence: tuple[SourceCitation, ...] = ()
    last_tool_outcome: ToolOutcome | None = None
    no_progress_fingerprint: Digest | None = None

    @model_validator(mode="after")
    def preserved_context(self) -> HumanReviewHandoff:
        if len(set(self.affected_subject_ids)) != len(self.affected_subject_ids):
            raise ValueError("Handoff subjects must be unique")
        blocker_ids = [blocker.blocker_id for blocker in self.blockers]
        if len(set(blocker_ids)) != len(blocker_ids):
            raise ValueError("Handoff blockers must be unique")
        return self


class BoundedWorkflowResult(ControlledActionModel):
    """Terminal result of one bounded local workflow invocation."""

    run: RunReference
    termination: WorkflowTermination
    events: tuple[DecisionEvent, ...] = ()
    selection_failures: tuple[SelectionFailureEvent, ...] = ()
    final_budget: Budget
    handoff: HumanReviewHandoff | None = None

    @model_validator(mode="after")
    def terminal_shape(self) -> BoundedWorkflowResult:
        completed = self.termination in {
            WorkflowTermination.VERIFIED,
            WorkflowTermination.WAITING_FOR_HUMAN,
        }
        if completed == (self.handoff is not None):
            raise ValueError("Only non-completed bounded workflows require a handoff")
        if any(event.proposal.run != self.run for event in self.events):
            raise ValueError("Bounded workflow events must belong to the same run")
        if any(event.run != self.run for event in self.selection_failures):
            raise ValueError("Selection failures must belong to the bounded run")
        if (
            self.termination == WorkflowTermination.VERIFIED
            and self.events
            and self.events[-1].disposition != "executed"
        ):
            raise ValueError("Failed execution cannot finish verified")
        return self


class WorkflowPause(ControlledActionModel):
    """Local handoff checkpoint, not a durable commit or Runtime release receipt."""

    pause_id: UUID
    result: BoundedWorkflowResult
    task_ids: tuple[UUID, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def waiting_only(self) -> WorkflowPause:
        events = self.result.events
        if (
            self.result.termination != WorkflowTermination.WAITING_FOR_HUMAN
            or not events
            or events[-1].disposition != "executed"
            or events[-1].executed_action != ActionKind.HUMAN
            or events[-1].state_after != WorkflowState.WAITING_FOR_HUMAN
            or events[-1].linked_task_id not in self.task_ids
            or self.result.final_budget != events[-1].budget_after
            or len(set(self.task_ids)) != len(self.task_ids)
        ):
            raise ValueError("Pause requires an actual successful human-task handoff")
        ids = [e.event_id for e in events] + [e.event_id for e in self.result.selection_failures]
        causal_events: tuple[DecisionEvent | SelectionFailureEvent, ...] = (
            *events,
            *self.result.selection_failures,
        )
        if len(set(ids)) != len(ids) or any(
            not set(e.parent_event_ids) <= set(ids) for e in causal_events
        ):
            raise ValueError("Pause must retain the complete local causal trace")
        pending = {event.event_id: set(event.parent_event_ids) for event in causal_events}
        while pending:
            ready = {event_id for event_id, parents in pending.items() if not parents}
            if not ready:
                raise ValueError("Pause causal trace must not contain cycles")
            pending = {key: parents - ready for key, parents in pending.items() if key not in ready}
        return self


class WorkflowContinuation(ControlledActionModel):
    """Link an admitted human response to pending new work, never dispatch success."""

    pause_id: UUID
    response_event_id: UUID
    task_id: UUID
    previous_run: RunReference
    next_run: RunReference

    @model_validator(mode="after")
    def fresh_run(self) -> WorkflowContinuation:
        if (
            self.previous_run.run_id == self.next_run.run_id
            or self.previous_run.revision.case_id != self.next_run.revision.case_id
            or self.next_run.attempt_id is not None
            or self.next_run.runtime_session_id is not None
        ):
            raise ValueError("Continuation requires a fresh same-case run without an attempt")
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


class FencedArtifactManifest(ContractModel):
    """Versioned public projection of a committed, possibly multi-context PDF.

    The original service-v1 local-only artifact remains unchanged. A consumer
    must explicitly support this version before accepting a fenced publication.
    """

    schema_version: Literal["artifact-manifest-v2"] = "artifact-manifest-v2"
    artifact_id: UUID
    content_hash: Digest
    media_type: Literal["application/pdf"] = "application/pdf"
    scope: Literal["review_contexts"] = "review_contexts"
    context: ComparisonContext
    contexts: tuple[ComparisonContext, ...] = Field(min_length=1)
    field_ids: tuple[str, ...] = Field(min_length=1)
    page_count: int = Field(ge=1, strict=True)
    template_hash: Digest
    field_map_hash: Digest
    font_hash: Digest
    writer_version: str = Field(min_length=1)
    manifest_digest: Digest
    verification: Literal["local_writer_reopened"] = "local_writer_reopened"
    publication: Literal["fenced"] = "fenced"

    @model_validator(mode="after")
    def exact_coverage(self) -> FencedArtifactManifest:
        keys = tuple(context.key() for context in self.contexts)
        if self.context != self.contexts[0] or len(keys) != len(set(keys)):
            raise ValueError("Primary context must lead the unique complete context list")
        if len(self.field_ids) != len(set(self.field_ids)):
            raise ValueError("Artifact fields must be unique")
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
    artifacts: tuple[ArtifactManifest | FencedArtifactManifest, ...] = Field(
        default=(), max_length=1
    )
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
