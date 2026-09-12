"""Durable report approvals with conditional transitions, next to the review state.

Two tables. `report_approvals` holds the current record, keyed for exact replay on
(job, actor, idempotency key); its status column exists so a decision can commit as a
conditional UPDATE - "from submitted only" - which is what makes a replayed old command
unable to resurrect a withdrawn approval. `report_approval_decisions` records each
decision command's replay row the same way.

Nothing here deletes or rewrites a decided approval; supersession and withdrawal are
new states with their own events, and history stays readable.
"""

from __future__ import annotations

from uuid import UUID

from appraisal_review.adapters.local.sqlite_publication import ReviewDatabase, _transaction
from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.domain.report_approval import ApprovalStatus, ReportApproval
from appraisal_review.domain.service_contracts import ServiceErrorCode


class SQLiteApprovalStore:
    def __init__(self, review_store: ReviewDatabase) -> None:
        self.database = review_store
        with _transaction(self.database) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS report_approvals ("
                "approval_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, actor_id TEXT NOT NULL, "
                "idempotency_key TEXT NOT NULL, payload_digest TEXT NOT NULL, "
                "status TEXT NOT NULL, binding_digest TEXT NOT NULL, record TEXT NOT NULL, "
                "UNIQUE(job_id, actor_id, idempotency_key))"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS report_approval_decisions ("
                "approval_id TEXT NOT NULL, actor_id TEXT NOT NULL, "
                "idempotency_key TEXT NOT NULL, payload_digest TEXT NOT NULL, "
                "record TEXT NOT NULL, "
                "PRIMARY KEY(approval_id, actor_id, idempotency_key))"
            )
            # Additive migration: every submission key maps to the approval it produced
            # or reused, so a replayed retry always finds its original outcome even
            # after the approval left the live set. Existing rows are never touched.
            connection.execute(
                "CREATE TABLE IF NOT EXISTS report_approval_submissions ("
                "job_id TEXT NOT NULL, actor_id TEXT NOT NULL, "
                "idempotency_key TEXT NOT NULL, approval_id TEXT NOT NULL, "
                "payload_digest TEXT NOT NULL, "
                "PRIMARY KEY(job_id, actor_id, idempotency_key))"
            )

    def create(
        self,
        approval: ReportApproval,
        *,
        actor_id: str,
        idempotency_key: str,
        payload_digest: str,
        binding_digest: str,
    ) -> tuple[ReportApproval, bool]:
        with _transaction(self.database) as connection:
            # Every key ever answered has a durable mapping to the approval it named:
            # a network retry replays the original outcome (whatever its status is
            # now), it never becomes a new request. A changed payload conflicts.
            mapped = connection.execute(
                "SELECT approval_id, payload_digest FROM report_approval_submissions "
                "WHERE job_id=? AND actor_id=? AND idempotency_key=?",
                (str(approval.job_id), actor_id, idempotency_key),
            ).fetchone()
            if mapped is not None:
                if mapped[1] != payload_digest:
                    raise ServiceFault(ServiceErrorCode.CONFLICT)
                original = connection.execute(
                    "SELECT record FROM report_approvals WHERE approval_id=?",
                    (mapped[0],),
                ).fetchone()
                if original is None:
                    raise ServiceFault(ServiceErrorCode.CONFLICT)
                return ReportApproval.model_validate_json(original[0]), False
            row = connection.execute(
                "SELECT payload_digest, record FROM report_approvals "
                "WHERE job_id=? AND actor_id=? AND idempotency_key=?",
                (str(approval.job_id), actor_id, idempotency_key),
            ).fetchone()
            if row is not None:
                if row[0] != payload_digest:
                    raise ServiceFault(ServiceErrorCode.CONFLICT)
                return ReportApproval.model_validate_json(row[1]), False
            # One live request per exact content: a resubmission of the same binding -
            # any key, any reviewer - reuses the existing submitted or approved record
            # instead of inserting a shadow that would mask the live one. The reused
            # key is recorded in the same transaction so its replay stays exact.
            live = connection.execute(
                "SELECT approval_id, record FROM report_approvals "
                "WHERE job_id=? AND binding_digest=? "
                "AND status IN ('submitted','approved') ORDER BY rowid DESC",
                (str(approval.job_id), binding_digest),
            ).fetchone()
            if live is not None:
                connection.execute(
                    "INSERT INTO report_approval_submissions VALUES(?,?,?,?,?)",
                    (
                        str(approval.job_id),
                        actor_id,
                        idempotency_key,
                        live[0],
                        payload_digest,
                    ),
                )
                return ReportApproval.model_validate_json(live[1]), False
            connection.execute(
                "INSERT INTO report_approvals VALUES(?,?,?,?,?,?,?,?)",
                (
                    str(approval.approval_id),
                    str(approval.job_id),
                    actor_id,
                    idempotency_key,
                    payload_digest,
                    approval.status,
                    binding_digest,
                    approval.model_dump_json(),
                ),
            )
            connection.execute(
                "INSERT INTO report_approval_submissions VALUES(?,?,?,?,?)",
                (
                    str(approval.job_id),
                    actor_id,
                    idempotency_key,
                    str(approval.approval_id),
                    payload_digest,
                ),
            )
        return approval, True

    def read(self, job_id: UUID, approval_id: UUID) -> ReportApproval | None:
        connection = self.database._connect()
        try:
            row = connection.execute(
                "SELECT record FROM report_approvals WHERE approval_id=? AND job_id=?",
                (str(approval_id), str(job_id)),
            ).fetchone()
        finally:
            connection.close()
        return None if row is None else ReportApproval.model_validate_json(row[0])

    def latest_for_binding(self, job_id: UUID, binding_digest: str) -> ReportApproval | None:
        """The approval a formal export must cite for this exact content.

        The live record (submitted or approved) wins; only when none exists does the
        newest historical record answer, so a withdrawn approval is reported as
        withdrawn rather than resurrected or shadowed.
        """
        connection = self.database._connect()
        try:
            live = connection.execute(
                "SELECT record FROM report_approvals WHERE job_id=? AND binding_digest=? "
                "AND status IN ('submitted','approved') ORDER BY rowid DESC",
                (str(job_id), binding_digest),
            ).fetchone()
            if live is not None:
                return ReportApproval.model_validate_json(live[0])
            rows = connection.execute(
                "SELECT record FROM report_approvals WHERE job_id=? AND binding_digest=? "
                "ORDER BY rowid DESC",
                (str(job_id), binding_digest),
            ).fetchone()
        finally:
            connection.close()
        return ReportApproval.model_validate_json(rows[0]) if rows else None

    def transition(
        self,
        job_id: UUID,
        approval_id: UUID,
        updated: ReportApproval,
        *,
        expected_status: ApprovalStatus,
        actor_id: str,
        idempotency_key: str,
        payload_digest: str,
    ) -> tuple[ReportApproval, bool]:
        """Commit one decision conditionally; a replayed command returns its own outcome."""
        with _transaction(self.database) as connection:
            replay = connection.execute(
                "SELECT payload_digest, record FROM report_approval_decisions "
                "WHERE approval_id=? AND actor_id=? AND idempotency_key=?",
                (str(approval_id), actor_id, idempotency_key),
            ).fetchone()
            if replay is not None:
                if replay[0] != payload_digest:
                    raise ServiceFault(ServiceErrorCode.CONFLICT)
                return ReportApproval.model_validate_json(replay[1]), False
            changed = connection.execute(
                "UPDATE report_approvals SET status=?, record=? "
                "WHERE approval_id=? AND job_id=? AND status=?",
                (
                    updated.status,
                    updated.model_dump_json(),
                    str(approval_id),
                    str(job_id),
                    expected_status,
                ),
            ).rowcount
            if changed != 1:
                # Someone else decided first, or the approval left the expected state.
                raise ServiceFault(ServiceErrorCode.CONFLICT)
            connection.execute(
                "INSERT INTO report_approval_decisions VALUES(?,?,?,?,?)",
                (
                    str(approval_id),
                    actor_id,
                    idempotency_key,
                    payload_digest,
                    updated.model_dump_json(),
                ),
            )
        return updated, True

    def current_status(self, job_id: UUID, approval_id: UUID) -> ApprovalStatus | None:
        connection = self.database._connect()
        try:
            row = connection.execute(
                "SELECT status FROM report_approvals WHERE approval_id=? AND job_id=?",
                (str(approval_id), str(job_id)),
            ).fetchone()
        finally:
            connection.close()
        return None if row is None else row[0]
