from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path

import pytest
import reportlab
from pydantic import ValidationError
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject
from reportlab.pdfgen.canvas import Canvas

from appraisal_review.adapters.local.pdf_config import PDFRenderConfig, PDFTemplatePolicy
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
    *,
    editable: frozenset[int] = frozenset({1}),
    reference_only: frozenset[int] = frozenset(),
    template_id: str = "synthetic-v1",
) -> PDFTemplatePolicy:
    return PDFTemplatePolicy(
        template_id=template_id,
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


def validator(
    *,
    config: PDFRenderConfig | None = None,
    template_policy: PDFTemplatePolicy | None = None,
) -> PDFPreflightValidator:
    return PDFPreflightValidator(
        render_config=config or render_config(),
        template_policy=template_policy or policy(),
    )


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
        validator(template_policy=policy(template_id="other")).validate(write_request, source)
    with pytest.raises(PDFFieldPlacementError, match="classify every"):
        validator(template_policy=policy()).validate(write_request, source)


def test_reference_only_and_out_of_range_pages_are_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    write_source(source, extra_blank_page=True)

    with pytest.raises(PDFFieldPlacementError, match="not explicitly editable"):
        validator(
            template_policy=policy(editable=frozenset({1}), reference_only=frozenset({2}))
        ).validate(request(source, [field(page=2)]), source)

    with pytest.raises(PDFFieldPlacementError, match="outside the source"):
        validator(
            template_policy=policy(editable=frozenset({1, 2}), reference_only=frozenset())
        ).validate(request(source, [field(page=3)]), source)


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
