"""C2 service boundaries; implementations receive trusted composition, not request paths."""

from typing import Protocol

from appraisal_review.application.service_guards import Principal
from appraisal_review.domain.document_transfer import (
    DocumentAuditEvent,
    DocumentOperation,
    ObjectKey,
    ObjectLabels,
    PrivacyAttestation,
    Purpose,
    StoredBytes,
)


class ImmutableDocumentStorage(Protocol):
    def create(self, key: ObjectKey, content: bytes, labels: ObjectLabels) -> str:
        """Atomically create once, return non-null storage version; conflicts never overwrite."""
        ...

    def read(self, key: ObjectKey, *, version: str | None, limit: int) -> StoredBytes:
        """Bound the read; pin exact version for content. Catalog keys are create-only."""
        ...


class DocumentAuthorization(Protocol):
    def require(
        self, principal: Principal, case_id: str, purpose: Purpose, operation: DocumentOperation
    ) -> None: ...


class ExportAttestationVerifier(Protocol):
    def verify(self, attestation: PrivacyAttestation, principal: Principal) -> None:
        """Check trusted key scope, signature, exact identity and bounded validity window."""
        ...


class DocumentAudit(Protocol):
    def append(self, event: DocumentAuditEvent) -> None:
        """Persist minimal audit before returning a read. Failure must propagate."""
        ...
