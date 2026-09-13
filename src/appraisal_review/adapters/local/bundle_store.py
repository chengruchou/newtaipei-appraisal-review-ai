"""Durable parent download requests, replayed exactly like the other stores."""

from __future__ import annotations

from uuid import UUID

from appraisal_review.adapters.local.sqlite_publication import ReviewDatabase, _transaction
from appraisal_review.application.export_bundles import BundleRecord
from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.domain.service_contracts import ServiceErrorCode


class SQLiteBundleStore:
    def __init__(self, review_store: ReviewDatabase) -> None:
        self.database = review_store
        with _transaction(self.database) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS export_bundles ("
                "bundle_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, actor_id TEXT NOT NULL, "
                "idempotency_key TEXT NOT NULL, payload_digest TEXT NOT NULL, "
                "record TEXT NOT NULL, "
                "UNIQUE(job_id, actor_id, idempotency_key))"
            )

    def create(
        self,
        record: BundleRecord,
        *,
        actor_id: str,
        idempotency_key: str,
        payload_digest: str,
    ) -> tuple[BundleRecord, bool]:
        with _transaction(self.database) as connection:
            row = connection.execute(
                "SELECT payload_digest, record FROM export_bundles "
                "WHERE job_id=? AND actor_id=? AND idempotency_key=?",
                (str(record.job_id), actor_id, idempotency_key),
            ).fetchone()
            if row is not None:
                if row[0] != payload_digest:
                    raise ServiceFault(ServiceErrorCode.CONFLICT)
                return BundleRecord.model_validate_json(row[1]), False
            connection.execute(
                "INSERT INTO export_bundles VALUES(?,?,?,?,?,?)",
                (
                    str(record.bundle_id),
                    str(record.job_id),
                    actor_id,
                    idempotency_key,
                    payload_digest,
                    record.model_dump_json(),
                ),
            )
        return record, True

    def read(self, job_id: UUID, bundle_id: UUID) -> BundleRecord | None:
        connection = self.database._connect()
        try:
            row = connection.execute(
                "SELECT record FROM export_bundles WHERE bundle_id=? AND job_id=?",
                (str(bundle_id), str(job_id)),
            ).fetchone()
        finally:
            connection.close()
        return None if row is None else BundleRecord.model_validate_json(row[0])
