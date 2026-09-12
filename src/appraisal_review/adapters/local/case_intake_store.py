"""Durable case-intake records next to the review state, bytes under the workbench.

Two tables with the approval store's replay rules. ``intake_cases`` maps one
(actor, idempotency key) to the case it created, so a network retry returns the
original case instead of minting a second one; a changed payload conflicts.
``intake_materials`` does the same per case for uploads, and its bytes are written
to the workbench root before the metadata row commits, so a committed record
without its bytes is impossible (a crash in between leaves an orphan file, never a
dangling record).

Bytes live at ``root/<case_id>/<material_id>`` - both server-minted identifiers,
never the caller's filename, so no request can choose a storage path. Directories
are created 0700 and files 0600, matching the workbench's private layout.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from appraisal_review.adapters.local.sqlite_publication import ReviewDatabase, _transaction
from appraisal_review.application.case_intake import CaseRecord, MaterialRecord
from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.domain.service_contracts import ServiceErrorCode


class SQLiteCaseIntakeStore:
    def __init__(self, review_store: ReviewDatabase, *, root: Path) -> None:
        self.database = review_store
        self.root = root
        with _transaction(self.database) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS intake_cases ("
                "case_id TEXT PRIMARY KEY, actor_id TEXT NOT NULL, "
                "idempotency_key TEXT NOT NULL, payload_digest TEXT NOT NULL, "
                "record TEXT NOT NULL, "
                "UNIQUE(actor_id, idempotency_key))"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS intake_materials ("
                "material_id TEXT PRIMARY KEY, case_id TEXT NOT NULL, "
                "actor_id TEXT NOT NULL, idempotency_key TEXT NOT NULL, "
                "payload_digest TEXT NOT NULL, record TEXT NOT NULL, "
                "UNIQUE(case_id, actor_id, idempotency_key))"
            )

    def create_case(
        self, record: CaseRecord, *, actor_id: str, idempotency_key: str, payload_digest: str
    ) -> tuple[CaseRecord, bool]:
        with _transaction(self.database) as connection:
            row = connection.execute(
                "SELECT payload_digest, record FROM intake_cases "
                "WHERE actor_id=? AND idempotency_key=?",
                (actor_id, idempotency_key),
            ).fetchone()
            if row is not None:
                if row[0] != payload_digest:
                    raise ServiceFault(ServiceErrorCode.CONFLICT)
                return CaseRecord.model_validate_json(row[1]), False
            connection.execute(
                "INSERT INTO intake_cases VALUES(?,?,?,?,?)",
                (
                    record.case_id,
                    actor_id,
                    idempotency_key,
                    payload_digest,
                    record.model_dump_json(),
                ),
            )
            return record, True

    def read_case(self, case_id: str) -> CaseRecord | None:
        with _transaction(self.database) as connection:
            row = connection.execute(
                "SELECT record FROM intake_cases WHERE case_id=?", (case_id,)
            ).fetchone()
        if row is None:
            return None
        return CaseRecord.model_validate_json(row[0])

    def material_path(self, case_id: str, material_id: str) -> Path:
        """Where a committed material's bytes live; for the composition root only."""
        return self.root / case_id / material_id

    def _write_bytes(self, record: MaterialRecord, data: bytes) -> None:
        directory = self.root / record.case_id
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        target = directory / record.material_id
        staging = directory / f".{record.material_id}.staging"
        descriptor = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            staging.unlink(missing_ok=True)
            raise
        os.replace(staging, target)

    def add_material(
        self,
        record: MaterialRecord,
        data: bytes,
        *,
        actor_id: str,
        idempotency_key: str,
        payload_digest: str,
    ) -> tuple[MaterialRecord, bool]:
        # The stored metadata must describe the exact bytes on disk; recomputing here
        # keeps a service-layer bug from committing an unverifiable record.
        if record.size != len(data) or record.sha256 != hashlib.sha256(data).hexdigest():
            raise ValueError("Material record does not describe the supplied bytes")
        with _transaction(self.database) as connection:
            row = connection.execute(
                "SELECT payload_digest, record FROM intake_materials "
                "WHERE case_id=? AND actor_id=? AND idempotency_key=?",
                (record.case_id, actor_id, idempotency_key),
            ).fetchone()
            if row is not None:
                if row[0] != payload_digest:
                    raise ServiceFault(ServiceErrorCode.CONFLICT)
                return MaterialRecord.model_validate_json(row[1]), False
            # Bytes first, inside the transaction scope: if the write fails the row
            # rolls back; if the commit fails the orphan file is harmless.
            self._write_bytes(record, data)
            connection.execute(
                "INSERT INTO intake_materials VALUES(?,?,?,?,?,?)",
                (
                    record.material_id,
                    record.case_id,
                    actor_id,
                    idempotency_key,
                    payload_digest,
                    record.model_dump_json(),
                ),
            )
            return record, True

    def list_materials(self, case_id: str) -> tuple[MaterialRecord, ...]:
        with _transaction(self.database) as connection:
            rows = connection.execute(
                "SELECT record FROM intake_materials WHERE case_id=? ORDER BY rowid",
                (case_id,),
            ).fetchall()
        return tuple(MaterialRecord.model_validate_json(row[0]) for row in rows)

    def memberships(self) -> tuple[tuple[str, str], ...]:
        """(case_id, creator actor_id) pairs, so a reopened composition can re-grant
        the durable intake memberships that the in-memory directory forgot."""
        with _transaction(self.database) as connection:
            rows = connection.execute(
                "SELECT case_id, actor_id FROM intake_cases ORDER BY rowid"
            ).fetchall()
        return tuple((row[0], row[1]) for row in rows)
