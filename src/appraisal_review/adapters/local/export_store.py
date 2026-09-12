"""Durable export operations and their produced bytes, in the same private SQLite file.

An export accepted with 202 must survive a process restart, and its produced files must
be readable later under the same authorization the rest of the case obeys. Operations and
object bytes live next to the review state so one database file remains the single thing
an operator backs up or moves.

The uniqueness row (job, actor, key) is what makes replay exact: the payload digest stored
with it decides between "same request, hand back the same operation" and "same key, changed
request, conflict". The format is inside that digest, so switching format can never reuse
a key silently.
"""

from __future__ import annotations

import hashlib
import sqlite3
from uuid import UUID

from appraisal_review.adapters.local.sqlite_publication import ReviewDatabase, _transaction
from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.domain.official_export import (
    ConvertedPDFArtifact,
    ExportOperation,
    ExportRequest,
)
from appraisal_review.domain.report_approval import ReportApproval
from appraisal_review.domain.service_contracts import ServiceErrorCode, ServiceProblem

_MAX_OBJECT_BYTES = 64 * 1024 * 1024


class SQLiteExportStore:
    def __init__(self, review_store: ReviewDatabase) -> None:
        self.database = review_store
        with _transaction(self.database) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS export_operations ("
                "export_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, actor_id TEXT NOT NULL, "
                "idempotency_key TEXT NOT NULL, payload_digest TEXT NOT NULL, "
                "operation TEXT NOT NULL, "
                "UNIQUE(job_id, actor_id, idempotency_key))"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS export_objects ("
                "artifact_id TEXT PRIMARY KEY, export_id TEXT NOT NULL, "
                "object_key TEXT NOT NULL UNIQUE, digest TEXT NOT NULL, "
                "size INTEGER NOT NULL, delivered INTEGER NOT NULL, body BLOB NOT NULL)"
            )

    def create(
        self, operation: ExportOperation, request: ExportRequest, actor_id: str
    ) -> tuple[ExportOperation, bool]:
        """Persist a new operation, or hand back the one this exact request already made."""
        digest = request.payload_digest()
        with _transaction(self.database) as connection:
            row = connection.execute(
                "SELECT payload_digest, operation FROM export_operations "
                "WHERE job_id=? AND actor_id=? AND idempotency_key=?",
                (str(operation.job_id), actor_id, request.idempotency_key),
            ).fetchone()
            if row is not None:
                if row[0] != digest:
                    # The key is taken by a different request - a changed format included.
                    raise ServiceFault(ServiceErrorCode.CONFLICT)
                return ExportOperation.model_validate_json(row[1]), False
            connection.execute(
                "INSERT INTO export_operations VALUES(?,?,?,?,?,?)",
                (
                    str(operation.export_id),
                    str(operation.job_id),
                    actor_id,
                    request.idempotency_key,
                    digest,
                    operation.model_dump_json(),
                ),
            )
        return operation, True

    def _current_revision_blocker(
        self, connection: sqlite3.Connection, operation: ExportOperation
    ) -> ServiceProblem | None:
        """The authoritative current-revision check, on the SAME open transaction.

        The review state singleton in this database file is the source of truth for
        which run and revision a job currently stands on; corrections, withdrawals
        and resumptions all commit there. Anything unreadable fails closed: a formal
        outcome never commits on a revision this transaction cannot verify.
        """
        try:
            row = connection.execute(
                "SELECT payload FROM review_state WHERE singleton=1"
            ).fetchone()
            if row is None:
                return ServiceProblem(code=ServiceErrorCode.CONFLICT)
            state = self.database._decode(row[0]).model_dump(mode="json")
            if state.get("schema_version") != "sqlite-review-v1":
                return ServiceProblem(code=ServiceErrorCode.CONFLICT)
            job = state["jobs"].get(str(operation.job_id))
            if job is None or job["current_run_id"] != str(operation.run.run_id):
                return ServiceProblem(code=ServiceErrorCode.CONFLICT)
            runs = [
                r["value"]
                for r in state["runs"]
                if r["job_id"] == str(operation.job_id)
                and r["value"]["run_id"] == job["current_run_id"]
            ]
            if len(runs) != 1 or runs[0]["revision"] != operation.run.revision.model_dump(
                mode="json"
            ):
                return ServiceProblem(code=ServiceErrorCode.CONFLICT)
        except (KeyError, TypeError, ValueError, sqlite3.Error):
            return ServiceProblem(code=ServiceErrorCode.CONFLICT)
        return None

    @staticmethod
    def _approval_blocker(
        connection: sqlite3.Connection, operation: ExportOperation
    ) -> ServiceProblem | None:
        """Approval status AND binding, re-read inside the committing transaction."""
        row = connection.execute(
            "SELECT status, job_id, record FROM report_approvals WHERE approval_id=?",
            (str(operation.approval_id),),
        ).fetchone()
        if row is None or row[1] != str(operation.job_id) or row[0] != "approved":
            return ServiceProblem(code=ServiceErrorCode.UNAUTHORIZED)
        try:
            approved = ReportApproval.model_validate_json(row[2])
        except ValueError:
            return ServiceProblem(code=ServiceErrorCode.UNAUTHORIZED)
        hashes = approved.binding.workbook_hashes
        for artifact in operation.artifacts:
            produced = (
                artifact.source_workbook_hash
                if isinstance(artifact, ConvertedPDFArtifact)
                else artifact.content_hash
            )
            if hashes.get(artifact.table) != produced:
                return ServiceProblem(code=ServiceErrorCode.CONFLICT)
        return None

    def save(self, operation: ExportOperation) -> None:
        with _transaction(self.database) as connection:
            if operation.effective_mode == "formal" and operation.status in {
                "succeeded",
                "partial",
            }:
                # The successful-formal outcome commits conditionally: the approval row
                # and the job's authoritative current revision live in the same database
                # file, so these reads and the UPDATE below are one serialized BEGIN
                # IMMEDIATE transaction - a withdrawal or correction that committed
                # first is always seen, and one that commits later finds the outcome
                # already recorded and refuses at the content route instead. No cached
                # or second-connection value participates in this decision.
                blocker = self._approval_blocker(
                    connection, operation
                ) or self._current_revision_blocker(connection, operation)
                if blocker is not None:
                    operation = operation.model_copy(
                        update={
                            "status": "failed",
                            "tables": tuple(
                                outcome.model_copy(update={"delivered": False, "artifact_id": None})
                                for outcome in operation.tables
                            ),
                            "artifacts": (),
                            "problem": blocker,
                        }
                    )
                    operation = ExportOperation.model_validate(operation.model_dump())
            updated = connection.execute(
                "UPDATE export_operations SET operation=? WHERE export_id=?",
                (operation.model_dump_json(), str(operation.export_id)),
            ).rowcount
        if updated != 1:
            raise ServiceFault(ServiceErrorCode.NOT_FOUND)

    def read(self, job_id: UUID, export_id: UUID) -> ExportOperation | None:
        connection = self.database._connect()
        try:
            row = connection.execute(
                "SELECT operation FROM export_operations WHERE export_id=? AND job_id=?",
                (str(export_id), str(job_id)),
            ).fetchone()
        finally:
            connection.close()
        return None if row is None else ExportOperation.model_validate_json(row[0])

    def pending(self) -> tuple[ExportOperation, ...]:
        connection = self.database._connect()
        try:
            rows = connection.execute("SELECT operation FROM export_operations").fetchall()
        finally:
            connection.close()
        operations = [ExportOperation.model_validate_json(row[0]) for row in rows]
        return tuple(op for op in operations if op.status in {"queued", "running"})

    def put_object(
        self, *, artifact_id: UUID, export_id: UUID, key: str, data: bytes, delivered: bool
    ) -> str:
        """Store one immutable produced file; a second write must carry identical bytes."""
        if not isinstance(data, bytes) or not 0 < len(data) <= _MAX_OBJECT_BYTES:
            raise ServiceFault(ServiceErrorCode.EXECUTION)
        digest = hashlib.sha256(data).hexdigest()
        with _transaction(self.database) as connection:
            row = connection.execute(
                "SELECT digest, size FROM export_objects WHERE artifact_id=?",
                (str(artifact_id),),
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO export_objects VALUES(?,?,?,?,?,?,?)",
                    (
                        str(artifact_id),
                        str(export_id),
                        key,
                        digest,
                        len(data),
                        1 if delivered else 0,
                        data,
                    ),
                )
            elif (row[0], row[1]) != (digest, len(data)):
                raise ServiceFault(ServiceErrorCode.CONFLICT)
        return digest

    def find_delivered(
        self, job_id: UUID, artifact_id: UUID
    ) -> tuple[ExportOperation, bytes] | None:
        """The committed export record and exact bytes for one delivered artifact."""
        connection = self.database._connect()
        try:
            row = connection.execute(
                "SELECT o.export_id, o.digest, o.size, o.body, p.operation "
                "FROM export_objects o JOIN export_operations p ON p.export_id=o.export_id "
                "WHERE o.artifact_id=? AND p.job_id=? AND o.delivered=1 "
                "AND length(o.body)<=?",
                (str(artifact_id), str(job_id), _MAX_OBJECT_BYTES),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        _, digest, size, body, operation_json = row
        if not isinstance(body, bytes) or len(body) != size:
            raise ServiceFault(ServiceErrorCode.EXECUTION)
        if hashlib.sha256(body).hexdigest() != digest:
            raise ServiceFault(ServiceErrorCode.EXECUTION)
        return ExportOperation.model_validate_json(operation_json), body
