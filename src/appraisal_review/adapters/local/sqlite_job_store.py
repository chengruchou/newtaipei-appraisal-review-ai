"""Durable job transitions over the existing state machine and ports."""

from __future__ import annotations

from dataclasses import dataclass, replace
from uuid import UUID, uuid4

from appraisal_review.adapters.local.review_database import ReviewTransaction, SQLiteReviewDatabase
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
from appraisal_review.application.source_processing import SourceProcessingScope
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


@dataclass
class _Job:
    record: JobRecord
    updated_at: int
    outbox_seq: int = 0


@dataclass
class _Run:
    job_id: UUID
    run_id: UUID
    revision: RevisionReference
    documents: tuple[DocumentReference, ...]
    payload_digest: str
    fencing_token: int = 0
    result_version: int = 0
    lease_owner: UUID | None = None
    lease_expires_at: int | None = None
    attempt_id: UUID | None = None


@dataclass
class _Outbox:
    record: DispatchRecord
    state: str = "pending"


@dataclass
class _Attempt:
    attempt: ClaimedAttempt
    outcome: str = "running"


def _facts(job: _Job) -> JobFacts:
    record = job.record
    return JobFacts(
        status=record.status,
        attempt_count=record.attempt_count,
        lease_takeover_count=record.lease_takeover_count,
        cancel_requested=record.cancel_requested,
        has_open_tasks=bool(record.open_task_ids),
    )


def _job(tx: ReviewTransaction, job_id: UUID) -> _Job:
    job = tx.get(_Job, "job", str(job_id))
    if job is None:
        raise ConditionFailed("Unknown job")
    return job


def _run(tx: ReviewTransaction, job: _Job) -> _Run:
    run = tx.get(_Run, "run", str(job.record.current_run.run_id))
    if run is None or run.job_id != job.record.job_id:
        raise ConditionFailed("Unknown current run")
    return run


def _save(tx: ReviewTransaction, job: _Job, run: _Run) -> JobRecord:
    job.record = replace(
        job.record,
        current_run=RunReference(
            run_id=run.run_id,
            revision=run.revision,
            attempt_id=run.attempt_id if job.record.status == JobStatus.RUNNING else None,
        ),
        result_version=run.result_version,
    )
    tx.put("run", str(run.run_id), run)
    tx.put("job", str(job.record.job_id), job)
    return job.record


def _enqueue(tx: ReviewTransaction, job: _Job, run: _Run, available_at: int) -> None:
    job.outbox_seq += 1
    entry = DispatchRecord(
        job_id=job.record.job_id,
        run_id=run.run_id,
        outbox_seq=job.outbox_seq,
        dispatch_token=uuid4(),
        available_at=available_at,
    )
    tx.put("outbox", str(entry.job_id), _Outbox(entry), str(entry.outbox_seq), insert=True)


class SQLiteJobStore:
    def __init__(
        self,
        database: SQLiteReviewDatabase,
        *,
        policy: JobPolicy | None = None,
        source_scopes: tuple[SourceProcessingScope, ...] = (),
    ) -> None:
        self.database = database
        # Reconstruct detached trusted configuration; persisted scopes do not become
        # active defaults for new jobs after a restart without source configuration.
        self.source_scopes = tuple(
            SourceProcessingScope(
                scope_id=scope.scope_id,
                version=scope.version,
                documents=scope.documents,
                privacy_handling=scope.privacy_handling,
                basis=scope.basis,
            )
            for scope in source_scopes
        )
        for index, scope in enumerate(self.source_scopes):
            if any(scope.matches(other.documents) for other in self.source_scopes[:index]):
                raise ValueError("Multiple source scopes select the same exact document set")
        with database.transaction(write=True) as tx:
            stored = tx.get(JobPolicy, "configuration", "job_policy")
            if stored is not None and policy is not None and stored != policy:
                raise ServiceFault(ServiceErrorCode.CAPABILITY)
            self.policy = stored or policy or JobPolicy()
            if stored is None:
                tx.put("configuration", "job_policy", self.policy, insert=True)
            for scope in self.source_scopes:
                previous = tx.get(
                    SourceProcessingScope, "source_processing_scope", scope.scope_id, scope.version
                )
                if previous is not None and previous != scope:
                    raise ServiceFault(ServiceErrorCode.CONFLICT)
                if previous is None:
                    tx.put(
                        "source_processing_scope", scope.scope_id, scope, scope.version, insert=True
                    )

    @staticmethod
    def _source_scope(tx: ReviewTransaction, run: _Run) -> SourceProcessingScope | None:
        scope = tx.get(SourceProcessingScope, "run_source_processing", str(run.run_id))
        if scope is not None and (
            not scope.matches(run.documents)
            or tx.get(
                SourceProcessingScope, "source_processing_scope", scope.scope_id, scope.version
            )
            != scope
        ):
            raise ServiceFault(ServiceErrorCode.EXECUTION)
        return scope

    async def read_source_processing(self, *, run_id: UUID) -> SourceProcessingScope | None:
        """Internal read after caller authorization; None is no scoped exemption.

        API composition must first authorize the owning job/run. This record is
        not a privacy attestation, source-content approval or verification result.
        """
        with self.database.transaction() as tx:
            run = tx.get(_Run, "run", str(run_id))
            return None if run is None else self._source_scope(tx, run)

    async def read_job(self, *, job_id: UUID) -> JobRecord | None:
        with self.database.transaction() as tx:
            return self.read_in_transaction(tx, job_id=job_id)

    def read_in_transaction(self, tx: ReviewTransaction, *, job_id: UUID) -> JobRecord | None:
        job = tx.get(_Job, "job", str(job_id))
        return None if job is None else job.record

    def check_documents_in_transaction(
        self, tx: ReviewTransaction, *, job_id: UUID, documents: tuple[DocumentReference, ...]
    ) -> None:
        run = _run(tx, _job(tx, job_id))
        if sorted(d.model_dump_json() for d in run.documents) != sorted(
            d.model_dump_json() for d in documents
        ):
            raise ConditionFailed("Material documents do not match the fixed run sources")

    async def read_result_reference(
        self, *, run_id: UUID, result_version: int
    ) -> ResultReference | None:
        with self.database.transaction() as tx:
            return tx.get(ResultReference, "result", str(run_id), str(result_version))

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
        principal.require(submission.revision.case_id, Permission.REVIEW)
        actor, key = principal.actor.actor_id, submission.idempotency_key
        with self.database.transaction(write=True) as tx:
            stored = tx.get(IdempotencyRecord, "submission", actor, key)
            if stored is not None:
                existing_id = check_idempotency(stored, principal, submission)
                run = tx.get(_Run, "run", str(existing_id))
                if run is None:
                    raise ConditionFailed("Submission references an unknown run")
                return _job(tx, run.job_id).record, False
            digest = submission_digest(submission)
            run = _Run(job_id, run_id, submission.revision, submission.documents, digest)
            job = _Job(
                JobRecord(
                    job_id=job_id,
                    case_id=submission.revision.case_id,
                    principal_id=actor,
                    status=initial_transition().status,
                    current_run=RunReference(run_id=run_id, revision=submission.revision),
                ),
                updated_at=now,
            )
            tx.put("job", str(job_id), job, insert=True)
            tx.put("run", str(run_id), run, insert=True)
            for scope in self.source_scopes:
                if scope.matches(submission.documents):
                    tx.put("run_source_processing", str(run_id), scope, insert=True)
            tx.put(
                "submission", actor, IdempotencyRecord(actor, key, digest, run_id), key, insert=True
            )
            _enqueue(tx, job, run, now)
            return _save(tx, job, run), True

    def _authority(
        self, tx: ReviewTransaction, attempt: ClaimedAttempt, now: int
    ) -> tuple[_Job, _Run]:
        job = _job(tx, attempt.job_id)
        run = _run(tx, job)
        if (
            run.run_id != attempt.run_id
            or run.lease_owner != attempt.owner
            or run.fencing_token != attempt.fencing_token
            or run.attempt_id != attempt.attempt_id
            or job.record.status != JobStatus.RUNNING
            or run.lease_expires_at is None
            or run.lease_expires_at <= now
        ):
            raise ConditionFailed("Attempt no longer holds a current lease")
        return job, run

    async def claim(
        self, *, job_id: UUID, run_id: UUID, owner: UUID, lease_seconds: int, now: int
    ) -> ClaimedAttempt:
        if lease_seconds < 1:
            raise ValueError("A lease must last at least one second")
        with self.database.transaction(write=True) as tx:
            job = _job(tx, job_id)
            run = _run(tx, job)
            if run.run_id != run_id or (
                run.lease_expires_at is not None and run.lease_expires_at > now
            ):
                raise ConditionFailed("Unknown, superseded or leased run")
            try:
                transition = next_state(_facts(job), JobEvent.CLAIM, policy=self.policy)
            except ServiceFault:
                raise ConditionFailed("Run is not claimable") from None
            run.fencing_token += 1
            run.lease_owner, run.lease_expires_at, run.attempt_id = (
                owner,
                now + lease_seconds,
                uuid4(),
            )
            job.record = replace(job.record, status=transition.status)
            job.updated_at = now
            attempt = ClaimedAttempt(
                job_id,
                run_id,
                run.attempt_id,
                owner,
                run.fencing_token,
                run.result_version,
                run.lease_expires_at,
            )
            tx.put("attempt", str(run.attempt_id), _Attempt(attempt), insert=True)
            _save(tx, job, run)
            return attempt

    async def heartbeat(
        self, attempt: ClaimedAttempt, *, lease_seconds: int, now: int
    ) -> HeartbeatState:
        if lease_seconds < 1:
            raise ValueError("A lease must last at least one second")
        with self.database.transaction(write=True) as tx:
            job, run = self._authority(tx, attempt, now)
            next_state(_facts(job), JobEvent.HEARTBEAT, policy=self.policy)
            run.lease_expires_at = now + lease_seconds
            job.updated_at = now
            _save(tx, job, run)
            return HeartbeatState(run.lease_expires_at, job.record.cancel_requested)

    @staticmethod
    def _release(run: _Run) -> None:
        run.lease_owner = run.lease_expires_at = run.attempt_id = None

    @staticmethod
    def _close_attempt(tx: ReviewTransaction, attempt_id: UUID | None, outcome: str) -> None:
        if attempt_id is not None:
            record = tx.get(_Attempt, "attempt", str(attempt_id))
            if record is not None:
                record.outcome = outcome
                tx.put("attempt", str(attempt_id), record)

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
        with self.database.transaction(write=True) as tx:
            return self.finish_in_transaction(
                tx,
                attempt,
                event=event,
                now=now,
                problem=problem,
                open_task_ids=open_task_ids,
                result=result,
                available_at=available_at,
            )

    def finish_in_transaction(
        self,
        tx: ReviewTransaction,
        attempt: ClaimedAttempt,
        *,
        event: JobEvent,
        now: int,
        problem: ServiceProblem | None = None,
        open_task_ids: tuple[UUID, ...] = (),
        result: ResultReference | None = None,
        available_at: int | None = None,
    ) -> JobRecord:
        del available_at
        if event not in {
            JobEvent.PUBLISH_RESULT,
            JobEvent.NEEDS_HUMAN,
            JobEvent.RETRYABLE_ERROR,
            JobEvent.PERMANENT_ERROR,
            JobEvent.CANCEL_ACKNOWLEDGED,
        }:
            raise ValueError("finish requires a lease-bound completion event")
        job, run = self._authority(tx, attempt, now)
        transition = next_state(
            replace(_facts(job), has_open_tasks=bool(open_task_ids)), event, policy=self.policy
        )
        if event == JobEvent.PUBLISH_RESULT:
            if result is None:
                raise ValueError("Publication requires a result reference")
            if (
                result.run_id != run.run_id
                or result.fencing_token != run.fencing_token
                or run.result_version != attempt.expected_result_version
                or result.result_version != attempt.expected_result_version + 1
            ):
                raise ConditionFailed("Stale publication")
            tx.put("result", str(run.run_id), result, str(result.result_version), insert=True)
            run.result_version = result.result_version
        elif result is not None:
            raise ValueError("Only publication commits result references")
        if transition.status == JobStatus.FAILED and problem is None:
            raise ValueError("Failure requires a sanitized problem")
        if transition.status == JobStatus.CANCELLED:
            problem = ServiceProblem(code=ServiceErrorCode.CONFLICT)
        elif transition.status != JobStatus.FAILED:
            problem = None
        job.record = replace(
            job.record,
            status=transition.status,
            problem=problem,
            attempt_count=job.record.attempt_count + transition.attempt_delta,
            open_task_ids=()
            if transition.status == JobStatus.CANCELLED
            else tuple(dict.fromkeys(open_task_ids)),
        )
        job.updated_at = now
        self._close_attempt(tx, run.attempt_id, event.value)
        if transition.release_lease:
            self._release(run)
        return _save(tx, job, run)

    async def expire_lease(self, lease: ExpiredLease, *, now: int) -> JobRecord:
        with self.database.transaction(write=True) as tx:
            job = _job(tx, lease.job_id)
            run = _run(tx, job)
            if (
                run.run_id != lease.run_id
                or run.fencing_token != lease.fencing_token
                or run.lease_expires_at is None
                or run.lease_expires_at > now
                or job.record.status != JobStatus.RUNNING
            ):
                raise ConditionFailed("Lease is not reclaimable")
            transition = next_state(_facts(job), JobEvent.LEASE_EXPIRED, policy=self.policy)
            problem = job.record.problem
            if transition.status in {JobStatus.FAILED, JobStatus.CANCELLED}:
                problem = ServiceProblem(
                    code=ServiceErrorCode.CONFLICT
                    if transition.status == JobStatus.CANCELLED
                    else ServiceErrorCode.EXECUTION
                )
            job.record = replace(
                job.record,
                status=transition.status,
                problem=problem,
                lease_takeover_count=job.record.lease_takeover_count + transition.takeover_delta,
                open_task_ids=()
                if transition.status == JobStatus.CANCELLED
                else job.record.open_task_ids,
            )
            job.updated_at = now
            self._close_attempt(tx, run.attempt_id, JobEvent.LEASE_EXPIRED.value)
            if transition.release_lease:
                self._release(run)
            if transition.enqueue_outbox:
                _enqueue(tx, job, run, now)
            return _save(tx, job, run)

    async def cancel(self, *, job_id: UUID, now: int) -> JobRecord:
        with self.database.transaction(write=True) as tx:
            job = _job(tx, job_id)
            run = _run(tx, job)
            transition = next_state(_facts(job), JobEvent.CANCEL, policy=self.policy)
            if transition.request_cancel:
                job.record = replace(job.record, cancel_requested=True)
            else:
                job.record = replace(
                    job.record,
                    status=transition.status,
                    open_task_ids=(),
                    problem=ServiceProblem(code=ServiceErrorCode.CONFLICT),
                )
                if transition.release_lease:
                    self._release(run)
            job.updated_at = now
            return _save(tx, job, run)

    async def schedule_retry(self, *, job_id: UUID, available_at: int, now: int) -> JobRecord:
        with self.database.transaction(write=True) as tx:
            job = _job(tx, job_id)
            run = _run(tx, job)
            try:
                transition = next_state(_facts(job), JobEvent.SCHEDULE_RETRY, policy=self.policy)
            except ServiceFault:
                raise ConditionFailed("Job is no longer awaiting retry") from None
            job.record = replace(job.record, status=transition.status, problem=None)
            job.updated_at = now
            _enqueue(tx, job, run, available_at)
            return _save(tx, job, run)

    def resume_in_transaction(
        self, tx: ReviewTransaction, *, job_id: UUID, run: RunReference, now: int
    ) -> JobRecord:
        """Enlist a task's new revision/run in the caller's existing SQL transaction."""
        job = _job(tx, job_id)
        previous = _run(tx, job)
        if (
            run.revision == previous.revision
            or run.revision.case_id != job.record.case_id
            or run.attempt_id is not None
        ):
            raise ConditionFailed("Resume requires a new revision/run")
        transition = next_state(_facts(job), JobEvent.HUMAN_RESPONSE_COMMITTED, policy=self.policy)
        new = _Run(job_id, run.run_id, run.revision, previous.documents, previous.payload_digest)
        tx.put("run", str(run.run_id), new, insert=True)
        source_scope = self._source_scope(tx, previous)
        if source_scope is not None:
            tx.put("run_source_processing", str(run.run_id), source_scope, insert=True)
        job.record = replace(job.record, status=transition.status, open_task_ids=())
        job.updated_at = now
        _enqueue(tx, job, new, now)
        return _save(tx, job, new)

    async def resume_after_human(self, *, job_id: UUID, run: RunReference, now: int) -> JobRecord:
        with self.database.transaction(write=True) as tx:
            return self.resume_in_transaction(tx, job_id=job_id, run=run, now=now)

    def reject_in_transaction(
        self, tx: ReviewTransaction, *, job_id: UUID, run_id: UUID, task_id: UUID, now: int
    ) -> JobRecord:
        job = _job(tx, job_id)
        run = _run(tx, job)
        if run.run_id != run_id or task_id not in job.record.open_task_ids:
            raise ConditionFailed("Task no longer belongs to the open run")
        remaining = tuple(item for item in job.record.open_task_ids if item != task_id)
        transition = next_state(
            replace(_facts(job), has_open_tasks=bool(remaining)),
            JobEvent.HUMAN_REJECTED,
            policy=self.policy,
        )
        job.record = replace(
            job.record,
            status=transition.status,
            open_task_ids=remaining,
            problem=ServiceProblem(code=ServiceErrorCode.CONFLICT)
            if transition.status == JobStatus.FAILED
            else None,
        )
        job.updated_at = now
        return _save(tx, job, run)

    async def reject_human_task(
        self, *, job_id: UUID, run_id: UUID, task_id: UUID, now: int
    ) -> JobRecord:
        with self.database.transaction(write=True) as tx:
            return self.reject_in_transaction(
                tx, job_id=job_id, run_id=run_id, task_id=task_id, now=now
            )

    @staticmethod
    def _dispatch(tx: ReviewTransaction, record: DispatchRecord) -> tuple[_Job, _Outbox]:
        job = _job(tx, record.job_id)
        entry = tx.get(_Outbox, "outbox", str(record.job_id), str(record.outbox_seq))
        if (
            entry is None
            or entry.record.run_id != record.run_id
            or entry.record.dispatch_token != record.dispatch_token
            or entry.state != "pending"
        ):
            raise ConditionFailed("Dispatch round is no longer pending")
        return job, entry

    async def mark_dispatched(self, record: DispatchRecord, *, now: int) -> JobRecord:
        with self.database.transaction(write=True) as tx:
            job, entry = self._dispatch(tx, record)
            entry.state = "sent"
            tx.put("outbox", str(record.job_id), entry, str(record.outbox_seq))
            run = _run(tx, job)
            if job.record.status == JobStatus.QUEUED and run.run_id == record.run_id:
                transition = next_state(
                    _facts(job), JobEvent.DISPATCH_SUCCEEDED, policy=self.policy
                )
                job.record = replace(job.record, status=transition.status)
            job.updated_at = now
            return _save(tx, job, run)

    async def reschedule_dispatch(self, record: DispatchRecord, *, available_at: int) -> bool:
        with self.database.transaction(write=True) as tx:
            job, entry = self._dispatch(tx, record)
            pending = job.record.status not in TERMINAL_STATUSES
            if not pending:
                entry.state = "abandoned"
            else:
                next_state(_facts(job), JobEvent.DISPATCH_FAILED, policy=self.policy)
                entry.record = replace(
                    entry.record,
                    available_at=available_at,
                    dispatch_attempts=entry.record.dispatch_attempts + 1,
                )
            tx.put("outbox", str(record.job_id), entry, str(record.outbox_seq))
            return pending

    @staticmethod
    def _limit(limit: int) -> None:
        if limit < 1:
            raise ValueError("A scan needs a positive limit")

    async def pending_dispatches(self, *, now: int, limit: int) -> tuple[DispatchRecord, ...]:
        self._limit(limit)
        with self.database.transaction() as tx:
            due = [
                entry.record
                for entry in tx.rows(_Outbox, "outbox")
                if entry.state == "pending" and entry.record.available_at <= now
            ]
            return tuple(sorted(due, key=lambda e: (e.available_at, e.outbox_seq))[:limit])

    async def stranded_retryables(
        self, *, stranded_before: int, limit: int
    ) -> tuple[JobRecord, ...]:
        self._limit(limit)
        with self.database.transaction() as tx:
            jobs = [
                job
                for job in tx.rows(_Job, "job")
                if job.record.status == JobStatus.RETRYABLE_FAILED
                and job.updated_at <= stranded_before
            ]
            return tuple(job.record for job in sorted(jobs, key=lambda j: j.updated_at)[:limit])

    async def expired_leases(self, *, now: int, limit: int) -> tuple[ExpiredLease, ...]:
        self._limit(limit)
        with self.database.transaction() as tx:
            expired = []
            for job in tx.rows(_Job, "job"):
                run = _run(tx, job)
                if (
                    job.record.status == JobStatus.RUNNING
                    and run.lease_expires_at is not None
                    and run.lease_expires_at <= now
                ):
                    expired.append(
                        ExpiredLease(
                            job.record.job_id, run.run_id, run.fencing_token, run.lease_expires_at
                        )
                    )
            return tuple(sorted(expired, key=lambda e: e.lease_expires_at)[:limit])


class SQLiteResultStore:
    """Immutable bodies in a separate record namespace from control-plane references."""

    def __init__(self, database: SQLiteReviewDatabase) -> None:
        self.database = database

    async def put(self, *, run_id: UUID, result_version: int, result: ServiceResult) -> Digest:
        body = ServiceResult.model_validate_json(result.model_dump_json())
        if body.run.run_id != run_id or body.result_version != result_version:
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        with self.database.transaction(write=True) as tx:
            stored = tx.get(ServiceResult, "result_body", str(run_id), str(result_version))
            if stored is not None:
                if content_digest(stored) != content_digest(body):
                    raise ServiceFault(ServiceErrorCode.CONFLICT)
            else:
                tx.put("result_body", str(run_id), body, str(result_version), insert=True)
            return content_digest(body)

    async def get(self, *, run_id: UUID, result_version: int) -> ServiceResult | None:
        with self.database.transaction() as tx:
            return tx.get(ServiceResult, "result_body", str(run_id), str(result_version))


def _port_conformance(database: SQLiteReviewDatabase) -> tuple[JobStore, ResultStore]:
    return SQLiteJobStore(database), SQLiteResultStore(database)
