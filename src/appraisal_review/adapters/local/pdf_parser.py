"""Allowlisted native PDF parsing and rendering; no caller-selected file access."""

from __future__ import annotations

import asyncio
import hashlib
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Literal

from appraisal_review.domain.document_models import Box, SourceDocument, SourcePage, SourceRegion
from appraisal_review.ports.workflow import ParsedDocument


@dataclass(frozen=True)
class DocumentInput:
    path: Path
    document_id: str
    version: str
    role: Literal["criteria", "forms", "reference", "brief"]
    expected_hash: str | None = None
    document_date: str | None = None


def top_left_to_pdf(box: Box, height: float) -> Box:
    x0, y0, x1, y1 = box
    return x0, height - y1, x1, height - y0


def pixels_to_pdf(box: Box, width_px: int, height_px: int, page: SourcePage) -> Box:
    scaled = (
        box[0] * page.width / width_px,
        box[1] * page.height / height_px,
        box[2] * page.width / width_px,
        box[3] * page.height / height_px,
    )
    return top_left_to_pdf(scaled, page.height)


def selection_state(text: str) -> Literal["checked", "unchecked", "ambiguous"] | None:
    checked = any(mark in text for mark in ("☑", "☒", "✓", "✔", "■", "●"))
    unchecked = any(mark in text for mark in ("☐", "□", "○"))
    if checked and unchecked:
        return "ambiguous"
    if checked:
        return "checked"
    if unchecked:
        return "unchecked"
    return None


class LocalPDFParser:
    def __init__(
        self, documents: list[DocumentInput], *, max_bytes: int = 100_000_000, max_pages: int = 200
    ) -> None:
        self.inputs = {d.path.resolve().as_uri(): d for d in documents}
        if len(self.inputs) != len(documents) or len({d.document_id for d in documents}) != len(
            documents
        ):
            raise ValueError("Duplicate document input")
        self.max_bytes, self.max_pages = max_bytes, max_pages

    def _load(self, uri: str) -> tuple[DocumentInput, bytes]:
        if uri not in self.inputs:
            raise ValueError("Document URI is not in the configured allowlist")
        spec = self.inputs[uri]
        if spec.path.resolve().as_uri() != uri:
            raise ValueError("Allowlisted source path changed")
        with spec.path.open("rb") as stream:
            data = stream.read(self.max_bytes + 1)
        if len(data) > self.max_bytes or not data.startswith(b"%PDF-"):
            raise ValueError("Unsupported PDF or size limit")
        if spec.expected_hash and hashlib.sha256(data).hexdigest() != spec.expected_hash:
            raise ValueError("Source hash changed")
        return spec, data

    async def parse_document(self, document_uri: str) -> ParsedDocument:
        return await asyncio.get_running_loop().run_in_executor(
            _pdf_worker(), self._parse, document_uri
        )

    def _parse(self, uri: str) -> ParsedDocument:
        import pymupdf

        spec, data = self._load(uri)
        with pymupdf.open(stream=data, filetype="pdf") as pdf:  # type: ignore[no-untyped-call]
            if pdf.needs_pass or not 0 < len(pdf) <= self.max_pages:
                raise ValueError("Encrypted or oversized PDF")
            pages = []
            for index, page in enumerate(pdf):
                rotation = page.rotation
                # Work in unrotated CropBox coordinates; original bytes are never saved.
                page.set_rotation(0)
                width, height = page.rect.width, page.rect.height
                regions = []
                for block in page.get_text("blocks"):
                    if block[6] != 0 or not block[4].strip():
                        continue
                    box = self._clipped(block[:4], width, height)
                    if box is None:
                        continue
                    text = block[4]
                    regions.append(
                        SourceRegion(
                            id=f"p{index + 1}-b{block[5]}",
                            kind="text",
                            bbox=top_left_to_pdf(box, height),
                            text=text,
                            selection=selection_state(text),
                        )
                    )
                mark_index = 0
                for block in page.get_text("rawdict")["blocks"]:
                    for line in block.get("lines", []):
                        for span in line["spans"]:
                            for char in span["chars"]:
                                state = selection_state(char["c"])
                                box = self._clipped(char["bbox"], width, height)
                                if state is not None and box is not None:
                                    regions.append(
                                        SourceRegion(
                                            id=f"p{index + 1}-mark{mark_index}",
                                            kind="selection",
                                            bbox=top_left_to_pdf(box, height),
                                            text=char["c"],
                                            selection=state,
                                        )
                                    )
                                    mark_index += 1
                tables = page.find_tables()
                for table_index, table in enumerate(tables.tables):
                    rows = table.extract()
                    for row_index, row in enumerate(table.rows):
                        for column_index, cell in enumerate(row.cells):
                            if cell is None:
                                continue
                            box = self._clipped(cell, width, height)
                            if box is None:
                                continue
                            text = rows[row_index][column_index] or ""
                            regions.append(
                                SourceRegion(
                                    id=f"p{index + 1}-t{table_index}-r{row_index}-c{column_index}",
                                    kind="cell",
                                    bbox=top_left_to_pdf(box, height),
                                    text=text,
                                    table_id=f"p{index + 1}-t{table_index}",
                                    row=row_index,
                                    column=column_index,
                                    selection=selection_state(text),
                                )
                            )
                # Whole-page image is an explicit location for visual-only candidates.
                regions.append(
                    SourceRegion(id=f"p{index + 1}-image", kind="image", bbox=(0, 0, width, height))
                )
                pages.append(
                    SourcePage(
                        number=index + 1,
                        width=width,
                        height=height,
                        rotation=rotation,
                        crop_box=tuple(page.cropbox),
                        has_text=any(r.kind == "text" for r in regions),
                        regions=regions,
                    )
                )
            source = SourceDocument(
                document_id=spec.document_id,
                uri=uri,
                content_hash=hashlib.sha256(data).hexdigest(),
                version=spec.version,
                role=spec.role,
                document_date=spec.document_date,
                pages=pages,
            )
            return ParsedDocument(document_uri=uri, page_count=len(pages), source=source)

    @staticmethod
    def _clipped(box: Any, width: float, height: float) -> Box | None:
        clipped = max(0.0, box[0]), max(0.0, box[1]), min(width, box[2]), min(height, box[3])
        return clipped if clipped[0] < clipped[2] and clipped[1] < clipped[3] else None

    async def render(self, uri: str, page_number: int, *, scale: float = 1.5) -> bytes:
        return await asyncio.get_running_loop().run_in_executor(
            _pdf_worker(), self._render, uri, page_number, scale
        )

    def _render(self, uri: str, page_number: int, scale: float) -> bytes:
        import pymupdf

        if not 0.25 <= scale <= 3:
            raise ValueError("Render scale outside bounded range")
        _, data = self._load(uri)
        with pymupdf.open(stream=data, filetype="pdf") as pdf:  # type: ignore[no-untyped-call]
            if not 1 <= page_number <= len(pdf):
                raise ValueError("Page outside source")
            page = pdf[page_number - 1]
            page.set_rotation(0)
            if page.rect.width * scale > 8000 or page.rect.height * scale > 8000:
                raise ValueError("Rendered dimensions exceed model input bounds")
            return bytes(
                page.get_pixmap(matrix=_matrix(pymupdf, scale), alpha=False).tobytes("png")
            )


def _matrix(module: Any, scale: float) -> Any:
    return module.Matrix(scale, scale)


@cache
def _pdf_worker() -> ProcessPoolExecutor:
    # MuPDF does not support concurrent threads. Start only on explicit PDF I/O.
    return ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn"))
