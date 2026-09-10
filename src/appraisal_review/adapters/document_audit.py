"""Append minimal document events to the same immutable storage boundary."""

from appraisal_review.domain.document_transfer import (
    DocumentAuditEvent,
    ObjectKey,
    ObjectLabels,
    canonical_bytes,
    digest_bytes,
)
from appraisal_review.ports.document_transfer import ImmutableDocumentStorage


class ImmutableDocumentAudit:
    def __init__(self, storage: ImmutableDocumentStorage) -> None:
        self.storage = storage

    def append(self, event: DocumentAuditEvent) -> None:
        event = DocumentAuditEvent.model_validate_json(event.model_dump_json())
        content = canonical_bytes(event)
        self.storage.create(
            ObjectKey(kind="audit", scope=event.case_id, identity=event.event_id),
            content,
            ObjectLabels(
                case_id=event.case_id,
                uploader=event.actor_id,
                created_at=event.occurred_at,
                content_hash=digest_bytes(content),
                byte_size=len(content),
                content_type="application/json",
                purpose="catalog",
            ),
        )
