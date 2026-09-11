"""In-memory reference implementation of the human-task store.

This is not a durable store: state dies with the process and a single asyncio lock stands
in for a cross-process transaction. Its purpose is to make every condition in
ports.human_tasks executable, so a DynamoDB adapter is checked against the same
expectations rather than against prose.

The job plane keeps its own lock, so the resume is not literally in this one. When it
refuses, this adapter rolls the task and revision writes back, which is the in-memory
stand-in for a TransactWriteItems that would simply not have committed. That rollback is
the part a cloud adapter must replace with a real transaction, not reimplement.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from uuid import UUID, uuid4

from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.domain.document_models import Digest
from appraisal_review.domain.job_contracts import JobReference
from appraisal_review.domain.service_contracts import (
    AcceptedResponse,
    HumanTask,
    MaterialRevision,
    RevisionReference,
    RunReference,
    ServiceErrorCode,
)
from appraisal_review.domain.task_contracts import ResponseReceipt
from appraisal_review.ports.human_tasks import StoredReceipt, TaskRecord
from appraisal_review.ports.jobs import ConditionFailed, JobRecord, JobStore


@dataclass(frozen=True)
class _Task:
    task: HumanTask
    job_id: UUID
    case_id: str
    principal_id: str
    seq: int


@dataclass(frozen=True)
class _Job:
    case_id: str
    principal_id: str
    head: RevisionReference


class LocalHumanTaskStore:
    """Reference adapter. Seeded by a trusted caller; it never invents a task itself."""

    def __init__(self, jobs: JobStore) -> None:
        self._lock = asyncio.Lock()
        self._jobs = jobs
        self._tasks: dict[UUID, _Task] = {}
        self._job_meta: dict[UUID, _Job] = {}
        self._chain: dict[UUID, list[str]] = {}
        self._snapshots: dict[tuple[str, str], RevisionSnapshot] = {}
        self._receipts: dict[tuple[str, str], StoredReceipt] = {}
        self._seq = 0

    # -- seeding -------------------------------------------------------------

    def seed(
        self,
        *,
        job_id: UUID,
        principal_id: str,
        snapshot: RevisionSnapshot,
        tasks: tuple[HumanTask, ...],
    ) -> None:
        """Install the state a finished attempt would have persisted alongside its tasks."""
        revision = snapshot.revision
        case_id = revision.reference.case_id
        meta = self._job_meta.get(job_id)
        if meta is not None and (
            meta.head != revision.reference
            or meta.principal_id != principal_id
            or meta.case_id != case_id
        ):
            raise ConditionFailed("Task registration requires the unchanged current head")
        old_snapshot = self._snapshots.get((case_id, revision.reference.revision_id))
        if old_snapshot is not None and old_snapshot.revision != revision:
            raise ConditionFailed("A revision identity cannot be replaced")
        if len({task.task_id for task in tasks}) != len(tasks):
            raise ConditionFailed("Duplicate task registration")
        for task in tasks:
            previous = self._tasks.get(task.task_id)
            if task.run.revision != revision.reference or (
                previous is not None
                and (
                    previous.task != task
                    or previous.job_id != job_id
                    or previous.principal_id != principal_id
                )
            ):
                raise ConditionFailed("Task registration conflicts with stored identity")
        self._job_meta[job_id] = _Job(
            case_id=case_id, principal_id=principal_id, head=revision.reference
        )
        if meta is None:
            self._chain[job_id] = [revision.model_dump_json()]
        self._snapshots[(case_id, revision.reference.revision_id)] = snapshot
        for task in tasks:
            if task.task_id in self._tasks:
                continue
            self._seq += 1
            self._tasks[task.task_id] = _Task(
                task=task,
                job_id=job_id,
                case_id=case_id,
                principal_id=principal_id,
                seq=self._seq,
            )

    # -- reads ---------------------------------------------------------------

    async def read_job(self, *, job_id: UUID) -> JobRecord | None:
        return await self._jobs.read_job(job_id=job_id)

    def _record(self, entry: _Task) -> TaskRecord:
        return TaskRecord(
            task=entry.task,
            job_id=entry.job_id,
            case_id=entry.case_id,
            principal_id=entry.principal_id,
            current_revision=self._job_meta[entry.job_id].head,
        )

    async def read_task(self, *, task_id: UUID) -> TaskRecord | None:
        async with self._lock:
            entry = self._tasks.get(task_id)
            return None if entry is None else self._record(entry)

    async def list_tasks(self, *, job_id: UUID) -> tuple[TaskRecord, ...]:
        async with self._lock:
            entries = sorted(
                (e for e in self._tasks.values() if e.job_id == job_id), key=lambda e: e.seq
            )
            return tuple(self._record(entry) for entry in entries)

    async def list_revisions(self, *, job_id: UUID) -> tuple[MaterialRevision, ...]:
        async with self._lock:
            return tuple(
                MaterialRevision.model_validate_json(raw) for raw in self._chain.get(job_id, [])
            )

    async def read_snapshot(self, *, revision: RevisionReference) -> RevisionSnapshot | None:
        async with self._lock:
            snapshot = self._snapshots.get((revision.case_id, revision.revision_id))
            return snapshot if snapshot and snapshot.revision.reference == revision else None

    async def read_receipt(self, *, principal_id: str, key: str) -> StoredReceipt | None:
        async with self._lock:
            return self._receipts.get((principal_id, key))

    # -- the one write -------------------------------------------------------

    async def commit_response(
        self,
        accepted: AcceptedResponse,
        *,
        task: HumanTask,
        next_revision: RevisionSnapshot | None,
        payload_digest: Digest,
        now: int,
    ) -> ResponseReceipt:
        async with self._lock:
            command = accepted.command
            entry = self._tasks.get(command.task_id)
            if entry is None:
                raise ConditionFailed("No such task")
            meta = self._job_meta[entry.job_id]
            key = (accepted.actor.actor_id, command.idempotency_key)
            stored = self._receipts.get(key)
            if stored is not None:
                # Authoritative key check. The service's earlier read is only a fast path;
                # two concurrent retries can both miss it and arrive here.
                if stored.payload_digest != payload_digest:
                    raise ServiceFault(ServiceErrorCode.CONFLICT)
                return stored.receipt
            # Everything the caller decided on is rechecked against stored state, because a
            # decision made from an earlier read is exactly what a conditional write exists
            # to invalidate.
            if (
                entry.task.state != "open"
                or entry.task.version != command.expected_version
                or entry.task.run.revision != meta.head
                or accepted.actor.actor_id != entry.principal_id
            ):
                raise ConditionFailed("The task moved before this response committed")
            if next_revision is not None and next_revision.revision.parent != meta.head:
                raise ConditionFailed("The revision chain moved before this response committed")

            undo_tasks = dict(self._tasks)
            undo_meta = dict(self._job_meta)
            undo_chain = {jid: list(rows) for jid, rows in self._chain.items()}
            undo_snapshots = dict(self._snapshots)
            try:
                return await self._apply(entry, accepted, next_revision, payload_digest, now, key)
            except BaseException:
                # The job plane refused, so nothing this answer implied may remain visible.
                self._tasks = undo_tasks
                self._job_meta = undo_meta
                self._chain = undo_chain
                self._snapshots = undo_snapshots
                raise

    async def _apply(
        self,
        entry: _Task,
        accepted: AcceptedResponse,
        next_revision: RevisionSnapshot | None,
        payload_digest: Digest,
        now: int,
        key: tuple[str, str],
    ) -> ResponseReceipt:
        command = accepted.command
        old_head = self._job_meta[entry.job_id].head
        # Bumping the version as well as the state means a second response carrying the
        # original version fails on either check alone.
        answered = _restate(entry.task, "answered", entry.task.version + 1)
        self._tasks[entry.task.task_id] = replace(entry, task=answered)

        superseded: tuple[UUID, ...] = ()
        resumed: RunReference | None = None
        if next_revision is not None:
            reference = next_revision.revision.reference
            self._chain[entry.job_id].append(next_revision.revision.model_dump_json())
            self._snapshots[(reference.case_id, reference.revision_id)] = next_revision
            self._job_meta[entry.job_id] = replace(self._job_meta[entry.job_id], head=reference)
            # Every other open question was asked about material that no longer exists.
            # Leaving them open would invite a second reviewer to answer a stale page.
            stale = sorted(
                (
                    other
                    for other in self._tasks.values()
                    if other.job_id == entry.job_id
                    and other.task.state == "open"
                    and other.task.run.revision == old_head
                ),
                key=lambda other: other.seq,
            )
            for other in stale:
                self._tasks[other.task.task_id] = replace(
                    other, task=_restate(other.task, "superseded", other.task.version + 1)
                )
            superseded = tuple(other.task.task_id for other in stale)
            resumed = RunReference(run_id=uuid4(), revision=reference)

        record = (
            await self._jobs.resume_after_human(job_id=entry.job_id, run=resumed, now=now)
            if resumed is not None
            else await self._jobs.reject_human_task(
                job_id=entry.job_id,
                run_id=entry.task.run.run_id,
                task_id=entry.task.task_id,
                now=now,
            )
        )
        if record is None:
            raise ConditionFailed("The job this task belongs to is gone")
        receipt = ResponseReceipt(
            task_id=entry.task.task_id,
            consumed_version=command.expected_version,
            action=command.action,
            job=JobReference(case_id=entry.case_id, job_id=entry.job_id),
            job_status=record.status,
            revision=next_revision.revision.reference if next_revision else None,
            resumed_run=resumed,
            superseded_task_ids=superseded,
        )
        self._receipts[key] = StoredReceipt(
            principal_id=key[0], key=key[1], payload_digest=payload_digest, receipt=receipt
        )
        return receipt


def _restate(task: HumanTask, state: str, version: int) -> HumanTask:
    """Revalidate rather than mutate: HumanTask is frozen and enforces its own invariants."""
    moved = task.model_dump(mode="json") | {"state": state, "version": version}
    return HumanTask.model_validate(moved)
