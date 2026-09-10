"""Pure admission checks for future adapters. No persistence or authority creation."""

from dataclasses import dataclass
from uuid import UUID

from appraisal_review.domain.review_contracts import content_digest
from appraisal_review.domain.service_contracts import (
    AcceptedResponse,
    ActionKind,
    ActionProposal,
    ActorReference,
    AllowedAction,
    Budget,
    HumanResponse,
    HumanTask,
    MaterialRevision,
    Permission,
    ReviewSubmission,
    RevisionReference,
    RunReference,
    ServiceErrorCode,
    ServiceProblem,
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
    run: RunReference,
    revision: MaterialRevision,
    allowed: tuple[AllowedAction, ...],
    satisfied: frozenset[str],
    budget: Budget,
) -> None:
    # The executor supplies the actual origin; a proposal cannot choose its budget class.
    proposal = ActionProposal.model_validate_json(proposal.model_dump_json())
    if proposal.proposer != proposer:
        raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
    if proposal.run != run or run.revision != revision.reference:
        raise ServiceFault(ServiceErrorCode.CONFLICT)
    matches = [a for a in allowed if a.action == proposal.action]
    if len(matches) != 1 or not set(matches[0].prerequisites) <= satisfied:
        raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
    if budget.steps_remaining == 0 or (
        proposer.kind == "model" and budget.model_calls_remaining == 0
    ):
        raise ServiceFault(ServiceErrorCode.CAPABILITY)
    if proposal.action in {ActionKind.EXTRACT, ActionKind.REFERENCE}:
        if proposal.document not in revision.documents or proposal.page is None:
            raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
        if proposal.action == ActionKind.REFERENCE and proposal.document.purpose != "reference":
            raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
    # Actual page/citation resolution and source-purpose checks remain tool/resolver duties.


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
