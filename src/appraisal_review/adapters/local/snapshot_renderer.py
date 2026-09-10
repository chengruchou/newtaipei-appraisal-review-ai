"""Render only immutable snapshot bytes in the existing isolated PDF worker."""

import asyncio
import hashlib
import json
from typing import Any

from appraisal_review.adapters.local.pdf_parser import _pdf_worker
from appraisal_review.ports.document_extraction import AuthorizedSanitizedSnapshot

RENDERING_CONFIGURATION = {
    "version": "snapshot-png-v1",
    "scale": 1.5,
    "alpha": False,
    "rotation": 0,
    "max_dimension": 8000,
    "max_pixels": 16_000_000,
}
RENDERING_CONFIGURATION_DIGEST = hashlib.sha256(
    json.dumps(RENDERING_CONFIGURATION, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()


def _render(content: bytes, number: int, count: int, width: float, height: float) -> bytes:
    import pymupdf

    module: Any = pymupdf
    with module.open(stream=content, filetype="pdf") as pdf:
        if pdf.needs_pass or len(pdf) != count:
            raise ValueError("Snapshot PDF page count or encryption mismatch")
        page = pdf[number - 1]
        page.set_rotation(0)
        if abs(page.rect.width - width) > 0.001 or abs(page.rect.height - height) > 0.001:
            raise ValueError("Snapshot page geometry mismatch")
        if width * 1.5 > 8000 or height * 1.5 > 8000 or width * height * 2.25 > 16_000_000:
            raise ValueError("Snapshot render exceeds bounds")
        return bytes(page.get_pixmap(matrix=module.Matrix(1.5, 1.5), alpha=False).tobytes("png"))


class SnapshotPDFRenderer:
    async def render(self, snapshot: AuthorizedSanitizedSnapshot, page: int) -> bytes:
        source = snapshot.source
        if not 1 <= page <= len(source.pages):
            raise ValueError("Snapshot page outside source")
        geometry = source.pages[page - 1]
        return await asyncio.get_running_loop().run_in_executor(
            _pdf_worker(),
            _render,
            snapshot.content,
            page,
            len(source.pages),
            geometry.width,
            geometry.height,
        )
