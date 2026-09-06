from __future__ import annotations

import asyncio
import hashlib
from io import BytesIO
from pathlib import Path

import pytest
import reportlab
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from pypdf import PdfReader, PdfWriter
from pypdf.annotations import FreeText
from pypdf.generic import FloatObject, NameObject, RectangleObject
from reportlab.pdfgen.canvas import Canvas

from appraisal_review.adapters.local.pdf_config import PDFRenderConfig, PDFTemplatePolicy
from appraisal_review.adapters.local.pdf_preflight import PDFPreflightValidator
from appraisal_review.adapters.local.pdf_render import PDFMutationExecutor
from appraisal_review.adapters.local.pdf_verify import PDFArtifactVerifier
from appraisal_review.adapters.local.pdf_writer import LocalPDFWriter
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
    PDFWriteError,
    PDFWriteRequest,
)


def vera_font() -> Path:
    return Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"


def render_config(**changes: object) -> PDFRenderConfig:
    values: dict[str, object] = {"font_path": vera_font()}
    values.update(changes)
    return PDFRenderConfig.model_validate(values)


def write_generated_cjk_font(path: Path) -> None:
    """Create an original synthetic font fixture without shipping a font binary."""
    glyph_order = [".notdef", "space", "cjk-middle"]
    glyphs = {}
    for name in glyph_order:
        pen = TTGlyphPen(None)
        if name != "space":
            pen.moveTo((100, 0))
            pen.lineTo((500, 0))
            pen.lineTo((500, 700))
            pen.lineTo((100, 700))
            pen.closePath()
        glyphs[name] = pen.glyph()

    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder(glyph_order)
    builder.setupCharacterMap({32: "space", ord("中"): "cjk-middle"})
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics({name: (600, 0) for name in glyph_order})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable(
        {
            "familyName": "Synthetic CJK Test",
            "styleName": "Regular",
            "uniqueFontIdentifier": "Synthetic-CJK-Test-Regular",
            "fullName": "Synthetic CJK Test Regular",
            "psName": "Synthetic-CJK-Test-Regular",
        }
    )
    builder.setupOS2(
        sTypoAscender=800,
        sTypoDescender=-200,
        usWinAscent=800,
        usWinDescent=200,
    )
    builder.setupPost()
    builder.setupMaxp()
    builder.save(path)


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


def pdf_field(
    field_id: str,
    box: tuple[float, float, float, float],
    operation: str,
    *,
    value: str = "adjustment_percent",
) -> PDFField:
    return PDFField.model_validate(
        {
            "field_id": field_id,
            "page": 1,
            "bounding_box": box,
            "operation": operation,
            "value_ref": {
                "scope": "regional",
                "target_id": "target",
                "comparable_id": "comparison-1",
                "factor_id": "road.width",
                "value": value,
            },
        }
    )


def write_source(path: Path, *, rotation: int = 0, user_unit: float = 1.0) -> None:
    stream = BytesIO()
    canvas = Canvas(stream, pagesize=(320, 220))
    canvas.rect(45, 55, 125, 35)
    canvas.drawString(55, 68, "STALE")
    canvas.drawString(230, 170, "KEEP")
    canvas.save()
    stream.seek(0)
    page = PdfReader(stream).pages[0]
    page.cropbox = RectangleObject((10, 20, 310, 210))
    if rotation:
        page.rotate(rotation)
    if user_unit != 1.0:
        page[NameObject("/UserUnit")] = FloatObject(user_unit)
    writer = PdfWriter()
    writer.add_page(page)
    with path.open("wb") as output:
        writer.write(output)


def add_reference_page(path: Path) -> None:
    reference_stream = BytesIO()
    canvas = Canvas(reference_stream, pagesize=(500, 250))
    canvas.drawString(40, 200, "REFERENCE MAP CONTENT")
    canvas.line(40, 180, 460, 180)
    canvas.save()
    reference_stream.seek(0)
    reference_page = PdfReader(reference_stream).pages[0]
    writer = PdfWriter(clone_from=PdfReader(path))
    writer.add_page(reference_page)
    writer.add_annotation(
        1,
        FreeText(text="REFERENCE NOTE", rect=(40, 120, 180, 150)),
    )
    with path.open("wb") as output:
        writer.write(output)


def write_request(source: Path) -> PDFWriteRequest:
    return PDFWriteRequest(
        source_uri=source.as_uri(),
        destination_uri=(source.parent / "published.pdf").as_uri(),
        result=result(),
        field_map=PDFFieldMap(
            template_id="synthetic-v1",
            fields=[
                pdf_field("blank", (170, 100, 280, 130), "fill_blank"),
                pdf_field("note", (190, 135, 290, 175), "annotate"),
                pdf_field("replacement", (40, 40, 155, 65), "correct"),
            ],
        ),
    )


def execute(source: Path, temporary: Path, config: PDFRenderConfig) -> None:
    template_policy = PDFTemplatePolicy(template_id="synthetic-v1", editable_pages=frozenset({1}))
    plan = PDFPreflightValidator(
        render_config=config,
        template_policy=template_policy,
    ).validate(write_request(source), source)
    mutation = PDFMutationExecutor(config).write_temporary(
        source_path=source,
        output_path=temporary,
        plan=plan,
    )
    assert mutation.page_count == 1
    assert mutation.written_field_ids == ("blank", "note", "replacement")
    PDFArtifactVerifier(template_policy).verify(
        source_path=source,
        output_path=temporary,
        plan=plan,
        mutation_result=mutation,
    )


def annotation_appearance(reader: PdfReader):
    annotation = reader.pages[0]["/Annots"][0].get_object()
    return annotation, annotation["/AP"]["/N"].get_object()


def has_embedded_true_type_font(resources) -> bool:
    fonts = resources["/Font"].get_object().values()
    for font_reference in fonts:
        font = font_reference.get_object()
        descendant = font.get("/DescendantFonts")
        candidates = [font]
        if descendant:
            candidates.extend(item.get_object() for item in descendant)
        for candidate in candidates:
            descriptor = candidate.get("/FontDescriptor")
            if descriptor and "/FontFile2" in descriptor.get_object():
                return True
    return False


def rectangle_operations(reader: PdfReader) -> list[tuple[float, ...]]:
    content = reader.pages[0].get_contents()
    assert content is not None
    return [
        tuple(float(value) for value in operands)
        for operands, operator in content.operations
        if operator == b"re"
    ]


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_executor_applies_all_operations_and_preserves_source(
    tmp_path: Path, rotation: int
) -> None:
    source = tmp_path / "source.pdf"
    temporary = tmp_path / "temporary.pdf"
    write_source(source, rotation=rotation)
    original_hash = hashlib.sha256(source.read_bytes()).digest()

    execute(source, temporary, render_config())

    assert hashlib.sha256(source.read_bytes()).digest() == original_hash
    reader = PdfReader(temporary)
    extracted = reader.pages[0].extract_text()
    assert "STALE" not in extracted
    assert "KEEP" in extracted
    assert extracted.count("+5.00%") == 2
    assert (45.0, 55.0, 125.0, 35.0) in rectangle_operations(reader)
    annotation, appearance = annotation_appearance(reader)
    assert annotation["/Subtype"] == "/FreeText"
    assert annotation["/Contents"] == "REVIEW: +5.00%"
    assert annotation["/F"] == 4
    assert "color:#B00020" in annotation["/DS"]
    assert appearance["/Subtype"] == "/Form"
    assert appearance.get_data()
    assert has_embedded_true_type_font(appearance["/Resources"])


def test_executor_scales_text_and_annotation_for_user_unit(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    temporary = tmp_path / "temporary.pdf"
    write_source(source, user_unit=2.0)
    config = render_config(font_size=9.0)
    request = write_request(source)
    request.field_map.fields = [
        pdf_field("blank", (170, 100, 280, 130), "fill_blank"),
        pdf_field("note", (190, 135, 290, 175), "annotate"),
    ]
    plan = PDFPreflightValidator(
        render_config=config,
        template_policy=PDFTemplatePolicy(
            template_id="synthetic-v1", editable_pages=frozenset({1})
        ),
    ).validate(request, source)

    PDFMutationExecutor(config).write_temporary(
        source_path=source, output_path=temporary, plan=plan
    )

    reader = PdfReader(temporary)
    annotation, appearance = annotation_appearance(reader)
    assert [float(value) for value in annotation["/Rect"]] == [105, 87.5, 155, 107.5]
    assert [float(value) for value in appearance["/BBox"]] == [0, 0, 50, 20]
    assert "+5.00%" in reader.pages[0].extract_text()


def test_executor_refuses_source_as_temporary_output(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    write_source(source)
    config = render_config()
    plan = PDFPreflightValidator(
        render_config=config,
        template_policy=PDFTemplatePolicy(
            template_id="synthetic-v1", editable_pages=frozenset({1})
        ),
    ).validate(write_request(source), source)

    with pytest.raises(PDFWriteError, match="must differ"):
        PDFMutationExecutor(config).write_temporary(
            source_path=source, output_path=source, plan=plan
        )


def test_executor_refuses_source_changed_after_preflight(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    temporary = tmp_path / "temporary.pdf"
    write_source(source)
    config = render_config()
    plan = PDFPreflightValidator(
        render_config=config,
        template_policy=PDFTemplatePolicy(
            template_id="synthetic-v1", editable_pages=frozenset({1})
        ),
    ).validate(write_request(source), source)
    write_source(source, rotation=90)

    with pytest.raises(PDFWriteError, match="changed after preflight"):
        PDFMutationExecutor(config).write_temporary(
            source_path=source, output_path=temporary, plan=plan
        )
    assert not temporary.exists()


def test_local_writer_verifies_and_atomically_publishes(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    destination = tmp_path / "published.pdf"
    write_source(source)
    original_hash = hashlib.sha256(source.read_bytes()).digest()
    config = render_config()
    writer = LocalPDFWriter(
        render_config=config,
        template_policy=PDFTemplatePolicy(
            template_id="synthetic-v1", editable_pages=frozenset({1})
        ),
    )

    result = asyncio.run(writer.write_pdf(write_request(source)))

    assert result.output_uri == destination.as_uri()
    assert result.page_count == 1
    assert result.written_field_ids == ["blank", "note", "replacement"]
    assert result.warnings == []
    assert destination.is_file()
    assert hashlib.sha256(source.read_bytes()).digest() == original_hash
    assert not list(tmp_path.glob(".published.pdf.*.tmp"))


def test_local_writer_does_not_publish_when_reopen_verification_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.pdf"
    destination = tmp_path / "published.pdf"
    write_source(source)
    writer = LocalPDFWriter(
        render_config=render_config(),
        template_policy=PDFTemplatePolicy(
            template_id="synthetic-v1", editable_pages=frozenset({1})
        ),
    )

    def fail_verification(**_kwargs: object) -> None:
        raise PDFWriteError("synthetic verification failure")

    monkeypatch.setattr(writer.verifier, "verify", fail_verification)

    with pytest.raises(PDFWriteError, match="verification failure"):
        asyncio.run(writer.write_pdf(write_request(source)))

    assert not destination.exists()
    assert not list(tmp_path.glob(".published.pdf.*.tmp"))


def test_local_writer_does_not_publish_when_mutation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.pdf"
    destination = tmp_path / "published.pdf"
    write_source(source)
    writer = LocalPDFWriter(
        render_config=render_config(),
        template_policy=PDFTemplatePolicy(
            template_id="synthetic-v1", editable_pages=frozenset({1})
        ),
    )

    def fail_mutation(**_kwargs: object) -> None:
        raise PDFWriteError("synthetic mutation failure")

    monkeypatch.setattr(writer.mutation, "write_temporary", fail_mutation)

    with pytest.raises(PDFWriteError, match="mutation failure"):
        asyncio.run(writer.write_pdf(write_request(source)))

    assert not destination.exists()
    assert not list(tmp_path.glob(".published.pdf.*.tmp"))


def test_local_writer_preserves_reference_only_page_structure(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    destination = tmp_path / "published.pdf"
    write_source(source)
    add_reference_page(source)
    source_reader = PdfReader(source)
    source_reference = source_reader.pages[1]
    source_content = source_reference.get_contents()
    assert source_content is not None
    source_content_bytes = source_content.get_data()
    source_resource_fingerprint = PDFArtifactVerifier._fingerprint(
        source_reference["/Resources"], frozenset()
    )
    source_annotation_fingerprint = PDFArtifactVerifier._fingerprint(
        source_reference["/Annots"], frozenset()
    )
    writer = LocalPDFWriter(
        render_config=render_config(),
        template_policy=PDFTemplatePolicy(
            template_id="synthetic-v1",
            editable_pages=frozenset({1}),
            reference_only_pages=frozenset({2}),
        ),
    )

    result = asyncio.run(writer.write_pdf(write_request(source)))

    output_reference = PdfReader(destination).pages[1]
    output_content = output_reference.get_contents()
    assert output_content is not None
    assert result.page_count == 2
    assert output_content.get_data() == source_content_bytes
    assert (
        PDFArtifactVerifier._fingerprint(output_reference["/Resources"], frozenset())
        == source_resource_fingerprint
    )
    assert (
        PDFArtifactVerifier._fingerprint(output_reference["/Annots"], frozenset())
        == source_annotation_fingerprint
    )


def test_local_writer_embeds_and_extracts_generated_cjk_glyph(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    destination = tmp_path / "published.pdf"
    font_path = tmp_path / "synthetic-cjk.ttf"
    write_source(source)
    write_generated_cjk_font(font_path)
    labels = {grade.value: grade.value for grade in Grade}
    labels[Grade.EXCELLENT.value] = "中"
    writer = LocalPDFWriter(
        render_config=render_config(
            font_path=font_path,
            font_name="SyntheticCJK",
            grade_labels=labels,
        ),
        template_policy=PDFTemplatePolicy(
            template_id="synthetic-v1", editable_pages=frozenset({1})
        ),
    )
    request = write_request(source)
    request.field_map.fields = [
        pdf_field("cjk-grade", (170, 100, 280, 130), "fill_blank", value="target_grade")
    ]

    result = asyncio.run(writer.write_pdf(request))

    reader = PdfReader(destination)
    assert result.written_field_ids == ["cjk-grade"]
    assert "中" in reader.pages[0].extract_text()
    assert has_embedded_true_type_font(reader.pages[0]["/Resources"])


def test_verifier_rejects_a_changed_reference_only_page(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    temporary = tmp_path / "temporary.pdf"
    tampered = tmp_path / "tampered.pdf"
    write_source(source)
    add_reference_page(source)
    config = render_config()
    template_policy = PDFTemplatePolicy(
        template_id="synthetic-v1",
        editable_pages=frozenset({1}),
        reference_only_pages=frozenset({2}),
    )
    plan = PDFPreflightValidator(render_config=config, template_policy=template_policy).validate(
        write_request(source), source
    )
    mutation = PDFMutationExecutor(config).write_temporary(
        source_path=source,
        output_path=temporary,
        plan=plan,
    )
    extra_stream = BytesIO()
    canvas = Canvas(extra_stream, pagesize=(500, 250))
    canvas.drawString(40, 100, "UNAUTHORIZED CHANGE")
    canvas.save()
    extra_stream.seek(0)
    output_writer = PdfWriter(clone_from=PdfReader(temporary))
    output_writer.pages[1].merge_page(PdfReader(extra_stream).pages[0])
    with tampered.open("wb") as output:
        output_writer.write(output)

    with pytest.raises(PDFWriteError, match="Reference-only"):
        PDFArtifactVerifier(template_policy).verify(
            source_path=source,
            output_path=tampered,
            plan=plan,
            mutation_result=mutation,
        )
