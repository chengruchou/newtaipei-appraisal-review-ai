"""Declarative mapping from a case snapshot to cells of an official table.

A mapping is data, not code: it names the exact workbook it was written against,
the visible sheet it writes, and one binding per cell. Nothing here computes a
value, decides applicability or approves an output. Validation compares the
mapping against an observed `WorkbookInventory`, so a binding that addresses a
merged range's interior, a formula cell or a cell the print area drops is caught
before a copy is written rather than during a page-by-page human check.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from appraisal_review.domain.document_models import Digest
from appraisal_review.domain.official_workbook import (
    CELL_REFERENCE,
    SheetInventory,
    WorkbookInventory,
    cell_position,
    within_print_areas,
)
from appraisal_review.domain.service_contracts import ContractModel

OfficialTable = Literal["table_3", "table_4", "table_5"]
ValueKind = Literal[
    "text",
    "integer",
    "decimal",
    "percentage_points",
    "percentage_fraction",
    "date",
]

#: Percentages are written in the unit each official form already prints in.
#: Five percentage points is 5 in table 5 and 0.05 in table 4's percent-formatted
#: cells. Binding the rule to the table stops the two conventions from mixing.
_REQUIRED_PERCENT_KIND: dict[str, ValueKind] = {
    "table_5": "percentage_points",
    "table_4": "percentage_fraction",
}


class AbsencePolicy(ContractModel):
    """How absence renders. Missing is never zero, and the three stay distinct."""

    missing: Literal["blank"] = "blank"
    not_applicable: Literal["blank", "text"] = "blank"
    not_applicable_text: str | None = Field(default=None, min_length=1, max_length=128)
    confirmed_zero: Literal["zero", "text"] = "zero"
    confirmed_zero_text: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def text_requires_wording(self) -> AbsencePolicy:
        if (self.not_applicable == "text") != (self.not_applicable_text is not None):
            raise ValueError("A text rendering of not_applicable needs exactly its wording")
        if (self.confirmed_zero == "text") != (self.confirmed_zero_text is not None):
            raise ValueError("A text rendering of confirmed_zero needs exactly its wording")
        return self


class CellBinding(ContractModel):
    """One snapshot value written to one cell of the visible sheet."""

    cell: str = Field(pattern=CELL_REFERENCE.pattern)
    source: str = Field(min_length=1, max_length=256)
    label: str = Field(min_length=1, max_length=256)
    value_kind: ValueKind
    unit: str | None = Field(default=None, min_length=1, max_length=64)
    precision: int | None = Field(default=None, ge=0, le=10)
    absence: AbsencePolicy = AbsencePolicy()

    @model_validator(mode="after")
    def numeric_shape(self) -> CellBinding:
        if self.value_kind == "text" and self.precision is not None:
            raise ValueError("Text bindings carry no precision")
        if self.value_kind in {"percentage_points", "percentage_fraction"} and self.unit is None:
            raise ValueError("A percentage binding must state its unit explicitly")
        return self


class TableMapping(ContractModel):
    """A whole mapping, pinned to the workbook bytes it was authored against."""

    schema_version: Literal["official-table-mapping-v1"] = "official-table-mapping-v1"
    table: OfficialTable
    template_name: str = Field(min_length=1, max_length=512)
    template_digest: Digest
    sheet_name: str = Field(min_length=1, max_length=31)
    bindings: tuple[CellBinding, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def one_binding_per_cell(self) -> TableMapping:
        cells = [binding.cell for binding in self.bindings]
        duplicates = sorted({cell for cell in cells if cells.count(cell) > 1})
        if duplicates:
            raise ValueError(f"Each cell takes one binding; repeated: {duplicates}")
        required = _REQUIRED_PERCENT_KIND.get(self.table)
        if required is not None:
            wrong = sorted(
                binding.cell
                for binding in self.bindings
                if binding.value_kind in {"percentage_points", "percentage_fraction"}
                and binding.value_kind != required
            )
            if wrong:
                raise ValueError(f"{self.table} writes percentages as {required}; wrong: {wrong}")
        return self


class MappingFinding(ContractModel):
    """One reviewable statement about a mapping. Never an approval."""

    code: Literal[
        "template_digest_mismatch",
        "sheet_not_found",
        "sheet_not_visible",
        "cell_outside_print_area",
        "unresolved_print_area",
        "cell_inside_merged_range",
        "cell_is_formula",
        "unverifiable_formula_coverage",
        "cell_outside_sheet_dimension",
    ]
    cell: str | None = None
    detail: str = Field(max_length=512)


def _merged_owner(cell: str, sheet: SheetInventory) -> str | None:
    """The anchor of the merged range containing `cell`, when it is not the anchor."""

    column, row = cell_position(cell)
    for merged in sheet.merged_anchors:
        start, end = (cell_position(part) for part in merged.range.split(":"))
        inside = start[0] <= column <= end[0] and start[1] <= row <= end[1]
        if inside and cell != merged.anchor:
            return merged.anchor
    return None


def _within_dimension(cell: str, dimension: str | None) -> bool:
    if dimension is None or ":" not in dimension:
        return True
    start, end = (cell_position(part) for part in dimension.split(":"))
    column, row = cell_position(cell)
    return start[0] <= column <= end[0] and start[1] <= row <= end[1]


def validate_mapping(
    mapping: TableMapping, inventory: WorkbookInventory
) -> tuple[MappingFinding, ...]:
    """Report every binding a written copy could not honor as intended."""

    findings: list[MappingFinding] = []
    if inventory.digest != mapping.template_digest:
        findings.append(
            MappingFinding(
                code="template_digest_mismatch",
                detail=(
                    f"The mapping was authored against {mapping.template_digest}, "
                    f"but {inventory.source_name} is {inventory.digest}"
                ),
            )
        )
    sheet = next((entry for entry in inventory.sheets if entry.name == mapping.sheet_name), None)
    if sheet is None:
        findings.append(
            MappingFinding(
                code="sheet_not_found",
                detail=f"{mapping.sheet_name} is not a sheet of {inventory.source_name}",
            )
        )
        return tuple(findings)
    if sheet.visibility != "visible":
        findings.append(
            MappingFinding(
                code="sheet_not_visible",
                detail=f"{sheet.name} is {sheet.visibility}; official output uses the visible form",
            )
        )
    formulas = {formula.cell for formula in sheet.formulas}
    if sheet.truncated_formulas:
        findings.append(
            MappingFinding(
                code="unverifiable_formula_coverage",
                detail=(
                    f"Only {len(sheet.formulas)} of {sheet.formula_count} formulas were sampled; "
                    "raise the sample limit before trusting the formula check"
                ),
            )
        )
    for binding in mapping.bindings:
        if not _within_dimension(binding.cell, sheet.dimension):
            findings.append(
                MappingFinding(
                    code="cell_outside_sheet_dimension",
                    cell=binding.cell,
                    detail=f"{binding.cell} lies outside the sheet's used range {sheet.dimension}",
                )
            )
        inside = within_print_areas(binding.cell, sheet.print_areas)
        if inside is None:
            findings.append(
                MappingFinding(
                    code="unresolved_print_area",
                    cell=binding.cell,
                    detail=f"{sheet.name} has no print area that can be compared to a cell",
                )
            )
        elif not inside:
            findings.append(
                MappingFinding(
                    code="cell_outside_print_area",
                    cell=binding.cell,
                    detail=(
                        f"{binding.cell} ({binding.label}) is outside the print area and would "
                        "not appear in a printed or converted page"
                    ),
                )
            )
        anchor = _merged_owner(binding.cell, sheet)
        if anchor is not None:
            findings.append(
                MappingFinding(
                    code="cell_inside_merged_range",
                    cell=binding.cell,
                    detail=f"{binding.cell} is inside a merged range; write {anchor} instead",
                )
            )
        if binding.cell in formulas:
            findings.append(
                MappingFinding(
                    code="cell_is_formula",
                    cell=binding.cell,
                    detail=f"{binding.cell} carries a formula in the original template",
                )
            )
    return tuple(findings)
