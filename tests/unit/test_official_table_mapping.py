"""Validate a table mapping against the template it claims to write."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from appraisal_review.domain.official_table_mapping import (
    AbsencePolicy,
    CellBinding,
    TableMapping,
    validate_mapping,
)
from appraisal_review.domain.official_workbook import (
    FormulaCell,
    MergedAnchor,
    SheetInventory,
    WorkbookInventory,
)

DIGEST = "f8" + "0" * 62
OTHER = "c5" + "0" * 62


def _sheet(**overrides: object) -> SheetInventory:
    fields: dict[str, object] = {
        "index": 0,
        "name": "Form",
        "visibility": "visible",
        "dimension": "A1:H40",
        "print_areas": ("Form!$A$1:$G$36",),
        "merged_anchors": (MergedAnchor(range="B2:D2", anchor="B2"),),
        "formula_count": 0,
        "external_formula_count": 0,
        "value_cell_count": 0,
        "print_area_resolved": True,
    }
    fields.update(overrides)
    return SheetInventory.model_validate(fields)


def _inventory(sheet: SheetInventory | None = None, digest: str = DIGEST) -> WorkbookInventory:
    return WorkbookInventory(
        source_name="table3.xlsx",
        digest=digest,
        byte_size=1024,
        sheets=(sheet if sheet is not None else _sheet(),),
        defined_name_count=1,
    )


def _binding(cell: str, **overrides: object) -> CellBinding:
    fields: dict[str, object] = {
        "cell": cell,
        "source": "snapshot.section.value",
        "label": "section value",
        "value_kind": "text",
    }
    fields.update(overrides)
    return CellBinding.model_validate(fields)


def _mapping(*bindings: CellBinding, **overrides: object) -> TableMapping:
    fields: dict[str, object] = {
        "table": "table_3",
        "template_name": "table3.xlsx",
        "template_digest": DIGEST,
        "sheet_name": "Form",
        "bindings": bindings or (_binding("B2"),),
    }
    fields.update(overrides)
    return TableMapping.model_validate(fields)


def test_a_mapping_addressing_writable_cells_has_no_findings() -> None:
    assert validate_mapping(_mapping(_binding("B2"), _binding("A5")), _inventory()) == ()


def test_a_mapping_authored_against_other_template_bytes_is_reported() -> None:
    findings = validate_mapping(_mapping(), _inventory(digest=OTHER))

    assert [finding.code for finding in findings] == ["template_digest_mismatch"]
    assert OTHER in findings[0].detail


def test_writing_into_a_merged_range_names_the_anchor_to_use_instead() -> None:
    findings = validate_mapping(_mapping(_binding("C2")), _inventory())

    assert [finding.code for finding in findings] == ["cell_inside_merged_range"]
    assert "write B2 instead" in findings[0].detail


def test_the_anchor_itself_is_accepted() -> None:
    assert validate_mapping(_mapping(_binding("B2")), _inventory()) == ()


def test_a_binding_outside_the_print_area_is_reported() -> None:
    findings = validate_mapping(_mapping(_binding("A37", label="closing note")), _inventory())

    assert [finding.code for finding in findings] == ["cell_outside_print_area"]
    assert "closing note" in findings[0].detail


def test_a_binding_outside_the_used_range_is_reported() -> None:
    codes = [finding.code for finding in validate_mapping(_mapping(_binding("Z99")), _inventory())]

    assert codes == ["cell_outside_sheet_dimension", "cell_outside_print_area"]


def test_a_formula_cell_of_the_original_is_reported() -> None:
    sheet = _sheet(
        formula_count=1,
        formulas=(FormulaCell(cell="A5", formula="SUM(A1:A4)"),),
    )

    findings = validate_mapping(_mapping(_binding("A5")), _inventory(sheet))

    assert [finding.code for finding in findings] == ["cell_is_formula"]


def test_a_truncated_formula_sample_is_not_treated_as_a_clean_check() -> None:
    sheet = _sheet(
        formula_count=300,
        formulas=(FormulaCell(cell="H1", formula="1+1"),),
        truncated_formulas=True,
    )

    codes = [finding.code for finding in validate_mapping(_mapping(), _inventory(sheet))]

    assert codes == ["unverifiable_formula_coverage"]


def test_a_hidden_sheet_target_is_reported_once_and_bindings_still_checked() -> None:
    sheet = _sheet(visibility="hidden")

    codes = [
        finding.code for finding in validate_mapping(_mapping(_binding("C2")), _inventory(sheet))
    ]

    assert codes == ["sheet_not_visible", "cell_inside_merged_range"]


def test_an_unknown_sheet_stops_before_binding_checks() -> None:
    findings = validate_mapping(_mapping(sheet_name="Missing"), _inventory())

    assert [finding.code for finding in findings] == ["sheet_not_found"]


def test_table_5_writes_percentage_points_and_table_4_writes_fractions() -> None:
    points = _binding("B2", value_kind="percentage_points", unit="percentage_point")
    fraction = _binding("B2", value_kind="percentage_fraction", unit="ratio")

    assert _mapping(points, table="table_5").bindings == (points,)
    assert _mapping(fraction, table="table_4").bindings == (fraction,)
    with pytest.raises(ValidationError, match="percentage_points"):
        _mapping(fraction, table="table_5")
    with pytest.raises(ValidationError, match="percentage_fraction"):
        _mapping(points, table="table_4")


def test_a_percentage_binding_must_state_its_unit() -> None:
    with pytest.raises(ValidationError, match="unit"):
        _binding("B2", value_kind="percentage_points")


def test_two_bindings_cannot_target_one_cell() -> None:
    with pytest.raises(ValidationError, match="B2"):
        _mapping(_binding("B2"), _binding("B2", source="other.value"))


def test_absence_keeps_missing_not_applicable_and_confirmed_zero_distinct() -> None:
    policy = AbsencePolicy(not_applicable="text", not_applicable_text="-")

    assert policy.missing == "blank"
    assert policy.confirmed_zero == "zero"
    with pytest.raises(ValidationError):
        AbsencePolicy(not_applicable="text")
    with pytest.raises(ValidationError):
        AbsencePolicy(confirmed_zero_text="0")
