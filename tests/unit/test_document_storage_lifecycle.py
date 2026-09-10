"""Real SQLite connection ownership and transaction rollback regressions."""

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from appraisal_review.adapters.local.document_storage import SQLiteDocumentStorage
from appraisal_review.domain.document_transfer import (
    DocumentAuditEvent,
    DocumentErrorCode,
    DocumentFault,
    DocumentOperation,
    ObjectKey,
    ObjectLabels,
    digest_bytes,
)


@pytest.fixture
def connections(monkeypatch):
    original = sqlite3.connect
    opened = []
    failures = set()

    class TrackedConnection(sqlite3.Connection):
        def execute(self, sql, parameters=(), /):
            result = super().execute(sql, parameters)
            if any(sql.startswith(prefix) for prefix in failures):
                raise sqlite3.OperationalError("Synthetic failure after real SQL write")
            return result

        def executescript(self, sql_script, /):
            result = super().executescript(sql_script)
            if "initialize" in failures:
                raise sqlite3.OperationalError("Synthetic initialization failure")
            return result

    def connect(*args, **kwargs):
        connection = original(*args, **kwargs, factory=TrackedConnection)
        opened.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", connect)
    yield opened, failures, original
    for connection in opened:
        connection.close()


def assert_closed(opened):
    assert opened
    for connection in opened:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")


def document():
    content = b"synthetic document bytes"
    key = ObjectKey(kind="content", scope=uuid4(), identity=uuid4())
    labels = ObjectLabels(
        case_id=key.scope,
        uploader=uuid4(),
        created_at=datetime.now(UTC),
        content_hash=digest_bytes(content),
        byte_size=len(content),
        content_type="application/pdf",
        purpose="forms",
    )
    return key, content, labels


def audit_event():
    return DocumentAuditEvent(
        event_id=uuid4(),
        occurred_at=datetime.now(UTC),
        actor_id=uuid4(),
        case_id=uuid4(),
        operation=DocumentOperation.INGEST,
        outcome="succeeded",
    )


def test_successful_operations_close_each_connection_and_commit(tmp_path, connections):
    opened, _, original = connections
    database = tmp_path / "documents.sqlite"
    storage = SQLiteDocumentStorage(database)
    assert len(opened) == 1
    assert_closed(opened)
    key, content, labels = document()
    version = storage.create(key, content, labels)
    assert len(opened) == 2
    assert_closed(opened)
    assert storage.read(key, version=version, limit=len(content)).content == content
    assert len(opened) == 3
    assert_closed(opened)
    storage.append(audit_event())
    assert len(opened) == 4
    assert_closed(opened)
    with closing(original(database)) as connection:
        assert connection.execute("SELECT content FROM documents").fetchall() == [(content,)]
        assert connection.execute("SELECT COUNT(*) FROM document_audit").fetchone() == (1,)


@pytest.mark.parametrize("failure", ["missing", "wrong_version", "too_large", "conflict"])
def test_document_fault_closes_connection(tmp_path, connections, failure):
    opened, _, _ = connections
    storage = SQLiteDocumentStorage(tmp_path / "documents.sqlite")
    key, content, labels = document()
    version = storage.create(key, content, labels)
    expected = {
        "missing": DocumentErrorCode.NOT_FOUND,
        "wrong_version": DocumentErrorCode.NOT_FOUND,
        "too_large": DocumentErrorCode.TOO_LARGE,
        "conflict": DocumentErrorCode.CONFLICT,
    }[failure]
    with pytest.raises(DocumentFault) as raised:
        if failure == "conflict":
            storage.create(key, content, labels)
        else:
            storage.read(
                document()[0] if failure == "missing" else key,
                version="absent" if failure == "wrong_version" else version,
                limit=1 if failure == "too_large" else len(content),
            )
    assert raised.value.problem.code == expected
    assert len(opened) == 3
    assert_closed(opened)


@pytest.mark.parametrize("operation", ["create", "append"])
def test_write_failure_rolls_back_before_closing(tmp_path, connections, operation):
    opened, failures, original = connections
    database = tmp_path / "documents.sqlite"
    storage = SQLiteDocumentStorage(database)
    key, content, labels = document()
    table = "documents" if operation == "create" else "document_audit"
    failures.add(f"INSERT INTO {table} ")
    with pytest.raises(DocumentFault if operation == "create" else sqlite3.OperationalError):
        if operation == "create":
            storage.create(key, content, labels)
        else:
            storage.append(audit_event())
    # Inspect a separate real connection: the failed INSERT must not have committed.
    with closing(original(database)) as observer:
        assert observer.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (0,)
        # A second writer also proves the failed transaction released its write lock.
        observer.execute("CREATE TABLE rollback_probe (value INTEGER)")
    assert len(opened) == 2
    assert_closed(opened)


def test_initialization_error_closes_connection(tmp_path, connections):
    opened, failures, _ = connections
    failures.add("initialize")
    with pytest.raises(sqlite3.OperationalError, match="initialization"):
        SQLiteDocumentStorage(tmp_path / "documents.sqlite")
    assert len(opened) == 1
    assert_closed(opened)
