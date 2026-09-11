"""Read-only local originals with explicit grants and exact durable run bindings.

This is not C2 admission: it creates no sanitized export receipt and provides no
cloud upload or HTTP original-download capability. Original browser preview must
use the separately paired local privacy service.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import stat
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass

from appraisal_review.adapters.local.document_manifest import InputManifest
from appraisal_review.adapters.local.integrated_service import LocalDirectory
from appraisal_review.adapters.local.pdf_parser import DocumentInput
from appraisal_review.adapters.local.sqlite_review_store import SQLiteReviewStore
from appraisal_review.application.service_guards import Principal
from appraisal_review.domain.document_transfer import (
    DocumentErrorCode,
    DocumentFault,
    DocumentOperation,
    Purpose,
)
from appraisal_review.domain.service_contracts import (
    DocumentReference,
    MaterialRevision,
    Permission,
    RunReference,
)
from appraisal_review.ports.document_transfer import DocumentAuthorization
from appraisal_review.ports.workflow import ParsedDocument


@dataclass
class LocalOriginalAuthorization:
    """Server-configured local purpose grants, bounded by session expiration."""

    principal: Principal
    purposes: frozenset[Purpose]
    expires_at: float
    clock: Callable[[], float] = time.time
    revoked: bool = False

    def require(
        self, principal: Principal, case_id: str, purpose: Purpose, operation: DocumentOperation
    ) -> None:
        if (
            self.revoked
            or self.clock() >= self.expires_at
            or principal != self.principal
            or principal.actor.kind != "human"
            or case_id not in principal.case_ids
            or Permission.REVIEW not in principal.permissions
            or purpose not in self.purposes
            or operation not in {DocumentOperation.READ, DocumentOperation.SNAPSHOT}
        ):
            raise DocumentFault(DocumentErrorCode.UNAUTHORIZED)


@dataclass(frozen=True)
class LocalOriginalBytes:
    content: bytes
    reference: DocumentReference
    boundary: str = "local-original-v1"


class LocalOriginalDocuments:
    """Configured immutable identities; every read verifies the actual local bytes."""

    authorization: DocumentAuthorization

    def __init__(
        self,
        manifest: InputManifest,
        store: SQLiteReviewStore,
        authorization: LocalOriginalAuthorization,
        *,
        max_pdf_bytes: int = 20 * 1024 * 1024,
    ) -> None:
        self.manifest = InputManifest.model_validate_json(manifest.model_dump_json())
        self.store, self.authorization, self.max_pdf_bytes = store, authorization, max_pdf_bytes
        if max_pdf_bytes <= 0:
            raise ValueError("A positive local document size limit is required")
        self.parser = self.manifest.parser()
        self.specs = {spec.document_id: spec for spec in self.manifest.documents}
        if len(self.specs) != len(self.manifest.documents):
            raise ValueError("Configured source identities must be unique")
        for spec in self.specs.values():
            if (
                not spec.path.is_absolute()
                or spec.path.is_symlink()
                or spec.path.resolve() != spec.path
            ):
                raise ValueError("Configure canonical regular local source paths")
        with closing(store._connect()) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS local_original_runs "
                "(run_id TEXT PRIMARY KEY, actor_id TEXT NOT NULL, revision TEXT NOT NULL)"
            )

    def _reference(self, document_id: str) -> DocumentReference:
        spec = self.specs.get(document_id)
        if spec is None:
            raise DocumentFault(DocumentErrorCode.UNAUTHORIZED)
        return DocumentReference.model_validate(
            {
                "case_id": self.manifest.identity.case_id,
                "document_id": spec.document_id,
                "version": spec.version,
                "content_hash": spec.expected_hash,
                "purpose": spec.role,
            }
        )

    def read(self, principal: Principal, reference: DocumentReference) -> LocalOriginalBytes:
        reference = DocumentReference.model_validate_json(reference.model_dump_json())
        self.authorization.require(
            principal, reference.case_id, reference.purpose, DocumentOperation.READ
        )
        if reference != self._reference(reference.document_id):
            raise DocumentFault(DocumentErrorCode.INTEGRITY)
        spec = self.specs[reference.document_id]
        try:
            if spec.path.resolve() != spec.path:
                raise DocumentFault(DocumentErrorCode.INTEGRITY)
            descriptor = os.open(spec.path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as source:
                if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                    raise DocumentFault(DocumentErrorCode.INVALID)
                content = source.read(self.max_pdf_bytes + 1)
        except OSError:
            raise DocumentFault(DocumentErrorCode.UNAVAILABLE) from None
        if len(content) > self.max_pdf_bytes:
            raise DocumentFault(DocumentErrorCode.TOO_LARGE)
        if not content.startswith(b"%PDF-") or not content.rstrip().endswith(b"%%EOF"):
            raise DocumentFault(DocumentErrorCode.NOT_PDF)
        if hashlib.sha256(content).hexdigest() != reference.content_hash:
            raise DocumentFault(DocumentErrorCode.INTEGRITY)
        self.authorization.require(
            principal, reference.case_id, reference.purpose, DocumentOperation.READ
        )
        return LocalOriginalBytes(content=content, reference=reference)

    def create_snapshot(
        self, principal: Principal, run: RunReference, revision: MaterialRevision
    ) -> MaterialRevision:
        run = RunReference.model_validate_json(run.model_dump_json())
        revision = MaterialRevision.model_validate_json(revision.model_dump_json())
        if run.revision != revision.reference or set(revision.documents) != {
            self._reference(identity) for identity in self.specs
        }:
            raise DocumentFault(DocumentErrorCode.INTEGRITY)
        for reference in revision.documents:
            self.authorization.require(
                principal, reference.case_id, reference.purpose, DocumentOperation.SNAPSHOT
            )
            self.read(principal, reference)
        expected = (principal.actor.actor_id, revision.model_dump_json())
        with closing(self.store._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                previous = connection.execute(
                    "SELECT actor_id,revision FROM local_original_runs WHERE run_id=?",
                    (str(run.run_id),),
                ).fetchone()
                if previous is not None and tuple(previous) != expected:
                    raise DocumentFault(DocumentErrorCode.CONFLICT)
                for reference in revision.documents:
                    self.authorization.require(
                        principal, reference.case_id, reference.purpose, DocumentOperation.SNAPSHOT
                    )
                connection.execute(
                    "INSERT OR IGNORE INTO local_original_runs VALUES(?,?,?)",
                    (str(run.run_id), *expected),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return revision

    def read_snapshot(
        self, principal: Principal, run: RunReference, reference: DocumentReference
    ) -> LocalOriginalBytes:
        with closing(self.store._connect()) as connection:
            row = connection.execute(
                "SELECT actor_id,revision FROM local_original_runs WHERE run_id=?",
                (str(run.run_id),),
            ).fetchone()
        if row is None or row[0] != principal.actor.actor_id:
            raise DocumentFault(DocumentErrorCode.UNAUTHORIZED)
        revision = MaterialRevision.model_validate_json(row[1])
        if revision.reference != run.revision or reference not in revision.documents:
            raise DocumentFault(DocumentErrorCode.INTEGRITY)
        return self.read(principal, reference)


@dataclass
class AuthorizedOriginalParser:
    """Reparse current authorized bytes; never reopen a caller-selected path."""

    documents: LocalOriginalDocuments
    directory: LocalDirectory
    principal: Principal
    run: RunReference

    async def parse_document(self, document_uri: str) -> ParsedDocument:
        matches = [
            spec for spec in self.documents.specs.values() if spec.path.as_uri() == document_uri
        ]
        if len(matches) != 1:
            raise ValueError("Source is not configured for this local run")
        spec = matches[0]
        principal = await self.directory.read(
            self.principal.actor.actor_id, self.run.revision.case_id
        )
        data = await asyncio.to_thread(
            self.documents.read_snapshot,
            principal,
            self.run,
            self.documents._reference(spec.document_id),
        )
        parsed = await asyncio.to_thread(
            self.documents.parser.parse_bytes,
            data.content,
            DocumentInput(**spec.model_dump()),
            uri=document_uri,
        )
        current = await self.directory.read(
            self.principal.actor.actor_id, self.run.revision.case_id
        )
        self.documents.authorization.require(
            current, self.run.revision.case_id, data.reference.purpose, DocumentOperation.READ
        )
        return parsed
