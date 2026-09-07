from io import BytesIO

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    FloatObject,
    NameObject,
    NumberObject,
    RectangleObject,
    TextStringObject,
)
from reportlab.pdfgen.canvas import Canvas

from appraisal_review.adapters.local.pdf_overlay import PageGeometry, remove_text_in_box
from appraisal_review.domain.pdf_models import PDFFieldPlacementError


def synthetic_page(*, rotation: int = 0):
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
    return page


@pytest.mark.parametrize(
    ("rotation", "point", "expected"),
    [
        (0, (0, 0), (0, 0)),
        (0, (300, 190), (300, 190)),
        (0, (150, 95), (150, 95)),
        (90, (0, 0), (0, 300)),
        (90, (300, 190), (190, 0)),
        (90, (150, 95), (95, 150)),
        (180, (0, 0), (300, 190)),
        (180, (300, 190), (0, 0)),
        (180, (150, 95), (150, 95)),
        (270, (0, 0), (190, 0)),
        (270, (300, 190), (0, 300)),
        (270, (150, 95), (95, 150)),
    ],
)
def test_crop_box_corner_and_center_conversion(
    rotation: int, point: tuple[int, int], expected: tuple[int, int]
) -> None:
    geometry = PageGeometry.from_page(synthetic_page(rotation=rotation))

    assert geometry.to_page_user_space(point) == (point[0] + 10, point[1] + 20)
    assert geometry.to_rotated_crop_space(point) == expected


def test_mixed_page_dimensions_are_inspected_independently(tmp_path) -> None:
    source = tmp_path / "mixed.pdf"
    writer = PdfWriter()
    first = writer.add_blank_page(width=200, height=300)
    first.cropbox = RectangleObject((10, 20, 190, 280))
    second = writer.add_blank_page(width=500, height=250)
    second.cropbox = RectangleObject((25, 5, 475, 245))
    second.rotate(90)
    with source.open("wb") as output:
        writer.write(output)

    pages = PdfReader(source).pages
    first_geometry = PageGeometry.from_page(pages[0])
    second_geometry = PageGeometry.from_page(pages[1])

    assert (first_geometry.width, first_geometry.height) == (180, 260)
    assert first_geometry.rotated_size == (180, 260)
    assert (second_geometry.width, second_geometry.height) == (450, 240)
    assert second_geometry.rotated_size == (240, 450)


def test_user_unit_converts_contract_points_to_page_units() -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(width=120, height=80)
    page.cropbox = RectangleObject((10, 5, 110, 75))
    page[NameObject("/UserUnit")] = FloatObject(2.0)

    geometry = PageGeometry.from_page(page)

    assert (geometry.width, geometry.height) == (200, 140)
    assert geometry.to_page_user_space((100, 70)) == (60, 40)


def rectangle_operations(page) -> list[tuple[float, ...]]:
    content = page.get_contents()
    assert content is not None
    return [
        tuple(float(value) for value in operands)
        for operands, operator in content.operations
        if operator == b"re"
    ]


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_correction_removes_stale_text_and_preserves_grid_lines(tmp_path, rotation: int) -> None:
    writer = PdfWriter()
    writer.add_page(synthetic_page(rotation=rotation))
    page = writer.pages[0]
    original_rectangles = rectangle_operations(page)

    removed = remove_text_in_box(page, (40, 40, 155, 65))

    overlay_stream = BytesIO()
    overlay = Canvas(overlay_stream, pagesize=(320, 220))
    overlay.drawString(55, 68, "REPLACED")
    overlay.save()
    overlay_stream.seek(0)
    page.merge_page(PdfReader(overlay_stream).pages[0])

    output_path = tmp_path / f"corrected-{rotation}.pdf"
    with output_path.open("wb") as output:
        writer.write(output)

    reopened = PdfReader(output_path).pages[0]
    text = reopened.extract_text()
    assert removed == ["STALE"]
    assert "STALE" not in text
    assert "REPLACED" in text
    assert "KEEP" in text
    assert all(rectangle in rectangle_operations(reopened) for rectangle in original_rectangles)


def test_correction_rejects_partial_text_intersection_without_mutation() -> None:
    writer = PdfWriter()
    writer.add_page(synthetic_page())
    page = writer.pages[0]
    original_operations = list(page.get_contents().operations)

    with pytest.raises(PDFFieldPlacementError, match="partially intersects"):
        remove_text_in_box(page, (40, 40, 55, 65))

    assert page.get_contents().operations == original_operations


def test_correction_rejects_xobject_text() -> None:
    writer = PdfWriter()
    writer.add_page(synthetic_page())
    page = writer.pages[0]
    overlay_stream = BytesIO()
    overlay = Canvas(overlay_stream, pagesize=(320, 220))
    overlay.beginForm("nested")
    overlay.drawString(55, 68, "NESTED")
    overlay.endForm()
    overlay.doForm("nested")
    overlay.save()
    overlay_stream.seek(0)
    page.merge_page(PdfReader(overlay_stream).pages[0])

    with pytest.raises(PDFFieldPlacementError, match="XObjects"):
        remove_text_in_box(page, (40, 40, 155, 65))


@pytest.mark.parametrize(
    ("setter", "value"),
    [
        ("setHorizScale", 300),
        ("setCharSpace", 2),
        ("setWordSpace", 2),
        ("setRise", 3),
        ("setTextRenderMode", 1),
    ],
)
def test_correction_rejects_unmodeled_text_geometry(setter: str, value: int) -> None:
    stream = BytesIO()
    canvas = Canvas(stream, pagesize=(320, 220))
    text = canvas.beginText(55, 68)
    getattr(text, setter)(value)
    text.textOut("STALE TEXT")
    canvas.drawText(text)
    canvas.save()
    page = PdfReader(BytesIO(stream.getvalue())).pages[0]
    original_operations = list(page.get_contents().operations)

    with pytest.raises(PDFFieldPlacementError, match="unsupported text state"):
        remove_text_in_box(page, (45, 55, 110, 90))

    assert page.get_contents().operations == original_operations


def test_correction_rejects_tj_positioning_adjustments() -> None:
    writer = PdfWriter()
    page = writer.add_page(synthetic_page())
    content = page.get_contents()
    assert content is not None
    index = next(
        index for index, (_operands, operator) in enumerate(content.operations) if operator == b"Tj"
    )
    content.operations[index] = (
        [ArrayObject([TextStringObject("STALE"), NumberObject(50)])],
        b"TJ",
    )
    page.replace_contents(content)

    with pytest.raises(PDFFieldPlacementError, match="positioning adjustments"):
        remove_text_in_box(page, (40, 40, 155, 65))
