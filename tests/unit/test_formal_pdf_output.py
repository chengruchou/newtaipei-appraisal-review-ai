"""Multi-context, placeholder and CJK output through the real local writer."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest
import reportlab
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from pydantic import ValidationError
from pypdf import PdfReader
from reportlab.pdfgen.canvas import Canvas

from appraisal_review.adapters.aws.pdf.s3_pdf_writer import S3PDFWriter
from appraisal_review.adapters.aws.storage.s3_object_store import S3ObjectStore
from appraisal_review.adapters.local.fake_pdf import FakePDFWriter
from appraisal_review.adapters.local.pdf_config import (
    PDFRenderConfig,
    PDFTemplatePolicy,
    field_map_sha256,
)
from appraisal_review.adapters.local.pdf_writer import LocalPDFWriter
from appraisal_review.adapters.local.placeholder_backfill import LocalPlaceholderBackfill
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
    PDFFontError,
    PDFReadError,
    PDFValueRef,
    PDFWriteError,
    PDFWriteRequest,
    UnsupportedDocumentURIError,
)
from appraisal_review.domain.review_contracts import ComparisonContext

TOKEN = "APR-PH-CASE-A3-0001"
SECOND_TOKEN = "APR-PH-CASE-A3-0002"


def vera_font() -> Path:
    return Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"


def render_config(**changes: object) -> PDFRenderConfig:
    values: dict[str, object] = {"font_path": vera_font()}
    values.update(changes)
    # The synthetic test composition explicitly approves its fixture font.
    # Passing None still exercises missing approval; production never auto-approves.
    values.setdefault(
        "approved_font_sha256", hashlib.sha256(Path(values["font_path"]).read_bytes()).hexdigest()
    )
    return PDFRenderConfig.model_validate(values)


def write_box_glyph_font(path: Path, characters: str, family: str = "SyntheticFormal") -> None:
    """Original synthetic fixture font; every covered codepoint gets its own glyph.

    reportlab caches loaded faces by internal PostScript name, so each distinct
    character set gets a distinct family to keep test fixtures independent.
    """
    covered = sorted({ord(char) for char in characters if char != " "})
    glyph_order = [".notdef", "space", *(f"uni{codepoint:04X}" for codepoint in covered)]
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
    builder.setupCharacterMap(
        {32: "space"} | {codepoint: f"uni{codepoint:04X}" for codepoint in covered}
    )
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics({name: (600, 0) for name in glyph_order})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable(
        {
            "familyName": family,
            "styleName": "Regular",
            "uniqueFontIdentifier": f"{family}-Regular",
            "fullName": f"{family} Regular",
            "psName": f"{family}-Regular",
        }
    )
    builder.setupOS2(sTypoAscender=800, sTypoDescender=-200, usWinAscent=800, usWinDescent=200)
    builder.setupPost()
    builder.setupMaxp()
    builder.save(str(path))


def write_template(path: Path, page_sizes: list[tuple[float, float]]) -> str:
    canvas = Canvas(str(path), pagesize=page_sizes[0], invariant=1)
    canvas.setAuthor("")
    canvas.setTitle("Synthetic formal template")
    for size in page_sizes:
        canvas.setPageSize(size)
        canvas.showPage()
    canvas.save()
    return hashlib.sha256(path.read_bytes()).hexdigest()


def comparison(comparable_id: str, rate: float, total: float) -> FactorReviewResult:
    return FactorReviewResult(
        case_id="case-1",
        rule_set_id="rules-1",
        rule_version="1",
        context=ComparisonContext(
            scope="regional", target_id="target-a", comparable_id=comparable_id
        ),
        results=[
            FactorEvaluationResult(
                factor_id="road.width",
                target_grade=Grade.EXCELLENT,
                comparable_grade=Grade.NORMAL,
                adjustment_percent=rate,
                rule_id="road.width.v1",
                calculation_trace="synthetic",
                status=EvaluationStatus.VERIFIED,
            )
        ],
        summary=EvaluationSummary(total_adjustment_percent=total, status=EvaluationStatus.VERIFIED),
    )


def value_field(
    field_id: str,
    page: int,
    box: tuple[float, float, float, float],
    comparable_id: str,
    value: str = "adjustment_percent",
) -> PDFField:
    return PDFField(
        field_id=field_id,
        page=page,
        bounding_box=box,
        value_ref=PDFValueRef(
            scope="regional",
            target_id="target-a",
            comparable_id=comparable_id,
            value=value,  # type: ignore[arg-type]
            factor_id=None if value == "total_adjustment_percent" else "road.width",
        ),
    )


def multi_context_map() -> PDFFieldMap:
    return PDFFieldMap(
        template_id="formal-v1",
        fields=[
            value_field("c1-rate", 1, (40.0, 150.0, 200.0, 180.0), "comp-1"),
            value_field(
                "c1-total", 1, (40.0, 100.0, 200.0, 130.0), "comp-1", "total_adjustment_percent"
            ),
            value_field("c2-rate", 2, (40.0, 150.0, 200.0, 180.0), "comp-2"),
            value_field(
                "c2-total", 2, (40.0, 100.0, 200.0, 130.0), "comp-2", "total_adjustment_percent"
            ),
        ],
    )


def policy_for(
    field_map: PDFFieldMap, template_sha: str, pages: frozenset[int]
) -> PDFTemplatePolicy:
    return PDFTemplatePolicy(
        template_id=field_map.template_id,
        template_sha256=template_sha,
        field_map_sha256=field_map_sha256(field_map),
        editable_pages=pages,
    )


def multi_request(
    tmp_path: Path, destination: str = "output.pdf"
) -> tuple[PDFWriteRequest, PDFTemplatePolicy]:
    template = tmp_path / "template.pdf"
    sha = (
        write_template(template, [(320.0, 220.0), (320.0, 220.0)])
        if not template.exists()
        else hashlib.sha256(template.read_bytes()).hexdigest()
    )
    field_map = multi_context_map()
    request = PDFWriteRequest(
        source_uri=template.as_uri(),
        destination_uri=(tmp_path / destination).as_uri(),
        result=comparison("comp-1", 5.0, 5.0),
        additional_results=[comparison("comp-2", -3.0, -3.0)],
        field_map=field_map,
    )
    return request, policy_for(field_map, sha, frozenset({1, 2}))


def test_multi_context_write_places_each_bound_comparison(tmp_path: Path) -> None:
    request, policy = multi_request(tmp_path)
    writer = LocalPDFWriter(render_config=render_config(), template_policy=policy)
    result = asyncio.run(writer.write_pdf(request))
    assert result.artifact_created
    assert result.written_field_ids == ["c1-rate", "c1-total", "c2-rate", "c2-total"]
    reader = PdfReader(tmp_path / "output.pdf")
    assert len(reader.pages) == 2
    first, second = (page.extract_text() for page in reader.pages)
    assert "+5.00%" in first and "-3.00%" not in first
    assert "-3.00%" in second and "+5.00%" not in second


def test_multi_context_output_and_manifest_are_deterministic(tmp_path: Path) -> None:
    request, policy = multi_request(tmp_path, "first.pdf")
    writer = LocalPDFWriter(render_config=render_config(), template_policy=policy)
    first = asyncio.run(writer.write_pdf(request))
    again, _ = multi_request(tmp_path, "second.pdf")
    second = asyncio.run(writer.write_pdf(again))
    assert first.written_field_ids == second.written_field_ids
    assert first.page_count == second.page_count
    first_bytes = (tmp_path / "first.pdf").read_bytes()
    second_bytes = (tmp_path / "second.pdf").read_bytes()
    assert hashlib.sha256(first_bytes).hexdigest() == hashlib.sha256(second_bytes).hexdigest()
    reader = PdfReader(tmp_path / "first.pdf")
    assert reader.metadata is not None
    assert reader.metadata.get("/AppraisalReviewWriterVersion") == "2"


def test_multi_context_request_validation_fails_closed(tmp_path: Path) -> None:
    request, _ = multi_request(tmp_path)
    unbound = comparison("comp-2", -3.0, -3.0).model_copy(update={"context": None})
    with pytest.raises(ValidationError, match="must bind its context"):
        PDFWriteRequest.model_validate(
            {**request.model_dump(), "additional_results": [unbound.model_dump()]}
        )
    with pytest.raises(ValidationError, match="must be unique"):
        PDFWriteRequest.model_validate(
            {**request.model_dump(), "additional_results": [request.result.model_dump()]}
        )
    other_case = comparison("comp-2", -3.0, -3.0).model_copy(update={"case_id": "case-2"})
    with pytest.raises(ValidationError, match="belong to one case"):
        PDFWriteRequest.model_validate(
            {**request.model_dump(), "additional_results": [other_case.model_dump()]}
        )
    # A field map that leaves any verified comparison unwritten is a partial
    # report and stays unsupported instead of silently dropping a context.
    with pytest.raises(ValidationError, match="partial report is unsupported"):
        PDFWriteRequest.model_validate(
            {
                **request.model_dump(),
                "additional_results": [
                    comparison("comp-2", -3.0, -3.0).model_dump(),
                    comparison("comp-3", 1.0, 1.0).model_dump(),
                ],
            }
        )
    extra_field = value_field("c3-rate", 2, (210.0, 150.0, 310.0, 180.0), "comp-3")
    covering_map = multi_context_map()
    covering_map = PDFFieldMap(
        template_id=covering_map.template_id, fields=[*covering_map.fields, extra_field]
    )
    with pytest.raises(ValidationError, match="does not match verified comparison"):
        PDFWriteRequest.model_validate(
            {**request.model_dump(), "field_map": covering_map.model_dump()}
        )
    # Without additional results the legacy single-context rules are preserved.
    unbound_single = comparison("comp-1", 5.0, 5.0).model_copy(update={"context": None})
    with pytest.raises(ValidationError, match="one comparison context"):
        PDFWriteRequest.model_validate(
            {
                **request.model_dump(),
                "additional_results": [],
                "result": unbound_single.model_dump(),
            }
        )
    with pytest.raises(ValidationError, match="does not match verified comparison"):
        PDFWriteRequest.model_validate({**request.model_dump(), "additional_results": []})


def test_only_capable_writers_advertise_multiple_contexts() -> None:
    assert not getattr(FakePDFWriter(), "supports_multiple_contexts", False)
    assert LocalPDFWriter.supports_multiple_contexts is True

    class NoTransfer:
        def download_file(self, Bucket: str, Key: str, Filename: str) -> None:
            raise AssertionError("no transfer expected")

        def put_object(self, **kwargs: object) -> None:
            raise AssertionError("no transfer expected")

    wrapper = S3PDFWriter(
        object_store=S3ObjectStore(NoTransfer()),
        local_writer=LocalPDFWriter(
            render_config=render_config(),
            template_policy=policy_for(multi_context_map(), "0" * 64, frozenset({1, 2})),
        ),
    )
    assert wrapper.supports_multiple_contexts is True
    assert not S3PDFWriter(
        object_store=S3ObjectStore(NoTransfer()), local_writer=FakePDFWriter()
    ).supports_multiple_contexts


def placeholder_map(second_field: bool = False) -> PDFFieldMap:
    fields = [
        PDFField(
            field_id="case-identity",
            page=1,
            bounding_box=(40.0, 150.0, 260.0, 180.0),
            placeholder_token=TOKEN,
        )
    ]
    if second_field:
        fields.append(
            PDFField(
                field_id="parcel-identity",
                page=1,
                bounding_box=(40.0, 100.0, 260.0, 130.0),
                placeholder_token=SECOND_TOKEN,
            )
        )
    return PDFFieldMap(template_id="formal-v1", fields=fields)


def placeholder_request(
    tmp_path: Path, field_map: PDFFieldMap, destination: str = "cloud.pdf"
) -> tuple[PDFWriteRequest, PDFTemplatePolicy]:
    template = tmp_path / "template.pdf"
    sha = (
        write_template(template, [(320.0, 220.0)])
        if not template.exists()
        else hashlib.sha256(template.read_bytes()).hexdigest()
    )
    request = PDFWriteRequest(
        source_uri=template.as_uri(),
        destination_uri=(tmp_path / destination).as_uri(),
        result=comparison("comp-1", 5.0, 5.0).model_copy(update={"context": None}),
        field_map=field_map,
    )
    return request, policy_for(field_map, sha, frozenset({1}))


def test_cloud_write_renders_only_the_opaque_token(tmp_path: Path) -> None:
    request, policy = placeholder_request(tmp_path, placeholder_map())
    writer = LocalPDFWriter(render_config=render_config(), template_policy=policy)
    result = asyncio.run(writer.write_pdf(request))
    assert result.written_field_ids == ["case-identity"]
    text = PdfReader(tmp_path / "cloud.pdf").pages[0].extract_text()
    assert TOKEN in text


def test_field_binds_exactly_one_value_source() -> None:
    with pytest.raises(ValidationError, match="not both"):
        PDFField(
            field_id="conflicted",
            page=1,
            bounding_box=(1.0, 1.0, 30.0, 20.0),
            value_ref=PDFValueRef(
                scope="regional",
                target_id="target-a",
                comparable_id="comp-1",
                value="adjustment_percent",
                factor_id="road.width",
            ),
            placeholder_token=TOKEN,
        )
    field_map = PDFFieldMap(
        template_id="formal-v1",
        fields=[PDFField(field_id="empty", page=1, bounding_box=(1.0, 1.0, 30.0, 20.0))],
    )
    with pytest.raises(ValidationError, match="explicit value reference"):
        PDFWriteRequest(
            source_uri="file:///tmp/template.pdf",
            destination_uri="file:///tmp/out.pdf",
            result=comparison("comp-1", 5.0, 5.0),
            field_map=field_map,
        )


def test_local_backfill_reveals_cjk_values_and_binds_the_cloud_artifact(
    tmp_path: Path,
) -> None:
    revealed = "新北市板橋區文化路"
    font_path = tmp_path / "formal.ttf"
    write_box_glyph_font(
        font_path,
        revealed + "第一二三小段" + TOKEN + SECOND_TOKEN + "+%.0123456789",
        family="SyntheticFormalA",
    )
    field_map = placeholder_map(second_field=True)
    request, policy = placeholder_request(tmp_path, field_map)
    cloud_writer = LocalPDFWriter(
        render_config=render_config(font_path=font_path, font_name="SyntheticFormalA"),
        template_policy=policy,
    )
    cloud_result = asyncio.run(cloud_writer.write_pdf(request))
    assert cloud_result.artifact_created
    cloud_bytes = (tmp_path / "cloud.pdf").read_bytes()
    cloud_sha = hashlib.sha256(cloud_bytes).hexdigest()
    assert TOKEN in PdfReader(tmp_path / "cloud.pdf").pages[0].extract_text()

    backfill = LocalPlaceholderBackfill(
        render_config=render_config(font_path=font_path, font_name="SyntheticFormalA"),
        template_policy=policy,
        values={TOKEN: revealed, SECOND_TOKEN: "第一二三小段"},
    )
    local_request = PDFWriteRequest.model_validate(
        {**request.model_dump(), "destination_uri": (tmp_path / "revealed.pdf").as_uri()}
    )
    result = asyncio.run(
        backfill.backfill(
            local_request,
            placeholder_artifact=tmp_path / "cloud.pdf",
            expected_artifact_sha256=cloud_sha,
        )
    )
    assert result.artifact_created
    text = PdfReader(tmp_path / "revealed.pdf").pages[0].extract_text()
    assert revealed in text and "第一二三小段" in text
    assert TOKEN not in text and SECOND_TOKEN not in text
    # The verified cloud artifact was never edited in place.
    assert hashlib.sha256((tmp_path / "cloud.pdf").read_bytes()).hexdigest() == cloud_sha


def test_backfill_guards_fail_closed(tmp_path: Path) -> None:
    font_path = tmp_path / "formal.ttf"
    write_box_glyph_font(font_path, TOKEN + SECOND_TOKEN + "value-ontw", family="SyntheticFormalB")
    field_map = placeholder_map(second_field=True)
    request, policy = placeholder_request(tmp_path, field_map, "revealed.pdf")
    with pytest.raises(ValueError, match="at least one placeholder value"):
        LocalPlaceholderBackfill(
            render_config=render_config(font_path=font_path, font_name="SyntheticFormalB"),
            template_policy=policy,
            values={},
        )
    with pytest.raises(ValueError, match="opaque placeholder tokens"):
        LocalPlaceholderBackfill(
            render_config=render_config(font_path=font_path, font_name="SyntheticFormalB"),
            template_policy=policy,
            values={"raw-name": "value"},
        )
    partial = LocalPlaceholderBackfill(
        render_config=render_config(font_path=font_path, font_name="SyntheticFormalB"),
        template_policy=policy,
        values={TOKEN: "value-one"},
    )
    with pytest.raises(PDFWriteError, match="not locally configured"):
        asyncio.run(partial.backfill(request))
    assert not (tmp_path / "revealed.pdf").exists()
    complete = LocalPlaceholderBackfill(
        render_config=render_config(font_path=font_path, font_name="SyntheticFormalB"),
        template_policy=policy,
        values={TOKEN: "value-one", SECOND_TOKEN: "value-two"},
    )
    remote = PDFWriteRequest.model_validate(
        {**request.model_dump(), "destination_uri": "s3://result-bucket/reviews/out.pdf"}
    )
    with pytest.raises(UnsupportedDocumentURIError, match="local files"):
        asyncio.run(complete.backfill(remote))
    with pytest.raises(PDFReadError, match="differs from the verified write"):
        asyncio.run(
            complete.backfill(
                request,
                placeholder_artifact=request_template(tmp_path),
                expected_artifact_sha256="0" * 64,
            )
        )
    # Coverage failure keeps CJK output honest instead of producing tofu.
    uncovered = LocalPlaceholderBackfill(
        render_config=render_config(font_path=font_path, font_name="SyntheticFormalB"),
        template_policy=policy,
        values={TOKEN: "未覆蓋字", SECOND_TOKEN: "value-two"},
    )
    with pytest.raises(PDFFontError, match="every required glyph"):
        asyncio.run(uncovered.backfill(request))


def request_template(tmp_path: Path) -> Path:
    return tmp_path / "template.pdf"


def test_publication_wrappers_refuse_revealing_writers(tmp_path: Path) -> None:
    font_path = tmp_path / "formal.ttf"
    write_box_glyph_font(font_path, TOKEN, family="SyntheticFormalC")
    field_map = placeholder_map()
    _, policy = placeholder_request(tmp_path, field_map)
    revealing = LocalPDFWriter(
        render_config=render_config(font_path=font_path, font_name="SyntheticFormalC"),
        template_policy=policy,
        placeholder_values={TOKEN: "secret"},
    )

    class NoTransfer:
        def download_file(self, Bucket: str, Key: str, Filename: str) -> None:
            raise AssertionError("no transfer expected")

        def put_object(self, **kwargs: object) -> None:
            raise AssertionError("no transfer expected")

    with pytest.raises(PDFWriteError, match="never be published"):
        S3PDFWriter(object_store=S3ObjectStore(NoTransfer()), local_writer=revealing)


def test_cjk_grade_labels_render_with_generated_font(tmp_path: Path) -> None:
    labels = {
        "excellent": "優",
        "slightly_superior": "略優",
        "normal": "普通",
        "slightly_inferior": "略劣",
        "inferior": "劣",
    }
    font_path = tmp_path / "formal.ttf"
    write_box_glyph_font(font_path, "優略普通劣+%.0123456789", family="SyntheticFormalD")
    template = tmp_path / "template.pdf"
    sha = write_template(template, [(320.0, 220.0)])
    field_map = PDFFieldMap(
        template_id="formal-v1",
        fields=[
            PDFField(
                field_id="target-grade",
                page=1,
                bounding_box=(40.0, 150.0, 200.0, 180.0),
                value_ref=PDFValueRef(
                    scope="regional",
                    target_id="target-a",
                    comparable_id="comp-1",
                    value="target_grade",
                    factor_id="road.width",
                ),
            )
        ],
    )
    request = PDFWriteRequest(
        source_uri=template.as_uri(),
        destination_uri=(tmp_path / "graded.pdf").as_uri(),
        result=comparison("comp-1", 5.0, 5.0),
        field_map=field_map,
    )
    writer = LocalPDFWriter(
        render_config=render_config(
            font_path=font_path, font_name="SyntheticFormalD", grade_labels=labels
        ),
        template_policy=policy_for(field_map, sha, frozenset({1})),
    )
    result = asyncio.run(writer.write_pdf(request))
    assert result.artifact_created
    assert "優" in PdfReader(tmp_path / "graded.pdf").pages[0].extract_text()
