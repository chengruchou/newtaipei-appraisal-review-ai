"""A3 confirmation/sink pair for the controlled C2 ingestion port.

Only compose this pair inside LocalPrivacyExportGate. A PrivacyExportPayload is
not itself proof of sanitization. The gate still owns verification and admission.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Protocol
from uuid import UUID, uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from appraisal_review.application.service_guards import Principal
from appraisal_review.domain.document_transfer import (
    DocumentErrorCode,
    DocumentFault,
    DocumentMetadata,
    ExportClaims,
    PrivacyAttestation,
    Purpose,
    canonical_bytes,
    digest_bytes,
)
from appraisal_review.domain.privacy_export import PrivacyExportPayload
from appraisal_review.domain.privacy_models import PrivacyManifest
from appraisal_review.domain.service_contracts import DocumentReference
from appraisal_review.ports.privacy_export import PrivacyExportConfirmation


class ControlledDocumentIngestion(Protocol):
    def ingest(
        self, principal: Principal, content: bytes, attestation: PrivacyAttestation
    ) -> DocumentMetadata: ...


class ConfirmedDocumentExport:
    """Injected real human presenter + local signing key; no serialized yes/approval flag.

    Use this same instance as both confirmation and sink for the A3 export gate.
    Keys are provisioned outside this service and are never persisted or logged here.
    """

    def __init__(
        self,
        confirmation: PrivacyExportConfirmation,
        ingestion: ControlledDocumentIngestion,
        principal: Principal,
        purpose: Purpose,
        private_key: Ed25519PrivateKey,
        key_id: UUID,
        *,
        previous: DocumentReference | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.confirmation = confirmation
        self.ingestion = ingestion
        self.principal = principal
        self.purpose = purpose
        self._private_key = private_key
        self.key_id = key_id
        self.previous = previous
        self.clock = clock
        self._lock = Lock()
        self._pending: tuple[PrivacyExportPayload, PrivacyAttestation] | None = None
        self.result: DocumentMetadata | None = None

    def confirm(self, payload: PrivacyExportPayload) -> bool:
        with self._lock:
            self._pending = None
            self.result = None
            try:
                if (
                    type(payload) is not PrivacyExportPayload
                    or type(payload.pdf) is not bytes
                    or payload.filename != "sanitized.pdf"
                    or payload.reviewer_text is not None
                ):
                    raise DocumentFault(DocumentErrorCode.INVALID)
                manifest = PrivacyManifest.model_validate_json(payload.manifest_json)
                if (
                    manifest.byte_size != len(payload.pdf)
                    or manifest.sanitized_digest != digest_bytes(payload.pdf)
                    or self.principal.actor.kind != "human"
                ):
                    raise DocumentFault(DocumentErrorCode.PRIVACY)
                if self.confirmation.confirm(payload) is not True:
                    return False
                # Recheck after the presenter; even object.__setattr__ cannot substitute bytes.
                if (
                    PrivacyManifest.model_validate_json(payload.manifest_json) != manifest
                    or digest_bytes(payload.pdf) != manifest.sanitized_digest
                    or payload.reviewer_text is not None
                ):
                    raise DocumentFault(DocumentErrorCode.PRIVACY)
                now = self.clock()
                claims = ExportClaims(
                    key_id=self.key_id,
                    export_id=uuid4(),
                    principal_id=UUID(self.principal.actor.actor_id),
                    manifest=manifest,
                    purpose=self.purpose,
                    confirmed_at=now,
                    expires_at=now + timedelta(minutes=10),
                    previous=self.previous,
                )
                attestation = PrivacyAttestation(
                    claims=claims,
                    signature_hex=self._private_key.sign(canonical_bytes(claims)).hex(),
                )
                self._pending = (payload, attestation)
                return True
            except DocumentFault:
                raise
            except Exception:
                raise DocumentFault(DocumentErrorCode.PRIVACY) from None

    def accept(self, payload: PrivacyExportPayload) -> None:
        with self._lock:
            pending, self._pending = self._pending, None
            if pending is None or pending[0] is not payload:
                raise DocumentFault(DocumentErrorCode.PRIVACY)
            try:
                attestation = pending[1]
                if (
                    PrivacyManifest.model_validate_json(payload.manifest_json)
                    != attestation.claims.manifest
                    or payload.reviewer_text is not None
                    or payload.filename != "sanitized.pdf"
                ):
                    raise DocumentFault(DocumentErrorCode.PRIVACY)
                # Validate before calling any transport: server rejection would be too late
                # if a substituted carrier contained original local content.
                if (
                    type(payload.pdf) is not bytes
                    or len(payload.pdf) != attestation.claims.manifest.byte_size
                    or digest_bytes(payload.pdf) != attestation.claims.manifest.sanitized_digest
                ):
                    raise DocumentFault(DocumentErrorCode.INTEGRITY)
                self.result = self.ingestion.ingest(self.principal, payload.pdf, attestation)
            except DocumentFault:
                raise
            except Exception:
                raise DocumentFault(DocumentErrorCode.UNAVAILABLE) from None
