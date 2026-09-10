"""Read-only verification of a mutated PDF before atomic publication."""

from __future__ import annotations

import json
import math
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Any

from pypdf import PageObject, PdfReader
from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject, StreamObject

from appraisal_review.adapters.local.pdf_config import PDFTemplatePolicy
from appraisal_review.adapters.local.pdf_overlay import BoundingBox, PageGeometry
from appraisal_review.adapters.local.pdf_preflight import PDFPreflightPlan, PreparedPDFField
from appraisal_review.adapters.local.pdf_render import PDFMutationResult
from appraisal_review.domain.pdf_models import PDFReadError, PDFWriteError

_TOLERANCE = 1e-5


class PDFArtifactVerifier:
    """Prove required field effects and protected-page preservation."""

    def __init__(self, template_policy: PDFTemplatePolicy) -> None:
        self.template_policy = template_policy

    def verify(
        self,
        *,
        source_path: Path,
        output_path: Path,
        plan: PDFPreflightPlan,
        mutation_result: PDFMutationResult,
    ) -> None:
        source = self._read_pdf(source_path, expected_sha256=plan.source_sha256)
        output = self._read_pdf(output_path)
        expected_ids = tuple(field.field_id for field in plan.fields)
        if len(source.pages) != plan.page_count or len(output.pages) != plan.page_count:
            raise PDFWriteError("PDF output page count verification failed")
        if mutation_result.page_count != plan.page_count:
            raise PDFWriteError("PDF mutation page count verification failed")
        if mutation_result.written_field_ids != expected_ids:
            raise PDFWriteError("PDF mutation field set verification failed")
        self._verify_metadata(output, expected_ids)
        self._verify_fields(output, plan)
        self._verify_reference_pages(source, output)

    @staticmethod
    def _read_pdf(path: Path, *, expected_sha256: bytes | None = None) -> PdfReader:
        try:
            raw = path.read_bytes()
            if expected_sha256 is not None and sha256(raw).digest() != expected_sha256:
                raise PDFReadError("PDF source changed during writing")
            reader = PdfReader(BytesIO(raw))
            if reader.is_encrypted or not reader.pages:
                raise PDFReadError("PDF verification input is unsupported")
            return reader
        except PDFReadError:
            raise
        except Exception as error:
            raise PDFReadError("PDF verification input is unreadable") from error

    @staticmethod
    def _verify_metadata(reader: PdfReader, expected_ids: tuple[str, ...]) -> None:
        metadata = reader.metadata
        if metadata is None or metadata.get("/AppraisalReviewWriterVersion") != "2":
            raise PDFWriteError("PDF writer metadata verification failed")
        try:
            field_ids = json.loads(metadata.get("/AppraisalReviewFieldIds", ""))
        except (TypeError, json.JSONDecodeError) as error:
            raise PDFWriteError("PDF field metadata is invalid") from error
        if field_ids != list(expected_ids) or len(field_ids) != len(set(field_ids)):
            raise PDFWriteError("PDF field metadata verification failed")

    def _verify_fields(self, reader: PdfReader, plan: PDFPreflightPlan) -> None:
        text_by_page: dict[int, list[tuple[str, tuple[float, float]]]] = {}
        for field in plan.fields:
            page = reader.pages[field.page - 1]
            geometry = PageGeometry.from_page(page)
            page_box = geometry.box_to_page_user_space(field.bounding_box)
            if field.operation == "annotate":
                self._verify_annotation(page, page_box, field, geometry)
                continue
            origins = text_by_page.setdefault(field.page, self._text_origins(page))
            replacements = [
                text
                for text, origin in origins
                if text == field.display_text and self._point_in_box(origin, page_box)
            ]
            if len(replacements) != 1:
                raise PDFWriteError("PDF replacement text verification failed")
            if field.operation == "correct" and any(
                text in field.stale_text and self._point_in_box(origin, page_box)
                for text, origin in origins
            ):
                raise PDFWriteError("PDF stale text verification failed")

    @staticmethod
    def _text_origins(page: PageObject) -> list[tuple[str, tuple[float, float]]]:
        found: list[tuple[str, tuple[float, float]]] = []

        def visitor(
            text: str,
            current_matrix: list[float],
            text_matrix: list[float],
            _font_dictionary: Any,
            _font_size: float,
        ) -> None:
            clean = text.rstrip("\r\n")
            if not clean:
                return
            x = (
                text_matrix[4] * current_matrix[0]
                + text_matrix[5] * current_matrix[2]
                + current_matrix[4]
            )
            y = (
                text_matrix[4] * current_matrix[1]
                + text_matrix[5] * current_matrix[3]
                + current_matrix[5]
            )
            found.append((clean, (float(x), float(y))))

        try:
            page.extract_text(visitor_text=visitor)
        except Exception as error:
            raise PDFReadError("PDF output text cannot be verified") from error
        return found

    @staticmethod
    def _point_in_box(point: tuple[float, float], box: BoundingBox) -> bool:
        return (
            box[0] - _TOLERANCE <= point[0] <= box[2] + _TOLERANCE
            and box[1] - _TOLERANCE <= point[1] <= box[3] + _TOLERANCE
        )

    def _verify_annotation(
        self,
        page: PageObject,
        page_box: BoundingBox,
        field: PreparedPDFField,
        geometry: PageGeometry,
    ) -> None:
        annotations = page.get("/Annots", [])
        matches = []
        for reference in annotations:
            annotation = reference.get_object()
            if annotation.get("/NM") == f"appraisal-review:{field.field_id}":
                matches.append(annotation)
        if len(matches) != 1:
            raise PDFWriteError("PDF annotation identity verification failed")
        annotation = matches[0]
        if annotation.get("/Subtype") != "/FreeText":
            raise PDFWriteError("PDF annotation type verification failed")
        if (
            annotation.get("/Contents") != field.display_text
            or int(annotation.get("/F", 0)) & 4 == 0
        ):
            raise PDFWriteError("PDF annotation content verification failed")
        self._require_close_box(annotation.get("/Rect"), page_box)
        try:
            appearance = annotation["/AP"]["/N"].get_object()
            if appearance.get("/Subtype") != "/Form" or not appearance.get_data():
                raise PDFWriteError("PDF annotation appearance verification failed")
            expected_bbox = (
                0.0,
                0.0,
                (field.bounding_box[2] - field.bounding_box[0]) / geometry.user_unit,
                (field.bounding_box[3] - field.bounding_box[1]) / geometry.user_unit,
            )
            self._require_close_box(appearance.get("/BBox"), expected_bbox)
            if not self._has_embedded_font(appearance.get("/Resources")):
                raise PDFWriteError("PDF annotation font embedding verification failed")
        except PDFWriteError:
            raise
        except Exception as error:
            raise PDFWriteError("PDF annotation appearance verification failed") from error

    @staticmethod
    def _require_close_box(actual: Any, expected: BoundingBox) -> None:
        try:
            values = tuple(float(value) for value in actual)
        except (TypeError, ValueError) as error:
            raise PDFWriteError("PDF box verification failed") from error
        if len(values) != 4 or any(
            not math.isclose(value, target, abs_tol=_TOLERANCE)
            for value, target in zip(values, expected, strict=True)
        ):
            raise PDFWriteError("PDF box verification failed")

    @staticmethod
    def _has_embedded_font(resources: Any) -> bool:
        if resources is None:
            return False
        resources = resources.get_object()
        fonts = resources.get("/Font")
        if fonts is None:
            return False
        for reference in fonts.get_object().values():
            font = reference.get_object()
            candidates = [font]
            descendants = font.get("/DescendantFonts")
            if descendants:
                candidates.extend(item.get_object() for item in descendants)
            for candidate in candidates:
                descriptor = candidate.get("/FontDescriptor")
                if descriptor is not None and "/FontFile2" in descriptor.get_object():
                    return True
        return False

    def _verify_reference_pages(self, source: PdfReader, output: PdfReader) -> None:
        for page_number in self.template_policy.reference_only_pages:
            source_page = source.pages[page_number - 1]
            output_page = output.pages[page_number - 1]
            if self._page_snapshot(source_page) != self._page_snapshot(output_page):
                raise PDFWriteError("Reference-only PDF page changed during writing")

    @classmethod
    def _page_snapshot(cls, page: PageObject) -> tuple[Any, Any, Any]:
        content = page.get_contents()
        content_data = None if content is None else content.get_data()
        resources = cls._fingerprint(page.get("/Resources"), frozenset())
        annotations = cls._fingerprint(page.get("/Annots"), frozenset())
        return content_data, resources, annotations

    @classmethod
    def _fingerprint(cls, value: Any, active: frozenset[int]) -> Any:
        if isinstance(value, IndirectObject):
            value = value.get_object()
        if value is None:
            return None
        marker = id(value)
        if marker in active:
            return ("cycle", type(value).__name__)
        nested = active | {marker}
        if isinstance(value, StreamObject):
            dictionary = tuple(
                (str(key), cls._fingerprint(item, nested))
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
                if str(key) not in {"/Length", "/P"}
            )
            return ("stream", dictionary, sha256(value.get_data()).digest())
        if isinstance(value, DictionaryObject):
            return tuple(
                (str(key), cls._fingerprint(item, nested))
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
                if str(key) != "/P"
            )
        if isinstance(value, ArrayObject):
            return tuple(cls._fingerprint(item, nested) for item in value)
        return (type(value).__name__, str(value))
