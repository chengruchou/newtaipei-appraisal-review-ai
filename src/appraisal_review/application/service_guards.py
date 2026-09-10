"""Pure admission checks for future adapters. No persistence or authority creation."""

from dataclasses import dataclass
from uuid import UUID

from appraisal_review.domain.review_contracts import content_digest
from appraisal_review.domain.service_contracts import (
    AcceptedResponse,
    ActionProposal,
    ActorReference,
    AllowedActionSet,
    DeterministicReviewArguments,
    ExtractPageArguments,
    HumanResponse,
    HumanTask,
    InspectReferenceArguments,
    Permission,
    RequestHumanReviewArguments,
    ReviewSubmission,
    RevisionReference,
    ServiceErrorCode,
    ServiceProblem,
    WorkflowSnapshot,
)


class ServiceFault(Exception):
    def __init__(self, code: ServiceErrorCode) -> None:
        self.problem = ServiceProblem(code=code)
        super().__init__(code.value)


@dataclass(frozen=True)
class Principal:
    """Trusted adapter output; never deserialize this from an external body."""

    actor: ActorReference
    case_ids: frozenset[str]
    permissions: frozenset[Permission]

    def require(self, case_id: str, permission: Permission) -> None:
        if case_id not in self.case_ids or permission not in self.permissions:
            raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)


def admit_response(
    task: HumanTask,
    command: HumanResponse,
    principal: Principal,
    *,
    current: RevisionReference,
) -> AcceptedResponse:
    task = HumanTask.model_validate_json(task.model_dump_json())
    command = HumanResponse.model_validate_json(command.model_dump_json())
    principal.require(current.case_id, task.required_permission)
    if principal.actor.kind != "human":
        raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
    if command.task_id != task.task_id:
        raise ServiceFault(ServiceErrorCode.NOT_FOUND)
    if (
        task.state != "open"
        or command.expected_version != task.version
        or command.revision != current
        or task.run.revision != current
        or command.result_digest != task.result_digest
        or command.side_digest != (task.side.input_digest if task.side else None)
    ):
        raise ServiceFault(ServiceErrorCode.CONFLICT)
    if command.action not in task.allowed_responses:
        raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
    return AcceptedResponse(command=command, actor=principal.actor)


def admit_action(
    proposal: ActionProposal,
    *,
    proposer: ActorReference,
    executor: ActorReference,
    snapshot: WorkflowSnapshot,
    allowed: AllowedActionSet,
) -> None:
    # The executor supplies the actual origin; a proposal cannot choose its budget class.
    proposal = ActionProposal.model_validate_json(proposal.model_dump_json())
    snapshot = WorkflowSnapshot.model_validate_json(snapshot.model_dump_json())
    allowed = AllowedActionSet.model_validate_json(allowed.model_dump_json())
    if proposal.proposer != proposer:
        raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
    snapshot_digest = content_digest(snapshot)
    if (
        proposal.run != snapshot.run
        or proposal.snapshot_digest != snapshot_digest
        or proposal.policy_version != allowed.policy_version
        or proposal.snapshot_digest != allowed.snapshot_digest
        or snapshot.revision.reference != allowed.revision
        or snapshot.revision.documents != allowed.documents
        or snapshot.revision.rules != allowed.rules
    ):
        raise ServiceFault(ServiceErrorCode.CONFLICT)
    matches = [
        action
        for action in allowed.actions
        if action.action_id == proposal.action_id and action.action == proposal.action
    ]
    if len(matches) != 1:
        raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
    admitted = matches[0]
    if (
        snapshot.state not in admitted.permitted_states
        or not set(admitted.prerequisites) <= set(snapshot.satisfied_prerequisites)
        or proposer.kind not in admitted.proposer_kinds
        or executor.kind not in admitted.executor_kinds
        or admitted.revision != snapshot.revision.reference
        or admitted.rules != snapshot.revision.rules
    ):
        raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
    required_model_calls = (
        proposal.attempt_count
        if proposal.proposer.kind == "model" and proposal.attempt_count is not None
        else admitted.cost.model_calls
    )
    provider_retries = (
        proposal.attempt_count - 1
        if proposal.proposer.kind == "model" and proposal.attempt_count is not None
        else 0
    )
    if (
        snapshot.budget.steps_remaining < admitted.cost.steps
        or snapshot.budget.model_calls_remaining < required_model_calls
        or snapshot.budget.retries_remaining < admitted.cost.retries + provider_retries
    ):
        raise ServiceFault(ServiceErrorCode.CAPABILITY)
    arguments = proposal.arguments
    if isinstance(arguments, (ExtractPageArguments, InspectReferenceArguments)):
        if (
            arguments.document not in snapshot.revision.documents
            or arguments.document.purpose not in admitted.permitted_document_purposes
        ):
            raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
    elif isinstance(arguments, DeterministicReviewArguments):
        if (
            arguments.revision != snapshot.revision.reference
            or arguments.rules != snapshot.revision.rules
        ):
            raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
    elif isinstance(arguments, RequestHumanReviewArguments):
        matching_blockers = tuple(
            blocker
            for blocker in snapshot.unresolved_blockers
            if blocker.reason_code == arguments.reason_code
            and blocker.affected_subject_ids == arguments.affected_subject_ids
        )
        documents = {
            (document.document_id, document.version, document.content_hash)
            for document in snapshot.revision.documents
        }
        if len(matching_blockers) != 1 or any(
            (citation.document_id, citation.version, citation.content_hash) not in documents
            or citation not in matching_blockers[0].evidence
            for citation in arguments.evidence
        ):
            raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
    # Actual access, page existence, citation resolution and purpose recheck remain resolver duties.


@dataclass(frozen=True)
class IdempotencyRecord:
    principal_id: str
    key: str
    payload_digest: str
    run_id: UUID


def submission_digest(submission: ReviewSubmission) -> str:
    # Sort the document set before hashing; idempotency key is a namespace, not payload.
    value = submission.model_copy(
        update={
            "documents": tuple(sorted(submission.documents, key=lambda d: d.document_id)),
            "idempotency_key": "canonical-payload",
        }
    )
    return content_digest(value)


def response_digest(command: HumanResponse) -> str:
    """Canonical payload identity of a response, excluding its idempotency key.

    Same rule as submission_digest: the key names the attempt, so hashing it would make
    every retry look like a different payload and defeat the check it feeds. A response
    carries no unordered collection, so nothing needs sorting first.
    """
    value = command.model_copy(update={"idempotency_key": "canonical-payload"})
    return content_digest(value)


def check_idempotency(
    record: IdempotencyRecord, principal: Principal, submission: ReviewSubmission
) -> UUID:
    principal.require(submission.revision.case_id, Permission.REVIEW)
    if record.principal_id != principal.actor.actor_id or record.key != submission.idempotency_key:
        raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
    if record.payload_digest != submission_digest(submission):
        raise ServiceFault(ServiceErrorCode.CONFLICT)
    return record.run_id
