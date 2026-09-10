"""Trusted exact-export signing and real C2 admission; no HTTP or arbitrary file API."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from threading import Lock
from typing import get_args
from uuid import UUID

import pymupdf
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from appraisal_review.adapters.local.document_authority import Ed25519ExportVerifier
from appraisal_review.adapters.local.document_export import ConfirmedDocumentExport
from appraisal_review.application.document_transfer import DocumentTransferService
from appraisal_review.application.service_guards import Principal
from appraisal_review.domain.document_transfer import (
    DocumentErrorCode,
    DocumentFault,
    DocumentMetadata,
    Purpose,
    digest_bytes,
)
from appraisal_review.domain.privacy_export import PrivacyExportPayload
from appraisal_review.domain.privacy_models import PrivacyManifest, public_manifest_json
from appraisal_review.ports.privacy_export import PrivacyExportConfirmation


class CloudExportSink:
    """Server-owned signer and admission/catalog configuration, bound per exact preview.

    Call bind_confirmation with the bridge's live exact-output confirmation port,
    then use that returned channel as BOTH confirmation and sink in the privacy
    gate. A bare payload or serialized yes flag never authorizes this adapter.
    Document-v1 carries PDFs only; optional reviewer text is rejected, not dropped.
    """

    def __init__(
        self,
        ingestion: DocumentTransferService,
        principal: Principal,
        private_key: Ed25519PrivateKey,
        key_id: UUID,
        purposes: Mapping[tuple[UUID, UUID], Purpose],
        *,
        on_admitted: Callable[[DocumentMetadata], None],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._ingestion, self._principal = ingestion, principal
        self._private_key, self._key_id = private_key, key_id
        self._purposes = dict(purposes)
        self._on_admitted, self._clock = on_admitted, clock
        self._lock = Lock()
        self._receipts: list[DocumentMetadata] = []
        if (
            not callable(on_admitted)
            or not self._purposes
            or any(
                not isinstance(case, UUID)
                or case.version != 4
                or not isinstance(document, UUID)
                or document.version != 4
                or purpose not in get_args(Purpose)
                for (case, document), purpose in self._purposes.items()
            )
        ):
            raise DocumentFault(DocumentErrorCode.INVALID)
        self._check_key()

    @property
    def receipts(self) -> tuple[DocumentMetadata, ...]:
        """Committed C2 metadata for local reconciliation, even if catalog callback failed."""
        with self._lock:
            return tuple(self._receipts)

    def _check_key(self) -> None:
        try:
            verifier = self._ingestion.verifier
            if not isinstance(verifier, Ed25519ExportVerifier):
                raise ValueError("Pinned Ed25519 verifier required")
            key = verifier.keys.get(self._key_id)
            cases = {case for case, _ in self._purposes}
            if (
                key is None
                or self._principal.actor.kind != "human"
                or str(key.actor_id) != self._principal.actor.actor_id
                or not cases <= key.case_ids
                or not {str(case) for case in cases} <= self._principal.case_ids
                or key.public_key.public_bytes_raw()
                != self._private_key.public_key().public_bytes_raw()
            ):
                raise ValueError("Export key configuration differs")
        except Exception:
            raise DocumentFault(DocumentErrorCode.PRIVACY) from None

    def bind_confirmation(self, confirmation: PrivacyExportConfirmation) -> _BoundCloudExport:
        """Bind one live presenter; this is trusted composition, never request input."""
        self._check_key()
        return _BoundCloudExport(self, confirmation)

    def _purpose(self, payload: PrivacyExportPayload) -> Purpose:
        try:
            if (
                type(payload) is not PrivacyExportPayload
                or type(payload.pdf) is not bytes
                or payload.filename != "sanitized.pdf"
                or payload.reviewer_text is not None
            ):
                raise ValueError("Unsupported document payload")
            manifest = PrivacyManifest.model_validate_json(payload.manifest_json)
            if (
                payload.manifest_json != public_manifest_json(manifest)
                or len(payload.pdf) != manifest.byte_size
                or digest_bytes(payload.pdf) != manifest.sanitized_digest
                or len(payload.pdf) > self._ingestion.max_pdf_bytes
            ):
                raise ValueError("Exact bytes differ")
            purpose = self._purposes.get((manifest.case_id, manifest.document_id))
            if purpose is None:
                raise ValueError("Unknown source purpose")
            # Parse the actual immutable bytes before signing. The privacy verifier,
            # not this parser, proves redaction/content-surface requirements.
            with pymupdf.open(stream=payload.pdf, filetype="pdf") as pdf:  # type: ignore[no-untyped-call]
                if pdf.is_encrypted or pdf.needs_pass or len(pdf) != len(manifest.pages):
                    raise ValueError("Unsupported PDF")
                for actual, expected in zip(pdf, manifest.pages, strict=True):
                    if (
                        actual.rotation != 0
                        or abs(actual.rect.width - expected.width) > 0.001
                        or abs(actual.rect.height - expected.height) > 0.001
                    ):
                        raise ValueError("PDF page identity differs")
            return purpose
        except Exception:
            raise DocumentFault(DocumentErrorCode.PRIVACY) from None

    def _record(self, receipt: DocumentMetadata, payload: PrivacyExportPayload) -> None:
        try:
            checked = DocumentMetadata.model_validate_json(receipt.model_dump_json())
            if checked.attestation.claims.manifest != PrivacyManifest.model_validate_json(
                payload.manifest_json
            ):
                raise ValueError("Admission receipt differs")
            # Preserve the known committed identity even if readback/callback later fails.
            with self._lock:
                self._receipts.append(checked)
            stored = self._ingestion.read(self._principal, checked.reference)
            if stored.metadata != checked or stored.content != payload.pdf:
                raise ValueError("Admitted bytes differ")
            self._on_admitted(checked)
        except DocumentFault:
            raise
        except Exception:
            raise DocumentFault(DocumentErrorCode.UNAVAILABLE) from None


class _BoundCloudExport:
    def __init__(self, owner: CloudExportSink, confirmation: PrivacyExportConfirmation) -> None:
        self._owner, self._confirmation = owner, confirmation
        self._delegate: ConfirmedDocumentExport | None = None
        self._attempted = False

    def confirm(self, payload: PrivacyExportPayload) -> bool:
        if self._delegate is not None or self._attempted:
            raise DocumentFault(DocumentErrorCode.CONFLICT)
        owner = self._owner
        owner._check_key()
        purpose = owner._purpose(payload)
        self._delegate = ConfirmedDocumentExport(
            self._confirmation,
            owner._ingestion,
            owner._principal,
            purpose,
            owner._private_key,
            owner._key_id,
            clock=owner._clock,
        )
        return self._delegate.confirm(payload)

    def accept(self, payload: PrivacyExportPayload) -> None:
        if self._delegate is None or self._attempted:
            raise DocumentFault(DocumentErrorCode.PRIVACY)
        self._attempted = True
        self._owner._check_key()
        self._owner._purpose(payload)
        self._delegate.accept(payload)
        if self._delegate.result is None:
            raise DocumentFault(DocumentErrorCode.UNAVAILABLE)
        self._owner._record(self._delegate.result, payload)
