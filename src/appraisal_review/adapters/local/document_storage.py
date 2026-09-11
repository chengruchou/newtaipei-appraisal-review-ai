"""Durable append-only local storage and minimal audit, with no loose PDF paths."""

import os
import sqlite3
import stat
from pathlib import Path
from uuid import uuid4

from appraisal_review.domain.document_transfer import (
    DocumentAuditEvent,
    DocumentErrorCode,
    DocumentFault,
    ObjectKey,
    ObjectLabels,
    StoredBytes,
    canonical_bytes,
    digest_bytes,
)


class SQLiteDocumentStorage:
    def __init__(self, database: Path) -> None:
        # The parent is an operator-owned private directory. Never create a path from a request.
        if database.is_symlink():
            raise DocumentFault(DocumentErrorCode.UNAVAILABLE)
        descriptor = os.open(database, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            status = os.fstat(descriptor)
            if not stat.S_ISREG(status.st_mode) or stat.S_IMODE(status.st_mode) & 0o077:
                raise DocumentFault(DocumentErrorCode.UNAVAILABLE)
        finally:
            os.close(descriptor)
        self.database = database
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS documents (
                    key TEXT PRIMARY KEY, version TEXT NOT NULL,
                    content BLOB NOT NULL, labels BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS document_audit (
                    event_id TEXT PRIMARY KEY, event BLOB NOT NULL
                );
            """)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database, timeout=10)

    def create(self, key: ObjectKey, content: bytes, labels: ObjectLabels) -> str:
        key = ObjectKey.model_validate_json(key.model_dump_json())
        labels = ObjectLabels.model_validate_json(labels.model_dump_json())
        if len(content) != labels.byte_size or digest_bytes(content) != labels.content_hash:
            raise DocumentFault(DocumentErrorCode.INTEGRITY)
        version = str(uuid4())
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO documents VALUES (?, ?, ?, ?)",
                    (key.relative_key(), version, content, canonical_bytes(labels)),
                )
        except sqlite3.IntegrityError:
            raise DocumentFault(DocumentErrorCode.CONFLICT) from None
        except sqlite3.Error:
            raise DocumentFault(DocumentErrorCode.UNAVAILABLE) from None
        return version

    def read(self, key: ObjectKey, *, version: str | None, limit: int) -> StoredBytes:
        key = ObjectKey.model_validate_json(key.model_dump_json())
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT version, length(content), labels FROM documents WHERE key = ?",
                    (key.relative_key(),),
                ).fetchone()
                if row is None or (version is not None and row[0] != version):
                    raise DocumentFault(DocumentErrorCode.NOT_FOUND)
                if row[1] > limit:
                    raise DocumentFault(DocumentErrorCode.TOO_LARGE)
                content = connection.execute(
                    "SELECT content FROM documents WHERE key = ?", (key.relative_key(),)
                ).fetchone()[0]
        except sqlite3.Error:
            raise DocumentFault(DocumentErrorCode.UNAVAILABLE) from None
        labels = ObjectLabels.model_validate_json(row[2])
        if digest_bytes(content) != labels.content_hash or len(content) != labels.byte_size:
            raise DocumentFault(DocumentErrorCode.INTEGRITY)
        return StoredBytes(content=content, version=row[0])

    def append(self, event: DocumentAuditEvent) -> None:
        event = DocumentAuditEvent.model_validate_json(event.model_dump_json())
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO document_audit VALUES (?, ?)",
                (str(event.event_id), canonical_bytes(event)),
            )
