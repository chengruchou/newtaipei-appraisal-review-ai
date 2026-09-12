"""Private SQLite transactions for the local case and job adapters."""

from __future__ import annotations

import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TypeVar

from pydantic import TypeAdapter, ValidationError

from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.domain.service_contracts import ServiceErrorCode
from appraisal_review.ports.jobs import ConditionFailed

T = TypeVar("T")


class ReviewTransaction:
    """Typed JSON rows; table keys are adapter-owned, never caller-selected SQL."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def get(self, model: type[T], kind: str, key: str, subkey: str = "") -> T | None:
        row = self.connection.execute(
            "SELECT payload FROM review_records WHERE kind=? AND key=? AND subkey=?",
            (kind, key, subkey),
        ).fetchone()
        return None if row is None else TypeAdapter(model).validate_json(row[0])

    def rows(self, model: type[T], kind: str, key: str | None = None) -> tuple[T, ...]:
        query = "SELECT payload FROM review_records WHERE kind=?"
        args = [kind]
        if key is not None:
            query += " AND key=?"
            args.append(key)
        query += " ORDER BY rowid"
        return tuple(
            TypeAdapter(model).validate_json(row[0]) for row in self.connection.execute(query, args)
        )

    def put(
        self, kind: str, key: str, value: object, subkey: str = "", *, insert: bool = False
    ) -> None:
        payload = TypeAdapter(type(value)).dump_json(value)
        query = "INSERT INTO review_records(kind,key,subkey,payload) VALUES (?,?,?,?)"
        if not insert:
            query += " ON CONFLICT(kind,key,subkey) DO UPDATE SET payload=excluded.payload"
        self.connection.execute(query, (kind, key, subkey, payload))


class SQLiteReviewDatabase:
    """One short synchronous transaction per operation, with bounded lock waiting.

    The parent directory must already be private and operator-owned. API and worker
    construct separate adapters against the same path; no process-local state is truth.
    """

    def __init__(self, path: Path, *, timeout: float = 0.25) -> None:
        if not 0 < timeout <= 5:
            raise ValueError("SQLite lock timeout must be positive and at most five seconds")
        self.path = path.absolute()
        self.timeout = timeout
        try:
            self._check_parent()
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                self._check_file(os.fstat(fd))
            finally:
                os.close(fd)
            with self.transaction(write=True, initialize=True) as tx:
                version = tx.connection.execute("PRAGMA user_version").fetchone()[0]
                if version == 0:
                    tables = tx.connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                    if tables:
                        raise ServiceFault(ServiceErrorCode.CAPABILITY)
                    tx.connection.execute(
                        "CREATE TABLE review_records (kind TEXT NOT NULL, key TEXT NOT NULL, "
                        "subkey TEXT NOT NULL, payload BLOB NOT NULL, UNIQUE(kind,key,subkey))"
                    )
                    tx.connection.execute("PRAGMA user_version=1")
                elif version != 1:
                    raise ServiceFault(ServiceErrorCode.CAPABILITY)
        except OSError:
            raise ServiceFault(ServiceErrorCode.CAPABILITY) from None

    def _check_parent(self) -> None:
        parent = self.path.parent
        # Disallow symlinked directory components and permission changes on reopen.
        if parent.resolve() != parent:
            raise ServiceFault(ServiceErrorCode.CAPABILITY)
        info = parent.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise ServiceFault(ServiceErrorCode.CAPABILITY)

    @staticmethod
    def _check_file(info: os.stat_result) -> None:
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise ServiceFault(ServiceErrorCode.CAPABILITY)

    @contextmanager
    def transaction(
        self, *, write: bool = False, initialize: bool = False
    ) -> Iterator[ReviewTransaction]:
        connection: sqlite3.Connection | None = None
        try:
            self._check_parent()
            self._check_file(self.path.lstat())
            connection = sqlite3.connect(
                self.path.as_uri() + "?mode=rw",
                uri=True,
                timeout=self.timeout,
                isolation_level=None,
            )
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            if not initialize and connection.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise ServiceFault(ServiceErrorCode.CAPABILITY)
            yield ReviewTransaction(connection)
            connection.commit()
        except sqlite3.IntegrityError:
            raise ConditionFailed("Stored identity already exists") from None
        except (sqlite3.Error, OSError):
            raise ServiceFault(ServiceErrorCode.CAPABILITY) from None
        except ValidationError:
            raise ServiceFault(ServiceErrorCode.EXECUTION) from None
        finally:
            # close rolls back any uncommitted write, including BaseException and
            # cancellation. There are no await points inside this transaction.
            if connection is not None:
                connection.close()
