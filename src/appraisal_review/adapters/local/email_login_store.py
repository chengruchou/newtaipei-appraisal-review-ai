"""Durable email-login codes, rate-limit counters and a send audit, in SQLite.

Same transaction discipline as the other local stores: additive
``CREATE TABLE IF NOT EXISTS`` schema on the shared ReviewDatabase, every write
under ``BEGIN IMMEDIATE``. Three tables:

- ``email_login_codes``: one row per minted code. Only the salted sha256 hash is
  stored - the plain code never touches the database. ``consume`` commits as a
  conditional UPDATE (unconsumed, attempts left, unexpired), which is what makes
  the code single-use under concurrency; ``spend_attempt`` decrements only while
  attempts remain, so the count can never go negative.
- ``email_login_requests``: append-only rate-limit counters. ``reserve_request``
  counts both windows and inserts the new row in ONE transaction, so two
  concurrent requests cannot both slip under the limit.
- ``email_login_send_audit``: one row per send outcome (sent/undeliverable plus
  the sanitized reason). No code material - the audit exists so operators can
  see sandbox "delivery restricted" failures that requesters never see.
- ``auth_accounts``: one row per password account, keyed by the normalized
  email. Only the scrypt salt and hash are stored - the plain password never
  touches the database. ``upsert_password`` is one atomic INSERT .. ON CONFLICT
  UPDATE, so register and reset are the same single write and
  ``password_set_count`` counts every replacement.
- ``auth_login_attempts``: append-only login rate-limit counters, the same
  count-then-insert-in-one-transaction discipline as ``email_login_requests``
  but a separate budget, so password guessing cannot ride on (or starve) the
  request-code window.
"""

from __future__ import annotations

from typing import Literal

from appraisal_review.adapters.local.sqlite_publication import ReviewDatabase, _transaction
from appraisal_review.application.email_login import (
    LoginCodeRecord,
    LoginDeliveryStatus,
    PasswordAccountRecord,
)

_CODE_COLUMNS = (
    "record_id, email, code_hash, salt, purpose, created_at, expires_at, "
    "attempts_left, consumed, delivery, failure_reason"
)


def _record_from_row(row: tuple[object, ...]) -> LoginCodeRecord:
    return LoginCodeRecord(
        record_id=str(row[0]),
        email=str(row[1]),
        code_hash=str(row[2]),
        salt=str(row[3]),
        purpose="login",
        created_at=int(str(row[5])),
        expires_at=int(str(row[6])),
        attempts_left=int(str(row[7])),
        consumed=bool(row[8]),
        delivery=str(row[9]),  # type: ignore[arg-type]
        failure_reason=None if row[10] is None else str(row[10]),
    )


class SQLiteEmailLoginStore:
    def __init__(self, review_store: ReviewDatabase) -> None:
        self.database = review_store
        with _transaction(self.database) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS email_login_codes ("
                "record_id TEXT PRIMARY KEY, email TEXT NOT NULL, code_hash TEXT NOT NULL, "
                "salt TEXT NOT NULL, purpose TEXT NOT NULL, created_at INTEGER NOT NULL, "
                "expires_at INTEGER NOT NULL, attempts_left INTEGER NOT NULL, "
                "consumed INTEGER NOT NULL, delivery TEXT NOT NULL, failure_reason TEXT)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS email_login_requests ("
                "email TEXT NOT NULL, caller TEXT NOT NULL, requested_at INTEGER NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS email_login_send_audit ("
                "record_id TEXT NOT NULL, email TEXT NOT NULL, outcome TEXT NOT NULL, "
                "reason TEXT, at INTEGER NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS auth_accounts ("
                "email_normalized TEXT PRIMARY KEY, password_scrypt_salt TEXT NOT NULL, "
                "password_scrypt_hash TEXT NOT NULL, created_at INTEGER NOT NULL, "
                "updated_at INTEGER NOT NULL, password_set_count INTEGER NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS auth_login_attempts ("
                "email TEXT NOT NULL, caller TEXT NOT NULL, attempted_at INTEGER NOT NULL)"
            )

    def reserve_request(
        self,
        *,
        email: str,
        caller: str,
        now: int,
        window_start: int,
        max_per_email: int,
        max_per_caller: int,
    ) -> bool:
        with _transaction(self.database) as connection:
            per_email = connection.execute(
                "SELECT COUNT(*) FROM email_login_requests WHERE email=? AND requested_at>?",
                (email, window_start),
            ).fetchone()[0]
            per_caller = connection.execute(
                "SELECT COUNT(*) FROM email_login_requests WHERE caller=? AND requested_at>?",
                (caller, window_start),
            ).fetchone()[0]
            if int(per_email) >= max_per_email or int(per_caller) >= max_per_caller:
                return False
            connection.execute(
                "INSERT INTO email_login_requests VALUES(?,?,?)", (email, caller, now)
            )
        return True

    def create_code(self, record: LoginCodeRecord) -> None:
        record = LoginCodeRecord.model_validate_json(record.model_dump_json())
        with _transaction(self.database) as connection:
            connection.execute(
                "INSERT INTO email_login_codes VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record.record_id,
                    record.email,
                    record.code_hash,
                    record.salt,
                    record.purpose,
                    record.created_at,
                    record.expires_at,
                    record.attempts_left,
                    int(record.consumed),
                    record.delivery,
                    record.failure_reason,
                ),
            )

    def latest_code(self, email: str) -> LoginCodeRecord | None:
        connection = self.database._connect()
        try:
            row = connection.execute(
                f"SELECT {_CODE_COLUMNS} FROM email_login_codes "
                "WHERE email=? ORDER BY rowid DESC LIMIT 1",
                (email,),
            ).fetchone()
        finally:
            connection.close()
        return None if row is None else _record_from_row(row)

    def spend_attempt(self, record_id: str) -> None:
        with _transaction(self.database) as connection:
            connection.execute(
                "UPDATE email_login_codes SET attempts_left = attempts_left - 1 "
                "WHERE record_id=? AND attempts_left > 0",
                (record_id,),
            )

    def consume(self, record_id: str, *, now: int) -> bool:
        with _transaction(self.database) as connection:
            changed = connection.execute(
                "UPDATE email_login_codes SET consumed=1 "
                "WHERE record_id=? AND consumed=0 AND attempts_left>0 AND expires_at>?",
                (record_id, now),
            ).rowcount
        return changed == 1

    def mark_delivery(
        self,
        record_id: str,
        delivery: Literal["sent", "undeliverable"],
        *,
        at: int,
        reason: str | None = None,
    ) -> None:
        with _transaction(self.database) as connection:
            connection.execute(
                "UPDATE email_login_codes SET delivery=?, failure_reason=? WHERE record_id=?",
                (delivery, reason, record_id),
            )
            row = connection.execute(
                "SELECT email FROM email_login_codes WHERE record_id=?", (record_id,)
            ).fetchone()
            email = "" if row is None else str(row[0])
            connection.execute(
                "INSERT INTO email_login_send_audit VALUES(?,?,?,?,?)",
                (record_id, email, delivery, reason, at),
            )

    def read_account(self, email: str) -> PasswordAccountRecord | None:
        connection = self.database._connect()
        try:
            row = connection.execute(
                "SELECT email_normalized, password_scrypt_salt, password_scrypt_hash, "
                "created_at, updated_at, password_set_count "
                "FROM auth_accounts WHERE email_normalized=?",
                (email,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        return PasswordAccountRecord(
            email=str(row[0]),
            password_scrypt_salt=str(row[1]),
            password_scrypt_hash=str(row[2]),
            created_at=int(str(row[3])),
            updated_at=int(str(row[4])),
            password_set_count=int(str(row[5])),
        )

    def upsert_password(self, *, email: str, salt_hex: str, hash_hex: str, now: int) -> None:
        """Register and reset are one atomic write; created_at survives resets."""
        with _transaction(self.database) as connection:
            connection.execute(
                "INSERT INTO auth_accounts VALUES(?,?,?,?,?,1) "
                "ON CONFLICT(email_normalized) DO UPDATE SET "
                "password_scrypt_salt=excluded.password_scrypt_salt, "
                "password_scrypt_hash=excluded.password_scrypt_hash, "
                "updated_at=excluded.updated_at, "
                "password_set_count=auth_accounts.password_set_count+1",
                (email, salt_hex, hash_hex, now, now),
            )

    def reserve_login_attempt(
        self,
        *,
        email: str,
        caller: str,
        now: int,
        window_start: int,
        max_per_email: int,
        max_per_caller: int,
    ) -> bool:
        with _transaction(self.database) as connection:
            per_email = connection.execute(
                "SELECT COUNT(*) FROM auth_login_attempts WHERE email=? AND attempted_at>?",
                (email, window_start),
            ).fetchone()[0]
            per_caller = connection.execute(
                "SELECT COUNT(*) FROM auth_login_attempts WHERE caller=? AND attempted_at>?",
                (caller, window_start),
            ).fetchone()[0]
            if int(per_email) >= max_per_email or int(per_caller) >= max_per_caller:
                return False
            connection.execute(
                "INSERT INTO auth_login_attempts VALUES(?,?,?)", (email, caller, now)
            )
        return True

    def delivery_statuses(self, *, limit: int = 50) -> tuple[LoginDeliveryStatus, ...]:
        connection = self.database._connect()
        try:
            rows = connection.execute(
                f"SELECT {_CODE_COLUMNS} FROM email_login_codes ORDER BY rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        finally:
            connection.close()
        statuses = []
        for row in rows:
            record = _record_from_row(row)
            statuses.append(
                LoginDeliveryStatus(
                    record_id=record.record_id,
                    email=record.email,
                    delivery=record.delivery,
                    failure_reason=record.failure_reason,
                    created_at=record.created_at,
                    expires_at=record.expires_at,
                    attempts_left=record.attempts_left,
                    consumed=record.consumed,
                )
            )
        return tuple(statuses)
