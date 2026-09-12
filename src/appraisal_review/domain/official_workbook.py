"""Read-only inventory contracts for the official valuation workbooks.

The organizer's workbooks are evidence, not inputs the exporter may rewrite.
This module describes what an inventory pass observed: sheet visibility, merged
anchors, print areas, formulas, external references and macro parts. It records
observations only. A cell being writable in Excel is not a statement that this
case may write it; the writable whitelist is a separate reviewed decision.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, model_validator

from appraisal_review.domain.document_models import Digest
from appraisal_review.domain.service_contracts import ContractModel

CELL_REFERENCE = re.compile(r"^\$?[A-Z]{1,3}\$?[1-9][0-9]{0,6}$")
CELL_RANGE = re.compile(r"^\$?[A-Z]{1,3}\$?[1-9][0-9]{0,6}:\$?[A-Z]{1,3}\$?[1-9][0-9]{0,6}$")

SheetVisibility = Literal["visible", "hidden", "veryHidden"]


class MergedAnchor(ContractModel):
    """A merged range and the single cell a writer must address."""

    range: str = Field(pattern=CELL_RANGE.pattern)
    anchor: str = Field(pattern=CELL_REFERENCE.pattern)

    @model_validator(mode="after")
    def anchor_starts_range(self) -> MergedAnchor:
        if self.range.split(":")[0].replace("$", "") != self.anchor:
            raise ValueError("The merged anchor must be the range's top-left cell")
        return self


class FormulaCell(ContractModel):
    """One observed formula. `external` marks a cross-workbook reference."""

    cell: str = Field(pattern=CELL_REFERENCE.pattern)
    formula: str = Field(max_length=2048)
    external: bool = False
    shared: bool = False


class ExternalReference(ContractModel):
    """An external workbook link declared by the package, with its raw target."""

    part: str = Field(min_length=1, max_length=512)
    target: str | None = Field(default=None, max_length=2048)
    mode: str | None = Field(default=None, max_length=64)


def column_index(letters: str) -> int:
    """One-based column index for A, B, ... Z, AA, ... The caller supplies letters only."""

    index = 0
    for character in letters:
        index = index * 26 + (ord(character) - ord("A") + 1)
    return index


def cell_position(reference: str) -> tuple[int, int]:
    """(column, row) for a cell reference, with absolute markers ignored."""

    plain = reference.replace("$", "")
    split = next(offset for offset, character in enumerate(plain) if character.isdigit())
    return column_index(plain[:split]), int(plain[split:])


def parse_print_area(area: str) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """Bounds of one print area, or None when the form is not a plain cell range.

    Whole-column and whole-row areas, defined-name indirection and any other
    shape return None so the caller reports the area as unresolved rather than
    claiming content falls outside it.
    """

    body = area.rpartition("!")[2].strip()
    if CELL_RANGE.match(body):
        start, end = body.split(":")
        return cell_position(start), cell_position(end)
    if CELL_REFERENCE.match(body):
        position = cell_position(body)
        return position, position
    return None


def within_print_areas(reference: str, areas: tuple[str, ...]) -> bool | None:
    """True/False when every area is a plain range, None when any is unresolved."""

    bounds: list[tuple[tuple[int, int], tuple[int, int]]] = []
    for area in areas:
        parsed = parse_print_area(area)
        if parsed is None:
            return None
        bounds.append(parsed)
    if not bounds:
        return None
    column, row = cell_position(reference)
    return any(start[0] <= column <= end[0] and start[1] <= row <= end[1] for start, end in bounds)


class SheetInventory(ContractModel):
    """Observations for one worksheet. Counts cover the whole sheet part."""

    index: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=31)
    visibility: SheetVisibility
    dimension: str | None = Field(default=None, max_length=64)
    print_areas: tuple[str, ...] = ()
    merged_anchors: tuple[MergedAnchor, ...] = ()
    formula_count: int = Field(ge=0)
    external_formula_count: int = Field(ge=0)
    formulas: tuple[FormulaCell, ...] = ()
    value_cell_count: int = Field(ge=0)
    truncated_formulas: bool = False
    print_area_resolved: bool = False
    cells_outside_print_area: tuple[str, ...] = ()
    outside_print_area_count: int = Field(default=0, ge=0)
    truncated_outside_cells: bool = False

    @model_validator(mode="after")
    def consistent_counts(self) -> SheetInventory:
        if self.external_formula_count > self.formula_count:
            raise ValueError("External formulas cannot exceed the observed formula count")
        if len(self.formulas) > self.formula_count:
            raise ValueError("More formula samples than observed formulas")
        if not self.truncated_formulas and len(self.formulas) != self.formula_count:
            raise ValueError("Untruncated inventories must sample every observed formula")
        if self.outside_print_area_count and not self.print_area_resolved:
            raise ValueError("Cells outside a print area require a resolved print area")
        if len(self.cells_outside_print_area) > self.outside_print_area_count:
            raise ValueError("More outside-area samples than observed cells")
        if (
            not self.truncated_outside_cells
            and len(self.cells_outside_print_area) != self.outside_print_area_count
        ):
            raise ValueError("Untruncated inventories must sample every outside-area cell")
        if self.outside_print_area_count > self.value_cell_count:
            raise ValueError("Outside-area cells cannot exceed the observed value cells")
        anchors = [merged.range for merged in self.merged_anchors]
        if len(anchors) != len(set(anchors)):
            raise ValueError("Merged ranges must be unique within a sheet")
        return self


class WorkbookInventory(ContractModel):
    """A complete read-only observation of one original workbook."""

    schema_version: Literal["official-workbook-inventory-v1"] = "official-workbook-inventory-v1"
    source_name: str = Field(min_length=1, max_length=512)
    digest: Digest
    byte_size: int = Field(ge=0)
    sheets: tuple[SheetInventory, ...] = Field(min_length=1)
    external_references: tuple[ExternalReference, ...] = ()
    defined_name_count: int = Field(ge=0)
    has_macros: bool = False

    @model_validator(mode="after")
    def ordered_distinct_sheets(self) -> WorkbookInventory:
        if [sheet.index for sheet in self.sheets] != list(range(len(self.sheets))):
            raise ValueError("Sheet indices must be contiguous and in workbook order")
        names = [sheet.name for sheet in self.sheets]
        if len(names) != len(set(names)):
            raise ValueError("Sheet names must be unique within a workbook")
        return self

    @property
    def visible_sheets(self) -> tuple[SheetInventory, ...]:
        return tuple(sheet for sheet in self.sheets if sheet.visibility == "visible")

    @property
    def hidden_sheets(self) -> tuple[SheetInventory, ...]:
        return tuple(sheet for sheet in self.sheets if sheet.visibility != "visible")


class InventoryFinding(ContractModel):
    """One reviewable statement about an inventory. Never an approval."""

    code: Literal[
        "no_visible_sheet",
        "multiple_visible_sheets",
        "formula_in_visible_sheet",
        "external_reference_present",
        "external_formula_in_visible_sheet",
        "macros_present",
        "visible_sheet_without_print_area",
        "unresolved_print_area",
        "value_outside_print_area",
    ]
    sheet: str | None = None
    detail: str = Field(max_length=512)


def review_inventory(inventory: WorkbookInventory) -> tuple[InventoryFinding, ...]:
    """Report what a human reviewer must resolve before any copy is written."""

    findings: list[InventoryFinding] = []
    visible = inventory.visible_sheets
    if not visible:
        findings.append(
            InventoryFinding(code="no_visible_sheet", detail="No worksheet is visible in Excel")
        )
    elif len(visible) > 1:
        findings.append(
            InventoryFinding(
                code="multiple_visible_sheets",
                detail=f"{len(visible)} visible sheets: {', '.join(s.name for s in visible)}",
            )
        )
    for sheet in visible:
        if sheet.formula_count:
            findings.append(
                InventoryFinding(
                    code="formula_in_visible_sheet",
                    sheet=sheet.name,
                    detail=f"{sheet.formula_count} formulas observed in a visible sheet",
                )
            )
        if sheet.external_formula_count:
            findings.append(
                InventoryFinding(
                    code="external_formula_in_visible_sheet",
                    sheet=sheet.name,
                    detail=f"{sheet.external_formula_count} formulas reference another workbook",
                )
            )
        if not sheet.print_areas:
            findings.append(
                InventoryFinding(
                    code="visible_sheet_without_print_area",
                    sheet=sheet.name,
                    detail="No print area is defined; page setup needs a human check",
                )
            )
        elif not sheet.print_area_resolved:
            findings.append(
                InventoryFinding(
                    code="unresolved_print_area",
                    sheet=sheet.name,
                    detail=f"Print area {';'.join(sheet.print_areas)} is not a plain cell range",
                )
            )
        if sheet.outside_print_area_count:
            sample = ", ".join(sheet.cells_outside_print_area[:20])
            findings.append(
                InventoryFinding(
                    code="value_outside_print_area",
                    sheet=sheet.name,
                    detail=(
                        f"{sheet.outside_print_area_count} cells carry a value outside the "
                        f"print area and will not appear in a printed or converted page: {sample}"
                    ),
                )
            )
    for reference in inventory.external_references:
        findings.append(
            InventoryFinding(
                code="external_reference_present",
                detail=f"{reference.part} -> {reference.target or 'unresolved target'}",
            )
        )
    if inventory.has_macros:
        findings.append(InventoryFinding(code="macros_present", detail="The package carries VBA"))
    return tuple(findings)
