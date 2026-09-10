"""Application service for durable review jobs.

Three audiences share one state machine here: the HTTP boundary (submit, poll, cancel),
the worker (claim, heartbeat, finish) and the reconciler (dispatch and lease recovery).
Keeping recovery in the application layer, above the store port, is what lets the local
contract suite and a future cloud deployment exercise the same recovery logic.

Nothing in this module executes a review. Accepting a submission means accepting durable
responsibility for it, never that any work has started or succeeded.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

from appraisal_review.application.job_state import JobEvent, JobPolicy
from appraisal_review.application.service_guards import Principal, ServiceFault
from appraisal_review.domain.job_contracts import (
    TERMINAL_STATUSES,
    JobAcceptance,
    JobReference,
    JobStatus,
    JobStatusView,
)
from appraisal_review.domain.review_contracts import content_digest
from appraisal_review.domain.service_contracts import (
    Permission,
    ReviewSubmission,
    RunReference,
    ServiceErrorCode,
    ServiceProblem,
    ServiceResult,
)
from appraisal_review.ports.jobs import (
    ClaimedAttempt,
    ConditionFailed,
    DispatchRecord,
    HeartbeatState,
    JobRecord,
    JobStore,
    ResultReference,
    ResultStore,
)

Clock = Callable[[], int]
IdFactory = Callable[[], UUID]
Jitter = Callable[[int], int]

# Codes a worker may attribute to a permanent payload or authorization failure. Anything
# else is either retryable infrastructure or a business result, never a third category.
PERMANENT_CODES = frozenset(
    {ServiceErrorCode.VALIDATION, ServiceErrorCode.UNAUTHORIZED, ServiceErrorCode.NOT_FOUND}
)


def _now() -> int:
    return int(time.time())


def _jitter(bound: int) -> int:
    return random.randrange(bound + 1) if bound > 0 else 0


@dataclass(frozen=True)
class SubmissionOutcome:
    """A submission is either newly accepted or an exact replay of a known one."""

    created: bool
    status: JobStatusView
    acceptance: JobAcceptance | None = None


def status_view(record: JobRecord) -> JobStatusView:
    """Project the stored control plane onto the authorized wire view."""
    return JobStatusView(
        job=JobReference(case_id=record.case_id, job_id=record.job_id),
        job_status=record.status,
        current_run=record.current_run,
        attempt_count=record.attempt_count,
        result_version=record.result_version,
        open_task_ids=record.open_task_ids,
        problem=record.problem,
    )


def result_reference(
    result: ServiceResult, attempt: ClaimedAttempt, digest: str
) -> ResultReference:
    """Keep identity, status, digest and counts; findings and citations stay in the body."""
    return ResultReference(
        run_id=attempt.run_id,
        result_version=result.result_version,
        fencing_token=attempt.fencing_token,
        execution_status=result.execution_status,
        business_status=result.business_status,
        artifact_status=result.artifact_status,
        result_digest=digest,
        artifact_ids=tuple(artifact.artifact_id for artifact in result.artifacts),
        finding_count=len(result.findings),
    )


class ReviewJobService:
    """Durable submission, execution authority and recovery over an injected store."""

    def __init__(
        self,
        store: JobStore,
        results: ResultStore,
        *,
        policy: JobPolicy | None = None,
        clock: Clock = _now,
        new_id: IdFactory = uuid4,
        jitter: Jitter = _jitter,
    ) -> None:
        self.store = store
        self.results = results
        self.policy = policy or JobPolicy()
        self.clock = clock
        self.new_id = new_id
        self.jitter = jitter

    # -- caller boundary -----------------------------------------------------

    async def submit(self, principal: Principal, submission: ReviewSubmission) -> SubmissionOutcome:
        """Persist the job, its run and its outbox entry before anything is dispatched.

        The API never runs a review inline: this returns once the work is durably owned.
        """
        submission = ReviewSubmission.model_validate_json(submission.model_dump_json())
        record, created = await self.store.create_job(
            principal,
            submission,
            job_id=self.new_id(),
            run_id=self.new_id(),
            now=self.clock(),
        )
        view = status_view(record)
        if not created:
            # An exact replay reports the job's real current status; claiming "queued"
            # for a job that is already running would be a fabricated acceptance.
            return SubmissionOutcome(created=False, status=view)
        return SubmissionOutcome(
            created=True,
            status=view,
            acceptance=JobAcceptance(job=view.job, run=record.current_run),
        )

    async def status(self, principal: Principal, job_id: UUID) -> JobStatusView:
        return status_view(await self._authorized(principal, job_id))

    async def result(self, principal: Principal, job_id: UUID) -> ServiceResult:
        """Return the committed result, or conflict while none has been published.

        A job that failed or was cancelled without producing a result reports that through
        its status view; this endpoint never fabricates a ServiceResult that no run wrote.
        """
        record = await self._authorized(principal, job_id)
        if record.result_version < 1:
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        run_id = record.current_run.run_id
        reference = await self.store.read_result_reference(
            run_id=run_id, result_version=record.result_version
        )
        body = await self.results.get(run_id=run_id, result_version=record.result_version)
        if reference is None or body is None or content_digest(body) != reference.result_digest:
            # A reference without a matching body is a server fault, not a caller error,
            # and an unverified body must never be served as a committed result.
            raise ServiceFault(ServiceErrorCode.EXECUTION)
        return body

    async def cancel(self, principal: Principal, job_id: UUID) -> JobStatusView:
        record = await self._authorized(principal, job_id)
        try:
            return status_view(await self.store.cancel(job_id=record.job_id, now=self.clock()))
        except ConditionFailed as failure:
            raise ServiceFault(ServiceErrorCode.CONFLICT) from failure

    async def _authorized(self, principal: Principal, job_id: UUID) -> JobRecord:
        record = await self.store.read_job(job_id=job_id)
        # Another principal's job, and a case this principal cannot see, are both reported
        # as absent. Answering "forbidden" would confirm that the job exists.
        if (
            record is None
            or record.principal_id != principal.actor.actor_id
            or record.case_id not in principal.case_ids
        ):
            raise ServiceFault(ServiceErrorCode.NOT_FOUND)
        principal.require(record.case_id, Permission.REVIEW)
        return record

    # -- worker boundary -----------------------------------------------------

    async def claim(self, *, job_id: UUID, run_id: UUID, owner: UUID) -> ClaimedAttempt:
        """Acquire execution authority. ConditionFailed means another attempt owns it."""
        return await self.store.claim(
            job_id=job_id,
            run_id=run_id,
            owner=owner,
            lease_seconds=self.policy.lease_seconds,
            now=self.clock(),
        )

    async def heartbeat(self, attempt: ClaimedAttempt) -> HeartbeatState:
        return await self.store.heartbeat(
            attempt, lease_seconds=self.policy.lease_seconds, now=self.clock()
        )

    async def publish(self, attempt: ClaimedAttempt, result: ServiceResult) -> JobRecord:
        """Write the body first, then commit its reference under this attempt's token.

        If the process dies between the two writes the body is an orphan that nothing
        references; if it dies after the commit the result is already durable. A stale
        attempt fails the commit and cannot overwrite a newer result.
        """
        result = ServiceResult.model_validate_json(result.model_dump_json())
        if (
            result.run.run_id != attempt.run_id
            or result.result_version != attempt.expected_result_version + 1
        ):
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        digest = await self.results.put(
            run_id=attempt.run_id, result_version=result.result_version, result=result
        )
        return await self.store.finish(
            attempt,
            event=JobEvent.PUBLISH_RESULT,
            now=self.clock(),
            result=result_reference(result, attempt, digest),
        )

    async def wait_for_human(
        self, attempt: ClaimedAttempt, task_ids: tuple[UUID, ...]
    ) -> JobRecord:
        """Finish the attempt and release its lease while a reviewer is required.

        This is not a failure and not progress: it consumes no attempt, schedules no
        retry and holds no execution resource open for the reviewer.
        """
        if not task_ids:
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        return await self.store.finish(
            attempt, event=JobEvent.NEEDS_HUMAN, now=self.clock(), open_task_ids=task_ids
        )

    async def fail(
        self, attempt: ClaimedAttempt, *, code: ServiceErrorCode, retryable: bool
    ) -> JobRecord:
        """Classify a failure as retryable infrastructure or permanent payload error.

        There is no third category. A permanent code can never be retried, and a retryable
        failure is rescheduled through the outbox rather than by holding the message.
        """
        if retryable == (code in PERMANENT_CODES):
            raise ValueError("Retryability must match the error classification")
        record = await self.store.finish(
            attempt,
            event=JobEvent.RETRYABLE_ERROR if retryable else JobEvent.PERMANENT_ERROR,
            now=self.clock(),
            problem=ServiceProblem(code=code),
        )
        if record.status != JobStatus.RETRYABLE_FAILED:
            return record
        return await self._reschedule(record)

    async def acknowledge_cancel(self, attempt: ClaimedAttempt) -> JobRecord:
        """A cooperating worker confirms it stopped; only the current attempt may do so."""
        return await self.store.finish(
            attempt, event=JobEvent.CANCEL_ACKNOWLEDGED, now=self.clock()
        )

    # -- human response integration -----------------------------------------

    async def resume(self, *, job_id: UUID, run: RunReference) -> JobRecord:
        """Attach and enqueue the run for a committed revision.

        The human-task owner calls this inside its own response transaction, so a stored
        revision cannot exist without scheduled work.
        """
        return await self.store.resume_after_human(job_id=job_id, run=run, now=self.clock())

    # -- reconciler ----------------------------------------------------------

    async def due_dispatches(self, *, limit: int = 25) -> tuple[DispatchRecord, ...]:
        """Outbox entries whose delay has elapsed. Candidates only, from a stale index."""
        return await self.store.pending_dispatches(now=self.clock(), limit=limit)

    async def confirm_dispatch(self, record: DispatchRecord) -> JobRecord:
        """Record that the queue accepted this exact round, after the send returned."""
        return await self.store.mark_dispatched(record, now=self.clock())

    async def defer_dispatch(self, record: DispatchRecord) -> None:
        """A send failed: back the outbox entry off. The job keeps its work and status."""
        delay = self.policy.backoff_seconds(record.dispatch_attempts + 1)
        await self.store.reschedule_dispatch(
            record, available_at=self.clock() + delay + self.jitter(delay)
        )

    async def reclaim_expired_leases(self, *, limit: int = 25) -> tuple[JobRecord, ...]:
        """Take back leases whose deadline passed, so a dead worker cannot strand a job."""
        now = self.clock()
        reclaimed = []
        for lease in await self.store.expired_leases(now=now, limit=limit):
            try:
                reclaimed.append(await self.store.expire_lease(lease, now=self.clock()))
            except ConditionFailed:
                # The candidate came from an index that may lag; losing here is expected.
                continue
        return tuple(reclaimed)

    async def recover_retryable(self, *, job_id: UUID) -> JobRecord | None:
        """Reschedule a job left in retryable_failed by a process that died mid-recovery."""
        record = await self.store.read_job(job_id=job_id)
        if record is None or record.status != JobStatus.RETRYABLE_FAILED:
            return None
        return await self._reschedule(record)

    async def _reschedule(self, record: JobRecord) -> JobRecord:
        delay = self.policy.backoff_seconds(record.attempt_count)
        return await self.store.schedule_retry(
            job_id=record.job_id,
            available_at=self.clock() + delay + self.jitter(delay),
            now=self.clock(),
        )

    def is_terminal(self, record: JobRecord) -> bool:
        return record.status in TERMINAL_STATUSES
