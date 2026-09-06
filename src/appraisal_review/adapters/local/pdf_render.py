"""Deterministic mutation of a preflighted PDF into a temporary artifact."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import ParamSpec, TypeVar

from pypdf import PageObject, PdfReader, PdfWriter, Transformation
from pypdf.annotations import FreeText
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    FloatObject,
    NameObject,
    NumberObject,
    TextStringObject,
)
from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

from appraisal_review.adapters.local.pdf_config import PDFRenderConfig
from appraisal_review.adapters.local.pdf_overlay import (
    BoundingBox,
    PageGeometry,
    remove_text_in_box,
)
from appraisal_review.adapters.local.pdf_preflight import PDFPreflightPlan, PreparedPDFField
from appraisal_review.domain.pdf_models import (
    PDFFieldPlacementError,
    PDFFontError,
    PDFReadError,
    PDFWriteError,
)

P = ParamSpec("P")
T = TypeVar("T")


@dataclass(frozen=True)
class PDFMutationResult:
    """Facts about a written temporary PDF, before reopen verification."""

    page_count: int
    written_field_ids: tuple[str, ...]


class PDFMutationExecutor:
    """Apply an immutable preflight plan without publishing the output."""

    def __init__(self, render_config: PDFRenderConfig) -> None:
        self.render_config = render_config

    def write_temporary(
        self,
        *,
        source_path: Path,
        output_path: Path,
        plan: PDFPreflightPlan,
    ) -> PDFMutationResult:
        """Write only ``output_path``; publication belongs to object access."""
        self._reject_same_file(source_path, output_path)
        font = self._register_font()
        writer = self._clone_source(source_path, plan.source_sha256)
        if len(writer.pages) != plan.page_count:
            raise PDFFieldPlacementError("PDF source changed after preflight")
        field_ids = [field.field_id for field in plan.fields]
        if len(field_ids) != len(set(field_ids)):
            raise PDFFieldPlacementError("Preflight plan contains duplicate PDF field IDs")
        if any(
            field.operation not in {"fill_blank", "annotate", "correct"} for field in plan.fields
        ):
            raise PDFFieldPlacementError("Preflight plan contains an unsupported operation")

        fields_by_page: dict[int, list[PreparedPDFField]] = defaultdict(list)
        for field in plan.fields:
            if field.page < 1 or field.page > len(writer.pages):
                raise PDFFieldPlacementError("Preflight plan targets an invalid PDF page")
            fields_by_page[field.page - 1].append(field)

        # Removing text first keeps operation indexes stable until each correction
        # is inspected, and prevents any newly rendered text from being considered.
        for page_index, fields in fields_by_page.items():
            page = writer.pages[page_index]
            for field in fields:
                if field.operation != "correct":
                    continue
                removed = tuple(
                    self._mutate(
                        remove_text_in_box,
                        page,
                        field.bounding_box,
                    )
                )
                if removed != field.stale_text:
                    raise PDFFieldPlacementError("PDF source changed after correction preflight")

        for page_index, fields in fields_by_page.items():
            page = writer.pages[page_index]
            geometry = PageGeometry.from_page(page)
            content_fields = [field for field in fields if field.operation != "annotate"]
            if content_fields:
                overlay_page = self._mutate(
                    self._make_page_overlay, page, geometry, content_fields, font
                )
                media = page.mediabox
                translation = Transformation().translate(
                    tx=float(media.left), ty=float(media.bottom)
                )
                self._mutate(
                    page.merge_transformed_page,
                    overlay_page,
                    translation,
                    over=True,
                    expand=False,
                )
            for field in fields:
                if field.operation == "annotate":
                    self._mutate(
                        self._add_annotation,
                        writer,
                        page_index,
                        geometry,
                        field,
                        font,
                    )

        writer.add_metadata(
            {
                "/AppraisalReviewWriterVersion": "1",
                "/AppraisalReviewFieldIds": json.dumps(
                    field_ids, ensure_ascii=True, separators=(",", ":")
                ),
            }
        )

        try:
            with output_path.open("wb") as output:
                writer.write(output)
        except PDFWriteError:
            raise
        except Exception as error:
            raise PDFWriteError("Temporary PDF could not be written") from error

        return PDFMutationResult(
            page_count=len(writer.pages),
            written_field_ids=tuple(field.field_id for field in plan.fields),
        )

    @staticmethod
    def _mutate(function: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return function(*args, **kwargs)
        except PDFWriteError:
            raise
        except Exception as error:
            raise PDFWriteError("PDF field mutation failed") from error

    @staticmethod
    def _reject_same_file(source_path: Path, output_path: Path) -> None:
        try:
            if source_path.resolve(strict=True) == output_path.resolve(strict=False):
                raise PDFWriteError("Temporary output must differ from the source PDF")
            if output_path.exists() and source_path.samefile(output_path):
                raise PDFWriteError("Temporary output must differ from the source PDF")
        except PDFWriteError:
            raise
        except OSError as error:
            raise PDFWriteError("PDF path identity could not be checked") from error

    def _register_font(self) -> TTFont:
        try:
            font = TTFont(
                self.render_config.font_name,
                str(self.render_config.font_path),
                validate=1,
            )
            pdfmetrics.registerFont(font)
            return font
        except Exception as error:
            raise PDFFontError("Configured PDF font could not be registered") from error

    @staticmethod
    def _clone_source(source_path: Path, expected_sha256: bytes) -> PdfWriter:
        try:
            source_bytes = source_path.read_bytes()
            if sha256(source_bytes).digest() != expected_sha256:
                raise PDFFieldPlacementError("PDF source changed after preflight")
            reader = PdfReader(BytesIO(source_bytes))
            if reader.is_encrypted:
                raise PDFReadError("Encrypted PDF sources are unsupported")
            return PdfWriter(clone_from=reader)
        except PDFFieldPlacementError:
            raise
        except PDFReadError:
            raise
        except Exception as error:
            raise PDFReadError("PDF source could not be cloned for writing") from error

    def _make_page_overlay(
        self,
        page: PageObject,
        geometry: PageGeometry,
        fields: list[PreparedPDFField],
        font: TTFont,
    ) -> PageObject:
        media = page.mediabox
        width = float(media.right) - float(media.left)
        height = float(media.top) - float(media.bottom)
        stream = BytesIO()
        canvas = Canvas(stream, pagesize=(width, height), pageCompression=1)
        canvas.setFillColor(HexColor(self.render_config.text_color))
        for field in fields:
            page_box = geometry.box_to_page_user_space(field.bounding_box)
            local_box = (
                page_box[0] - float(media.left),
                page_box[1] - float(media.bottom),
                page_box[2] - float(media.left),
                page_box[3] - float(media.bottom),
            )
            self._draw_text(canvas, local_box, field, geometry, font)
        canvas.save()
        stream.seek(0)
        return PdfReader(stream).pages[0]

    def _draw_text(
        self,
        canvas: Canvas,
        box: BoundingBox,
        field: PreparedPDFField,
        geometry: PageGeometry,
        font: TTFont,
    ) -> None:
        scale = geometry.user_unit
        font_size = self.render_config.font_size / scale
        text_width = field.text_width / scale
        text_height = field.text_height / scale
        x1, y1, x2, y2 = box
        if self.render_config.text_alignment == "left":
            x = x1
        elif self.render_config.text_alignment == "center":
            x = x1 + ((x2 - x1) - text_width) / 2.0
        else:
            x = x2 - text_width
        descent = float(font.face.descent) * font_size / 1000.0
        baseline = y1 + ((y2 - y1) - text_height) / 2.0 - descent
        canvas.setFont(self.render_config.font_name, font_size)
        canvas.drawString(x, baseline, field.display_text)

    def _add_annotation(
        self,
        writer: PdfWriter,
        page_index: int,
        geometry: PageGeometry,
        field: PreparedPDFField,
        font: TTFont,
    ) -> None:
        page_box = geometry.box_to_page_user_space(field.bounding_box)
        annotation = FreeText(
            text=field.display_text,
            rect=page_box,
            font=self.render_config.font_name,
            font_size=f"{self.render_config.font_size / geometry.user_unit:g}pt",
            font_color=self.render_config.annotation_color.lstrip("#"),
            border_color=self.render_config.annotation_color.lstrip("#"),
            background_color=None,
        )
        appearance = self._make_annotation_appearance(field, geometry, font)
        appearance[NameObject("/Resources")] = appearance["/Resources"].clone(
            writer, force_duplicate=True
        )
        appearance_reference = writer._add_object(appearance)
        annotation[NameObject("/AP")] = DictionaryObject({NameObject("/N"): appearance_reference})
        annotation[NameObject("/F")] = NumberObject(4)  # Print the annotation.
        annotation[NameObject("/NM")] = TextStringObject(f"appraisal-review:{field.field_id}")
        writer.add_annotation(page_index, annotation)

    def _make_annotation_appearance(
        self,
        field: PreparedPDFField,
        geometry: PageGeometry,
        font: TTFont,
    ) -> DecodedStreamObject:
        scale = geometry.user_unit
        width = (field.bounding_box[2] - field.bounding_box[0]) / scale
        height = (field.bounding_box[3] - field.bounding_box[1]) / scale
        local_field = PreparedPDFField(
            field_id=field.field_id,
            page=field.page,
            bounding_box=(0.0, 0.0, width * scale, height * scale),
            operation=field.operation,
            display_text=field.display_text,
            text_width=field.text_width,
            text_height=field.text_height,
            stale_text=field.stale_text,
        )
        stream = BytesIO()
        canvas = Canvas(stream, pagesize=(width, height), pageCompression=1)
        color = HexColor(self.render_config.annotation_color)
        canvas.setStrokeColor(color)
        canvas.setFillColor(color)
        canvas.setLineWidth(max(0.5 / scale, 0.1))
        canvas.rect(0, 0, width, height, stroke=1, fill=0)
        self._draw_text(canvas, (0.0, 0.0, width, height), local_field, geometry, font)
        canvas.save()
        stream.seek(0)
        appearance_page = PdfReader(stream).pages[0]
        content = appearance_page.get_contents()
        if content is None:
            raise PDFWriteError("Annotation appearance has no content")
        appearance = DecodedStreamObject()
        appearance.set_data(content.get_data())
        appearance.update(
            {
                NameObject("/Type"): NameObject("/XObject"),
                NameObject("/Subtype"): NameObject("/Form"),
                NameObject("/FormType"): NumberObject(1),
                NameObject("/BBox"): ArrayObject(
                    [FloatObject(0), FloatObject(0), FloatObject(width), FloatObject(height)]
                ),
                NameObject("/Resources"): appearance_page["/Resources"],
            }
        )
        return appearance
