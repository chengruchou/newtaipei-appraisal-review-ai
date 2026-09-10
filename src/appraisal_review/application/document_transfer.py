"""Controlled sanitized ingestion and durable, immutable run-source binding."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TypeVar
from uuid import UUID, uuid4

from pydantic import BaseModel

from appraisal_review.application.service_guards import Principal
from appraisal_review.domain.document_transfer import (
    AuthorizedDocumentBytes,
    DocumentAllocation,
    DocumentAuditEvent,
    DocumentErrorCode,
    DocumentFault,
    DocumentMetadata,
    DocumentOperation,
    ObjectKey,
    ObjectLabels,
    PrivacyAttestation,
    Purpose,
    RunSourceSnapshot,
    SnapshotEntry,
    StoredDocument,
    canonical_bytes,
    digest_bytes,
)
from appraisal_review.domain.service_contracts import (
    DocumentReference,
    MaterialRevision,
    RunReference,
)
from appraisal_review.ports.document_transfer import (
    DocumentAudit,
    DocumentAuthorization,
    ExportAttestationVerifier,
    ImmutableDocumentStorage,
)

CATALOG_LIMIT = 2 * 1024 * 1024
BoundaryModel = TypeVar("BoundaryModel", bound=BaseModel)


def checked_boundary(
    model: type[BoundaryModel],
    value: BoundaryModel,
    code: DocumentErrorCode = DocumentErrorCode.INVALID,
) -> BoundaryModel:
    try:
        if type(value) is not model:
            raise ValueError("Unexpected boundary type")
        return model.model_validate_json(value.model_dump_json(warnings="error"))
    except Exception:
        raise DocumentFault(code) from None


def opaque_uuid(value: str) -> UUID:
    parsed = UUID(value)
    if parsed.version != 4 or str(parsed) != value:
        raise DocumentFault(DocumentErrorCode.INVALID)
    return parsed


class DocumentTransferService:
    """No URI entry point. Auth adapters supply Principal and explicit document grants.

    This synchronous application port is for a trusted upload gateway/worker.
    Async hosts must dispatch blocking storage I/O outside their event loop.
    """

    def __init__(
        self,
        storage: ImmutableDocumentStorage,
        authorization: DocumentAuthorization,
        verifier: ExportAttestationVerifier,
        audit: DocumentAudit,
        *,
        max_pdf_bytes: int = 20 * 1024 * 1024,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if max_pdf_bytes < 1:
            raise ValueError("A positive PDF size bound is required")
        self.storage = storage
        self.authorization = authorization
        self.verifier = verifier
        self.audit = audit
        self.max_pdf_bytes = max_pdf_bytes
        self.clock = clock

    @contextmanager
    def _operation(
        self,
        principal: Principal,
        case_id: str,
        operation: DocumentOperation,
        *,
        document_id: str | None = None,
        run_id: UUID | None = None,
    ) -> Iterator[None]:
        try:
            actor = opaque_uuid(principal.actor.actor_id)
            case = opaque_uuid(case_id)
        except (ValueError, DocumentFault):
            raise DocumentFault(DocumentErrorCode.INVALID) from None
        code = None
        try:
            yield
        except DocumentFault as error:
            code = error.problem.code
            raise
        except Exception:
            code = DocumentErrorCode.UNAVAILABLE
            raise DocumentFault(code) from None
        finally:
            try:
                self.audit.append(
                    DocumentAuditEvent(
                        event_id=uuid4(),
                        occurred_at=self.clock(),
                        actor_id=actor,
                        case_id=case,
                        operation=operation,
                        outcome="succeeded" if code is None else "rejected",
                        code=code,
                        document_id=UUID(document_id) if document_id else None,
                        run_id=run_id,
                    )
                )
            except Exception:
                raise DocumentFault(DocumentErrorCode.UNAVAILABLE) from None

    def _require(
        self, principal: Principal, case_id: str, purpose: Purpose, operation: DocumentOperation
    ) -> None:
        self.authorization.require(principal, case_id, purpose, operation)

    def _labels(
        self,
        principal: Principal,
        case_id: str,
        content: bytes,
        purpose: Purpose | str = "catalog",
    ) -> ObjectLabels:
        # JSON validation rejects any unexpected purpose rather than reflecting it.
        return ObjectLabels.model_validate_json(
            json.dumps(
                {
                    "case_id": case_id,
                    "uploader": principal.actor.actor_id,
                    "created_at": self.clock().isoformat(),
                    "content_hash": digest_bytes(content),
                    "byte_size": len(content),
                    "purpose": purpose,
                    "content_type": "application/json"
                    if purpose in {"catalog", "snapshot"}
                    else "application/pdf",
                }
            )
        )

    def _create_once(self, key: ObjectKey, content: bytes, labels: ObjectLabels) -> str:
        try:
            return self.storage.create(key, content, labels)
        except DocumentFault as error:
            if error.problem.code != DocumentErrorCode.CONFLICT:
                raise
        existing = self.storage.read(key, version=None, limit=max(len(content), 1))
        if existing.content != content:
            raise DocumentFault(DocumentErrorCode.CONFLICT)
        return existing.version

    def ingest(
        self, principal: Principal, content: bytes, attestation: PrivacyAttestation
    ) -> DocumentMetadata:
        """Admit only signed exact sanitized output; replay returns the same service IDs."""
        attestation = checked_boundary(PrivacyAttestation, attestation, DocumentErrorCode.PRIVACY)
        case_id = str(attestation.claims.manifest.case_id)
        with self._operation(principal, case_id, DocumentOperation.INGEST):
            if type(content) is not bytes or type(attestation) is not PrivacyAttestation:
                raise DocumentFault(DocumentErrorCode.INVALID)
            claims = attestation.claims
            self._require(principal, case_id, claims.purpose, DocumentOperation.INGEST)
            self.verifier.verify(attestation, principal)
            if len(canonical_bytes(attestation)) > CATALOG_LIMIT // 2:
                raise DocumentFault(DocumentErrorCode.TOO_LARGE)
            if len(content) > self.max_pdf_bytes:
                raise DocumentFault(DocumentErrorCode.TOO_LARGE)
            if (
                len(content) != claims.manifest.byte_size
                or digest_bytes(content) != claims.manifest.sanitized_digest
            ):
                raise DocumentFault(DocumentErrorCode.INTEGRITY)
            if not content.startswith(b"%PDF-") or not content.rstrip().endswith(b"%%EOF"):
                raise DocumentFault(DocumentErrorCode.NOT_PDF)
            if claims.previous is not None:
                self._resolve(principal, claims.previous, DocumentOperation.INGEST)
            allocation_key = ObjectKey(
                kind="exports", scope=claims.manifest.case_id, identity=claims.export_id
            )
            allocation = DocumentAllocation(
                document_id=opaque_uuid(claims.previous.document_id)
                if claims.previous
                else uuid4(),
                version=uuid4(),
                attestation_digest=digest_bytes(canonical_bytes(attestation)),
                created_at=self.clock(),
            )
            encoded = canonical_bytes(allocation)
            try:
                self.storage.create(
                    allocation_key, encoded, self._labels(principal, case_id, encoded)
                )
            except DocumentFault as error:
                if error.problem.code != DocumentErrorCode.CONFLICT:
                    raise
                allocation = DocumentAllocation.model_validate_json(
                    self.storage.read(allocation_key, version=None, limit=CATALOG_LIMIT).content
                )
                if allocation.attestation_digest != digest_bytes(canonical_bytes(attestation)):
                    raise DocumentFault(DocumentErrorCode.CONFLICT) from None
            reference = DocumentReference(
                case_id=case_id,
                document_id=str(allocation.document_id),
                version=str(allocation.version),
                content_hash=digest_bytes(content),
                purpose=claims.purpose,
            )
            metadata = DocumentMetadata(
                reference=reference,
                attestation=attestation,
                uploaded_by=opaque_uuid(principal.actor.actor_id),
                created_at=allocation.created_at,
                byte_size=len(content),
            )
            content_key = self._key(reference, "content")
            version = self._create_once(
                content_key, content, self._labels(principal, case_id, content, claims.purpose)
            )
            stored = canonical_bytes(StoredDocument(metadata=metadata, storage_version=version))
            self._create_once(
                self._key(reference, "documents"), stored, self._labels(principal, case_id, stored)
            )
            return metadata

    @staticmethod
    def _key(reference: DocumentReference, kind: str) -> ObjectKey:
        return ObjectKey.model_validate_json(
            json.dumps(
                {
                    "kind": kind,
                    "scope": str(opaque_uuid(reference.document_id)),
                    "identity": str(opaque_uuid(reference.version)),
                }
            )
        )

    def _resolve(
        self, principal: Principal, reference: DocumentReference, operation: DocumentOperation
    ) -> StoredDocument:
        try:
            reference = DocumentReference.model_validate_json(reference.model_dump_json())
            self._key(reference, "documents")
        except (ValueError, DocumentFault):
            raise DocumentFault(DocumentErrorCode.INVALID) from None
        self._require(principal, reference.case_id, reference.purpose, operation)
        stored = StoredDocument.model_validate_json(
            self.storage.read(
                self._key(reference, "documents"), version=None, limit=CATALOG_LIMIT
            ).content
        )
        if stored.metadata.reference != reference:
            raise DocumentFault(DocumentErrorCode.INTEGRITY)
        return stored

    def _read(self, stored: StoredDocument) -> AuthorizedDocumentBytes:
        metadata = stored.metadata
        result = self.storage.read(
            self._key(metadata.reference, "content"),
            version=stored.storage_version,
            limit=self.max_pdf_bytes,
        )
        if (
            result.version != stored.storage_version
            or len(result.content) != metadata.byte_size
            or digest_bytes(result.content) != metadata.reference.content_hash
        ):
            raise DocumentFault(DocumentErrorCode.INTEGRITY)
        return AuthorizedDocumentBytes(metadata=metadata, content=result.content)

    def read(self, principal: Principal, reference: DocumentReference) -> AuthorizedDocumentBytes:
        """Authorized pre-run access. Running workers must instead use read_snapshot()."""
        reference = checked_boundary(DocumentReference, reference)
        # Invalid external identities are rejected without recording their raw text.
        try:
            document_id = str(opaque_uuid(reference.document_id))
        except (ValueError, DocumentFault):
            document_id = None
        with self._operation(
            principal, reference.case_id, DocumentOperation.READ, document_id=document_id
        ):
            return self._read(self._resolve(principal, reference, DocumentOperation.READ))

    def create_snapshot(
        self, principal: Principal, run: RunReference, revision: MaterialRevision
    ) -> RunSourceSnapshot:
        """Pin every document of the exact admitted material before job/outbox admission."""
        run = checked_boundary(RunReference, run)
        revision = checked_boundary(MaterialRevision, revision)
        with self._operation(
            principal, run.revision.case_id, DocumentOperation.SNAPSHOT, run_id=run.run_id
        ):
            try:
                opaque_uuid(run.revision.revision_id)
            except (ValueError, DocumentFault):
                raise DocumentFault(DocumentErrorCode.INVALID) from None
            if run.revision != revision.reference or run.run_id.version != 4:
                raise DocumentFault(DocumentErrorCode.CONFLICT)
            entries = []
            for reference in sorted(revision.documents, key=lambda d: d.document_id):
                stored = self._resolve(principal, reference, DocumentOperation.SNAPSHOT)
                # Verify actual source availability and digest before exposing a usable snapshot.
                self._read(stored)
                metadata = stored.metadata
                manifest = metadata.attestation.claims.manifest
                entries.append(
                    SnapshotEntry(
                        document=reference,
                        privacy_manifest_digest=digest_bytes(canonical_bytes(manifest)),
                        stored_document_digest=digest_bytes(canonical_bytes(stored)),
                        page_count=len(manifest.pages),
                    )
                )
            snapshot = RunSourceSnapshot(
                run_id=run.run_id,
                revision=run.revision,
                created_at=self.clock(),
                created_by=opaque_uuid(principal.actor.actor_id),
                documents=tuple(entries),
            )
            key = ObjectKey(
                kind="snapshots", scope=opaque_uuid(run.revision.case_id), identity=run.run_id
            )
            data = canonical_bytes(snapshot)
            try:
                self.storage.create(
                    key, data, self._labels(principal, run.revision.case_id, data, "snapshot")
                )
            except DocumentFault as error:
                if error.problem.code != DocumentErrorCode.CONFLICT:
                    raise
                existing = RunSourceSnapshot.model_validate_json(
                    self.storage.read(key, version=None, limit=CATALOG_LIMIT).content
                )
                if (
                    existing.run_id != snapshot.run_id
                    or existing.revision != snapshot.revision
                    or existing.documents != snapshot.documents
                    or existing.created_by != snapshot.created_by
                ):
                    raise DocumentFault(DocumentErrorCode.CONFLICT) from None
                return existing
            return snapshot

    def read_snapshot(
        self, principal: Principal, run: RunReference, reference: DocumentReference
    ) -> AuthorizedDocumentBytes:
        run = checked_boundary(RunReference, run)
        reference = checked_boundary(DocumentReference, reference)
        with self._operation(
            principal, run.revision.case_id, DocumentOperation.READ, run_id=run.run_id
        ):
            if run.revision.case_id != reference.case_id:
                raise DocumentFault(DocumentErrorCode.UNAUTHORIZED)
            self._require(principal, reference.case_id, reference.purpose, DocumentOperation.READ)
            key = ObjectKey(
                kind="snapshots", scope=opaque_uuid(reference.case_id), identity=run.run_id
            )
            snapshot = RunSourceSnapshot.model_validate_json(
                self.storage.read(key, version=None, limit=CATALOG_LIMIT).content
            )
            if snapshot.run_id != run.run_id or snapshot.revision != run.revision:
                raise DocumentFault(DocumentErrorCode.CONFLICT)
            entries = [e for e in snapshot.documents if e.document == reference]
            if len(entries) != 1:
                raise DocumentFault(DocumentErrorCode.UNAUTHORIZED)
            stored = self._resolve(principal, reference, DocumentOperation.READ)
            if digest_bytes(canonical_bytes(stored)) != entries[0].stored_document_digest:
                raise DocumentFault(DocumentErrorCode.INTEGRITY)
            return self._read(stored)
