"""Durable fact candidates and confirmation receipts in the shared review SQLite.

Three additive tables, same discipline as approval_store: `fact_candidates`
keeps the current record with a status column so a decision commits as a
conditional UPDATE ("from candidate only"), `fact_candidate_receipts` keeps
every receipt, and `fact_candidate_confirmations` maps each decision command's
(case, actor, idempotency key) to the receipt it produced so a network retry
replays its original outcome instead of becoming a new decision. Nothing here
deletes or rewrites a decided candidate; the status flip and the receipt insert
happen in ONE transaction.
"""

from __future__ import annotations

import sqlite3

from appraisal_review.adapters.local.sqlite_publication import ReviewDatabase, _transaction
from appraisal_review.application.fact_candidates import CandidateConfirmation, FactCandidate
from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.domain.service_contracts import ServiceErrorCode


class SQLiteCandidateStore:
    def __init__(self, review_store: ReviewDatabase) -> None:
        self.database = review_store
        with _transaction(self.database) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS fact_candidates ("
                "candidate_id TEXT PRIMARY KEY, case_id TEXT NOT NULL, actor_id TEXT NOT NULL, "
                "idempotency_key TEXT NOT NULL, payload_digest TEXT NOT NULL, "
                "status TEXT NOT NULL, record TEXT NOT NULL, "
                "UNIQUE(case_id, actor_id, idempotency_key))"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS fact_candidate_receipts ("
                "receipt_id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, "
                "case_id TEXT NOT NULL, actor_id TEXT NOT NULL, "
                "idempotency_key TEXT NOT NULL, payload_digest TEXT NOT NULL, "
                "record TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS fact_candidate_confirmations ("
                "case_id TEXT NOT NULL, actor_id TEXT NOT NULL, "
                "idempotency_key TEXT NOT NULL, receipt_id TEXT NOT NULL, "
                "payload_digest TEXT NOT NULL, "
                "PRIMARY KEY(case_id, actor_id, idempotency_key))"
            )

    def register(
        self, candidate: FactCandidate, *, actor_id: str, idempotency_key: str, payload_digest: str
    ) -> tuple[FactCandidate, bool]:
        with _transaction(self.database) as connection:
            row = connection.execute(
                "SELECT payload_digest, record FROM fact_candidates "
                "WHERE case_id=? AND actor_id=? AND idempotency_key=?",
                (candidate.case_id, actor_id, idempotency_key),
            ).fetchone()
            if row is not None:
                if row[0] != payload_digest:
                    raise ServiceFault(ServiceErrorCode.CONFLICT)
                return FactCandidate.model_validate_json(row[1]), False
            connection.execute(
                "INSERT INTO fact_candidates VALUES(?,?,?,?,?,?,?)",
                (
                    candidate.candidate_id,
                    candidate.case_id,
                    actor_id,
                    idempotency_key,
                    payload_digest,
                    candidate.status,
                    candidate.model_dump_json(),
                ),
            )
        return candidate, True

    def read(self, case_id: str, candidate_id: str) -> FactCandidate | None:
        connection = self.database._connect()
        try:
            row = connection.execute(
                "SELECT record FROM fact_candidates WHERE candidate_id=? AND case_id=?",
                (candidate_id, case_id),
            ).fetchone()
        finally:
            connection.close()
        return None if row is None else FactCandidate.model_validate_json(row[0])

    def list_candidates(self, case_id: str) -> tuple[FactCandidate, ...]:
        connection = self.database._connect()
        try:
            rows = connection.execute(
                "SELECT record FROM fact_candidates WHERE case_id=? ORDER BY rowid",
                (case_id,),
            ).fetchall()
        finally:
            connection.close()
        return tuple(FactCandidate.model_validate_json(row[0]) for row in rows)

    def confirmed_unadopted(self, case_id: str) -> tuple[FactCandidate, ...]:
        # Adoption (application.fact_adoption) flips a row to "adopted" via
        # mark_adopted below, so filtering on "confirmed" is exactly the
        # awaiting-adoption set.
        connection = self.database._connect()
        try:
            rows = connection.execute(
                "SELECT record FROM fact_candidates "
                "WHERE case_id=? AND status='confirmed' ORDER BY rowid",
                (case_id,),
            ).fetchall()
        finally:
            connection.close()
        return tuple(FactCandidate.model_validate_json(row[0]) for row in rows)

    @staticmethod
    def mark_adopted(connection: sqlite3.Connection, adopted: FactCandidate) -> bool:
        """Flip one CONFIRMED candidate to adopted inside the CALLER's transaction.

        The adoption store calls this with the connection of the review-state
        transaction that also advances the job revision and inserts the adoption
        record, so the flip commits or rolls back with everything else. The UPDATE
        is conditional on the stored status still being ``confirmed``; a False
        return means someone else decided first and the caller must conflict.
        """
        if adopted.status != "adopted":
            raise ValueError("mark_adopted persists only records already marked adopted")
        changed = connection.execute(
            "UPDATE fact_candidates SET status=?, record=? "
            "WHERE candidate_id=? AND case_id=? AND status='confirmed'",
            (adopted.status, adopted.model_dump_json(), adopted.candidate_id, adopted.case_id),
        ).rowcount
        return changed == 1

    def confirm(
        self,
        updated: FactCandidate,
        receipt: CandidateConfirmation,
        *,
        actor_id: str,
        idempotency_key: str,
        payload_digest: str,
    ) -> tuple[CandidateConfirmation, bool]:
        """Commit one decision atomically; a replayed command returns its own receipt."""
        with _transaction(self.database) as connection:
            mapped = connection.execute(
                "SELECT receipt_id, payload_digest FROM fact_candidate_confirmations "
                "WHERE case_id=? AND actor_id=? AND idempotency_key=?",
                (receipt.case_id, actor_id, idempotency_key),
            ).fetchone()
            if mapped is not None:
                if mapped[1] != payload_digest:
                    raise ServiceFault(ServiceErrorCode.CONFLICT)
                original = connection.execute(
                    "SELECT record FROM fact_candidate_receipts WHERE receipt_id=?",
                    (mapped[0],),
                ).fetchone()
                if original is None:
                    raise ServiceFault(ServiceErrorCode.CONFLICT)
                return CandidateConfirmation.model_validate_json(original[0]), False
            changed = connection.execute(
                "UPDATE fact_candidates SET status=?, record=? "
                "WHERE candidate_id=? AND case_id=? AND status='candidate'",
                (
                    updated.status,
                    updated.model_dump_json(),
                    updated.candidate_id,
                    updated.case_id,
                ),
            ).rowcount
            if changed != 1:
                # Someone decided first, or the candidate left the open state.
                raise ServiceFault(ServiceErrorCode.CONFLICT)
            connection.execute(
                "INSERT INTO fact_candidate_receipts VALUES(?,?,?,?,?,?,?)",
                (
                    receipt.receipt_id,
                    receipt.candidate_id,
                    receipt.case_id,
                    actor_id,
                    idempotency_key,
                    payload_digest,
                    receipt.model_dump_json(),
                ),
            )
            connection.execute(
                "INSERT INTO fact_candidate_confirmations VALUES(?,?,?,?,?)",
                (receipt.case_id, actor_id, idempotency_key, receipt.receipt_id, payload_digest),
            )
        return receipt, True
