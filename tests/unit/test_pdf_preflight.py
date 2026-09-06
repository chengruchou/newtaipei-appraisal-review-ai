from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path

import pytest
import reportlab
from PIL import Image
from pydantic import ValidationError
from pypdf import PdfReader, PdfWriter
from pypdf.annotations import FreeText
from pypdf.generic import RectangleObject
from reportlab.pdfgen.canvas import Canvas

from appraisal_review.adapters.local.pdf_config import (
    PDFRenderConfig,
    PDFTemplatePolicy,
    field_map_sha256,
)
from appraisal_review.adapters.local.pdf_preflight import PDFPreflightValidator
from appraisal_review.domain.factor_models import (
    EvaluationStatus,
    EvaluationSummary,
    FactorEvaluationResult,
    FactorReviewResult,
    Grade,
)
from appraisal_review.domain.pdf_models import (
    PDFField,
    PDFFieldMap,
    PDFFieldPlacementError,
    PDFFontError,
    PDFReadError,
    PDFValueRef,
    PDFWriteRequest,
)


def vera_font() -> Path:
    return Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"


def render_config(**changes: object) -> PDFRenderConfig:
    values: dict[str, object] = {"font_path": vera_font()}
    values.update(changes)
    return PDFRenderConfig.model_validate(values)


def result() -> FactorReviewResult:
    return FactorReviewResult(
        case_id="case-1",
        rule_set_id="rules-1",
        rule_version="1",
        results=[
            FactorEvaluationResult(
                factor_id="road.width",
                target_grade=Grade.EXCELLENT,
                comparable_grade=Grade.NORMAL,
                adjustment_percent=5.0,
                rule_id="road.width.v1",
                calculation_trace="synthetic",
                status=EvaluationStatus.VERIFIED,
            )
        ],
        summary=EvaluationSummary(
            total_adjustment_percent=5.0,
            status=EvaluationStatus.VERIFIED,
        ),
    )


def value_ref(value: str = "adjustment_percent") -> PDFValueRef:
    return PDFValueRef.model_validate(
        {
            "scope": "regional",
            "target_id": "target",
            "comparable_id": "comparison-1",
            "factor_id": "road.width",
            "value": value,
        }
    )


def field(
    field_id: str = "road-rate",
    *,
    page: int = 1,
    box: tuple[float, float, float, float] = (170, 100, 280, 130),
    operation: str = "fill_blank",
    value: str = "adjustment_percent",
    max_characters: int | None = None,
) -> PDFField:
    return PDFField.model_validate(
        {
            "field_id": field_id,
            "page": page,
            "bounding_box": box,
            "operation": operation,
            "value_ref": value_ref(value).model_dump(),
            "max_characters": max_characters,
        }
    )


def request(source: Path, fields: list[PDFField]) -> PDFWriteRequest:
    return PDFWriteRequest(
        source_uri=source.as_uri(),
        destination_uri=(source.parent / "output.pdf").as_uri(),
        result=result(),
        field_map=PDFFieldMap(template_id="synthetic-v1", fields=fields),
    )


def policy(
    source: Path,
    field_map: PDFFieldMap,
    *,
    editable: frozenset[int] = frozenset({1}),
    reference_only: frozenset[int] = frozenset(),
    template_id: str = "synthetic-v1",
) -> PDFTemplatePolicy:
    return PDFTemplatePolicy(
        template_id=template_id,
        template_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        field_map_sha256=field_map_sha256(field_map),
        editable_pages=editable,
        reference_only_pages=reference_only,
    )


def write_source(path: Path, *, extra_blank_page: bool = False) -> None:
    stream = BytesIO()
    canvas = Canvas(stream, pagesize=(320, 220))
    canvas.rect(45, 55, 125, 35)
    canvas.drawString(55, 68, "STALE")
    canvas.drawString(230, 170, "KEEP")
    canvas.save()
    stream.seek(0)
    source_page = PdfReader(stream).pages[0]
    source_page.cropbox = RectangleObject((10, 20, 310, 210))
    writer = PdfWriter()
    writer.add_page(source_page)
    if extra_blank_page:
        writer.add_blank_page(width=500, height=250)
    with path.open("wb") as output:
        writer.write(output)


def write_inline_image_source(path: Path) -> None:
    stream = BytesIO()
    canvas = Canvas(stream, pagesize=(320, 220))
    canvas.drawInlineImage(Image.new("RGB", (8, 8), "black"), 180, 105, 40, 20)
    canvas.save()
    path.write_bytes(stream.getvalue())


class TrustedValidator:
    def __init__(
        self,
        *,
        config: PDFRenderConfig | None = None,
        editable: frozenset[int] = frozenset({1}),
        reference_only: frozenset[int] = frozenset(),
        template_id: str = "synthetic-v1",
        template_sha256: str | None = None,
        approved_field_map_sha256: str | None = None,
    ) -> None:
        self.config = config or render_config()
        self.editable = editable
        self.reference_only = reference_only
        self.template_id = template_id
        self.template_sha256 = template_sha256
        self.approved_field_map_sha256 = approved_field_map_sha256

    def validate(self, write_request: PDFWriteRequest, source: Path):
        trusted = policy(
            source,
            write_request.field_map,
            editable=self.editable,
            reference_only=self.reference_only,
            template_id=self.template_id,
        )
        if self.template_sha256 is not None:
            trusted = trusted.model_copy(update={"template_sha256": self.template_sha256})
        if self.approved_field_map_sha256 is not None:
            trusted = trusted.model_copy(
                update={"field_map_sha256": self.approved_field_map_sha256}
            )
        return PDFPreflightValidator(
            render_config=self.config,
            template_policy=trusted,
        ).validate(write_request, source)


def validator(**kwargs: object) -> TrustedValidator:
    return TrustedValidator(**kwargs)


def test_complete_preflight_resolves_operations_without_mutating_source(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    write_source(source)
    source_hash = hashlib.sha256(source.read_bytes()).digest()
    write_request = request(
        source,
        [
            field(),
            field(
                "road-note",
                box=(20, 140, 150, 170),
                operation="annotate",
            ),
            field(
                "road-correction",
                box=(40, 40, 155, 65),
                operation="correct",
            ),
        ],
    )

    plan = validator().validate(write_request, source)

    assert plan.page_count == 1
    assert plan.source_sha256 == source_hash
    assert [prepared.display_text for prepared in plan.fields] == [
        "+5.00%",
        "REVIEW: +5.00%",
        "+5.00%",
    ]
    assert plan.fields[2].stale_text == ("STALE",)
    assert hashlib.sha256(source.read_bytes()).digest() == source_hash
    assert "STALE" in PdfReader(source).pages[0].extract_text()


def test_fill_blank_rejects_occupied_region(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    write_source(source)

    with pytest.raises(PDFFieldPlacementError, match="occupied"):
        validator().validate(
            request(source, [field(box=(40, 40, 155, 65))]),
            source,
        )


def test_template_identity_and_complete_page_classification_are_required(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.pdf"
    write_source(source, extra_blank_page=True)
    write_request = request(source, [field()])

    with pytest.raises(PDFFieldPlacementError, match="do not match"):
        validator(template_id="other").validate(write_request, source)
    with pytest.raises(PDFFieldPlacementError, match="classify every"):
        validator().validate(write_request, source)


def test_template_bytes_and_approved_field_map_are_bound_by_digest(tmp_path: Path) -> None:
    approved = tmp_path / "approved.pdf"
    substituted = tmp_path / "substituted.pdf"
    write_source(approved)
    write_source(substituted)
    substituted.write_bytes(substituted.read_bytes() + b"\n%different-template")
    approved_request = request(approved, [field()])
    approved_policy = policy(approved, approved_request.field_map)
    raw_validator = PDFPreflightValidator(
        render_config=render_config(), template_policy=approved_policy
    )

    with pytest.raises(PDFFieldPlacementError, match="trusted template bytes"):
        raw_validator.validate(
            approved_request.model_copy(update={"source_uri": substituted.as_uri()}),
            substituted,
        )

    changed_map = approved_request.field_map.model_copy(deep=True)
    changed_map.fields[0].bounding_box = (171, 100, 280, 130)
    with pytest.raises(PDFFieldPlacementError, match="approved coordinates"):
        raw_validator.validate(
            approved_request.model_copy(update={"field_map": changed_map}), approved
        )


def test_fill_blank_rejects_inline_image_occupancy(tmp_path: Path) -> None:
    source = tmp_path / "inline-image.pdf"
    write_inline_image_source(source)
    write_request = request(source, [field(box=(170, 80, 230, 120))])

    with pytest.raises(PDFFieldPlacementError, match="occupied"):
        validator().validate(write_request, source)


def test_fill_blank_rejects_annotation_occupancy(tmp_path: Path) -> None:
    source = tmp_path / "annotation.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=320, height=220)
    writer.add_annotation(0, FreeText(text="EXISTING", rect=(180, 105, 220, 125)))
    with source.open("wb") as output:
        writer.write(output)
    write_request = request(source, [field(box=(170, 80, 230, 120))])

    with pytest.raises(PDFFieldPlacementError, match="occupied"):
        validator().validate(write_request, source)


def test_reference_only_and_out_of_range_pages_are_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    write_source(source, extra_blank_page=True)

    with pytest.raises(PDFFieldPlacementError, match="not explicitly editable"):
        validator(editable=frozenset({1}), reference_only=frozenset({2})).validate(
            request(source, [field(page=2)]), source
        )

    with pytest.raises(PDFFieldPlacementError, match="outside the source"):
        validator(editable=frozenset({1, 2}), reference_only=frozenset()).validate(
            request(source, [field(page=3)]), source
        )


def test_off_page_and_overlapping_fields_are_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    write_source(source)

    with pytest.raises(PDFFieldPlacementError, match="outside"):
        validator().validate(request(source, [field(box=(250, 100, 301, 130))]), source)

    fields = [
        field("first", box=(170, 100, 250, 130)),
        field("second", box=(240, 100, 280, 130)),
    ]
    with pytest.raises(PDFFieldPlacementError, match="must not overlap"):
        validator().validate(request(source, fields), source)


def test_max_characters_and_measured_overflow_are_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    write_source(source)

    with pytest.raises(PDFFieldPlacementError, match="max_characters"):
        validator().validate(request(source, [field(max_characters=3)]), source)
    with pytest.raises(PDFFieldPlacementError, match="does not fit"):
        validator().validate(
            request(source, [field(box=(170, 100, 175, 130))]),
            source,
        )
    with pytest.raises(PDFFieldPlacementError, match="does not fit"):
        validator(config=render_config(font_size=40.0)).validate(
            request(source, [field(box=(170, 100, 280, 110))]),
            source,
        )


def test_missing_invalid_and_directory_fonts_fail(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    write_source(source)
    invalid_font = tmp_path / "invalid.ttf"
    invalid_font.write_bytes(b"not a font")

    with pytest.raises(PDFFontError, match="not a readable file"):
        validator(config=render_config(font_path=tmp_path / "missing.ttf")).validate(
            request(source, [field()]), source
        )
    with pytest.raises(PDFFontError, match="not a readable file"):
        validator(config=render_config(font_path=tmp_path)).validate(
            request(source, [field()]), source
        )
    with pytest.raises(PDFFontError, match="could not be loaded"):
        validator(config=render_config(font_path=invalid_font)).validate(
            request(source, [field()]), source
        )


def test_missing_cjk_glyph_fails_without_fallback(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    write_source(source)
    labels = {grade.value: grade.value for grade in Grade}
    labels[Grade.EXCELLENT.value] = "評價"

    with pytest.raises(PDFFontError, match="cover every required glyph"):
        validator(config=render_config(grade_labels=labels)).validate(
            request(source, [field(value="target_grade")]),
            source,
        )


def test_encrypted_source_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "encrypted.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=320, height=220)
    writer.encrypt("secret")
    with source.open("wb") as output:
        writer.write(output)

    with pytest.raises(PDFReadError, match="Encrypted"):
        validator().validate(request(source, [field()]), source)


def test_invalid_value_reference_stops_preflight(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    write_source(source)
    write_request = request(source, [field()])
    write_request.result.results = []

    with pytest.raises(PDFFieldPlacementError, match="exactly once"):
        validator().validate(write_request, source)


def test_invalid_operation_is_rejected_by_shared_contract() -> None:
    with pytest.raises(ValidationError):
        field(operation="draw_map")
