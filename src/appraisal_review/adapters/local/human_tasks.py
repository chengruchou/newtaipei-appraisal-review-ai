"""Single-process transactional task/revision/re-entry store; deliberately non-durable."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from uuid import UUID, uuid4

from pydantic import RootModel

from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.application.service_guards import Principal, ServiceFault, admit_response
from appraisal_review.domain.factor_models import CaseReviewResult
from appraisal_review.domain.review_contracts import content_digest
from appraisal_review.domain.service_contracts import (
    ActorReference,
    BoundedWorkflowResult,
    FactSideReference,
    HumanResponse,
    HumanResponseResult,
    HumanTask,
    Permission,
    ResponseAction,
    RevisionReference,
    RunReference,
    ServiceErrorCode,
    TaskKind,
    WorkflowContinuation,
    WorkflowPause,
)
from appraisal_review.ports.service import HumanTaskContext, ReentryReview, TaskTransition


class NonDurableInMemoryHumanTaskRepository:
    """Serialize all records; one lock covers admission and side-effect-free transforms."""

    def __init__(self, *, event_id_factory: Callable[[], UUID] = uuid4) -> None:
        self._lock = asyncio.Lock()
        self._event_id = event_id_factory
        self._snapshots: dict[tuple[str, str], RevisionSnapshot] = {}
        self._current: dict[str, str] = {}
        self._active_run: dict[str, UUID] = {}
        self._runs: dict[UUID, str] = {}
        self._reviews: dict[UUID, str] = {}
        self._tasks: dict[UUID, str] = {}
        self._responses: dict[tuple[str, str], str] = {}
        self._pending: set[UUID] = set()
        self._pauses: dict[UUID, str] = {}
        self._continuations: dict[UUID, str] = {}
        self._confirmations: dict[str, tuple[tuple[str, str], ...]] = {}

    async def pause(self, principal: Principal, result: BoundedWorkflowResult) -> WorkflowPause:
        result = BoundedWorkflowResult.model_validate_json(result.model_dump_json())
        principal.require(result.run.revision.case_id, Permission.REVIEW)
        if principal.actor.kind not in {"human", "system"}:
            raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
        async with self._lock:
            prior = self._pauses.get(result.run.run_id)
            if prior is not None:
                checkpoint = WorkflowPause.model_validate_json(prior)
                if checkpoint.result != result:
                    raise ServiceFault(ServiceErrorCode.CONFLICT)
                return checkpoint
            self._context(principal, result.run)
            tasks = tuple(
                task
                for record in self._tasks.values()
                if (task := HumanTask.model_validate_json(record)).run == result.run
                and task.state == "open"
            )
            checkpoint = WorkflowPause(
                pause_id=self._event_id(),
                result=result,
                task_ids=tuple(task.task_id for task in tasks),
            )
            receipt = result.events[-1].tool_result
            if receipt is None or receipt.result_digest != content_digest(
                RootModel[tuple[HumanTask, ...]](tasks)
            ):
                raise ServiceFault(ServiceErrorCode.CONFLICT)
            self._pauses[result.run.run_id] = checkpoint.model_dump_json()
            return WorkflowPause.model_validate_json(self._pauses[result.run.run_id])

    async def read_pause(self, principal: Principal, run: RunReference) -> WorkflowPause:
        principal.require(run.revision.case_id, Permission.REVIEW)
        async with self._lock:
            record = self._pauses.get(run.run_id)
            if record is None:
                raise ServiceFault(ServiceErrorCode.NOT_FOUND)
            checkpoint = WorkflowPause.model_validate_json(record)
            if checkpoint.result.run != run:
                raise ServiceFault(ServiceErrorCode.CONFLICT)
            return checkpoint

    async def continuation(
        self, principal: Principal, response_event_id: UUID
    ) -> WorkflowContinuation:
        async with self._lock:
            record = self._continuations.get(response_event_id)
            if record is None:
                raise ServiceFault(ServiceErrorCode.NOT_FOUND)
            continuation = WorkflowContinuation.model_validate_json(record)
            principal.require(continuation.previous_run.revision.case_id, Permission.REVIEW)
            return continuation

    async def register(
        self, snapshot: RevisionSnapshot, run: RunReference, review: CaseReviewResult
    ) -> None:
        """Trusted bootstrap only; application supplies an actual deterministic result."""
        material = snapshot.material
        revision = snapshot.revision
        review = CaseReviewResult.model_validate_json(review.model_dump_json())
        if (
            run.revision != revision.reference
            or content_digest(material) != revision.reference.material_digest
            or review.identity != material.policy.identity
        ):
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        async with self._lock:
            case = revision.reference.case_id
            if case in self._current or run.run_id in self._runs:
                raise ServiceFault(ServiceErrorCode.CONFLICT)
            self._snapshots[case, revision.reference.revision_id] = snapshot
            self._current[case] = revision.reference.revision_id
            self._active_run[case] = run.run_id
            self._runs[run.run_id] = run.model_dump_json()
            self._reviews[run.run_id] = review.model_dump_json()

    def _context(
        self, principal: Principal, run: RunReference, *, allow_pending: bool = False
    ) -> HumanTaskContext:
        principal.require(run.revision.case_id, Permission.REVIEW)
        if self._runs.get(run.run_id) != run.model_dump_json():
            raise ServiceFault(ServiceErrorCode.NOT_FOUND)
        if (
            self._current.get(run.revision.case_id) != run.revision.revision_id
            or self._active_run.get(run.revision.case_id) != run.run_id
        ):
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        snapshot = self._snapshots[run.revision.case_id, run.revision.revision_id]
        if snapshot.revision.reference != run.revision:
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        result = self._reviews.get(run.run_id)
        if result is None or (run.run_id in self._pending and not allow_pending):
            raise ServiceFault(ServiceErrorCode.CAPABILITY)
        confirmed = tuple(
            FactSideReference.model_validate_json(side)
            for actor, side in self._confirmations.get(run.revision.case_id, ())
            if ActorReference.model_validate_json(actor) == principal.actor
        )
        return HumanTaskContext(
            snapshot, run, CaseReviewResult.model_validate_json(result), confirmed
        )

    async def context(self, principal: Principal, run: RunReference) -> HumanTaskContext:
        async with self._lock:
            return self._context(principal, run)

    async def read_revision(
        self, principal: Principal, reference: RevisionReference
    ) -> RevisionSnapshot:
        principal.require(reference.case_id, Permission.REVIEW)
        async with self._lock:
            snapshot = self._snapshots.get((reference.case_id, reference.revision_id))
            if snapshot is None:
                raise ServiceFault(ServiceErrorCode.NOT_FOUND)
            if snapshot.revision.reference != reference:
                raise ServiceFault(ServiceErrorCode.CONFLICT)
            return snapshot

    async def create_task(self, principal: Principal, task: HumanTask) -> HumanTask:
        return (await self.create_tasks(principal, (task,)))[0]

    async def create_tasks(
        self, principal: Principal, tasks: tuple[HumanTask, ...]
    ) -> tuple[HumanTask, ...]:
        async with self._lock:
            before = self._tasks
            self._tasks = before.copy()
            try:
                return tuple(self._create_task(principal, task) for task in tasks)
            except BaseException:
                self._tasks = before
                raise

    def _create_task(self, principal: Principal, task: HumanTask) -> HumanTask:
        task = HumanTask.model_validate_json(task.model_dump_json())
        context = self._context(principal, task.run)
        if principal.actor.kind not in {"human", "system"}:
            raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
        findings = {finding.id for finding in context.review.findings}
        if (
            task.state != "open"
            or task.version != 1
            or not task.reason_code
            or not task.affected_subject_ids
            or not set(task.finding_ids) <= findings
        ):
            raise ServiceFault(ServiceErrorCode.VALIDATION)
        prior = self._tasks.get(task.task_id)
        if prior is not None:
            if prior != task.model_dump_json():
                raise ServiceFault(ServiceErrorCode.CONFLICT)
            return HumanTask.model_validate_json(prior)
        for record in self._tasks.values():
            existing = HumanTask.model_validate_json(record)
            if (
                existing.run == task.run
                and existing.state == "open"
                and existing.kind == task.kind
                and set(existing.finding_ids) == set(task.finding_ids)
                and existing.affected_subject_ids == task.affected_subject_ids
            ):
                if existing.model_copy(update={"task_id": task.task_id}) != task:
                    raise ServiceFault(ServiceErrorCode.CONFLICT)
                return existing
        self._tasks[task.task_id] = task.model_dump_json()
        return HumanTask.model_validate_json(self._tasks[task.task_id])

    def _task(self, principal: Principal, task_id: UUID) -> HumanTask:
        record = self._tasks.get(task_id)
        if record is None:
            raise ServiceFault(ServiceErrorCode.NOT_FOUND)
        task = HumanTask.model_validate_json(record)
        principal.require(task.run.revision.case_id, Permission.REVIEW)
        return task

    async def get_task(self, principal: Principal, task_id: UUID) -> HumanTask:
        async with self._lock:
            return self._task(principal, task_id)

    async def respond(
        self, principal: Principal, command: HumanResponse, transition: TaskTransition
    ) -> HumanResponseResult:
        command = HumanResponse.model_validate_json(command.model_dump_json())
        async with self._lock:
            task = self._task(principal, command.task_id)
            principal.require(task.run.revision.case_id, task.required_permission)
            if principal.actor.kind != "human":
                raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
            key = principal.actor.actor_id, command.idempotency_key
            replay = self._responses.get(key)
            if replay is not None:
                previous = HumanResponseResult.model_validate_json(replay)
                if (
                    previous.accepted.command != command
                    or previous.accepted.actor != principal.actor
                ):
                    raise ServiceFault(ServiceErrorCode.CONFLICT)
                return previous
            context = self._context(principal, task.run)
            accepted = admit_response(
                task, command, principal, current=context.snapshot.revision.reference
            )
            change = transition(task, context)
            revision = change.snapshot.revision
            material = change.snapshot.material
            if content_digest(material) != revision.reference.material_digest:
                raise ServiceFault(ServiceErrorCode.CONFLICT)
            changed = revision != context.snapshot.revision
            if changed and (
                revision.parent != context.snapshot.revision.reference
                or revision.reference.case_id != context.snapshot.revision.reference.case_id
                or (revision.reference.case_id, revision.reference.revision_id) in self._snapshots
                or change.next_run is None
            ):
                raise ServiceFault(ServiceErrorCode.CONFLICT)
            if change.next_run is not None and (
                change.next_run.revision != revision.reference
                or change.next_run.run_id in self._runs
                or change.next_run.attempt_id is not None
                or change.next_run.runtime_session_id is not None
            ):
                raise ServiceFault(ServiceErrorCode.CONFLICT)
            answered = task.model_copy(update={"state": "answered", "version": task.version + 1})
            result = HumanResponseResult(
                event_id=self._event_id(),
                accepted=accepted,
                task=answered,
                revision=revision,
                next_run=change.next_run,
                authorization=change.authorization,
            )
            # Prepare every potentially failing serialization before changing any store.
            result_json = result.model_dump_json()
            updates = {task.task_id: answered.model_dump_json()}
            if changed or change.next_run is not None:
                for task_id, record in self._tasks.items():
                    sibling = HumanTask.model_validate_json(record)
                    if (
                        task_id != task.task_id
                        and sibling.run.revision == context.snapshot.revision.reference
                        and sibling.state == "open"
                    ):
                        updates[task_id] = sibling.model_copy(
                            update={"state": "superseded", "version": sibling.version + 1}
                        ).model_dump_json()
            next_json = change.next_run.model_dump_json() if change.next_run is not None else None
            previous_review_json = self._reviews[task.run.run_id]
            continuation_json = None
            if change.next_run is not None and task.run.run_id in self._pauses:
                checkpoint = WorkflowPause.model_validate_json(self._pauses[task.run.run_id])
                if task.task_id not in checkpoint.task_ids:
                    raise ServiceFault(ServiceErrorCode.CONFLICT)
                continuation_json = WorkflowContinuation(
                    pause_id=checkpoint.pause_id,
                    response_event_id=result.event_id,
                    task_id=task.task_id,
                    previous_run=task.run,
                    next_run=change.next_run,
                ).model_dump_json()
            confirmations = self._confirmations.get(revision.reference.case_id, ())
            if changed:
                confirmations = (
                    (
                        *(
                            (principal.actor.model_dump_json(), side.model_dump_json())
                            for side in context.confirmed_sides
                        ),
                        (principal.actor.model_dump_json(), task.side.model_dump_json()),
                    )
                    if task.kind == TaskKind.FACT
                    and command.action == ResponseAction.CONFIRM
                    and task.side is not None
                    else ()
                )
            self._tasks.update(updates)
            self._confirmations[revision.reference.case_id] = confirmations
            if changed:
                self._snapshots[revision.reference.case_id, revision.reference.revision_id] = (
                    change.snapshot
                )
                self._current[revision.reference.case_id] = revision.reference.revision_id
            if change.next_run is not None and next_json is not None:
                self._runs[change.next_run.run_id] = next_json
                self._active_run[revision.reference.case_id] = change.next_run.run_id
                # Previous findings remain available as context until work recomputes them.
                self._reviews[change.next_run.run_id] = previous_review_json
                self._pending.add(change.next_run.run_id)
            self._responses[key] = result_json
            if continuation_json is not None:
                self._continuations[result.event_id] = continuation_json
            return HumanResponseResult.model_validate_json(result_json)

    async def reenter(
        self, principal: Principal, run: RunReference, review: ReentryReview
    ) -> CaseReviewResult:
        async with self._lock:
            context = self._context(principal, run, allow_pending=True)
            if run.run_id in self._pauses:
                raise ServiceFault(ServiceErrorCode.CAPABILITY)
            if run.run_id not in self._pending:
                return context.review
            # Local synchronous review remains under the lock: a newer correction cannot
            # interleave and publish an obsolete result. Cross-process claims belong to #9.
            result = CaseReviewResult.model_validate_json(review(context).model_dump_json())
            if result.identity != context.snapshot.material.policy.identity:
                raise ServiceFault(ServiceErrorCode.CONFLICT)
            serialized = result.model_dump_json()
            self._reviews[run.run_id] = serialized
            self._pending.remove(run.run_id)
            return CaseReviewResult.model_validate_json(serialized)

    async def responses(
        self, principal: Principal, case_id: str
    ) -> tuple[HumanResponseResult, ...]:
        principal.require(case_id, Permission.REVIEW)
        async with self._lock:
            return tuple(
                response
                for record in self._responses.values()
                if (
                    response := HumanResponseResult.model_validate_json(record)
                ).revision.reference.case_id
                == case_id
            )
