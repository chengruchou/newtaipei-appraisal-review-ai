"""Durable fact adoptions in the SAME private review SQLite as the jobs they advance.

One additive table, ``fact_adoptions``, maps each adoption command's
(job, actor, idempotency key) to the exact AdoptionResult it produced, so a
network retry replays its original outcome instead of adopting twice.

The revision advance deliberately reuses the review store's own transaction
machinery rather than inventing a parallel case store: ``commit`` runs inside
``SQLiteReviewStore._transaction`` (BEGIN IMMEDIATE over the ``review_state``
singleton), which decodes, mutates and re-validates the very same job/run
shapes ``resume_after_human`` and ``commit_response`` persist. Within that one
transaction it compare-and-sets the job's current revision, appends the new run
(documents carried forward, payload digest recomputed exactly as the review
store does on resume), flips the candidates ``confirmed`` -> ``adopted`` via
the candidate store's own conditional UPDATE, and inserts the adoption record.
Any failed condition raises CONFLICT and the transaction rolls back with
nothing persisted.

The snapshot registration is the one write outside SQLite (RegisteredSnapshots
is file-backed). It happens last inside the transaction body, after every
condition has passed: if the registration itself fails the transaction rolls
back; if the final commit fails after it, the orphaned file names a revision no
job ever references, which nothing can read and re-running the command replaces.
"""

from __future__ import annotations

import asyncio
import sqlite3
from uuid import UUID

from appraisal_review.adapters.local import human_task_store as task_memory
from appraisal_review.adapters.local import job_store as job_memory
from appraisal_review.adapters.local.candidate_store import SQLiteCandidateStore
from appraisal_review.adapters.local.snapshot_registry import SnapshotRegistryError
from appraisal_review.adapters.local.sqlite_publication import _transaction
from appraisal_review.adapters.local.sqlite_review_store import SQLiteReviewStore
from appraisal_review.application.fact_adoption import AdoptionResult, AdoptionSnapshots
from appraisal_review.application.fact_candidates import CandidateConfirmation, FactCandidate
from appraisal_review.application.service_guards import ServiceFault, submission_digest
from appraisal_review.domain.calculation_snapshot import CalculationSnapshot
from appraisal_review.domain.job_contracts import TERMINAL_STATUSES, JobStatus
from appraisal_review.domain.service_contracts import ReviewSubmission, ServiceErrorCode


class SQLiteAdoptionStore:
    """Adoption records plus the CAS revision advance, all in one transaction."""

    def __init__(self, review_store: SQLiteReviewStore, *, snapshots: AdoptionSnapshots) -> None:
        self.store = review_store
        self.snapshots = snapshots
        with _transaction(self.store) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS fact_adoptions ("
                "adoption_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, case_id TEXT NOT NULL, "
                "actor_id TEXT NOT NULL, idempotency_key TEXT NOT NULL, "
                "payload_digest TEXT NOT NULL, record TEXT NOT NULL, "
                "UNIQUE(job_id, actor_id, idempotency_key))"
            )

    def replay(
        self, *, job_id: UUID, actor_id: str, idempotency_key: str, payload_digest: str
    ) -> AdoptionResult | None:
        connection = self.store._connect()
        try:
            row = connection.execute(
                "SELECT payload_digest, record FROM fact_adoptions "
                "WHERE job_id=? AND actor_id=? AND idempotency_key=?",
                (str(job_id), actor_id, idempotency_key),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        if row[0] != payload_digest:
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        return AdoptionResult.model_validate_json(row[1])

    def accepted_receipt(self, case_id: str, candidate_id: str) -> CandidateConfirmation | None:
        """The accept receipt backing one confirmed candidate, if any.

        Receipts live in the candidate store's ``fact_candidate_receipts`` table in
        this same shared database. A composition that never built the candidate
        plane has no receipts table and therefore no receipts - not an error.
        """
        connection = self.store._connect()
        try:
            try:
                rows = connection.execute(
                    "SELECT record FROM fact_candidate_receipts "
                    "WHERE candidate_id=? AND case_id=? ORDER BY rowid",
                    (candidate_id, case_id),
                ).fetchall()
            except sqlite3.OperationalError:
                return None
        finally:
            connection.close()
        accepted = [
            receipt
            for receipt in (CandidateConfirmation.model_validate_json(row[0]) for row in rows)
            if receipt.decision == "accept"
        ]
        return accepted[-1] if accepted else None

    async def commit(
        self,
        result: AdoptionResult,
        adopted: tuple[FactCandidate, ...],
        *,
        snapshot: CalculationSnapshot,
        new_run_id: UUID,
        payload_digest: str,
    ) -> tuple[AdoptionResult, bool]:
        record = result.record
        rendered = result.model_dump_json()
        # Detach the snapshot now; the transaction body must not share caller state.
        registered = CalculationSnapshot.model_validate_json(snapshot.model_dump_json())
        if registered.revision != result.new_revision or any(
            candidate.status != "adopted" for candidate in adopted
        ):
            raise ServiceFault(ServiceErrorCode.CONFLICT)

        async def apply(
            j: job_memory.InMemoryJobStore,
            t: task_memory.LocalHumanTaskStore,
            connection: sqlite3.Connection,
        ) -> tuple[AdoptionResult, bool]:
            del t  # The task plane is untouched: adoption requires no open task.
            row = connection.execute(
                "SELECT payload_digest, record FROM fact_adoptions "
                "WHERE job_id=? AND actor_id=? AND idempotency_key=?",
                (str(record.job_id), record.actor.actor_id, record.idempotency_key),
            ).fetchone()
            if row is not None:
                if row[0] != payload_digest:
                    raise ServiceFault(ServiceErrorCode.CONFLICT)
                return AdoptionResult.model_validate_json(row[1]), False
            job = j._jobs.get(record.job_id)
            if job is None:
                raise ServiceFault(ServiceErrorCode.CONFLICT)
            current = j._runs[(record.job_id, job.current_run_id)]
            if (
                # The CAS: the revision the caller pinned must still be current.
                current.revision.revision_id != record.from_revision
                or job.case_id != record.case_id
                or job.status in TERMINAL_STATUSES
                or job.status == JobStatus.RUNNING
                or job.cancel_requested
                or bool(job.open_task_ids)
                or (record.job_id, new_run_id) in j._runs
                or any(identity == new_run_id for _, identity in j._runs)
            ):
                raise ServiceFault(ServiceErrorCode.CONFLICT)
            for candidate in adopted:
                if not SQLiteCandidateStore.mark_adopted(connection, candidate):
                    # The candidate left "confirmed" since the service validated it.
                    raise ServiceFault(ServiceErrorCode.CONFLICT)
            connection.execute(
                "INSERT INTO fact_adoptions VALUES(?,?,?,?,?,?,?)",
                (
                    record.adoption_id,
                    str(record.job_id),
                    record.case_id,
                    record.actor.actor_id,
                    record.idempotency_key,
                    payload_digest,
                    rendered,
                ),
            )
            # Same shape resume_after_human persists: a fresh run for the new
            # revision, documents carried forward, payload digest recomputed so
            # read_submission keeps verifying.
            j._runs[(record.job_id, new_run_id)] = job_memory._Run(
                run_id=new_run_id,
                revision=result.new_revision,
                documents=current.documents,
                payload_digest=submission_digest(
                    ReviewSubmission(
                        revision=result.new_revision,
                        documents=current.documents,
                        idempotency_key=f"run-{new_run_id}",
                    )
                ),
            )
            job.current_run_id = new_run_id
            job.updated_at = record.adopted_at
            try:
                self.snapshots.register(registered)
            except SnapshotRegistryError as error:
                raise ServiceFault(ServiceErrorCode.CONFLICT) from error
            return result, True

        return await asyncio.to_thread(self.store._transaction, apply)
