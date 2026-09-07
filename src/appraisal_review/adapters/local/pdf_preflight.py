"""Read-only validation and planning for a complete local PDF write."""

from __future__ import annotations

import math
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Literal

from pypdf import PageObject, PdfReader
from reportlab.pdfbase.ttfonts import TTFont

from appraisal_review.adapters.local.pdf_config import (
    PDFRenderConfig,
    PDFTemplatePolicy,
    field_map_sha256,
)
from appraisal_review.adapters.local.pdf_overlay import (
    BoundingBox,
    PageGeometry,
    correction_runs_in_box,
    text_runs_in_box,
    visual_content_in_box,
)
from appraisal_review.adapters.local.pdf_values import PDFValueFormatter
from appraisal_review.domain.pdf_models import (
    PDFFieldPlacementError,
    PDFFontError,
    PDFReadError,
    PDFWriteRequest,
)


@dataclass(frozen=True)
class PreparedPDFField:
    """A validated field operation containing no unresolved expressions."""

    field_id: str
    page: int
    bounding_box: BoundingBox
    operation: Literal["fill_blank", "annotate", "correct"]
    display_text: str
    text_width: float
    text_height: float
    stale_text: tuple[str, ...] = ()


@dataclass(frozen=True)
class PDFPreflightPlan:
    """Immutable inputs that a later mutation phase is allowed to execute."""

    page_count: int
    source_sha256: bytes
    fields: tuple[PreparedPDFField, ...]


class PDFPreflightValidator:
    """Validate the complete write request without modifying its source PDF."""

    def __init__(
        self,
        *,
        render_config: PDFRenderConfig,
        template_policy: PDFTemplatePolicy,
    ) -> None:
        self.render_config = render_config
        self.template_policy = template_policy
        self.value_formatter = PDFValueFormatter(render_config)

    def validate(self, request: PDFWriteRequest, source_path: Path) -> PDFPreflightPlan:
        reader, source_sha256 = self._read_source(source_path)
        self._validate_template(request, len(reader.pages), source_sha256)
        font = self._load_font()
        prepared: list[PreparedPDFField] = []
        seen_boxes: dict[int, list[BoundingBox]] = {}

        for field in request.field_map.fields:
            if field.page > len(reader.pages):
                raise PDFFieldPlacementError("PDF field page is outside the source document")
            self.template_policy.require_editable_page(field.page)
            x1, y1, x2, y2 = field.bounding_box
            box: BoundingBox = (float(x1), float(y1), float(x2), float(y2))
            geometry = PageGeometry.from_page(reader.pages[field.page - 1])
            geometry.box_to_page_user_space(box)
            self._reject_overlap(seen_boxes.setdefault(field.page, []), box)

            if field.value_ref is None:
                raise PDFFieldPlacementError("PDF field requires an explicit value reference")
            value = self.value_formatter.resolve(request.result, field.value_ref)
            display_text = (
                f"{self.render_config.annotation_label}: {value}"
                if field.operation == "annotate"
                else value
            )
            self._validate_characters(display_text, field.max_characters, font)
            text_width, text_height = self._measure_text(display_text, font)
            self._validate_fit(box, text_width, text_height)
            stale_text = self._validate_operation(
                reader.pages[field.page - 1], box, field.operation
            )
            if field.operation == "correct" and display_text in stale_text:
                raise PDFFieldPlacementError(
                    "PDF correction replacement must differ from stale text"
                )
            prepared.append(
                PreparedPDFField(
                    field_id=field.field_id,
                    page=field.page,
                    bounding_box=box,
                    operation=field.operation,
                    display_text=display_text,
                    text_width=text_width,
                    text_height=text_height,
                    stale_text=stale_text,
                )
            )
            seen_boxes[field.page].append(box)

        return PDFPreflightPlan(
            page_count=len(reader.pages),
            source_sha256=source_sha256,
            fields=tuple(prepared),
        )

    @staticmethod
    def _read_source(source_path: Path) -> tuple[PdfReader, bytes]:
        try:
            source_bytes = source_path.read_bytes()
            reader = PdfReader(BytesIO(source_bytes))
            if reader.is_encrypted:
                raise PDFReadError("Encrypted PDF sources are unsupported")
            if not reader.pages:
                raise PDFReadError("PDF source must contain at least one page")
            return reader, sha256(source_bytes).digest()
        except PDFReadError:
            raise
        except Exception as error:
            raise PDFReadError("PDF source is unreadable") from error

    def _validate_template(
        self,
        request: PDFWriteRequest,
        page_count: int,
        source_sha256: bytes,
    ) -> None:
        if request.field_map.template_id != self.template_policy.template_id:
            raise PDFFieldPlacementError("PDF field map and template policy do not match")
        if source_sha256.hex() != self.template_policy.template_sha256:
            raise PDFFieldPlacementError("PDF source does not match the trusted template bytes")
        if field_map_sha256(request.field_map) != self.template_policy.field_map_sha256:
            raise PDFFieldPlacementError("PDF field map does not match the approved coordinates")
        declared_pages = (
            self.template_policy.editable_pages | self.template_policy.reference_only_pages
        )
        if declared_pages != frozenset(range(1, page_count + 1)):
            raise PDFFieldPlacementError(
                "PDF template policy must classify every source page exactly once"
            )

    def _load_font(self) -> TTFont:
        path = self.render_config.font_path
        try:
            if not path.is_file():
                raise PDFFontError("Configured PDF font is not a readable file")
            return TTFont(self.render_config.font_name, str(path), validate=1)
        except PDFFontError:
            raise
        except Exception as error:
            raise PDFFontError("Configured PDF font could not be loaded") from error

    def _validate_characters(
        self, display_text: str, max_characters: int | None, font: TTFont
    ) -> None:
        if any(ord(character) < 32 for character in display_text):
            raise PDFFieldPlacementError("PDF display text must be a single printable line")
        if max_characters is not None and len(display_text) > max_characters:
            raise PDFFieldPlacementError("PDF display text exceeds max_characters")
        character_map = font.face.charToGlyph
        if any(ord(character) not in character_map for character in display_text):
            raise PDFFontError("Configured PDF font does not cover every required glyph")

    def _measure_text(self, display_text: str, font: TTFont) -> tuple[float, float]:
        try:
            width = float(font.stringWidth(display_text, self.render_config.font_size))
            height = float(
                (font.face.ascent - font.face.descent) * self.render_config.font_size / 1000.0
            )
        except Exception as error:
            raise PDFFontError("Configured PDF font cannot measure display text") from error
        if not all(math.isfinite(value) and value > 0 for value in (width, height)):
            raise PDFFontError("Configured PDF font produced invalid text dimensions")
        return width, height

    @staticmethod
    def _validate_fit(box: BoundingBox, text_width: float, text_height: float) -> None:
        box_width = box[2] - box[0]
        box_height = box[3] - box[1]
        if text_width > box_width or text_height > box_height:
            raise PDFFieldPlacementError("PDF display text does not fit the field bounding box")

    @staticmethod
    def _validate_operation(
        page: PageObject,
        box: BoundingBox,
        operation: Literal["fill_blank", "annotate", "correct"],
    ) -> tuple[str, ...]:
        if operation == "annotate":
            return ()
        if operation == "fill_blank":
            if text_runs_in_box(page, box) or visual_content_in_box(page, box):
                raise PDFFieldPlacementError("PDF fill_blank field is occupied")
            return ()
        if operation == "correct":
            return tuple(run.text for run in correction_runs_in_box(page, box))
        raise PDFFieldPlacementError("Unsupported PDF field operation")

    @staticmethod
    def _reject_overlap(existing: list[BoundingBox], candidate: BoundingBox) -> None:
        for box in existing:
            if not (
                candidate[2] <= box[0]
                or candidate[0] >= box[2]
                or candidate[3] <= box[1]
                or candidate[1] >= box[3]
            ):
                raise PDFFieldPlacementError("PDF field bounding boxes must not overlap")
