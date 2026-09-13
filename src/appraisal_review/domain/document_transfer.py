"""Authorized sanitized documents and immutable run inputs, document-v1."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import UUID4, AfterValidator, AwareDatetime, Field, model_validator

from appraisal_review.domain.privacy_models import Digest, PrivacyManifest, PrivacyModel
from appraisal_review.domain.service_contracts import DocumentReference, RevisionReference


def _server_actor_uuid(value: UUID) -> UUID:
    """Principal identities are server-minted: random v4 (fixtures, operators) or
    deterministic v5 (the email-login directory's per-mailbox actor). Object and
    run identifiers elsewhere stay strictly v4."""
    if value.version not in (4, 5):
        raise ValueError("Actor ids are server-minted uuid4 or uuid5")
    return value


ActorUUID = Annotated[UUID, AfterValidator(_server_actor_uuid)]

Purpose = Literal["criteria", "forms", "reference", "brief", "template"]


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_bytes(model: PrivacyModel) -> bytes:
    return json.dumps(
        model.model_dump(mode="json", warnings="error"), sort_keys=True, separators=(",", ":")
    ).encode()


class DocumentErrorCode(StrEnum):
    INVALID = "document_invalid_request"
    UNAUTHORIZED = "document_unauthorized"
    PRIVACY = "document_privacy_attestation_invalid"
    TOO_LARGE = "document_too_large"
    NOT_PDF = "document_not_pdf"
    NOT_FOUND = "document_not_found"
    CONFLICT = "document_version_conflict"
    INTEGRITY = "document_integrity_failure"
    UNAVAILABLE = "document_capability_unavailable"


class DocumentProblem(PrivacyModel):
    schema_version: Literal["document-v1"] = "document-v1"
    code: DocumentErrorCode
    message: Literal["Document operation could not be completed."] = (
        "Document operation could not be completed."
    )


class DocumentFault(Exception):
    """Only this finite public diagnostic crosses the service boundary."""

    def __init__(self, code: DocumentErrorCode) -> None:
        self.problem = DocumentProblem(code=code)
        super().__init__(code.value)


class ExportClaims(PrivacyModel):
    """Signed by an operator-provisioned local export gate after exact human consent."""

    schema_version: Literal["document-export-v1"] = "document-export-v1"
    key_id: UUID4
    export_id: UUID4
    principal_id: ActorUUID
    manifest: PrivacyManifest
    purpose: Purpose
    confirmed_at: AwareDatetime
    expires_at: AwareDatetime
    previous: DocumentReference | None = None

    @model_validator(mode="after")
    def binding(self) -> ExportClaims:
        if self.expires_at <= self.confirmed_at:
            raise ValueError("Invalid export validity window")
        if self.previous is not None and (
            self.previous.case_id != str(self.manifest.case_id)
            or self.previous.purpose != self.purpose
        ):
            raise ValueError("Export predecessor binding differs")
        return self


class PrivacyAttestation(PrivacyModel):
    claims: ExportClaims
    algorithm: Literal["Ed25519"] = "Ed25519"
    signature_hex: Annotated[str, Field(pattern=r"^[a-f0-9]{128}$", repr=False)]


class DocumentMetadata(PrivacyModel):
    schema_version: Literal["document-v1"] = "document-v1"
    reference: DocumentReference
    attestation: PrivacyAttestation
    uploaded_by: ActorUUID
    created_at: AwareDatetime
    classification: Literal["sanitized"] = "sanitized"
    content_type: Literal["application/pdf"] = "application/pdf"
    byte_size: int = Field(gt=0)

    @model_validator(mode="after")
    def binding(self) -> DocumentMetadata:
        claims = self.attestation.claims
        if (
            self.reference.case_id != str(claims.manifest.case_id)
            or self.reference.content_hash != claims.manifest.sanitized_digest
            or self.reference.purpose != claims.purpose
            or self.uploaded_by != claims.principal_id
            or self.byte_size != claims.manifest.byte_size
        ):
            raise ValueError("Document metadata binding differs")
        return self


class ObjectKey(PrivacyModel):
    """Internal service-issued key components, never a URI or bucket selection."""

    kind: Literal["content", "documents", "snapshots", "exports", "audit"]
    scope: UUID4
    identity: UUID4

    def relative_key(self) -> str:
        return f"{self.kind}/{self.scope}/{self.identity}"


class StoredDocument(PrivacyModel):
    metadata: DocumentMetadata
    storage_version: str = Field(min_length=1, max_length=1024, repr=False)


class DocumentAllocation(PrivacyModel):
    document_id: UUID4
    version: UUID4
    attestation_digest: Digest
    created_at: AwareDatetime


@dataclass(frozen=True)
class StoredBytes:
    content: bytes = field(repr=False)
    version: str = field(repr=False)


class SnapshotEntry(PrivacyModel):
    document: DocumentReference
    privacy_manifest_digest: Digest
    stored_document_digest: Digest
    page_count: int = Field(gt=0)


class RunSourceSnapshot(PrivacyModel):
    schema_version: Literal["document-snapshot-v1"] = "document-snapshot-v1"
    run_id: UUID4
    revision: RevisionReference
    created_at: AwareDatetime
    created_by: ActorUUID
    documents: tuple[SnapshotEntry, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def binding(self) -> RunSourceSnapshot:
        if any(d.document.case_id != self.revision.case_id for d in self.documents):
            raise ValueError("Snapshot document case differs")
        if len({d.document.document_id for d in self.documents}) != len(self.documents):
            raise ValueError("Duplicate snapshot document")
        return self


@dataclass(frozen=True)
class AuthorizedDocumentBytes:
    """Detached exact sanitized bytes; no storage location or original material."""

    metadata: DocumentMetadata
    content: bytes = field(repr=False)


class DocumentOperation(StrEnum):
    INGEST = "ingest"
    SNAPSHOT = "snapshot"
    READ = "read"


class DocumentAuditEvent(PrivacyModel):
    event_id: UUID4
    occurred_at: AwareDatetime
    actor_id: ActorUUID
    case_id: UUID4
    operation: DocumentOperation
    outcome: Literal["succeeded", "rejected"]
    code: DocumentErrorCode | None = None
    document_id: UUID | None = None
    run_id: UUID | None = None


class ObjectLabels(PrivacyModel):
    """Exact allowlist for object metadata; no names, addresses or custom labels."""

    case_id: UUID4
    uploader: ActorUUID
    created_at: AwareDatetime
    content_hash: Digest
    byte_size: int = Field(gt=0)
    classification: Literal["sanitized"] = "sanitized"
    content_type: Literal["application/pdf", "application/json"]
    purpose: Purpose | Literal["snapshot", "catalog"]

    def headers(self) -> dict[str, str]:
        return {k.replace("_", "-"): str(v) for k, v in self.model_dump(mode="json").items()}
