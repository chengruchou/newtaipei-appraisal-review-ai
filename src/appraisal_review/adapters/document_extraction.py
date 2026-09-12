"""C2 immutable run sources to the existing A2 parser and extraction boundary."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Literal, cast

from appraisal_review.adapters.local.pdf_parser import DocumentInput, LocalPDFParser, _pdf_worker
from appraisal_review.application.document_transfer import DocumentTransferService
from appraisal_review.application.service_guards import Principal
from appraisal_review.domain.document_transfer import (
    DocumentErrorCode,
    DocumentFault,
    canonical_bytes,
    digest_bytes,
)
from appraisal_review.domain.extraction_contracts import PageRequest
from appraisal_review.ports.document_extraction import (
    AuthorizedSanitizedSnapshot,
    ExtractionBoundaryError,
)

PARSER_VERSION = "native-snapshot-v1"


def parse_source(content: bytes, request: PageRequest, max_bytes: int, max_pages: int) -> bytes:
    document = request.source.document
    # The legacy path carrier is unused by parse_bytes; no temporary source is created.
    spec = DocumentInput(
        path=Path("."),
        document_id=document.document_id,
        version=document.version,
        role=cast(Literal["criteria", "forms", "reference", "brief"], document.purpose),
        expected_hash=document.content_hash,
    )
    parsed = LocalPDFParser([], max_bytes=max_bytes, max_pages=max_pages).parse_bytes(
        content, spec, uri=f"urn:document:{document.document_id}:{document.version}"
    )
    if parsed.source is None:
        raise ValueError("Parser produced no registry")
    return parsed.source.model_dump_json().encode()


class DocumentSnapshotResolver:
    """Resolve only C2-admitted run documents, with current authorization on every use."""

    def __init__(
        self,
        documents: DocumentTransferService,
        *,
        max_bytes: int = 20 * 1024 * 1024,
        max_pages: int = 100,
        timeout_seconds: float = 60,
    ) -> None:
        if min(max_bytes, max_pages, timeout_seconds) <= 0:
            raise ValueError("Positive parser limits required")
        self.documents = documents
        self.max_bytes, self.max_pages, self.timeout = max_bytes, max_pages, timeout_seconds

    async def resolve(
        self, principal: Principal, request: PageRequest
    ) -> AuthorizedSanitizedSnapshot:
        try:
            request = PageRequest.model_validate_json(request.model_dump_json(warnings="error"))
            admitted = await asyncio.to_thread(
                self.documents.read_snapshot, principal, request.run, request.source.document
            )
            manifest = admitted.metadata.attestation.claims.manifest
            if (
                digest_bytes(canonical_bytes(manifest)) != request.source.privacy_manifest_digest
                or len(manifest.pages) != request.source.page_count
                or len(admitted.content) > self.max_bytes
                or len(manifest.pages) > self.max_pages
            ):
                raise ExtractionBoundaryError("source_changed")
            encoded = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(
                    _pdf_worker(),
                    parse_source,
                    admitted.content,
                    request,
                    self.max_bytes,
                    self.max_pages,
                ),
                self.timeout,
            )
            snapshot = AuthorizedSanitizedSnapshot(request.source, admitted.content, encoded)
            for actual, expected in zip(snapshot.source.pages, manifest.pages, strict=True):
                if (
                    actual.number != expected.number
                    or abs(actual.width - expected.width) > 0.001
                    or abs(actual.height - expected.height) > 0.001
                ):
                    raise ExtractionBoundaryError("source_changed")
            # Parsing can take time. Recheck revocation/version before any provider receives bytes.
            current = await asyncio.to_thread(
                self.documents.read_snapshot, principal, request.run, request.source.document
            )
            if current != admitted:
                raise ExtractionBoundaryError("source_changed")
            return snapshot
        except ExtractionBoundaryError:
            raise
        except DocumentFault as error:
            if error.problem.code == DocumentErrorCode.UNAUTHORIZED:
                raise ExtractionBoundaryError("unauthorized_source") from None
            raise ExtractionBoundaryError("source_changed") from None
        except TimeoutError:
            raise ExtractionBoundaryError("timeout") from None
        except Exception:
            raise ExtractionBoundaryError("unsupported_input") from None
