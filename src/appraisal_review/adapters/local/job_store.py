"""In-memory reference implementation of the durable job store.

This is the local half of the shared contract suite. It is not a durable store: state is
lost with the process and a single asyncio lock stands in for a cross-process
transaction. Its purpose is to make every condition in ports.jobs executable, so that the
DynamoDB adapter is verified against the same expectations rather than against prose.

Every mutation runs under one lock and rejects the same situations the conditional writes
in docs/adr/0015-durable-review-jobs.md reject.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, replace
from uuid import UUID, uuid4

from appraisal_review.application.job_state import (
    JobEvent,
    JobFacts,
    JobPolicy,
    initial_transition,
    next_state,
)
from appraisal_review.application.service_guards import (
    IdempotencyRecord,
    Principal,
    ServiceFault,
    check_idempotency,
    submission_digest,
)
from appraisal_review.domain.document_models import Digest
from appraisal_review.domain.job_contracts import TERMINAL_STATUSES, JobStatus
from appraisal_review.domain.review_contracts import content_digest
from appraisal_review.domain.service_contracts import (
    DocumentReference,
    Permission,
    ReviewSubmission,
    RevisionReference,
    RunReference,
    ServiceErrorCode,
    ServiceProblem,
    ServiceResult,
)
from appraisal_review.ports.jobs import (
    ClaimedAttempt,
    ConditionFailed,
    DispatchRecord,
    ExpiredLease,
    HeartbeatState,
    JobRecord,
    JobStore,
    ResultReference,
    ResultStore,
)

# Cancellation has no dedicated v1 error code. version_conflict is the least misleading
# of the six: the job stopped because its state changed by request, and it is not a
# server fault. The exact reason stays in JobStatusView.job_status.
CANCELLED_PROBLEM = ServiceProblem(code=ServiceErrorCode.CONFLICT)

FINISH_EVENTS = frozenset(
    {
        JobEvent.PUBLISH_RESULT,
        JobEvent.NEEDS_HUMAN,
        JobEvent.RETRYABLE_ERROR,
        JobEvent.PERMANENT_ERROR,
        JobEvent.CANCEL_ACKNOWLEDGED,
    }
)


@dataclass
class _Job:
    job_id: UUID
    case_id: str
    principal_id: str
    status: JobStatus
    current_run_id: UUID
    attempt_count: int = 0
    lease_takeover_count: int = 0
    cancel_requested: bool = False
    open_task_ids: tuple[UUID, ...] = ()
    problem: ServiceProblem | None = None
    outbox_seq: int = 0
    created_at: int = 0
    updated_at: int = 0


@dataclass
class _Run:
    run_id: UUID
    revision: RevisionReference
    documents: tuple[DocumentReference, ...]
    payload_digest: str
    fencing_token: int = 0
    result_version: int = 0
    lease_owner: UUID | None = None
    lease_expires_at: int | None = None
    attempt_id: UUID | None = None
    run_status: JobStatus = JobStatus.QUEUED


@dataclass
class _Outbox:
    job_id: UUID
    run_id: UUID
    outbox_seq: int
    dispatch_token: UUID
    available_at: int
    dispatch_attempts: int = 0
    dispatch_state: str = "pending"


@dataclass
class _Attempt:
    job_id: UUID
    run_id: UUID
    attempt_id: UUID
    fencing_token: int
    owner: UUID
    claimed_at: int
    outcome: str = "running"


def idempotency_partition(principal_id: str, key: str) -> str:
    """Hash the caller-supplied key so its content and length never shape a stored key."""
    return f"IDEM#{principal_id}#{hashlib.sha256(key.encode('utf-8')).hexdigest()}"


class InMemoryResultStore:
    """Attempt-scoped result bodies held in the process, standing in for object storage."""

    def __init__(self) -> None:
        self._bodies: dict[tuple[UUID, int], ServiceResult] = {}
        self._lock = asyncio.Lock()

    async def put(self, *, run_id: UUID, result_version: int, result: ServiceResult) -> Digest:
        body = ServiceResult.model_validate_json(result.model_dump_json())
        if body.run.run_id != run_id or body.result_version != result_version:
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        digest: Digest = content_digest(body)
        async with self._lock:
            stored = self._bodies.get((run_id, result_version))
            # A retried write of the identical body is fine; a different body is not,
            # because the committed reference already pins one digest.
            if stored is not None and content_digest(stored) != digest:
                raise ServiceFault(ServiceErrorCode.CONFLICT)
            self._bodies[(run_id, result_version)] = body
        return digest

    async def get(self, *, run_id: UUID, result_version: int) -> ServiceResult | None:
        async with self._lock:
            return self._bodies.get((run_id, result_version))


class InMemoryJobStore:
    """Single-process job store with DynamoDB's conditional-write semantics."""

    def __init__(self, *, policy: JobPolicy | None = None) -> None:
        self.policy = policy or JobPolicy()
        self._jobs: dict[UUID, _Job] = {}
        self._runs: dict[tuple[UUID, UUID], _Run] = {}
        self._outbox: dict[tuple[UUID, int], _Outbox] = {}
        self._attempts: list[_Attempt] = []
        self._idempotency: dict[str, IdempotencyRecord] = {}
        self._results: dict[tuple[UUID, int], ResultReference] = {}
        self._lock = asyncio.Lock()

    # -- reads ---------------------------------------------------------------

    def _record(self, job: _Job) -> JobRecord:
        run = self._runs[(job.job_id, job.current_run_id)]
        return JobRecord(
            job_id=job.job_id,
            case_id=job.case_id,
            principal_id=job.principal_id,
            status=job.status,
            current_run=RunReference(
                run_id=run.run_id,
                revision=run.revision,
                attempt_id=run.attempt_id if job.status == JobStatus.RUNNING else None,
            ),
            attempt_count=job.attempt_count,
            lease_takeover_count=job.lease_takeover_count,
            result_version=run.result_version,
            cancel_requested=job.cancel_requested,
            open_task_ids=job.open_task_ids,
            problem=job.problem,
        )

    def _facts(self, job: _Job) -> JobFacts:
        return JobFacts(
            status=job.status,
            attempt_count=job.attempt_count,
            lease_takeover_count=job.lease_takeover_count,
            cancel_requested=job.cancel_requested,
            has_open_tasks=bool(job.open_task_ids),
        )

    async def read_job(self, *, job_id: UUID) -> JobRecord | None:
        async with self._lock:
            job = self._jobs.get(job_id)
            return None if job is None else self._record(job)

    async def jobs_for_case(self, *, case_id: str, principal_id: str) -> tuple[JobRecord, ...]:
        """The case's reviews, most presentable first, so none is opened blindly.

        Visibility is the CALLER's decision: every caller gates on case membership
        before asking, and a case member may see the case's reviews whoever submitted
        them - reviews are case records, not private drafts. ``principal_id`` only
        breaks ties, preferring the caller's own job between equals.

        Ordering puts first the job a reviewer can actually read: no recorded problem
        beats a stranded attempt, and a delivered result (result_version above zero)
        beats a run that never finished.
        """
        async with self._lock:
            found = [
                (self._record(job), job.principal_id)
                for job in self._jobs.values()
                if job.case_id == case_id
            ]
        return tuple(
            record
            for record, _ in sorted(
                found,
                key=lambda pair: (
                    pair[0].problem is not None,
                    -pair[0].result_version,
                    pair[1] != principal_id,
                ),
            )
        )

    async def read_result_reference(
        self, *, run_id: UUID, result_version: int
    ) -> ResultReference | None:
        async with self._lock:
            return self._results.get((run_id, result_version))

    # -- submission ----------------------------------------------------------

    async def create_job(
        self,
        principal: Principal,
        submission: ReviewSubmission,
        *,
        job_id: UUID,
        run_id: UUID,
        now: int,
    ) -> tuple[JobRecord, bool]:
        submission = ReviewSubmission.model_validate_json(submission.model_dump_json())
        digest = submission_digest(submission)
        partition = idempotency_partition(principal.actor.actor_id, submission.idempotency_key)
        async with self._lock:
            stored = self._idempotency.get(partition)
            if stored is not None:
                # Raises CONFLICT for the same key with a different canonical payload and
                # re-checks the caller's case permission before revealing anything.
                existing_run = check_idempotency(stored, principal, submission)
                job = self._jobs[self._run_owner(existing_run)]
                return self._record(job), False
            principal.require(submission.revision.case_id, Permission.REVIEW)
            if job_id in self._jobs or (job_id, run_id) in self._runs:
                raise ConditionFailed("Job identity already exists")
            transition = initial_transition()
            job = _Job(
                job_id=job_id,
                case_id=submission.revision.case_id,
                principal_id=principal.actor.actor_id,
                status=transition.status,
                current_run_id=run_id,
                created_at=now,
                updated_at=now,
            )
            self._runs[(job_id, run_id)] = _Run(
                run_id=run_id,
                revision=submission.revision,
                documents=submission.documents,
                payload_digest=digest,
            )
            self._jobs[job_id] = job
            self._idempotency[partition] = IdempotencyRecord(
                principal_id=principal.actor.actor_id,
                key=submission.idempotency_key,
                payload_digest=digest,
                run_id=run_id,
            )
            self._enqueue(job, run_id, available_at=now)
            return self._record(job), True

    def _run_owner(self, run_id: UUID) -> UUID:
        for (job_id, stored_run), _ in self._runs.items():
            if stored_run == run_id:
                return job_id
        raise ConditionFailed("Idempotency record references an unknown run")

    def _enqueue(self, job: _Job, run_id: UUID, *, available_at: int) -> None:
        job.outbox_seq += 1
        self._outbox[(job.job_id, job.outbox_seq)] = _Outbox(
            job_id=job.job_id,
            run_id=run_id,
            outbox_seq=job.outbox_seq,
            dispatch_token=uuid4(),
            available_at=available_at,
        )

    # -- execution -----------------------------------------------------------

    async def claim(
        self, *, job_id: UUID, run_id: UUID, owner: UUID, lease_seconds: int, now: int
    ) -> ClaimedAttempt:
        if lease_seconds < 1:
            raise ValueError("A lease must last at least one second")
        async with self._lock:
            job = self._jobs.get(job_id)
            run = self._runs.get((job_id, run_id))
            if job is None or run is None or job.current_run_id != run_id:
                raise ConditionFailed("Unknown or superseded run")
            if run.lease_expires_at is not None and run.lease_expires_at > now:
                raise ConditionFailed("A valid lease is held elsewhere")
            try:
                transition = next_state(self._facts(job), JobEvent.CLAIM, policy=self.policy)
            except ServiceFault as fault:
                # Losing a race is not the caller's error; it stops without failing work.
                raise ConditionFailed("Run is not claimable") from fault
            # The store owns the increment. A read-modify-write here would be a lost update.
            run.fencing_token += 1
            run.lease_owner = owner
            run.lease_expires_at = now + lease_seconds
            run.attempt_id = uuid4()
            run.run_status = transition.status
            job.status = transition.status
            job.updated_at = now
            self._attempts.append(
                _Attempt(
                    job_id=job_id,
                    run_id=run_id,
                    attempt_id=run.attempt_id,
                    fencing_token=run.fencing_token,
                    owner=owner,
                    claimed_at=now,
                )
            )
            return ClaimedAttempt(
                job_id=job_id,
                run_id=run_id,
                attempt_id=run.attempt_id,
                owner=owner,
                fencing_token=run.fencing_token,
                expected_result_version=run.result_version,
                lease_expires_at=run.lease_expires_at,
            )

    def _authority(self, attempt: ClaimedAttempt) -> tuple[_Job, _Run]:
        job = self._jobs.get(attempt.job_id)
        run = self._runs.get((attempt.job_id, attempt.run_id))
        if job is None or run is None:
            raise ConditionFailed("Unknown run")
        if (
            run.lease_owner != attempt.owner
            or run.fencing_token != attempt.fencing_token
            or run.attempt_id != attempt.attempt_id
            or job.status != JobStatus.RUNNING
        ):
            raise ConditionFailed("This attempt no longer holds the lease")
        return job, run

    async def heartbeat(
        self, attempt: ClaimedAttempt, *, lease_seconds: int, now: int
    ) -> HeartbeatState:
        if lease_seconds < 1:
            raise ValueError("A lease must last at least one second")
        async with self._lock:
            job, run = self._authority(attempt)
            next_state(self._facts(job), JobEvent.HEARTBEAT, policy=self.policy)
            run.lease_expires_at = now + lease_seconds
            job.updated_at = now
            return HeartbeatState(
                lease_expires_at=run.lease_expires_at, cancel_requested=job.cancel_requested
            )

    async def finish(
        self,
        attempt: ClaimedAttempt,
        *,
        event: JobEvent,
        now: int,
        problem: ServiceProblem | None = None,
        open_task_ids: tuple[UUID, ...] = (),
        result: ResultReference | None = None,
        available_at: int | None = None,
    ) -> JobRecord:
        del available_at  # Retry scheduling is a separate, reconciler-driven write.
        if event not in FINISH_EVENTS:
            raise ValueError("finish applies only lease-bound terminal or waiting events")
        async with self._lock:
            job, run = self._authority(attempt)
            facts = replace(self._facts(job), has_open_tasks=bool(open_task_ids))
            transition = next_state(facts, event, policy=self.policy)
            if event == JobEvent.PUBLISH_RESULT:
                self._commit_result(run, attempt, result)
            elif result is not None:
                raise ValueError("Only publication commits a result reference")
            if transition.status in {JobStatus.FAILED} and problem is None:
                raise ValueError("A terminal failure requires a sanitized problem")
            if transition.status == JobStatus.CANCELLED:
                problem = CANCELLED_PROBLEM
            if transition.status not in {JobStatus.FAILED, JobStatus.CANCELLED}:
                problem = None
            job.attempt_count += transition.attempt_delta
            job.status = transition.status
            job.problem = problem
            # A cancel that preempted this outcome drops the tasks with it: they were
            # never opened for a reviewer, and the job is terminal.
            job.open_task_ids = (
                ()
                if transition.status == JobStatus.CANCELLED
                else tuple(dict.fromkeys(open_task_ids))
            )
            job.updated_at = now
            run.run_status = transition.status
            self._release(run, transition.release_lease)
            self._close_attempt(attempt, event.value)
            return self._record(job)

    def _commit_result(
        self, run: _Run, attempt: ClaimedAttempt, result: ResultReference | None
    ) -> None:
        if result is None:
            raise ValueError("Publication requires a result reference")
        if (
            result.run_id != attempt.run_id
            or result.fencing_token != attempt.fencing_token
            or run.result_version != attempt.expected_result_version
            or result.result_version != attempt.expected_result_version + 1
        ):
            # A resumed old attempt lands here: its token and expected version are stale.
            raise ConditionFailed("Stale attempt cannot publish over a newer result")
        if (result.run_id, result.result_version) in self._results:
            raise ConditionFailed("Result versions are append-only")
        self._results[(result.run_id, result.result_version)] = result
        run.result_version = result.result_version

    def _release(self, run: _Run, release: bool) -> None:
        if not release:
            return
        # Releasing means removing the attributes, so the run also leaves the sparse
        # expired-lease index instead of being scanned forever.
        run.lease_owner = None
        run.lease_expires_at = None
        run.attempt_id = None

    def _close_attempt(self, attempt: ClaimedAttempt, outcome: str) -> None:
        for record in self._attempts:
            if record.attempt_id == attempt.attempt_id:
                record.outcome = outcome

    async def expire_lease(self, lease: ExpiredLease, *, now: int) -> JobRecord:
        async with self._lock:
            job = self._jobs.get(lease.job_id)
            run = self._runs.get((lease.job_id, lease.run_id))
            if job is None or run is None:
                raise ConditionFailed("Unknown run")
            if (
                run.fencing_token != lease.fencing_token
                or run.lease_expires_at is None
                or run.lease_expires_at > now
                or job.status != JobStatus.RUNNING
            ):
                raise ConditionFailed("The lease is still valid or already reclaimed")
            transition = next_state(self._facts(job), JobEvent.LEASE_EXPIRED, policy=self.policy)
            job.lease_takeover_count += transition.takeover_delta
            job.status = transition.status
            job.updated_at = now
            run.run_status = transition.status
            if transition.status == JobStatus.FAILED:
                job.problem = ServiceProblem(code=ServiceErrorCode.EXECUTION)
            if transition.status == JobStatus.CANCELLED:
                # The reclaim completed a cancellation the dead worker never acknowledged.
                job.problem = CANCELLED_PROBLEM
                job.open_task_ids = ()
            self._release(run, transition.release_lease)
            if transition.enqueue_outbox:
                # A fresh round, because the previous one was already marked sent and its
                # message may have died with the worker. A duplicate delivery loses the
                # claim race; a lost one would strand the job.
                self._enqueue(job, lease.run_id, available_at=now)
            return self._record(job)

    # -- caller and reconciler driven ---------------------------------------

    async def cancel(self, *, job_id: UUID, now: int) -> JobRecord:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise ConditionFailed("Unknown job")
            transition = next_state(self._facts(job), JobEvent.CANCEL, policy=self.policy)
            run = self._runs[(job_id, job.current_run_id)]
            if transition.request_cancel:
                job.cancel_requested = True
            else:
                job.status = transition.status
                job.problem = CANCELLED_PROBLEM
                job.open_task_ids = ()
                run.run_status = transition.status
                self._release(run, transition.release_lease)
            job.updated_at = now
            return self._record(job)

    async def schedule_retry(self, *, job_id: UUID, available_at: int, now: int) -> JobRecord:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise ConditionFailed("Unknown job")
            try:
                transition = next_state(
                    self._facts(job), JobEvent.SCHEDULE_RETRY, policy=self.policy
                )
            except ServiceFault as fault:
                # The owner's own retry, or a claim, landed first. Two schedulers racing
                # here is expected now that the reconciler scans for stranded jobs, and
                # losing is not the caller's error.
                raise ConditionFailed("The job is no longer awaiting a retry") from fault
            job.status = transition.status
            job.problem = None
            job.updated_at = now
            run = self._runs[(job_id, job.current_run_id)]
            run.run_status = transition.status
            self._enqueue(job, job.current_run_id, available_at=available_at)
            return self._record(job)

    async def reject_human_task(
        self, *, job_id: UUID, run_id: UUID, task_id: UUID, now: int
    ) -> JobRecord:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.current_run_id != run_id or task_id not in job.open_task_ids:
                raise ConditionFailed("The task no longer belongs to the current open run")
            remaining = tuple(item for item in job.open_task_ids if item != task_id)
            transition = next_state(
                replace(self._facts(job), has_open_tasks=bool(remaining)),
                JobEvent.HUMAN_REJECTED,
                policy=self.policy,
            )
            job.open_task_ids = remaining
            job.status = transition.status
            job.problem = (
                ServiceProblem(code=ServiceErrorCode.CONFLICT)
                if transition.status == JobStatus.FAILED
                else None
            )
            job.updated_at = now
            self._runs[(job_id, run_id)].run_status = transition.status
            return self._record(job)

    async def resume_after_human(self, *, job_id: UUID, run: RunReference, now: int) -> JobRecord:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise ConditionFailed("Unknown job")
            current = self._runs[(job_id, job.current_run_id)]
            if (
                run.run_id == current.run_id
                or (job_id, run.run_id) in self._runs
                or run.revision == current.revision
                or run.revision.case_id != job.case_id
                or run.attempt_id is not None
            ):
                # A response produces a new revision and therefore a new run; it never
                # revives the finished attempt or reuses its identity.
                raise ConditionFailed("Resuming requires a new run for a new revision")
            transition = next_state(
                self._facts(job), JobEvent.HUMAN_RESPONSE_COMMITTED, policy=self.policy
            )
            self._runs[(job_id, run.run_id)] = _Run(
                run_id=run.run_id,
                revision=run.revision,
                documents=current.documents,
                payload_digest=current.payload_digest,
            )
            job.current_run_id = run.run_id
            job.status = transition.status
            job.open_task_ids = ()
            job.updated_at = now
            self._enqueue(job, run.run_id, available_at=now)
            return self._record(job)

    # -- outbox --------------------------------------------------------------

    async def mark_dispatched(self, record: DispatchRecord, *, now: int) -> JobRecord:
        async with self._lock:
            entry = self._outbox.get((record.job_id, record.outbox_seq))
            job = self._jobs.get(record.job_id)
            if entry is None or job is None:
                raise ConditionFailed("Unknown outbox entry")
            if entry.dispatch_token != record.dispatch_token or entry.dispatch_state != "pending":
                raise ConditionFailed("This dispatch round is no longer pending")
            entry.dispatch_state = "sent"
            # A worker can claim between the send and this mark. Recording the send must
            # still succeed; only the job status transition is conditional.
            if job.status == JobStatus.QUEUED:
                transition = next_state(
                    self._facts(job), JobEvent.DISPATCH_SUCCEEDED, policy=self.policy
                )
                job.status = transition.status
                self._runs[(job.job_id, entry.run_id)].run_status = transition.status
            job.updated_at = now
            return self._record(job)

    async def reschedule_dispatch(self, record: DispatchRecord, *, available_at: int) -> bool:
        async with self._lock:
            entry = self._outbox.get((record.job_id, record.outbox_seq))
            if entry is None:
                raise ConditionFailed("Unknown outbox entry")
            if entry.dispatch_token != record.dispatch_token or entry.dispatch_state != "pending":
                raise ConditionFailed("This dispatch round is no longer pending")
            job = self._jobs[record.job_id]
            if job.status in TERMINAL_STATUSES:
                # The job finished — often by cancellation — while this round waited. There
                # is no work left to hand over, so the round is abandoned rather than
                # retried forever against a job no worker may claim.
                entry.dispatch_state = "abandoned"
                return False
            # T3: the work is still durably owned, so only the schedule moves.
            next_state(self._facts(job), JobEvent.DISPATCH_FAILED, policy=self.policy)
            entry.available_at = available_at
            entry.dispatch_attempts += 1
            return True

    async def pending_dispatches(self, *, now: int, limit: int) -> tuple[DispatchRecord, ...]:
        if limit < 1:
            raise ValueError("A scan needs a positive limit")
        async with self._lock:
            due = [
                entry
                for entry in self._outbox.values()
                if entry.dispatch_state == "pending" and entry.available_at <= now
            ]
            due.sort(key=lambda entry: (entry.available_at, entry.outbox_seq))
            return tuple(
                DispatchRecord(
                    job_id=entry.job_id,
                    run_id=entry.run_id,
                    outbox_seq=entry.outbox_seq,
                    dispatch_token=entry.dispatch_token,
                    available_at=entry.available_at,
                    dispatch_attempts=entry.dispatch_attempts,
                )
                for entry in due[:limit]
            )

    async def stranded_retryables(
        self, *, stranded_before: int, limit: int
    ) -> tuple[JobRecord, ...]:
        if limit < 1:
            raise ValueError("A scan needs a positive limit")
        async with self._lock:
            stranded = [
                job
                for job in self._jobs.values()
                if job.status == JobStatus.RETRYABLE_FAILED and job.updated_at <= stranded_before
            ]
            stranded.sort(key=lambda job: job.updated_at)
            return tuple(self._record(job) for job in stranded[:limit])

    async def expired_leases(self, *, now: int, limit: int) -> tuple[ExpiredLease, ...]:
        if limit < 1:
            raise ValueError("A scan needs a positive limit")
        async with self._lock:
            expired = [
                (job_id, run)
                for (job_id, _), run in self._runs.items()
                if run.lease_expires_at is not None
                and run.lease_expires_at <= now
                and self._jobs[job_id].status == JobStatus.RUNNING
            ]
            expired.sort(key=lambda item: item[1].lease_expires_at or 0)
            return tuple(
                ExpiredLease(
                    job_id=job_id,
                    run_id=run.run_id,
                    fencing_token=run.fencing_token,
                    lease_expires_at=run.lease_expires_at or 0,
                )
                for job_id, run in expired[:limit]
            )

    # -- test and canary support --------------------------------------------

    def stored_items(self) -> tuple[object, ...]:
        """Everything this store holds, for a privacy canary scan over the whole plane."""
        return (
            tuple(self._jobs.values()),
            tuple(self._runs.values()),
            tuple(self._outbox.values()),
            tuple(self._attempts),
            tuple(self._idempotency.values()),
            tuple(self._results.values()),
        )

    def terminal_jobs(self) -> tuple[UUID, ...]:
        return tuple(
            job_id for job_id, job in self._jobs.items() if job.status in TERMINAL_STATUSES
        )


def _port_conformance() -> tuple[JobStore, ResultStore]:
    """Static proof that these adapters still satisfy the ports.

    Type checking fails here if a port method is renamed or its signature drifts, which
    is cheaper than discovering it when the DynamoDB adapter is written against the port.
    """
    return InMemoryJobStore(), InMemoryResultStore()
