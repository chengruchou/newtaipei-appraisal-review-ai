"""SQLite support for the configured Controller/model/task composition."""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing
from uuid import UUID

from appraisal_review.adapters.local.human_task_store import LocalHumanTaskStore
from appraisal_review.adapters.local.integrated_service import LocalDirectory
from appraisal_review.adapters.local.job_store import InMemoryJobStore
from appraisal_review.adapters.local.sqlite_review_store import SQLiteReviewStore
from appraisal_review.application.document_transfer import DocumentTransferService
from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.application.runtime_sources import source_fault
from appraisal_review.application.service_guards import Principal, ServiceFault
from appraisal_review.domain.document_transfer import DocumentFault
from appraisal_review.domain.factor_models import AgentReviewRun
from appraisal_review.domain.review_contracts import content_digest
from appraisal_review.domain.service_contracts import (
    DecisionEvent,
    HumanTask,
    RunReference,
    SelectionFailureEvent,
    ServiceErrorCode,
)
from appraisal_review.ports.jobs import ClaimedAttempt, ConditionFailed, JobRecord


class SQLiteExecutionAuthority:
    """Current C2 reads precede transactionally fenced task registration."""

    def __init__(
        self,
        store: SQLiteReviewStore,
        documents: DocumentTransferService,
        directory: LocalDirectory,
    ) -> None:
        self.store, self.documents, self.directory = store, documents, directory
        # Share the publication adapter's durable case fence, including in a
        # composition that has not yet constructed its artifact repository.
        with closing(store._connect()) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS publication_epochs ("
                "case_id TEXT PRIMARY KEY, epoch INTEGER NOT NULL)"
            )

    @staticmethod
    def _binding(record: JobRecord, attempt: ClaimedAttempt, snapshot: RevisionSnapshot) -> None:
        if (
            (attempt.job_id, attempt.run_id, attempt.attempt_id)
            != (record.job_id, record.current_run.run_id, record.current_run.attempt_id)
            or attempt.expected_result_version != record.result_version
            or record.case_id != record.current_run.revision.case_id
            or record.current_run.revision != snapshot.revision.reference
            or content_digest(snapshot.material) != snapshot.revision.reference.material_digest
        ):
            raise ConditionFailed("Claim, job and exact material must belong to the same run")

    async def _check(
        self,
        principal: Principal,
        record: JobRecord,
        attempt: ClaimedAttempt,
        snapshot: RevisionSnapshot,
        jobs: InMemoryJobStore,
        connection: sqlite3.Connection,
    ) -> JobRecord:
        self._binding(record, attempt, snapshot)
        self.store._authority(jobs, attempt, int(self.store.clock()))
        job = await jobs.read_job(job_id=record.job_id)
        if (
            job is None
            or job.cancel_requested
            or job.current_run != record.current_run
            or job.case_id != record.case_id
            or job.principal_id != record.principal_id
            or job.principal_id != principal.actor.actor_id
            or job.result_version != attempt.expected_result_version
            or set(jobs._runs[(record.job_id, attempt.run_id)].documents)
            != set(snapshot.revision.documents)
        ):
            raise ConditionFailed("Current uncancelled run and pinned documents required")
        # Directory lookup is process-local and performs no source or database I/O.
        # Trusted revocation advances the publication epoch before changing external
        # grants; reading it under this write lock orders task commit with revocation.
        if await self.directory.read(record.principal_id, record.case_id) != principal:
            raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
        epoch = connection.execute(
            "SELECT epoch FROM publication_epochs WHERE case_id=?", (record.case_id,)
        ).fetchone()
        if epoch is not None and epoch[0] != 0:
            raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
        return job

    async def require_current(
        self,
        principal: Principal,
        record: JobRecord,
        attempt: ClaimedAttempt,
        snapshot: RevisionSnapshot,
    ) -> None:
        self._binding(record, attempt, snapshot)
        current = await self.directory.read(record.principal_id, record.case_id)
        if current != principal:
            raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
        try:
            for reference in snapshot.revision.documents:
                await asyncio.to_thread(
                    self.documents.read_snapshot, current, record.current_run, reference
                )
        except DocumentFault as error:
            raise source_fault(error) from None

        async def check(j: InMemoryJobStore, t: LocalHumanTaskStore, c: sqlite3.Connection) -> None:
            await self._check(current, record, attempt, snapshot, j, c)

        await asyncio.to_thread(self.store._transaction, check)

    async def register(
        self,
        record: JobRecord,
        attempt: ClaimedAttempt,
        snapshot: RevisionSnapshot,
        tasks: tuple[HumanTask, ...],
    ) -> None:
        principal = await self.directory.read(record.principal_id, record.case_id)
        await self.require_current(principal, record, attempt, snapshot)
        tasks = tuple(HumanTask.model_validate_json(task.model_dump_json()) for task in tasks)

        async def apply(j: InMemoryJobStore, t: LocalHumanTaskStore, c: sqlite3.Connection) -> None:
            job = await self._check(principal, record, attempt, snapshot, j, c)
            if any(task.run != job.current_run for task in tasks):
                raise ConditionFailed("Task registration lost its current attempt")
            t.seed(job_id=job.job_id, principal_id=job.principal_id, snapshot=snapshot, tasks=tasks)

        await asyncio.to_thread(self.store._transaction, apply)


class SQLiteWorkflowReviews:
    """Immutable internal review output; it cannot authorize or publish an artifact."""

    def __init__(self, store: SQLiteReviewStore) -> None:
        self.store = store
        with closing(store._connect()) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS workflow_reviews "
                "(run_id TEXT PRIMARY KEY, run TEXT NOT NULL, review TEXT NOT NULL)"
            )

    async def put(self, run: RunReference, review: AgentReviewRun) -> None:
        run = RunReference.model_validate_json(run.model_dump_json())
        review = AgentReviewRun.model_validate_json(review.model_dump_json())
        if review.case_id != run.revision.case_id or (
            review.case_review is not None
            and review.case_review.identity.case_id != run.revision.case_id
        ):
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        with closing(self.store._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT run,review FROM workflow_reviews WHERE run_id=?", (str(run.run_id),)
                ).fetchone()
                expected = (run.model_dump_json(), review.model_dump_json())
                if row is not None and tuple(row) != expected:
                    raise ServiceFault(ServiceErrorCode.CONFLICT)
                connection.execute(
                    "INSERT OR IGNORE INTO workflow_reviews VALUES(?,?,?)",
                    (str(run.run_id), *expected),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    async def read(self, run: RunReference) -> AgentReviewRun | None:
        try:
            run = RunReference.model_validate_json(run.model_dump_json())
            with closing(self.store._connect()) as connection:
                row = connection.execute(
                    "SELECT run,review FROM workflow_reviews WHERE run_id=?", (str(run.run_id),)
                ).fetchone()
            if row is None:
                return None
            stored_run = RunReference.model_validate_json(row[0])
            review = AgentReviewRun.model_validate_json(row[1])
            if (
                stored_run != run
                or review.case_id != run.revision.case_id
                or (
                    review.case_review is not None
                    and review.case_review.identity.case_id != run.revision.case_id
                )
            ):
                raise ServiceFault(ServiceErrorCode.CONFLICT)
            return review
        except (ValueError, TypeError):
            raise ServiceFault(ServiceErrorCode.CONFLICT) from None


class SQLiteDecisionTrace:
    """Append-only causal trace with exact event replay and unique event identities."""

    def __init__(self, store: SQLiteReviewStore) -> None:
        self.store = store
        with closing(store._connect()) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS workflow_trace "
                "(seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL, "
                "run_id TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL)"
            )

    @staticmethod
    def _run(event: DecisionEvent | SelectionFailureEvent) -> RunReference:
        return event.proposal.run if isinstance(event, DecisionEvent) else event.run

    @classmethod
    def _load(
        cls, connection: sqlite3.Connection, run_id: UUID
    ) -> list[DecisionEvent | SelectionFailureEvent]:
        rows = connection.execute(
            "SELECT event_id,run_id,kind,payload FROM workflow_trace WHERE run_id=? ORDER BY seq",
            (str(run_id),),
        ).fetchall()
        events: list[DecisionEvent | SelectionFailureEvent] = []
        seen: set[UUID] = set()
        bound_run: RunReference | None = None
        try:
            for event_id, stored_run_id, kind, payload in rows:
                event: DecisionEvent | SelectionFailureEvent
                if kind == "decision":
                    event = DecisionEvent.model_validate_json(payload)
                elif kind == "failure":
                    event = SelectionFailureEvent.model_validate_json(payload)
                else:
                    raise ServiceFault(ServiceErrorCode.CONFLICT)
                run = cls._run(event)
                if (
                    str(event.event_id) != event_id
                    or stored_run_id != str(run_id)
                    or run.run_id != run_id
                    or event.event_id in seen
                    or (bound_run is not None and run != bound_run)
                    or not set(event.parent_event_ids) <= seen
                ):
                    raise ServiceFault(ServiceErrorCode.CONFLICT)
                bound_run = run
                seen.add(event.event_id)
                events.append(event)
        except (ValueError, TypeError):
            raise ServiceFault(ServiceErrorCode.CONFLICT) from None
        return events

    def _append(self, event: DecisionEvent | SelectionFailureEvent, kind: str) -> None:
        run = self._run(event)
        payload = event.model_dump_json()
        with closing(self.store._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                events = self._load(connection, run.run_id)
                if events and self._run(events[0]) != run:
                    raise ServiceFault(ServiceErrorCode.CONFLICT)
                row = connection.execute(
                    "SELECT run_id,kind,payload FROM workflow_trace WHERE event_id=?",
                    (str(event.event_id),),
                ).fetchone()
                if row is not None:
                    if tuple(row) != (str(run.run_id), kind, payload):
                        raise ServiceFault(ServiceErrorCode.CONFLICT)
                else:
                    if not set(event.parent_event_ids) <= {e.event_id for e in events}:
                        raise ServiceFault(ServiceErrorCode.CONFLICT)
                    connection.execute(
                        "INSERT INTO workflow_trace(event_id,run_id,kind,payload) VALUES(?,?,?,?)",
                        (str(event.event_id), str(run.run_id), kind, payload),
                    )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    async def append(self, event: DecisionEvent) -> None:
        event = DecisionEvent.model_validate_json(event.model_dump_json())
        await asyncio.to_thread(self._append, event, "decision")

    async def append_failure(self, event: SelectionFailureEvent) -> None:
        event = SelectionFailureEvent.model_validate_json(event.model_dump_json())
        await asyncio.to_thread(self._append, event, "failure")

    def _read(self, run_id: UUID) -> list[DecisionEvent | SelectionFailureEvent]:
        with closing(self.store._connect()) as connection:
            return self._load(connection, run_id)

    async def read(self, run_id: UUID) -> tuple[DecisionEvent, ...]:
        events = await asyncio.to_thread(self._read, run_id)
        return tuple(event for event in events if isinstance(event, DecisionEvent))

    async def read_failures(self, run_id: UUID) -> tuple[SelectionFailureEvent, ...]:
        events = await asyncio.to_thread(self._read, run_id)
        return tuple(event for event in events if isinstance(event, SelectionFailureEvent))
