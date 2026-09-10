"""Local-only re-identification of placeholder output. Never a publication path.

Cloud writers render opaque placeholder tokens and can hold no original values.
Re-identification is a fresh, fully validated local write from the pristine
template with the same approved field map, using an operator-supplied private
mapping. The downloaded cloud artifact is verified by digest, never edited, and
the revealed output is confined to local file destinations; publication
wrappers refuse writers that reveal placeholders.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from pathlib import Path

from appraisal_review.adapters.local.pdf_config import PDFRenderConfig, PDFTemplatePolicy
from appraisal_review.adapters.local.pdf_writer import LocalPDFWriter
from appraisal_review.domain.pdf_models import (
    PDFReadError,
    PDFWriteRequest,
    PDFWriteResult,
    SourceDestinationConflictError,
    UnsupportedDocumentURIError,
    document_identity,
)

_TOKEN_PATTERN = re.compile(r"^APR-PH-[A-Z0-9][A-Z0-9-]{7,62}$")


class LocalPlaceholderBackfill:
    """Rewrite one placeholder request locally with revealed private values."""

    def __init__(
        self,
        *,
        render_config: PDFRenderConfig,
        template_policy: PDFTemplatePolicy,
        values: Mapping[str, str],
    ) -> None:
        if not values:
            raise ValueError("Backfill requires at least one placeholder value")
        if any(_TOKEN_PATTERN.fullmatch(token) is None for token in values):
            raise ValueError("Backfill keys must be opaque placeholder tokens")
        self.writer = LocalPDFWriter(
            render_config=render_config,
            template_policy=template_policy,
            placeholder_values=dict(values),
        )

    async def backfill(
        self,
        request: PDFWriteRequest,
        *,
        placeholder_artifact: Path | None = None,
        expected_artifact_sha256: str | None = None,
    ) -> PDFWriteResult:
        """Produce the revealed local copy; optionally bind to a verified cloud artifact."""
        if document_identity(request.source_uri)[0] != "file":
            raise UnsupportedDocumentURIError("Backfill reads the template from a local file")
        if document_identity(request.destination_uri)[0] != "file":
            raise UnsupportedDocumentURIError("Backfilled output must stay on local files")
        if (placeholder_artifact is None) != (expected_artifact_sha256 is None):
            raise ValueError("Artifact verification needs both the file and its digest")
        if placeholder_artifact is not None and expected_artifact_sha256 is not None:
            try:
                actual = hashlib.sha256(placeholder_artifact.read_bytes()).hexdigest()
            except OSError as error:
                raise PDFReadError("Placeholder artifact is unreadable") from error
            if actual != expected_artifact_sha256.lower():
                raise PDFReadError("Placeholder artifact differs from the verified write")
            # Preserve caller protections and let the existing staged writer
            # check path/inode aliases both before rendering and at publication.
            artifact_uri = placeholder_artifact.resolve(strict=True).as_uri()
            if document_identity(request.destination_uri) == document_identity(artifact_uri):
                raise SourceDestinationConflictError(
                    "PDF destination aliases the verified placeholder artifact"
                )
            request = PDFWriteRequest.model_validate(
                {
                    **request.model_dump(),
                    "protected_source_uris": [
                        *request.protected_source_uris,
                        artifact_uri,
                    ],
                }
            )
        return await self.writer.write_pdf(request)
