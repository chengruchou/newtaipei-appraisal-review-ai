from __future__ import annotations

import asyncio
import hashlib
from io import BytesIO
from pathlib import Path

import pytest
import reportlab
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.annotations import FreeText
from pypdf.generic import (
    ArrayObject,
    BooleanObject,
    DictionaryObject,
    FloatObject,
    NameObject,
    NumberObject,
    RectangleObject,
)
from reportlab.pdfgen.canvas import Canvas

from appraisal_review.adapters.local.pdf_config import (
    PDFRenderConfig,
    PDFTemplatePolicy,
    field_map_sha256,
)
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
    PDFFieldPlacementError,
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


def write_unsafe_source(path: Path, kind: str) -> None:
    stream = BytesIO()
    canvas = Canvas(stream, pagesize=(320, 220))
    if kind == "scaled_text":
        text = canvas.beginText(55, 68)
        text.setHorizScale(300)
        text.textOut("STALE")
        canvas.drawText(text)
    elif kind == "inline_image":
        canvas.drawInlineImage(Image.new("RGB", (8, 8), "black"), 180, 105, 40, 20)
    elif kind == "painted_vector":
        canvas.rect(45, 55, 90, 35, fill=1, stroke=0)
    elif kind == "thick_stroke":
        canvas.setStrokeColorRGB(0, 0, 0)
        canvas.setLineWidth(35)
        canvas.line(45, 72.5, 135, 72.5)
    elif kind == "wide_table_border":
        canvas.setLineWidth(35)
        canvas.rect(45, 55, 90, 35, fill=0, stroke=1)
    elif kind == "transformed_wide_table_border":
        canvas.saveState()
        canvas.scale(3, 1)
        canvas.setLineWidth(1)
        canvas.rect(15, 55, 30, 35, fill=0, stroke=1)
        canvas.restoreState()
    elif kind == "nondefault_border_style":
        canvas.setLineCap(1)
        canvas.setLineJoin(1)
        canvas.rect(45, 55, 90, 35, fill=0, stroke=1)
    elif kind in {
        "extgstate_thick_border",
        "extgstate_stroke_adjustment",
        "extgstate_no_stroke_adjustment",
        "table_border",
    }:
        canvas.rect(45, 55, 90, 35, fill=0, stroke=1)
    elif kind == "line_table_border":
        for edge in (
            (45, 55, 135, 55),
            (135, 55, 135, 90),
            (135, 90, 45, 90),
            (45, 90, 45, 55),
        ):
            canvas.line(*edge)
    elif kind == "transformed_table_border":
        canvas.saveState()
        canvas.setLineWidth(35)
        canvas.line(250, 180, 290, 180)
        canvas.restoreState()
        canvas.saveState()
        canvas.translate(5, 5)
        canvas.rect(40, 50, 90, 35, fill=0, stroke=1)
        canvas.restoreState()
    elif kind == "custom_font_widths":
        canvas.drawString(55, 68, "STALE")
    else:
        raise ValueError("Unsupported synthetic PDF fixture")
    canvas.save()
    path.write_bytes(stream.getvalue())
    if kind == "extgstate_thick_border":
        apply_extgstate(path, line_width=35)
    elif kind == "extgstate_stroke_adjustment":
        apply_extgstate(path, stroke_adjustment=True)
    elif kind == "extgstate_no_stroke_adjustment":
        apply_extgstate(path, stroke_adjustment=False)
    if kind == "custom_font_widths":
        replace_helvetica_widths(path)


def apply_extgstate(
    path: Path,
    *,
    line_width: int | None = None,
    stroke_adjustment: bool | None = None,
) -> None:
    writer = PdfWriter(clone_from=PdfReader(BytesIO(path.read_bytes())))
    page = writer.pages[0]
    resources = page["/Resources"].get_object()
    states = resources.get("/ExtGState")
    if states is None:
        states = DictionaryObject()
        resources[NameObject("/ExtGState")] = states
    else:
        states = states.get_object()
    parameters = DictionaryObject({NameObject("/Type"): NameObject("/ExtGState")})
    if line_width is not None:
        parameters[NameObject("/LW")] = NumberObject(line_width)
    if stroke_adjustment is not None:
        parameters[NameObject("/SA")] = BooleanObject(stroke_adjustment)
    states[NameObject("/GSUnsafe")] = writer._add_object(parameters)
    content = page.get_contents()
    assert content is not None
    content.operations.insert(0, ([NameObject("/GSUnsafe")], b"gs"))
    page.replace_contents(content)
    with path.open("wb") as output:
        writer.write(output)


def replace_helvetica_widths(path: Path) -> None:
    writer = PdfWriter(clone_from=PdfReader(BytesIO(path.read_bytes())))
    fonts = writer.pages[0]["/Resources"]["/Font"].get_object().values()
    font = next(
        reference.get_object()
        for reference in fonts
        if str(reference.get_object().get("/BaseFont")) == "/Helvetica"
    )
    font[NameObject("/FirstChar")] = NumberObject(0)
    font[NameObject("/LastChar")] = NumberObject(255)
    font[NameObject("/Widths")] = ArrayObject([NumberObject(2000) for _ in range(256)])
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


def trusted_policy(
    source: Path,
    field_map: PDFFieldMap | None = None,
    *,
    editable_pages: frozenset[int] = frozenset({1}),
    reference_only_pages: frozenset[int] = frozenset(),
) -> PDFTemplatePolicy:
    approved_map = field_map or write_request(source).field_map
    return PDFTemplatePolicy(
        template_id="synthetic-v1",
        template_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        field_map_sha256=field_map_sha256(approved_map),
        editable_pages=editable_pages,
        reference_only_pages=reference_only_pages,
    )


def execute(source: Path, temporary: Path, config: PDFRenderConfig) -> None:
    template_policy = trusted_policy(source)
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
        template_policy=trusted_policy(source, request.field_map),
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
        template_policy=trusted_policy(source),
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
        template_policy=trusted_policy(source),
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
        template_policy=trusted_policy(source),
    )

    result = asyncio.run(writer.write_pdf(write_request(source)))

    assert result.output_uri == destination.as_uri()
    assert result.page_count == 1
    assert result.written_field_ids == ["blank", "note", "replacement"]
    assert result.warnings == []
    assert destination.is_file()
    assert hashlib.sha256(source.read_bytes()).digest() == original_hash
    assert not list(tmp_path.glob(".published.pdf.*.tmp"))


def test_local_writer_rejects_same_page_count_template_substitution(
    tmp_path: Path,
) -> None:
    approved = tmp_path / "approved.pdf"
    substituted = tmp_path / "substituted.pdf"
    destination = tmp_path / "published.pdf"
    write_source(approved)
    write_source(substituted, rotation=90)
    approved_request = write_request(approved)
    substituted_request = approved_request.model_copy(
        update={
            "source_uri": substituted.as_uri(),
            "destination_uri": destination.as_uri(),
        }
    )
    writer = LocalPDFWriter(
        render_config=render_config(),
        template_policy=trusted_policy(approved, approved_request.field_map),
    )

    with pytest.raises(PDFFieldPlacementError, match="trusted template bytes"):
        asyncio.run(writer.write_pdf(substituted_request))

    assert not destination.exists()
    assert not list(tmp_path.glob(".published.pdf.*.tmp"))


def test_local_writer_does_not_publish_when_reopen_verification_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.pdf"
    destination = tmp_path / "published.pdf"
    write_source(source)
    writer = LocalPDFWriter(
        render_config=render_config(),
        template_policy=trusted_policy(source),
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
        template_policy=trusted_policy(source),
    )

    def fail_mutation(**_kwargs: object) -> None:
        raise PDFWriteError("synthetic mutation failure")

    monkeypatch.setattr(writer.mutation, "write_temporary", fail_mutation)

    with pytest.raises(PDFWriteError, match="mutation failure"):
        asyncio.run(writer.write_pdf(write_request(source)))

    assert not destination.exists()
    assert not list(tmp_path.glob(".published.pdf.*.tmp"))


@pytest.mark.parametrize("name", ["output%20file.pdf", "output%2Ffile.pdf", "100%.pdf"])
def test_local_writer_preserves_literal_percent_filename_and_uri(tmp_path: Path, name: str) -> None:
    source = tmp_path / "source.pdf"
    destination = tmp_path / name
    write_source(source)
    request = write_request(source).model_copy(update={"destination_uri": destination.as_uri()})
    writer = LocalPDFWriter(
        render_config=render_config(),
        template_policy=trusted_policy(source, request.field_map),
    )

    result = asyncio.run(writer.write_pdf(request))

    assert result.output_uri == destination.as_uri()
    assert destination.is_file()


@pytest.mark.parametrize("kind", ["scaled_text", "inline_image"])
def test_local_writer_rejects_ambiguous_occupancy_without_publication(
    tmp_path: Path, kind: str
) -> None:
    source = tmp_path / f"{kind}.pdf"
    destination = tmp_path / "published.pdf"
    write_unsafe_source(source, kind)
    request = write_request(source)
    request.field_map.fields = [
        pdf_field(
            "unsafe",
            (45, 55, 110, 90) if kind == "scaled_text" else (170, 80, 230, 120),
            "correct" if kind == "scaled_text" else "fill_blank",
        )
    ]
    writer = LocalPDFWriter(
        render_config=render_config(),
        template_policy=trusted_policy(source, request.field_map),
    )

    with pytest.raises(PDFFieldPlacementError):
        asyncio.run(writer.write_pdf(request))

    assert not destination.exists()
    assert not list(tmp_path.glob(".published.pdf.*.tmp"))


@pytest.mark.parametrize(
    "kind",
    [
        "painted_vector",
        "thick_stroke",
        "wide_table_border",
        "transformed_wide_table_border",
        "nondefault_border_style",
        "extgstate_thick_border",
        "extgstate_stroke_adjustment",
        "custom_font_widths",
    ],
)
def test_local_writer_rejects_untrusted_paint_or_font_before_publication(
    tmp_path: Path, kind: str
) -> None:
    source = tmp_path / f"{kind}.pdf"
    destination = tmp_path / "published.pdf"
    write_unsafe_source(source, kind)
    source_bytes = source.read_bytes()
    request = write_request(source)
    paint_occupancy = kind != "custom_font_widths"
    request.field_map.fields = [
        pdf_field(
            "unsafe",
            (45, 55, 135, 90) if paint_occupancy else (45, 55, 110, 90),
            "fill_blank" if paint_occupancy else "correct",
        )
    ]
    writer = LocalPDFWriter(
        render_config=render_config(),
        template_policy=trusted_policy(source, request.field_map),
    )

    if kind == "custom_font_widths":
        expected_error = "Source PDF font metrics cannot be established safely"
    elif kind == "extgstate_stroke_adjustment":
        expected_error = "stroke adjustment geometry cannot be established safely"
    else:
        expected_error = "occupied"
    with pytest.raises(PDFFieldPlacementError, match=expected_error):
        asyncio.run(writer.write_pdf(request))

    assert source.read_bytes() == source_bytes
    assert not destination.exists()
    assert not list(tmp_path.glob(".published.pdf.*.tmp"))


@pytest.mark.parametrize(
    "kind",
    [
        "table_border",
        "line_table_border",
        "transformed_table_border",
        "extgstate_no_stroke_adjustment",
    ],
)
def test_local_writer_fills_empty_cell_with_ordinary_table_border(
    tmp_path: Path, kind: str
) -> None:
    source = tmp_path / f"{kind}.pdf"
    destination = tmp_path / "published.pdf"
    write_unsafe_source(source, kind)
    request = write_request(source)
    request.field_map.fields = [pdf_field("bordered-blank", (45, 55, 135, 90), "fill_blank")]
    writer = LocalPDFWriter(
        render_config=render_config(),
        template_policy=trusted_policy(source, request.field_map),
    )

    result = asyncio.run(writer.write_pdf(request))

    assert result.written_field_ids == ["bordered-blank"]
    assert destination.is_file()
    assert "+5.00%" in PdfReader(destination).pages[0].extract_text()


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
        template_policy=trusted_policy(
            source,
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
    request = write_request(source)
    request.field_map.fields = [
        pdf_field("cjk-grade", (170, 100, 280, 130), "fill_blank", value="target_grade")
    ]
    writer = LocalPDFWriter(
        render_config=render_config(
            font_path=font_path,
            font_name="SyntheticCJK",
            grade_labels=labels,
        ),
        template_policy=trusted_policy(source, request.field_map),
    )

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
    template_policy = trusted_policy(
        source,
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
