"""Reserved B/D service ports. M0 supplies no production persistence implementation."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.application.service_guards import Principal
from appraisal_review.domain.factor_models import CaseReviewResult
from appraisal_review.domain.service_contracts import (
    AuthorizationRecord,
    BoundedWorkflowResult,
    DocumentReference,
    FactSideReference,
    HumanResponse,
    HumanResponseResult,
    HumanTask,
    MaterialRevision,
    ReviewSubmission,
    RevisionReference,
    RunReference,
    ServiceResult,
    WorkflowContinuation,
    WorkflowPause,
)


@dataclass(frozen=True)
class HumanTaskContext:
    snapshot: RevisionSnapshot
    run: RunReference
    review: CaseReviewResult
    confirmed_sides: tuple[FactSideReference, ...] = ()


@dataclass(frozen=True)
class HumanTaskTransition:
    snapshot: RevisionSnapshot
    next_run: RunReference | None = None
    authorization: AuthorizationRecord | None = None


TaskTransition = Callable[[HumanTask, HumanTaskContext], HumanTaskTransition]
ReentryReview = Callable[[HumanTaskContext], CaseReviewResult]


@dataclass(frozen=True)
class ResolvedDocument:
    """Internal resolver output; the URI must not enter external service responses."""

    reference: DocumentReference
    storage_uri: str


class PrincipalResolver(Protocol):
    async def current_principal(self) -> Principal:
        """Authenticate adapter-bound request context; never trust body actor/roles."""
        ...


class DocumentResolver(Protocol):
    async def resolve(self, principal: Principal, reference: DocumentReference) -> ResolvedDocument:
        """Authorize case/document/version before storage access; missing -> not_found."""
        ...


class RevisionRepository(Protocol):
    async def read_revision(
        self, principal: Principal, reference: RevisionReference
    ) -> RevisionSnapshot:
        """Return an authorized detached historical revision with exact digest binding."""
        ...

    async def create(
        self,
        principal: Principal,
        snapshot: RevisionSnapshot,
        *,
        expected_parent: RevisionReference | None,
    ) -> MaterialRevision:
        """Append via CAS; None means create only if absent; stale -> version_conflict."""
        ...


class HumanTaskRepository(Protocol):
    async def context(self, principal: Principal, run: RunReference) -> HumanTaskContext: ...

    async def create_task(self, principal: Principal, task: HumanTask) -> HumanTask: ...

    async def create_tasks(
        self, principal: Principal, tasks: tuple[HumanTask, ...]
    ) -> tuple[HumanTask, ...]:
        """Validate and store the entire handoff batch atomically."""
        ...

    async def get_task(self, principal: Principal, task_id: UUID) -> HumanTask: ...

    async def respond(
        self, principal: Principal, command: HumanResponse, transition: TaskTransition
    ) -> HumanResponseResult:
        """Recheck admission, transform, consume key and append revision/work atomically.

        The synchronous transition is trusted application code, not an external command.
        Exact actor/key/payload replay returns the original result. Implementations must
        roll back every task/revision/event/work change if validation or transform fails.
        """
        ...

    async def reenter(
        self, principal: Principal, run: RunReference, review: ReentryReview
    ) -> CaseReviewResult:
        """Run and save deterministic review for current queued work, at most once locally."""
        ...


class PauseResumeRepository(Protocol):
    """Trusted local integration seam; no HTTP command or cloud durability claim.

    Durable adapters must commit waiting/tasks/trace/attempt release together (#24/#29).
    An admitted response must atomically link continuation with revision and outbox;
    publication still belongs to #28. A DTO never authorizes external execution.
    """

    async def pause(self, principal: Principal, result: BoundedWorkflowResult) -> WorkflowPause: ...

    async def read_pause(self, principal: Principal, run: RunReference) -> WorkflowPause: ...

    async def continuation(
        self, principal: Principal, response_event_id: UUID
    ) -> WorkflowContinuation: ...


class JobRepository(Protocol):
    async def submit(self, principal: Principal, request: ReviewSubmission) -> RunReference:
        """Atomically persist principal/key/digest, fixed run input and dispatch outbox."""
        ...

    async def publish(
        self,
        principal: Principal,
        result: ServiceResult,
        *,
        fencing_token: int,
        expected_result_version: int,
    ) -> None:
        """Require publication authority and current attempt/lease/token/version via CAS.

        Persist only verified manifest references; old attempts cannot publish.
        Store implementations must provide cross-process transactions/recovery.
        M0's pure guards provide none of these durability guarantees.
        """
        ...
