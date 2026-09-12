"""Atomic human responses, revisions and job continuations in one SQLite database."""

from __future__ import annotations

from dataclasses import dataclass, replace
from uuid import UUID, uuid4

from appraisal_review.adapters.local.review_database import ReviewTransaction
from appraisal_review.adapters.local.sqlite_job_store import SQLiteJobStore
from appraisal_review.application.job_state import JobEvent
from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.application.service_guards import ServiceFault, response_digest
from appraisal_review.domain.document_models import Digest
from appraisal_review.domain.job_contracts import JobReference, JobStatus
from appraisal_review.domain.service_contracts import (
    AcceptedResponse,
    HumanTask,
    MaterialRevision,
    ResponseAction,
    RevisionReference,
    RunReference,
    ServiceErrorCode,
)
from appraisal_review.domain.task_contracts import ResponseReceipt
from appraisal_review.ports.human_tasks import HumanTaskStore, StoredReceipt, TaskRecord
from appraisal_review.ports.jobs import ClaimedAttempt, ConditionFailed, JobRecord


@dataclass(frozen=True)
class _Task:
    task: HumanTask
    job_id: UUID
    principal_id: str
    case_id: str


@dataclass(frozen=True)
class _Snapshot:
    material_json: str
    revision_json: str

    @classmethod
    def capture(cls, snapshot: RevisionSnapshot) -> _Snapshot:
        revision = snapshot.revision
        checked = RevisionSnapshot.capture(
            snapshot.material,
            revision.reference.revision_id,
            parent=revision.parent,
            changes=revision.changes,
        )
        if checked.revision != revision:
            raise ConditionFailed("Snapshot content does not match its revision")
        return cls(checked.material.model_dump_json(), revision.model_dump_json())

    def snapshot(self) -> RevisionSnapshot:
        result = RevisionSnapshot(self.material_json, self.revision_json)
        self.capture(result)
        return result


@dataclass(frozen=True)
class _Response:
    accepted: AcceptedResponse
    payload_digest: Digest
    receipt: ResponseReceipt
    committed_at: int


def _restated(task: HumanTask, state: str) -> HumanTask:
    return HumanTask.model_validate(
        task.model_dump(mode="json")
        | {
            "state": state,
            "version": task.version + 1,
        }
    )


class SQLiteHumanTaskStore:
    def __init__(self, jobs: SQLiteJobStore) -> None:
        self.jobs = jobs
        self.database = jobs.database

    @staticmethod
    def _head(tx: ReviewTransaction, job_id: UUID) -> RevisionReference:
        head = tx.get(RevisionReference, "head", str(job_id))
        if head is None:
            raise ConditionFailed("Missing task revision head")
        return head

    def _record(self, tx: ReviewTransaction, entry: _Task) -> TaskRecord:
        return TaskRecord(
            entry.task,
            entry.job_id,
            entry.case_id,
            entry.principal_id,
            self._head(tx, entry.job_id),
        )

    @staticmethod
    def _save_snapshot(tx: ReviewTransaction, snapshot: RevisionSnapshot) -> None:
        record = _Snapshot.capture(snapshot)
        reference = snapshot.revision.reference
        stored = tx.get(_Snapshot, "material", reference.case_id, reference.revision_id)
        if stored is not None:
            if stored != record:
                raise ConditionFailed("Revision identity cannot be replaced")
        else:
            tx.put("material", reference.case_id, record, reference.revision_id, insert=True)

    def _register(
        self,
        tx: ReviewTransaction,
        *,
        job: JobRecord,
        snapshot: RevisionSnapshot,
        tasks: tuple[HumanTask, ...],
    ) -> None:
        reference = snapshot.revision.reference
        head = tx.get(RevisionReference, "head", str(job.job_id))
        self.jobs.check_documents_in_transaction(
            tx, job_id=job.job_id, documents=snapshot.revision.documents
        )
        if (
            job.current_run.revision != reference
            or (head is not None and head != reference)
            or reference.case_id != job.case_id
            or len({t.task_id for t in tasks}) != len(tasks)
        ):
            raise ConditionFailed("Task registration requires the unchanged current head")
        if tasks and (
            job.status != JobStatus.WAITING_FOR_HUMAN
            or set(job.open_task_ids) != {task.task_id for task in tasks}
        ):
            raise ConditionFailed("Tasks must match the current waiting job")
        for task in tasks:
            if (
                task.run.revision != reference
                or task.run.run_id != job.current_run.run_id
                or task.state != "open"
            ):
                raise ConditionFailed("Task is not open on the current run")
            entry = _Task(task, job.job_id, job.principal_id, job.case_id)
            stored = tx.get(_Task, "task", str(task.task_id))
            if stored is not None and stored != entry:
                raise ConditionFailed("Task identity cannot be replaced")
            if stored is None:
                tx.put("task", str(task.task_id), entry, insert=True)
        self._save_snapshot(tx, snapshot)
        if head is None:
            tx.put("head", str(job.job_id), reference, insert=True)
            tx.put("revision_chain", str(job.job_id), reference, reference.revision_id, insert=True)

    async def register_snapshot(
        self,
        *,
        job_id: UUID,
        principal_id: str,
        snapshot: RevisionSnapshot,
        tasks: tuple[HumanTask, ...] = (),
    ) -> None:
        """Trusted bootstrap/recovery; workers should use finish_with_tasks instead."""
        with self.database.transaction(write=True) as tx:
            job = self.jobs.read_in_transaction(tx, job_id=job_id)
            if job is None or job.principal_id != principal_id:
                raise ConditionFailed("Job owner does not match registration")
            self._register(tx, job=job, snapshot=snapshot, tasks=tasks)

    async def finish_with_tasks(
        self,
        attempt: ClaimedAttempt,
        *,
        snapshot: RevisionSnapshot,
        tasks: tuple[HumanTask, ...],
        now: int,
    ) -> JobRecord:
        """Atomically persist a worker's waiting state, material and complete task set."""
        if not tasks:
            raise ValueError("Waiting requires at least one task")
        with self.database.transaction(write=True) as tx:
            job = self.jobs.finish_in_transaction(
                tx,
                attempt,
                event=JobEvent.NEEDS_HUMAN,
                open_task_ids=tuple(t.task_id for t in tasks),
                now=now,
            )
            self._register(tx, job=job, snapshot=snapshot, tasks=tasks)
            return job

    async def read_job(self, *, job_id: UUID) -> JobRecord | None:
        return await self.jobs.read_job(job_id=job_id)

    async def read_task(self, *, task_id: UUID) -> TaskRecord | None:
        with self.database.transaction() as tx:
            entry = tx.get(_Task, "task", str(task_id))
            return None if entry is None else self._record(tx, entry)

    async def list_tasks(self, *, job_id: UUID) -> tuple[TaskRecord, ...]:
        with self.database.transaction() as tx:
            return tuple(
                self._record(tx, entry)
                for entry in tx.rows(_Task, "task")
                if entry.job_id == job_id
            )

    async def read_snapshot(self, *, revision: RevisionReference) -> RevisionSnapshot | None:
        with self.database.transaction() as tx:
            record = tx.get(_Snapshot, "material", revision.case_id, revision.revision_id)
            if record is None:
                return None
            snapshot = record.snapshot()
            return snapshot if snapshot.revision.reference == revision else None

    async def list_revisions(self, *, job_id: UUID) -> tuple[MaterialRevision, ...]:
        with self.database.transaction() as tx:
            results = []
            for reference in tx.rows(RevisionReference, "revision_chain", str(job_id)):
                stored = tx.get(_Snapshot, "material", reference.case_id, reference.revision_id)
                if stored is None:
                    raise ServiceFault(ServiceErrorCode.EXECUTION)
                snapshot = stored.snapshot()
                if snapshot.revision.reference != reference:
                    raise ServiceFault(ServiceErrorCode.EXECUTION)
                results.append(snapshot.revision)
            return tuple(results)

    async def read_receipt(self, *, principal_id: str, key: str) -> StoredReceipt | None:
        with self.database.transaction() as tx:
            response = tx.get(_Response, "response", principal_id, key)
            return (
                None
                if response is None
                else StoredReceipt(principal_id, key, response.payload_digest, response.receipt)
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
        accepted = AcceptedResponse.model_validate_json(accepted.model_dump_json())
        command = accepted.command
        if accepted.actor.kind != "human":
            raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
        if response_digest(command) != payload_digest:
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        with self.database.transaction(write=True) as tx:
            entry = tx.get(_Task, "task", str(command.task_id))
            if entry is None or entry.principal_id != accepted.actor.actor_id:
                raise ConditionFailed("Task ownership mismatch")
            authoritative_job = self.jobs.read_in_transaction(tx, job_id=entry.job_id)
            if (
                authoritative_job is None
                or authoritative_job.principal_id != entry.principal_id
                or authoritative_job.case_id != entry.case_id
            ):
                raise ConditionFailed("Job ownership mismatch")
            previous = tx.get(_Response, "response", entry.principal_id, command.idempotency_key)
            if previous is not None:
                if previous.payload_digest != payload_digest:
                    raise ServiceFault(ServiceErrorCode.CONFLICT)
                return previous.receipt
            head = self._head(tx, entry.job_id)
            current = entry.task
            if (
                current != task
                or current.state != "open"
                or current.version != command.expected_version
                or current.run.revision != head
                or command.revision != head
                or command.action not in current.allowed_responses
                or command.side_digest != (current.side.input_digest if current.side else None)
                or command.result_digest != current.result_digest
                or authoritative_job.current_run.run_id != current.run.run_id
                or authoritative_job.current_run.revision != head
                or current.task_id not in authoritative_job.open_task_ids
            ):
                raise ConditionFailed("Task or revision changed before commit")
            if (next_revision is None) != (command.action == ResponseAction.REJECT):
                raise ConditionFailed("Only a rejection omits a revision")
            tx.put(
                "task", str(current.task_id), replace(entry, task=_restated(current, "answered"))
            )
            resumed = None
            superseded: tuple[UUID, ...] = ()
            if next_revision is not None:
                revision = next_revision.revision
                self.jobs.check_documents_in_transaction(
                    tx, job_id=entry.job_id, documents=revision.documents
                )
                if (
                    revision.parent != head
                    or revision.reference.case_id != head.case_id
                    or revision.reference.revision_id == head.revision_id
                ):
                    raise ConditionFailed("New revision does not extend the current head")
                self._save_snapshot(tx, next_revision)
                tx.put(
                    "revision_chain",
                    str(entry.job_id),
                    revision.reference,
                    revision.reference.revision_id,
                    insert=True,
                )
                tx.put("head", str(entry.job_id), revision.reference)
                siblings = [
                    other
                    for other in tx.rows(_Task, "task")
                    if other.job_id == entry.job_id
                    and other.task.state == "open"
                    and other.task.run.revision == head
                ]
                superseded = tuple(other.task.task_id for other in siblings)
                for other in siblings:
                    tx.put(
                        "task",
                        str(other.task.task_id),
                        replace(other, task=_restated(other.task, "superseded")),
                    )
                resumed = RunReference(run_id=uuid4(), revision=revision.reference)
                job = self.jobs.resume_in_transaction(tx, job_id=entry.job_id, run=resumed, now=now)
            else:
                job = self.jobs.reject_in_transaction(
                    tx,
                    job_id=entry.job_id,
                    run_id=current.run.run_id,
                    task_id=current.task_id,
                    now=now,
                )
            receipt = ResponseReceipt(
                task_id=current.task_id,
                consumed_version=command.expected_version,
                action=command.action,
                job=JobReference(case_id=entry.case_id, job_id=entry.job_id),
                job_status=job.status,
                revision=None if next_revision is None else next_revision.revision.reference,
                resumed_run=resumed,
                superseded_task_ids=superseded,
            )
            tx.put(
                "response",
                entry.principal_id,
                _Response(accepted, payload_digest, receipt, now),
                command.idempotency_key,
                insert=True,
            )
            return receipt


def _port_conformance(jobs: SQLiteJobStore) -> HumanTaskStore:
    return SQLiteHumanTaskStore(jobs)
