"""Read an original .xlsx package without opening it in a spreadsheet library.

The inventory pass must never write to the organizer's originals and must not
depend on a writer's normalization, so it reads the OPC package directly with
the standard library. Nothing here resolves, refreshes or follows an external
link; targets are reported exactly as the package declares them.
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from appraisal_review.domain.official_workbook import (
    CELL_RANGE,
    CELL_REFERENCE,
    ExternalReference,
    FormulaCell,
    MergedAnchor,
    SheetInventory,
    SheetVisibility,
    WorkbookInventory,
    within_print_areas,
)

_EXTERNAL_FORMULA = re.compile(r"\[\d+\]")
_VISIBILITY: dict[str, SheetVisibility] = {
    "visible": "visible",
    "hidden": "hidden",
    "veryhidden": "veryHidden",
}
_PRINT_AREA = "_xlnm.Print_Area"
_MAX_BYTES = 200_000_000
_MAX_ENTRY_BYTES = 100_000_000


class WorkbookInventoryError(Exception):
    """Stable code only; package internals never cross this boundary."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


def _local(tag: str) -> str:
    return tag.rpartition("}")[2]


def _read(archive: zipfile.ZipFile, name: str) -> bytes:
    try:
        info = archive.getinfo(name)
    except KeyError as error:
        raise WorkbookInventoryError("missing_part", name) from error
    if info.file_size > _MAX_ENTRY_BYTES:
        raise WorkbookInventoryError("part_too_large", name)
    return archive.read(info)


def _parse(archive: zipfile.ZipFile, name: str) -> ElementTree.Element:
    try:
        return ElementTree.fromstring(_read(archive, name))
    except ElementTree.ParseError as error:
        raise WorkbookInventoryError("unparsable_part", name) from error


def _relationships(archive: zipfile.ZipFile, part: str) -> dict[str, tuple[str, str]]:
    directory, _, filename = part.rpartition("/")
    rels_name = f"{directory}/_rels/{filename}.rels" if directory else f"_rels/{filename}.rels"
    if rels_name not in archive.namelist():
        return {}
    root = _parse(archive, rels_name)
    found: dict[str, tuple[str, str]] = {}
    for element in root:
        if _local(element.tag) != "Relationship":
            continue
        identifier = element.get("Id")
        target = element.get("Target")
        if identifier is None or target is None:
            continue
        found[identifier] = (target, element.get("TargetMode", "Internal"))
    return found


def _resolve(base: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    parts = base.split("/")[:-1]
    for segment in target.split("/"):
        if segment in {"", "."}:
            continue
        if segment == "..":
            if parts:
                parts.pop()
            continue
        parts.append(segment)
    return "/".join(parts)


def _print_areas(workbook: ElementTree.Element) -> tuple[dict[int, list[str]], int]:
    areas: dict[int, list[str]] = {}
    total = 0
    for group in workbook:
        if _local(group.tag) != "definedNames":
            continue
        for element in group:
            if _local(element.tag) != "definedName":
                continue
            total += 1
            if element.get("name") != _PRINT_AREA:
                continue
            local_id = element.get("localSheetId")
            text = (element.text or "").strip()
            if local_id is None or not text:
                continue
            areas.setdefault(int(local_id), []).extend(
                part.strip() for part in text.split(",") if part.strip()
            )
    return areas, total


def _merged_anchor(ref: str) -> MergedAnchor:
    if not CELL_RANGE.match(ref):
        raise WorkbookInventoryError("unsupported_merged_range", ref)
    return MergedAnchor(range=ref, anchor=ref.split(":")[0].replace("$", ""))


def _sheet_inventory(
    archive: zipfile.ZipFile,
    *,
    index: int,
    name: str,
    state: str,
    part: str,
    print_areas: tuple[str, ...],
    max_formulas: int,
) -> SheetInventory:
    visibility = _VISIBILITY.get(state.lower())
    if visibility is None:
        raise WorkbookInventoryError("unknown_sheet_state", state)
    dimension: str | None = None
    merged: list[MergedAnchor] = []
    formulas: list[FormulaCell] = []
    formula_count = 0
    external_formula_count = 0
    value_cells = 0
    outside_cells: list[str] = []
    outside_count = 0
    print_area_resolved = bool(print_areas)
    cell_reference: str | None = None
    has_value = False
    for event, element in ElementTree.iterparse(archive.open(part), events=("start", "end")):
        tag = _local(element.tag)
        if event == "start":
            if tag == "c":
                cell_reference = element.get("r")
                has_value = False
            continue
        if tag == "dimension":
            dimension = element.get("ref")
        elif tag == "mergeCell":
            ref = element.get("ref")
            if ref is not None:
                merged.append(_merged_anchor(ref))
        elif tag in {"v", "is"}:
            has_value = True
        elif tag == "f":
            formula_count += 1
            text = (element.text or "").strip()
            external = bool(_EXTERNAL_FORMULA.search(text))
            external_formula_count += int(external)
            if len(formulas) < max_formulas and cell_reference is not None:
                if not CELL_REFERENCE.match(cell_reference):
                    raise WorkbookInventoryError("unsupported_cell_reference", cell_reference)
                formulas.append(
                    FormulaCell(
                        cell=cell_reference.replace("$", ""),
                        formula=text[:2048],
                        external=external,
                        shared=element.get("t") == "shared",
                    )
                )
        elif tag == "c":
            if has_value:
                value_cells += 1
                if print_area_resolved and cell_reference is not None:
                    if not CELL_REFERENCE.match(cell_reference):
                        raise WorkbookInventoryError("unsupported_cell_reference", cell_reference)
                    inside = within_print_areas(cell_reference, print_areas)
                    if inside is None:
                        print_area_resolved = False
                        outside_cells.clear()
                        outside_count = 0
                    elif not inside:
                        outside_count += 1
                        if len(outside_cells) < max_formulas:
                            outside_cells.append(cell_reference.replace("$", ""))
            cell_reference = None
            element.clear()
    return SheetInventory(
        index=index,
        name=name,
        visibility=visibility,
        dimension=dimension,
        print_areas=print_areas,
        merged_anchors=tuple(merged),
        formula_count=formula_count,
        external_formula_count=external_formula_count,
        formulas=tuple(formulas),
        value_cell_count=value_cells,
        truncated_formulas=len(formulas) < formula_count,
        print_area_resolved=print_area_resolved,
        cells_outside_print_area=tuple(outside_cells),
        outside_print_area_count=outside_count,
        truncated_outside_cells=len(outside_cells) < outside_count,
    )


def _external_references(archive: zipfile.ZipFile) -> tuple[ExternalReference, ...]:
    found: list[ExternalReference] = []
    for part in sorted(archive.namelist()):
        if not part.startswith("xl/externalLinks/") or not part.endswith(".xml"):
            continue
        targets = _relationships(archive, part)
        if not targets:
            found.append(ExternalReference(part=part))
            continue
        found.extend(
            ExternalReference(part=part, target=target[:2048], mode=mode)
            for target, mode in targets.values()
        )
    return tuple(found)


def _shared_strings(archive: zipfile.ZipFile) -> tuple[str, ...]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return ()
    root = _parse(archive, "xl/sharedStrings.xml")
    return tuple(
        "".join(node.text or "" for node in item.iter() if _local(node.tag) == "t")
        for item in root
        if _local(item.tag) == "si"
    )


def read_sheet_cells(path: Path, sheet_name: str) -> tuple[tuple[str, str], ...]:
    """Populated (cell, text) pairs of one sheet, for authoring and review only.

    The text is what the original already prints, such as the form's own labels.
    It is never a case value and never becomes one.
    """

    with zipfile.ZipFile(path) as archive:
        workbook = _parse(archive, "xl/workbook.xml")
        relationships = _relationships(archive, "xl/workbook.xml")
        strings = _shared_strings(archive)
        part: str | None = None
        for group in workbook:
            if _local(group.tag) != "sheets":
                continue
            for element in group:
                if _local(element.tag) != "sheet" or element.get("name") != sheet_name:
                    continue
                identifier = next(
                    (value for key, value in element.attrib.items() if _local(key) == "id"), None
                )
                if identifier is None or identifier not in relationships:
                    raise WorkbookInventoryError("unresolved_sheet_relationship", sheet_name)
                part = _resolve("xl/workbook.xml", relationships[identifier][0])
        if part is None:
            raise WorkbookInventoryError("sheet_not_found", sheet_name)
        sheet = _parse(archive, part)

    found: list[tuple[str, str]] = []
    for cell in sheet.iter():
        if _local(cell.tag) != "c":
            continue
        reference = cell.get("r")
        if reference is None:
            continue
        kind = cell.get("t")
        if kind == "inlineStr":
            text = "".join(node.text or "" for node in cell.iter() if _local(node.tag) == "t")
        else:
            value = next((node for node in cell if _local(node.tag) == "v"), None)
            if value is None or value.text is None:
                continue
            if kind == "s":
                index = int(value.text)
                if index >= len(strings):
                    raise WorkbookInventoryError("unresolved_shared_string", value.text)
                text = strings[index]
            else:
                text = value.text
        if text.strip():
            found.append((reference, text))
    return tuple(found)


def inventory_workbook(path: Path, *, max_formulas: int = 200) -> WorkbookInventory:
    """Observe one original workbook. The file is opened read-only and unchanged."""

    if max_formulas < 0:
        raise WorkbookInventoryError("invalid_sample_limit", str(max_formulas))
    size = path.stat().st_size
    if size > _MAX_BYTES:
        raise WorkbookInventoryError("workbook_too_large", str(size))
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as error:
        raise WorkbookInventoryError("not_a_workbook", path.name) from error
    with archive:
        names = set(archive.namelist())
        workbook = _parse(archive, "xl/workbook.xml")
        relationships = _relationships(archive, "xl/workbook.xml")
        areas, defined_names = _print_areas(workbook)
        sheets: list[SheetInventory] = []
        for group in workbook:
            if _local(group.tag) != "sheets":
                continue
            for index, element in enumerate(group):
                if _local(element.tag) != "sheet":
                    continue
                name = element.get("name")
                identifier = next(
                    (value for key, value in element.attrib.items() if _local(key) == "id"), None
                )
                if name is None or identifier is None:
                    raise WorkbookInventoryError("incomplete_sheet_entry", name or "")
                relationship = relationships.get(identifier)
                if relationship is None:
                    raise WorkbookInventoryError("unresolved_sheet_relationship", identifier)
                part = _resolve("xl/workbook.xml", relationship[0])
                if part not in names:
                    raise WorkbookInventoryError("missing_part", part)
                sheets.append(
                    _sheet_inventory(
                        archive,
                        index=index,
                        name=name,
                        state=element.get("state", "visible"),
                        part=part,
                        print_areas=tuple(areas.get(index, ())),
                        max_formulas=max_formulas,
                    )
                )
        if not sheets:
            raise WorkbookInventoryError("no_worksheets", path.name)
        return WorkbookInventory(
            source_name=path.name,
            digest=digest.hexdigest(),
            byte_size=size,
            sheets=tuple(sheets),
            external_references=_external_references(archive),
            defined_name_count=defined_names,
            has_macros=any(part.endswith("vbaProject.bin") for part in names),
        )
