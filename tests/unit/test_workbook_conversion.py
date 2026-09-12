"""Check a converted official table without trusting the converter's own report."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError
from reportlab.pdfgen import canvas

from appraisal_review.adapters.local.converted_pdf import (
    ConversionInspectionError,
    inspect_converted_pdf,
)
from appraisal_review.domain.workbook_conversion import (
    ConversionExpectation,
    ConversionRecord,
    FontObservation,
    PageObservation,
    verify_conversion,
)

SOURCE_DIGEST = "f8" + "0" * 62
OTHER_DIGEST = "c5" + "0" * 62
NOTE = "considers individual factor items 1-5"


def _pdf(path: Path, pages: tuple[str, ...]) -> Path:
    document = canvas.Canvas(str(path))
    for text in pages:
        if text:
            document.drawString(72, 720, text)
        document.showPage()
    document.save()
    return path


def _expectation(**overrides: object) -> ConversionExpectation:
    fields: dict[str, object] = {
        "source_name": "table4.xlsx",
        "source_digest": SOURCE_DIGEST,
    }
    fields.update(overrides)
    return ConversionExpectation.model_validate(fields)


def _record(**overrides: object) -> ConversionRecord:
    fields: dict[str, object] = {
        "source_digest": SOURCE_DIGEST,
        "converter": "soffice 25.2",
        "output_name": "table4.pdf",
        "output_digest": "a" * 64,
        "output_byte_size": 1024,
        "pages": (PageObservation(number=1, width=595, height=842, character_count=400),),
        "fonts": (FontObservation(name="AAAAAA+NotoSansCJK", embedded=True, pages=(1,)),),
    }
    fields.update(overrides)
    return ConversionRecord.model_validate(fields)


def test_inspection_records_pages_digest_and_embedded_fonts(tmp_path: Path) -> None:
    path = _pdf(tmp_path / "table4.pdf", ("Table 4 page one", "page two"))

    record = inspect_converted_pdf(
        path, source_digest=SOURCE_DIGEST, converter="reportlab", probes=("page two", NOTE)
    )

    assert record.output_digest == hashlib.sha256(path.read_bytes()).hexdigest()
    assert record.output_name == "table4.pdf"
    assert [page.number for page in record.pages] == [1, 2]
    assert record.pages[0].character_count > 0
    assert record.pages[0].width == pytest.approx(595, abs=1)
    assert record.encrypted is False
    assert record.found_text == ("page two",)
    assert record.source_digest == SOURCE_DIGEST


def test_inspection_reports_a_standard_font_as_not_embedded(tmp_path: Path) -> None:
    path = _pdf(tmp_path / "helvetica.pdf", ("plain text",))

    record = inspect_converted_pdf(path, source_digest=SOURCE_DIGEST, converter="reportlab")

    assert [font.name for font in record.fonts] == ["Helvetica"]
    assert record.fonts[0].embedded is False
    assert record.fonts[0].subset is False
    assert [finding.code for finding in verify_conversion(_expectation(), record)] == [
        "font_not_embedded"
    ]


def test_a_blank_page_is_reported_rather_than_counted_as_converted(tmp_path: Path) -> None:
    path = _pdf(tmp_path / "blank.pdf", ("first page", ""))

    record = inspect_converted_pdf(path, source_digest=SOURCE_DIGEST, converter="reportlab")
    codes = [finding.code for finding in verify_conversion(_expectation(), record)]

    assert record.pages[1].character_count == 0
    assert "blank_page" in codes


def test_a_pdf_converted_from_another_workbook_is_rejected() -> None:
    findings = verify_conversion(_expectation(), _record(source_digest=OTHER_DIGEST))

    assert [finding.code for finding in findings] == ["source_digest_mismatch"]
    assert OTHER_DIGEST in findings[0].detail


def test_content_lost_outside_the_print_area_is_reported_as_missing(tmp_path: Path) -> None:
    """Table 4's A37 note sits outside the print area and can vanish on conversion."""

    path = _pdf(tmp_path / "cropped.pdf", ("Table 4 body without the closing note",))

    record = inspect_converted_pdf(
        path, source_digest=SOURCE_DIGEST, converter="reportlab", probes=(NOTE,)
    )
    findings = verify_conversion(_expectation(required_text=(NOTE,), expected_page_count=1), record)

    assert record.found_text == ()
    assert [finding.code for finding in findings] == ["font_not_embedded", "missing_required_text"]


def test_a_leaked_hidden_example_is_reported(tmp_path: Path) -> None:
    path = _pdf(tmp_path / "leaked.pdf", ("101 legacy example workbook",))

    record = inspect_converted_pdf(
        path,
        source_digest=SOURCE_DIGEST,
        converter="reportlab",
        probes=("101 legacy example",),
    )
    findings = verify_conversion(_expectation(forbidden_text=("101 legacy example",)), record)

    assert "forbidden_text_present" in [finding.code for finding in findings]


def test_page_count_mismatch_and_empty_output_are_distinct() -> None:
    mismatch = verify_conversion(_expectation(expected_page_count=2), _record())
    empty = verify_conversion(_expectation(expected_page_count=2), _record(pages=(), fonts=()))

    assert [finding.code for finding in mismatch] == ["page_count_mismatch"]
    assert [finding.code for finding in empty] == ["no_pages"]


def test_a_consistent_conversion_produces_no_findings(tmp_path: Path) -> None:
    record = _record(found_text=(NOTE,))

    assert (
        verify_conversion(_expectation(required_text=(NOTE,), expected_page_count=1), record) == ()
    )


def test_contracts_reject_inconsistent_conversion_records() -> None:
    with pytest.raises(ValidationError):
        ConversionExpectation(
            source_name="table4.xlsx",
            source_digest=SOURCE_DIGEST,
            required_text=(NOTE,),
            forbidden_text=(NOTE,),
        )
    with pytest.raises(ValidationError):
        _record(pages=(PageObservation(number=2, width=1, height=1, character_count=0),))
    with pytest.raises(ValidationError):
        _record(fonts=(FontObservation(name="X", embedded=True, pages=(3,)),))


def test_an_unreadable_output_fails_explicitly(tmp_path: Path) -> None:
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"not a pdf")

    with pytest.raises(ConversionInspectionError) as error:
        inspect_converted_pdf(path, source_digest=SOURCE_DIGEST, converter="reportlab")

    assert error.value.code == "unreadable_output"
