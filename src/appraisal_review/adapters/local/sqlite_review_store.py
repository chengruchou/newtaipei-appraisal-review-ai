"""Cross-process local review state, atomically persisted as versioned typed JSON.

The reference state machines are transaction-local implementation details. SQLite,
not their asyncio locks, serializes workers. No reference store survives a call.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import stat
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from typing import Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from appraisal_review.adapters.local import human_task_store as task_memory
from appraisal_review.adapters.local import job_store as job_memory
from appraisal_review.application.job_state import JobEvent, JobPolicy
from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.application.service_guards import (
    IdempotencyRecord,
    Principal,
    ServiceFault,
    response_digest,
    submission_digest,
)
from appraisal_review.domain.document_models import Digest
from appraisal_review.domain.factor_models import ReviewMaterial
from appraisal_review.domain.job_contracts import TERMINAL_STATUSES, JobStatus
from appraisal_review.domain.review_contracts import content_digest
from appraisal_review.domain.service_contracts import (
    AcceptedResponse,
    HumanTask,
    MaterialRevision,
    ResponseAction,
    ReviewSubmission,
    RevisionReference,
    RunReference,
    ServiceErrorCode,
    ServiceProblem,
    ServiceResult,
)
from appraisal_review.domain.task_contracts import ResponseReceipt
from appraisal_review.ports.human_tasks import HumanTaskStore, StoredReceipt, TaskRecord
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

T = TypeVar("T")


class SQLiteReviewStoreError(RuntimeError):
    """Private local storage is unavailable, incompatible or corrupt."""


class _Row(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class _RunRow(_Row):
    job_id: UUID
    value: job_memory._Run


class _SnapshotRow(_Row):
    material: ReviewMaterial
    revision: MaterialRevision

    def snapshot(self) -> RevisionSnapshot:
        # Recompute the reference from the actual material, not a trusted JSON label.
        rebuilt = RevisionSnapshot.capture(
            self.material,
            self.revision.reference.revision_id,
            parent=self.revision.parent,
            changes=self.revision.changes,
        )
        if rebuilt.revision != self.revision:
            raise SQLiteReviewStoreError("Stored snapshot does not match its revision")
        return rebuilt


class _State(_Row):
    schema_version: Literal["sqlite-review-v1"] = "sqlite-review-v1"
    policy: JobPolicy
    jobs: dict[UUID, job_memory._Job] = Field(default_factory=dict)
    runs: list[_RunRow] = Field(default_factory=list)
    outbox: list[job_memory._Outbox] = Field(default_factory=list)
    attempts: list[job_memory._Attempt] = Field(default_factory=list)
    idempotency: dict[str, IdempotencyRecord] = Field(default_factory=dict)
    results: list[ResultReference] = Field(default_factory=list)
    tasks: list[task_memory._Task] = Field(default_factory=list)
    task_jobs: dict[UUID, task_memory._Job] = Field(default_factory=dict)
    chain: dict[UUID, list[MaterialRevision]] = Field(default_factory=dict)
    snapshots: list[_SnapshotRow] = Field(default_factory=list)
    receipts: list[StoredReceipt] = Field(default_factory=list)
    task_seq: int = Field(default=0, ge=0)

    def stores(self) -> tuple[job_memory.InMemoryJobStore, task_memory.LocalHumanTaskStore]:
        job_store = job_memory.InMemoryJobStore(policy=self.policy)
        job_store._jobs = self.jobs
        job_store._runs = {(row.job_id, row.value.run_id): row.value for row in self.runs}
        job_store._outbox = {(row.job_id, row.outbox_seq): row for row in self.outbox}
        job_store._attempts = self.attempts
        job_store._idempotency = self.idempotency
        job_store._results = {(row.run_id, row.result_version): row for row in self.results}
        task_store = task_memory.LocalHumanTaskStore(job_store)
        task_store._tasks = {row.task.task_id: row for row in self.tasks}
        task_store._job_meta = self.task_jobs
        task_store._chain = {
            job_id: [row.model_dump_json() for row in chain] for job_id, chain in self.chain.items()
        }
        task_store._snapshots = {
            (row.revision.reference.case_id, row.revision.reference.revision_id): row.snapshot()
            for row in self.snapshots
        }
        task_store._receipts = {(row.principal_id, row.key): row for row in self.receipts}
        task_store._seq = self.task_seq
        if (
            len(job_store._runs) != len(self.runs)
            or len(job_store._outbox) != len(self.outbox)
            or len(task_store._tasks) != len(self.tasks)
            or len(task_store._snapshots) != len(self.snapshots)
            or len(task_store._receipts) != len(self.receipts)
        ):
            raise SQLiteReviewStoreError("Duplicate local state identities")
        return job_store, task_store

    @classmethod
    def capture(cls, j: job_memory.InMemoryJobStore, t: task_memory.LocalHumanTaskStore) -> _State:
        return cls(
            policy=j.policy,
            jobs=j._jobs,
            runs=[_RunRow(job_id=job_id, value=run) for (job_id, _), run in j._runs.items()],
            outbox=list(j._outbox.values()),
            attempts=j._attempts,
            idempotency=j._idempotency,
            results=list(j._results.values()),
            tasks=list(t._tasks.values()),
            task_jobs=t._job_meta,
            chain={
                job_id: [MaterialRevision.model_validate_json(raw) for raw in chain]
                for job_id, chain in t._chain.items()
            },
            snapshots=[
                _SnapshotRow(material=snapshot.material, revision=snapshot.revision)
                for snapshot in t._snapshots.values()
            ],
            receipts=list(t._receipts.values()),
            task_seq=t._seq,
        )


class SQLiteReviewStore:
    """Combined JobStore and HumanTaskStore for private local SQLite deployments.

    ``path`` must name a regular, singly linked file in a directory owned by this
    user with mode 0700. Missing immediate parent/file are created as 0700/0600.
    Existing permissions are never silently changed. Use one database per isolated
    local service; the JSON snapshot is deliberately a bounded-use local adapter,
    not a distributed database or AWS acceptance substitute.
    """

    def __init__(
        self,
        path: Path,
        *,
        policy: JobPolicy | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = path.absolute()
        self.policy = policy or JobPolicy()
        self.clock = clock
        self.path.parent.mkdir(mode=0o700, parents=False, exist_ok=True)
        self._check_directory()
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
        self._identity = self._check_file()
        connection = self._connect()
        try:
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS review_state "
                    "(singleton INTEGER PRIMARY KEY CHECK(singleton = 1), payload TEXT NOT NULL)"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS review_results (run_id TEXT NOT NULL, "
                    "version INTEGER NOT NULL, attempt_id TEXT NOT NULL, digest TEXT NOT NULL, "
                    "payload TEXT NOT NULL, PRIMARY KEY(run_id, version, attempt_id))"
                )
                row = connection.execute(
                    "SELECT payload FROM review_state WHERE singleton=1"
                ).fetchone()
                if row is None:
                    self._save(connection, _State(policy=self.policy))
                else:
                    self._decode(row[0])
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        finally:
            connection.close()
        self.results = SQLiteResultStore(self)

    def _check_directory(self) -> None:
        info = self.path.parent.lstat()
        if (
            self.path.parent.resolve() != self.path.parent
            or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise SQLiteReviewStoreError("SQLite review directory must be private and unaliased")

    def _check_file(self) -> tuple[int, int]:
        info = self.path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise SQLiteReviewStoreError("SQLite review file must be private and unaliased")
        return info.st_dev, info.st_ino

    def _connect(self) -> sqlite3.Connection:
        self._check_directory()
        if self._check_file() != self._identity:
            raise SQLiteReviewStoreError("SQLite review file identity changed")
        connection = sqlite3.connect(self.path, timeout=15.0, isolation_level=None)
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA trusted_schema=OFF")
        return connection

    def _decode(self, payload: str) -> _State:
        try:
            state = _State.model_validate_json(payload)
        except ValueError:
            raise SQLiteReviewStoreError("Invalid local review state") from None
        if state.policy != self.policy:
            raise SQLiteReviewStoreError("SQLite review policy differs from the stored policy")
        return state

    @staticmethod
    def _save(connection: sqlite3.Connection, state: _State) -> None:
        payload = state.model_dump_json()
        # Serialization validates a detached typed round trip before any commit.
        _State.model_validate_json(payload)
        connection.execute(
            "INSERT INTO review_state(singleton,payload) VALUES(1,?) "
            "ON CONFLICT(singleton) DO UPDATE SET payload=excluded.payload",
            (payload,),
        )

    def _transaction(
        self,
        operation: Callable[
            [job_memory.InMemoryJobStore, task_memory.LocalHumanTaskStore, sqlite3.Connection],
            Awaitable[T],
        ],
    ) -> T:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM review_state WHERE singleton=1"
            ).fetchone()
            if row is None:
                raise SQLiteReviewStoreError("Missing local review state")
            j, t = self._decode(row[0]).stores()

            async def run_operation() -> T:
                return await operation(j, t, connection)

            value = asyncio.run(run_operation())
            self._close_terminal_tasks(j, t)
            self._save(connection, _State.capture(j, t))
            connection.commit()
            return value
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    async def _execute(
        self,
        operation: Callable[
            [job_memory.InMemoryJobStore, task_memory.LocalHumanTaskStore], Awaitable[T]
        ],
    ) -> T:
        # Only fresh in-memory state machines run in this thread, never network I/O.
        # Caller cancellation can leave the outcome unknown; the transaction still
        # commits all effects or none, and the same idempotency key recovers it.
        return await asyncio.to_thread(self._transaction, lambda j, t, c: operation(j, t))

    @staticmethod
    def _close_terminal_tasks(
        j: job_memory.InMemoryJobStore, t: task_memory.LocalHumanTaskStore
    ) -> None:
        for task_id, entry in tuple(t._tasks.items()):
            if j._jobs[entry.job_id].status in TERMINAL_STATUSES and entry.task.state == "open":
                t._tasks[task_id] = replace(
                    entry,
                    task=HumanTask.model_validate(
                        entry.task.model_dump(mode="json")
                        | {"state": "superseded", "version": entry.task.version + 1}
                    ),
                )

    @staticmethod
    def _authority(j: job_memory.InMemoryJobStore, attempt: ClaimedAttempt, now: int) -> None:
        job, run = j._authority(attempt)
        if (
            job.current_run_id != attempt.run_id
            or run.lease_expires_at is None
            or run.lease_expires_at <= now
            or run.result_version != attempt.expected_result_version
        ):
            raise ConditionFailed("Attempt is expired, superseded or has a stale result version")

    async def register_tasks(
        self,
        *,
        job_id: UUID,
        principal_id: str,
        snapshot: RevisionSnapshot,
        tasks: tuple[HumanTask, ...],
    ) -> None:
        async def apply(j: job_memory.InMemoryJobStore, t: task_memory.LocalHumanTaskStore) -> None:
            checked = _SnapshotRow(
                material=snapshot.material, revision=snapshot.revision
            ).snapshot()
            job = await j.read_job(job_id=job_id)
            if (
                job is None
                or job.principal_id != principal_id
                or job.current_run.revision != checked.revision.reference
                or job.status in TERMINAL_STATUSES
                or any(
                    task.run.run_id != job.current_run.run_id
                    or task.run.revision != job.current_run.revision
                    or task.state != "open"
                    for task in tasks
                )
            ):
                raise ConditionFailed(
                    "Task registration requires the owned current run and revision"
                )
            if tasks and (
                job.status not in {JobStatus.WAITING_FOR_HUMAN, JobStatus.RUNNING, JobStatus.QUEUED}
                or (
                    job.status == JobStatus.WAITING_FOR_HUMAN
                    and {task.task_id for task in tasks} != set(job.open_task_ids)
                )
            ):
                raise ConditionFailed("Register exactly the waiting run's open tasks")
            t.seed(job_id=job_id, principal_id=principal_id, snapshot=checked, tasks=tasks)

        await self._execute(apply)

    async def seed(
        self,
        *,
        job_id: UUID,
        principal_id: str,
        snapshot: RevisionSnapshot,
        tasks: tuple[HumanTask, ...],
    ) -> None:
        """Async trusted registration alias; unlike the reference store it is durable."""
        await self.register_tasks(
            job_id=job_id, principal_id=principal_id, snapshot=snapshot, tasks=tasks
        )

    async def commit_response(
        self,
        accepted: AcceptedResponse,
        *,
        task: HumanTask,
        next_revision: RevisionSnapshot | None,
        payload_digest: Digest,
        now: int,
    ) -> ResponseReceipt:
        async def apply(
            j: job_memory.InMemoryJobStore, t: task_memory.LocalHumanTaskStore
        ) -> ResponseReceipt:
            if payload_digest != response_digest(accepted.command):
                raise ConditionFailed("Response digest does not match the command")
            old_receipt = await t.read_receipt(
                principal_id=accepted.actor.actor_id, key=accepted.command.idempotency_key
            )
            if old_receipt is not None:
                if old_receipt.payload_digest != payload_digest:
                    raise ServiceFault(ServiceErrorCode.CONFLICT)
                return old_receipt.receipt
            stored = await t.read_task(task_id=accepted.command.task_id)
            job = None if stored is None else await j.read_job(job_id=stored.job_id)
            if (
                stored is None
                or job is None
                or stored.task != task
                or job.principal_id != accepted.actor.actor_id
                or stored.principal_id != job.principal_id
                or job.status != JobStatus.WAITING_FOR_HUMAN
                or job.cancel_requested
                or task.task_id not in job.open_task_ids
                or task.run.run_id != job.current_run.run_id
                or accepted.command.revision != job.current_run.revision
                or accepted.command.revision != stored.current_revision
                or accepted.command.action not in task.allowed_responses
                or (
                    task.side is not None and accepted.command.side_digest != task.side.input_digest
                )
            ):
                raise ConditionFailed("Response requires the owned current waiting job and task")
            if (next_revision is None) != (accepted.command.action == ResponseAction.REJECT):
                raise ConditionFailed("Only rejection may omit the next revision")
            checked = None
            if next_revision is not None:
                checked = _SnapshotRow(
                    material=next_revision.material, revision=next_revision.revision
                ).snapshot()
                ref = checked.revision.reference
                if (
                    ref.case_id != job.case_id
                    or ref.revision_id == stored.current_revision.revision_id
                    or (ref.case_id, ref.revision_id) in t._snapshots
                ):
                    raise ConditionFailed("A response requires a fresh same-case revision")
            receipt = await t.commit_response(
                accepted, task=task, next_revision=checked, payload_digest=payload_digest, now=now
            )
            if receipt.resumed_run is not None and checked is not None:
                # The reference adapter inherits documents/digest from the old run.
                # Bind execution metadata to the new committed revision instead.
                run = j._runs[(job.job_id, receipt.resumed_run.run_id)]
                run.documents = checked.revision.documents
                run.payload_digest = submission_digest(
                    ReviewSubmission(
                        revision=run.revision,
                        documents=run.documents,
                        idempotency_key=f"run-{run.run_id}",
                    )
                )
            return receipt

        return await self._execute(apply)

    async def read_submission(self, *, run_id: UUID) -> ReviewSubmission | None:
        async def read(
            j: job_memory.InMemoryJobStore, t: task_memory.LocalHumanTaskStore
        ) -> ReviewSubmission | None:
            matches = [run for (_, identity), run in j._runs.items() if identity == run_id]
            if not matches:
                return None
            if len(matches) != 1:
                raise SQLiteReviewStoreError("Run identity has multiple owners")
            run = matches[0]
            submission = ReviewSubmission(
                revision=run.revision, documents=run.documents, idempotency_key=f"run-{run_id}"
            )
            if submission_digest(submission) != run.payload_digest:
                raise SQLiteReviewStoreError("Stored run submission digest does not match")
            return submission

        return await self._execute(read)

    async def create_job(
        self,
        principal: Principal,
        submission: ReviewSubmission,
        *,
        job_id: UUID,
        run_id: UUID,
        now: int,
    ) -> tuple[JobRecord, bool]:
        async def apply(
            j: job_memory.InMemoryJobStore, t: task_memory.LocalHumanTaskStore
        ) -> tuple[JobRecord, bool]:
            # A run ID is global to Runtime's read_submission API.
            if any(identity == run_id and owner != job_id for owner, identity in j._runs):
                raise ConditionFailed("Run identity already belongs to another job")
            return await j.create_job(principal, submission, job_id=job_id, run_id=run_id, now=now)

        return await self._execute(apply)

    async def read_job(self, *, job_id: UUID) -> JobRecord | None:
        return await self._execute(lambda j, t: j.read_job(job_id=job_id))

    async def read_result_reference(
        self, *, run_id: UUID, result_version: int
    ) -> ResultReference | None:
        return await self._execute(
            lambda j, t: j.read_result_reference(run_id=run_id, result_version=result_version)
        )

    async def claim(
        self, *, job_id: UUID, run_id: UUID, owner: UUID, lease_seconds: int, now: int
    ) -> ClaimedAttempt:
        return await self._execute(
            lambda j, t: j.claim(
                job_id=job_id, run_id=run_id, owner=owner, lease_seconds=lease_seconds, now=now
            )
        )

    async def heartbeat(
        self, attempt: ClaimedAttempt, *, lease_seconds: int, now: int
    ) -> HeartbeatState:
        async def apply(
            j: job_memory.InMemoryJobStore, t: task_memory.LocalHumanTaskStore
        ) -> HeartbeatState:
            self._authority(j, attempt, now)
            return await j.heartbeat(attempt, lease_seconds=lease_seconds, now=now)

        return await self._execute(apply)

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
        async def apply(
            j: job_memory.InMemoryJobStore,
            t: task_memory.LocalHumanTaskStore,
            connection: sqlite3.Connection,
        ) -> JobRecord:
            self._authority(j, attempt, now)
            if event == JobEvent.PUBLISH_RESULT and j._jobs[attempt.job_id].cancel_requested:
                raise ConditionFailed("Cancelled attempts cannot publish results")
            if result is not None:
                candidates = connection.execute(
                    "SELECT attempt_id, digest FROM review_results WHERE run_id=? AND version=?",
                    (str(result.run_id), result.result_version),
                ).fetchall()
                if candidates and (str(attempt.attempt_id), result.result_digest) not in candidates:
                    raise ConditionFailed("Result digest does not belong to the publishing attempt")
            return await j.finish(
                attempt,
                event=event,
                now=now,
                problem=problem,
                open_task_ids=open_task_ids,
                result=result,
                available_at=available_at,
            )

        return await asyncio.to_thread(self._transaction, apply)

    async def expire_lease(self, lease: ExpiredLease, *, now: int) -> JobRecord:
        return await self._execute(lambda j, t: j.expire_lease(lease, now=now))

    async def cancel(self, *, job_id: UUID, now: int) -> JobRecord:
        return await self._execute(lambda j, t: j.cancel(job_id=job_id, now=now))

    async def schedule_retry(self, *, job_id: UUID, available_at: int, now: int) -> JobRecord:
        return await self._execute(
            lambda j, t: j.schedule_retry(job_id=job_id, available_at=available_at, now=now)
        )

    async def resume_after_human(self, *, job_id: UUID, run: RunReference, now: int) -> JobRecord:
        async def apply(
            j: job_memory.InMemoryJobStore, t: task_memory.LocalHumanTaskStore
        ) -> JobRecord:
            if any(identity == run.run_id for _, identity in j._runs):
                raise ConditionFailed("Run identity already exists")
            result = await j.resume_after_human(job_id=job_id, run=run, now=now)
            stored = j._runs[(job_id, run.run_id)]
            snapshot = await t.read_snapshot(revision=run.revision)
            if snapshot is not None:
                stored.documents = snapshot.revision.documents
            stored.payload_digest = submission_digest(
                ReviewSubmission(
                    revision=stored.revision,
                    documents=stored.documents,
                    idempotency_key=f"run-{run.run_id}",
                )
            )
            return result

        return await self._execute(apply)

    async def reject_human_task(
        self, *, job_id: UUID, run_id: UUID, task_id: UUID, now: int
    ) -> JobRecord:
        return await self._execute(
            lambda j, t: j.reject_human_task(job_id=job_id, run_id=run_id, task_id=task_id, now=now)
        )

    async def mark_dispatched(self, record: DispatchRecord, *, now: int) -> JobRecord:
        return await self._execute(lambda j, t: j.mark_dispatched(record, now=now))

    async def reschedule_dispatch(self, record: DispatchRecord, *, available_at: int) -> bool:
        return await self._execute(
            lambda j, t: j.reschedule_dispatch(record, available_at=available_at)
        )

    async def pending_dispatches(self, *, now: int, limit: int) -> tuple[DispatchRecord, ...]:
        return await self._execute(lambda j, t: j.pending_dispatches(now=now, limit=limit))

    async def expired_leases(self, *, now: int, limit: int) -> tuple[ExpiredLease, ...]:
        return await self._execute(lambda j, t: j.expired_leases(now=now, limit=limit))

    async def stranded_retryables(
        self, *, stranded_before: int, limit: int
    ) -> tuple[JobRecord, ...]:
        return await self._execute(
            lambda j, t: j.stranded_retryables(stranded_before=stranded_before, limit=limit)
        )

    async def read_task(self, *, task_id: UUID) -> TaskRecord | None:
        return await self._execute(lambda j, t: t.read_task(task_id=task_id))

    async def list_tasks(self, *, job_id: UUID) -> tuple[TaskRecord, ...]:
        return await self._execute(lambda j, t: t.list_tasks(job_id=job_id))

    async def list_revisions(self, *, job_id: UUID) -> tuple[MaterialRevision, ...]:
        return await self._execute(lambda j, t: t.list_revisions(job_id=job_id))

    async def read_snapshot(self, *, revision: RevisionReference) -> RevisionSnapshot | None:
        return await self._execute(lambda j, t: t.read_snapshot(revision=revision))

    async def read_receipt(self, *, principal_id: str, key: str) -> StoredReceipt | None:
        return await self._execute(lambda j, t: t.read_receipt(principal_id=principal_id, key=key))


def _port_conformance(store: SQLiteReviewStore) -> tuple[JobStore, HumanTaskStore]:
    return store, store


class SQLiteResultStore:
    """Typed result candidates in the same private SQLite database.

    A candidate is immutable for its run/version/attempt. Another fenced attempt
    can stage a different digest without poisoning the eventual committed result.
    Readers select only the authoritative reference; orphan candidates are hidden.
    """

    def __init__(self, store: SQLiteReviewStore) -> None:
        self.store = store

    async def put(self, *, run_id: UUID, result_version: int, result: ServiceResult) -> Digest:
        body = ServiceResult.model_validate_json(result.model_dump_json())
        if (
            body.run.run_id != run_id
            or body.result_version != result_version
            or body.run.attempt_id is None
            or result_version < 1
        ):
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        digest = content_digest(body)

        async def apply(
            j: job_memory.InMemoryJobStore,
            t: task_memory.LocalHumanTaskStore,
            connection: sqlite3.Connection,
        ) -> Digest:
            key = (str(run_id), result_version, str(body.run.attempt_id))
            existing = connection.execute(
                "SELECT digest, payload FROM review_results "
                "WHERE run_id=? AND version=? AND attempt_id=?",
                key,
            ).fetchone()
            if existing is not None:
                restored = ServiceResult.model_validate_json(existing[1])
                if existing[0] != digest or content_digest(restored) != digest or restored != body:
                    raise ServiceFault(ServiceErrorCode.CONFLICT)
                return digest
            matches = [
                (owner, run) for (owner, identity), run in j._runs.items() if identity == run_id
            ]
            if len(matches) != 1:
                raise ConditionFailed("Unknown result run")
            job_id, run = matches[0]
            job = j._jobs[job_id]
            if (
                job.current_run_id != run_id
                or job.status != JobStatus.RUNNING
                or job.cancel_requested
                or run.attempt_id != body.run.attempt_id
                or run.lease_expires_at is None
                or run.lease_expires_at <= self.store.clock()
                or run.revision != body.run.revision
                or run.result_version + 1 != result_version
            ):
                raise ConditionFailed("Only the current attempt may stage result bytes")
            connection.execute(
                "INSERT INTO review_results(run_id,version,attempt_id,digest,payload) "
                "VALUES(?,?,?,?,?)",
                (*key, digest, body.model_dump_json()),
            )
            return digest

        return await asyncio.to_thread(self.store._transaction, apply)

    @staticmethod
    def _get(
        j: job_memory.InMemoryJobStore,
        connection: sqlite3.Connection,
        *,
        run_id: UUID,
        result_version: int,
        expected: ResultReference | None = None,
    ) -> ServiceResult | None:
        reference = j._results.get((run_id, result_version))
        if reference is None:
            if expected is not None:
                raise ConditionFailed("Result reference has not been committed")
            return None
        if expected is not None and expected != reference:
            raise ConditionFailed("Result reference differs from committed state")
        rows = connection.execute(
            "SELECT payload FROM review_results WHERE run_id=? AND version=? AND digest=?",
            (str(run_id), result_version, reference.result_digest),
        ).fetchall()
        if len(rows) != 1:
            raise SQLiteReviewStoreError("Committed result body is missing or ambiguous")
        body = ServiceResult.model_validate_json(rows[0][0])
        if (
            body.run.run_id != run_id
            or body.result_version != result_version
            or content_digest(body) != reference.result_digest
        ):
            raise SQLiteReviewStoreError("Committed result body digest is invalid")
        return body

    async def get(self, *, run_id: UUID, result_version: int) -> ServiceResult | None:
        async def read(
            j: job_memory.InMemoryJobStore,
            t: task_memory.LocalHumanTaskStore,
            connection: sqlite3.Connection,
        ) -> ServiceResult | None:
            return self._get(j, connection, run_id=run_id, result_version=result_version)

        return await asyncio.to_thread(self.store._transaction, read)

    async def get_committed(self, reference: ResultReference) -> ServiceResult:
        async def read(
            j: job_memory.InMemoryJobStore,
            t: task_memory.LocalHumanTaskStore,
            connection: sqlite3.Connection,
        ) -> ServiceResult:
            body = self._get(
                j,
                connection,
                run_id=reference.run_id,
                result_version=reference.result_version,
                expected=reference,
            )
            if body is None:
                raise SQLiteReviewStoreError("Missing committed result")
            return body

        return await asyncio.to_thread(self.store._transaction, read)


def _result_port_conformance(store: SQLiteReviewStore) -> ResultStore:
    return store.results
