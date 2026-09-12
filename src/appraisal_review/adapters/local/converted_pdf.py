"""Observe a converted PDF locally. It reads the output and changes nothing.

This inspects a PDF produced by converting a delivered workbook. It is not the
coordinate-based template writer in `pdf_writer.py` and shares none of that
path's field maps, approved-font digests or Base-14 ASCII assumptions: a
converted official table carries embedded CJK fonts and no field map.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Literal

from pypdf import PdfReader
from pypdf.errors import PdfReadError as PyPdfReadError
from pypdf.generic import DictionaryObject

from appraisal_review.domain.workbook_conversion import (
    ConversionRecord,
    FontObservation,
    PageObservation,
)

_ROTATIONS: dict[int, Literal[0, 90, 180, 270]] = {0: 0, 90: 90, 180: 180, 270: 270}
_FONT_FILES = ("/FontFile", "/FontFile2", "/FontFile3")
_MAX_BYTES = 200_000_000


class ConversionInspectionError(Exception):
    """Stable code only; PDF internals never cross this boundary."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


def _resolve(value: Any) -> Any:
    return value.get_object() if hasattr(value, "get_object") else value


def _descriptors(font: DictionaryObject) -> list[Any]:
    found: list[Any] = []
    descriptor = _resolve(font.get("/FontDescriptor"))
    if isinstance(descriptor, DictionaryObject):
        found.append(descriptor)
    descendants = _resolve(font.get("/DescendantFonts"))
    if isinstance(descendants, list):
        for descendant in descendants:
            nested = _resolve(_resolve(descendant).get("/FontDescriptor"))
            if isinstance(nested, DictionaryObject):
                found.append(nested)
    return found


def _is_embedded(font: DictionaryObject) -> bool:
    return any(any(key in descriptor for key in _FONT_FILES) for descriptor in _descriptors(font))


def _is_subset(name: str) -> bool:
    return len(name) > 7 and name[6] == "+" and name[:6].isupper() and name[:6].isalpha()


def _page_fonts(page: Any) -> dict[str, bool]:
    resources = _resolve(page.get("/Resources"))
    if not isinstance(resources, DictionaryObject):
        return {}
    fonts = _resolve(resources.get("/Font"))
    if not isinstance(fonts, DictionaryObject):
        return {}
    observed: dict[str, bool] = {}
    for entry in fonts.values():
        font = _resolve(entry)
        if not isinstance(font, DictionaryObject):
            continue
        name = str(_resolve(font.get("/BaseFont")) or "")
        if not name:
            continue
        observed[name.lstrip("/")] = _is_embedded(font)
    return observed


def inspect_converted_pdf(
    path: Path, *, source_digest: str, converter: str, probes: tuple[str, ...] = ()
) -> ConversionRecord:
    """Observe the converted output. `source_digest` is the operator's assertion."""

    size = path.stat().st_size
    if size > _MAX_BYTES:
        raise ConversionInspectionError("output_too_large", str(size))
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    try:
        reader = PdfReader(path)
    except (PyPdfReadError, OSError, ValueError) as error:
        raise ConversionInspectionError("unreadable_output", path.name) from error
    if reader.is_encrypted:
        return ConversionRecord(
            source_digest=source_digest,
            converter=converter,
            output_name=path.name,
            output_digest=digest.hexdigest(),
            output_byte_size=size,
            encrypted=True,
        )

    pages: list[PageObservation] = []
    fonts: dict[str, tuple[bool, list[int]]] = {}
    text_parts: list[str] = []
    for index, page in enumerate(reader.pages, start=1):
        box = page.mediabox
        rotation = _ROTATIONS.get(int(page.get("/Rotate", 0)) % 360)
        if rotation is None:
            raise ConversionInspectionError("unsupported_page_rotation", str(index))
        try:
            text = page.extract_text() or ""
        except (PyPdfReadError, ValueError, KeyError) as error:
            raise ConversionInspectionError("unreadable_page_text", str(index)) from error
        text_parts.append(text)
        pages.append(
            PageObservation(
                number=index,
                width=float(box.width),
                height=float(box.height),
                rotation=rotation,
                character_count=len(text),
            )
        )
        for name, embedded in _page_fonts(page).items():
            current = fonts.setdefault(name, (embedded, []))
            fonts[name] = (current[0] and embedded, [*current[1], index])

    joined = "".join(text_parts)
    return ConversionRecord(
        source_digest=source_digest,
        converter=converter,
        output_name=path.name,
        output_digest=digest.hexdigest(),
        output_byte_size=size,
        pages=tuple(pages),
        fonts=tuple(
            FontObservation(
                name=name, embedded=embedded, subset=_is_subset(name), pages=tuple(numbers)
            )
            for name, (embedded, numbers) in sorted(fonts.items())
        ),
        found_text=tuple(probe for probe in probes if probe in joined),
    )
