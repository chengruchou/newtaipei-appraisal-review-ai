"""Inventory an original workbook package without writing to it."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from appraisal_review.adapters.local.workbook_inventory import (
    WorkbookInventoryError,
    inventory_workbook,
)
from appraisal_review.domain.official_workbook import (
    MergedAnchor,
    SheetInventory,
    WorkbookInventory,
    review_inventory,
)

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
PACKAGE_RELS = "http://schemas.openxmlformats.org/package/2006/relationships"
DOCUMENT_RELS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _sheet(
    *,
    dimension: str = "A1:C3",
    merged: tuple[str, ...] = (),
    cells: str = "",
) -> str:
    merges = "".join(f'<mergeCell ref="{ref}"/>' for ref in merged)
    merge_block = f'<mergeCells count="{len(merged)}">{merges}</mergeCells>' if merged else ""
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<worksheet xmlns="{MAIN}"><dimension ref="{dimension}"/>'
        f"<sheetData>{cells}</sheetData>{merge_block}</worksheet>"
    )


def _workbook_xml(sheets: tuple[tuple[str, str], ...], defined_names: str = "") -> str:
    entries = "".join(
        f'<sheet name="{name}" sheetId="{index + 1}" state="{state}" r:id="rId{index + 1}"/>'
        for index, (name, state) in enumerate(sheets)
    )
    names = f"<definedNames>{defined_names}</definedNames>" if defined_names else ""
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<workbook xmlns="{MAIN}" xmlns:r="{DOCUMENT_RELS}">'
        f"<sheets>{entries}</sheets>{names}</workbook>"
    )


def _rels(targets: tuple[str, ...], *, external: tuple[str, ...] = ()) -> str:
    entries = "".join(
        f'<Relationship Id="rId{index + 1}" Target="{target}" Type="{DOCUMENT_RELS}/worksheet"/>'
        for index, target in enumerate(targets)
    )
    entries += "".join(
        f'<Relationship Id="rIdX{index}" Target="{target}" TargetMode="External" '
        f'Type="{DOCUMENT_RELS}/externalLinkPath"/>'
        for index, target in enumerate(external)
    )
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<Relationships xmlns="{PACKAGE_RELS}">{entries}</Relationships>'
    )


def _write_workbook(path: Path, parts: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return path


def _official_like(tmp_path: Path) -> Path:
    """One visible form sheet plus a hidden example sheet with formulas."""

    visible = _sheet(
        dimension="A1:H40",
        merged=("A1:H1", "B2:C2"),
        cells=(
            '<row r="1"><c r="A1" t="inlineStr"><is><t>Table 3</t></is></c></row>'
            '<row r="3"><c r="B3"><v>5</v></c><c r="C3"/></row>'
        ),
    )
    hidden = _sheet(
        dimension="A1:D9",
        cells=(
            '<row r="1"><c r="A1"><f>SUM(B1:C1)</f><v>3</v></c>'
            '<c r="B1"><f>[1]Legacy!$A$1*2</f><v>8</v></c></row>'
        ),
    )
    return _write_workbook(
        tmp_path / "table3.xlsx",
        {
            "xl/workbook.xml": _workbook_xml(
                (("Table 3", "visible"), ("Example", "hidden")),
                defined_names=(
                    '<definedName name="_xlnm.Print_Area" localSheetId="0">'
                    "'Table 3'!$A$1:$H$40</definedName>"
                ),
            ),
            "xl/_rels/workbook.xml.rels": _rels(("worksheets/sheet1.xml", "worksheets/sheet2.xml")),
            "xl/worksheets/sheet1.xml": visible,
            "xl/worksheets/sheet2.xml": hidden,
            "xl/externalLinks/externalLink1.xml": f'<externalLink xmlns="{MAIN}"/>',
            "xl/externalLinks/_rels/externalLink1.xml.rels": _rels(
                (), external=("file:///Volumes/old/legacy.xlsx",)
            ),
        },
    )


def test_inventory_records_visibility_merges_and_print_areas(tmp_path: Path) -> None:
    inventory = inventory_workbook(_official_like(tmp_path))

    assert inventory.source_name == "table3.xlsx"
    assert len(inventory.digest) == 64
    assert [sheet.name for sheet in inventory.sheets] == ["Table 3", "Example"]
    visible, hidden = inventory.sheets
    assert visible.visibility == "visible"
    assert hidden.visibility == "hidden"
    assert visible.dimension == "A1:H40"
    assert visible.print_areas == ("'Table 3'!$A$1:$H$40",)
    assert visible.merged_anchors == (
        MergedAnchor(range="A1:H1", anchor="A1"),
        MergedAnchor(range="B2:C2", anchor="B2"),
    )
    assert visible.formula_count == 0
    assert visible.value_cell_count == 2
    assert inventory.visible_sheets == (visible,)
    assert inventory.hidden_sheets == (hidden,)


def test_inventory_separates_external_formulas_from_local_ones(tmp_path: Path) -> None:
    inventory = inventory_workbook(_official_like(tmp_path))

    hidden = inventory.sheets[1]
    assert hidden.formula_count == 2
    assert hidden.external_formula_count == 1
    assert [formula.cell for formula in hidden.formulas] == ["A1", "B1"]
    assert [formula.external for formula in hidden.formulas] == [False, True]
    assert hidden.truncated_formulas is False
    assert [reference.target for reference in inventory.external_references] == [
        "file:///Volumes/old/legacy.xlsx"
    ]
    assert inventory.external_references[0].mode == "External"


def test_review_reports_external_links_but_not_hidden_sheet_formulas(tmp_path: Path) -> None:
    findings = review_inventory(inventory_workbook(_official_like(tmp_path)))

    assert [finding.code for finding in findings] == ["external_reference_present"]


def test_review_flags_formulas_and_missing_print_area_in_a_visible_sheet(tmp_path: Path) -> None:
    path = _write_workbook(
        tmp_path / "derived.xlsx",
        {
            "xl/workbook.xml": _workbook_xml((("Copy", "visible"),)),
            "xl/_rels/workbook.xml.rels": _rels(("worksheets/sheet1.xml",)),
            "xl/worksheets/sheet1.xml": _sheet(
                cells='<row r="1"><c r="A1"><f>[2]Old!$B$2</f><v>1</v></c></row>'
            ),
        },
    )

    codes = [finding.code for finding in review_inventory(inventory_workbook(path))]

    assert codes == [
        "formula_in_visible_sheet",
        "external_formula_in_visible_sheet",
        "visible_sheet_without_print_area",
    ]


def test_inventory_leaves_the_original_bytes_unchanged(tmp_path: Path) -> None:
    path = _official_like(tmp_path)
    before = path.read_bytes()

    digest = inventory_workbook(path).digest

    assert path.read_bytes() == before
    assert digest == __import__("hashlib").sha256(before).hexdigest()


def test_formula_sampling_is_capped_and_marked_truncated(tmp_path: Path) -> None:
    path = _write_workbook(
        tmp_path / "many.xlsx",
        {
            "xl/workbook.xml": _workbook_xml((("Sheet", "hidden"),)),
            "xl/_rels/workbook.xml.rels": _rels(("worksheets/sheet1.xml",)),
            "xl/worksheets/sheet1.xml": _sheet(
                cells="".join(
                    f'<row r="{row}"><c r="A{row}"><f>A{row}+1</f><v>1</v></c></row>'
                    for row in range(1, 6)
                )
            ),
        },
    )

    sheet = inventory_workbook(path, max_formulas=2).sheets[0]

    assert sheet.formula_count == 5
    assert len(sheet.formulas) == 2
    assert sheet.truncated_formulas is True


def test_unknown_sheet_state_and_missing_part_fail_explicitly(tmp_path: Path) -> None:
    unknown_state = _write_workbook(
        tmp_path / "state.xlsx",
        {
            "xl/workbook.xml": _workbook_xml((("Sheet", "archived"),)),
            "xl/_rels/workbook.xml.rels": _rels(("worksheets/sheet1.xml",)),
            "xl/worksheets/sheet1.xml": _sheet(),
        },
    )
    missing_part = _write_workbook(
        tmp_path / "missing.xlsx",
        {
            "xl/workbook.xml": _workbook_xml((("Sheet", "visible"),)),
            "xl/_rels/workbook.xml.rels": _rels(("worksheets/sheet1.xml",)),
        },
    )
    not_a_workbook = tmp_path / "plain.xlsx"
    not_a_workbook.write_bytes(b"not a package")

    with pytest.raises(WorkbookInventoryError) as unknown:
        inventory_workbook(unknown_state)
    with pytest.raises(WorkbookInventoryError) as missing:
        inventory_workbook(missing_part)
    with pytest.raises(WorkbookInventoryError) as plain:
        inventory_workbook(not_a_workbook)

    assert unknown.value.code == "unknown_sheet_state"
    assert missing.value.code == "missing_part"
    assert plain.value.code == "not_a_workbook"


def test_contracts_reject_inconsistent_observations() -> None:
    with pytest.raises(ValidationError):
        MergedAnchor(range="A1:B2", anchor="B2")
    with pytest.raises(ValidationError):
        SheetInventory(
            index=0,
            name="Sheet",
            visibility="visible",
            formula_count=1,
            external_formula_count=2,
            value_cell_count=0,
        )
    with pytest.raises(ValidationError):
        WorkbookInventory(
            source_name="x.xlsx",
            digest="a" * 64,
            byte_size=1,
            sheets=(
                SheetInventory(
                    index=1,
                    name="Sheet",
                    visibility="visible",
                    formula_count=0,
                    external_formula_count=0,
                    value_cell_count=0,
                ),
            ),
            defined_name_count=0,
        )


def _with_print_area(tmp_path: Path, area: str, cells: str) -> Path:
    return _write_workbook(
        tmp_path / "area.xlsx",
        {
            "xl/workbook.xml": _workbook_xml(
                (("Form", "visible"),),
                defined_names=(
                    f'<definedName name="_xlnm.Print_Area" localSheetId="0">{area}</definedName>'
                ),
            ),
            "xl/_rels/workbook.xml.rels": _rels(("worksheets/sheet1.xml",)),
            "xl/worksheets/sheet1.xml": _sheet(dimension="A1:D40", cells=cells),
        },
    )


def test_values_below_the_print_area_are_reported(tmp_path: Path) -> None:
    """The organizer's table 4 keeps a note in A37 while printing only to row 36."""

    path = _with_print_area(
        tmp_path,
        "Form!$A$1:$C$36",
        '<row r="36"><c r="A36"><v>1</v></c></row>'
        '<row r="37"><c r="A37" t="inlineStr"><is><t>note</t></is></c>'
        '<c r="B37"/></row>',
    )

    sheet = inventory_workbook(path).sheets[0]
    findings = review_inventory(inventory_workbook(path))

    assert sheet.print_area_resolved is True
    assert sheet.outside_print_area_count == 1
    assert sheet.cells_outside_print_area == ("A37",)
    assert sheet.value_cell_count == 2
    assert [finding.code for finding in findings] == ["value_outside_print_area"]
    assert "A37" in findings[0].detail


def test_empty_cells_outside_the_print_area_are_not_reported(tmp_path: Path) -> None:
    path = _with_print_area(
        tmp_path,
        "Form!$A$1:$C$36",
        '<row r="1"><c r="A1"><v>1</v></c></row><row r="37"><c r="S37"/></row>',
    )

    sheet = inventory_workbook(path).sheets[0]

    assert sheet.outside_print_area_count == 0
    assert review_inventory(inventory_workbook(path)) == ()


def test_a_whole_column_print_area_stays_unresolved_rather_than_claiming_exclusion(
    tmp_path: Path,
) -> None:
    path = _with_print_area(tmp_path, "Form!$A:$C", '<row r="99"><c r="Z99"><v>1</v></c></row>')

    sheet = inventory_workbook(path).sheets[0]
    codes = [finding.code for finding in review_inventory(inventory_workbook(path))]

    assert sheet.print_area_resolved is False
    assert sheet.outside_print_area_count == 0
    assert sheet.cells_outside_print_area == ()
    assert codes == ["unresolved_print_area"]


def test_outside_area_counts_require_a_resolved_print_area() -> None:
    with pytest.raises(ValidationError):
        SheetInventory(
            index=0,
            name="Sheet",
            visibility="visible",
            formula_count=0,
            external_formula_count=0,
            value_cell_count=1,
            print_area_resolved=False,
            cells_outside_print_area=("A37",),
            outside_print_area_count=1,
        )
