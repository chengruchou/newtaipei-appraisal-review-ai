"""Derive a render-only copy of a filled workbook with hidden sheets removed.

The delivered spreadsheet keeps all twenty-four sheets exactly as the organizer's
template carries them. But the installed headless converter rasterizes hidden sheets
too, so a PDF made from the full workbook leaks twenty-three legacy example sheets
into the delivered document. The organizer's own guidance already accepts print-setup
corrections on a derived copy; removing never-printed hidden sheets from the copy that
is only ever rasterized is exactly that kind of correction.

Guarantees, enforced and tested:
- Visible sheet parts are byte-identical to the input's. No cell, value or style of
  anything that renders is touched.
- Only bookkeeping parts change: workbook.xml (sheet list, defined names), its rels,
  [Content_Types].xml, and the calc chain (which references removed sheets) is dropped.
- Defined names belonging to removed sheets go with them, and sheet-local indexes of
  kept names are re-based, so the kept print areas still point at their own sheets.
- The derived copy exists for one conversion call and is hashed, so the delivered PDF
  records both the filled workbook it stands for and the exact bytes rasterized.
"""

from __future__ import annotations

import posixpath
import re
import zipfile
from io import BytesIO
from xml.etree import ElementTree as ET

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"


class RenderCopyError(Exception):
    """Stable reason only; no paths or sheet content."""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def visible_only_copy(workbook: bytes) -> bytes:
    """Return the workbook with every hidden/veryHidden sheet removed for rendering."""
    source = zipfile.ZipFile(BytesIO(workbook))
    names = set(source.namelist())
    if "xl/workbook.xml" not in names or "xl/_rels/workbook.xml.rels" not in names:
        raise RenderCopyError("not_a_workbook_package")

    ET.register_namespace("", MAIN_NS)
    ET.register_namespace("r", REL_NS)
    workbook_root = ET.fromstring(source.read("xl/workbook.xml"))
    sheets_parent = workbook_root.find(f"{{{MAIN_NS}}}sheets")
    if sheets_parent is None:
        raise RenderCopyError("workbook_has_no_sheets")

    rels_root = ET.fromstring(source.read("xl/_rels/workbook.xml.rels"))
    rel_targets = {
        rel.get("Id"): rel.get("Target", "")
        for rel in rels_root
        if _local(rel.tag) == "Relationship"
    }

    kept_indexes: list[int] = []
    removed_rel_ids: set[str] = set()
    removed_parts: set[str] = set()
    removed_names: list[str] = []
    for index, sheet in enumerate(list(sheets_parent)):
        state = sheet.get("state") or "visible"
        if state == "visible":
            kept_indexes.append(index)
            continue
        rel_id = sheet.get(f"{{{REL_NS}}}id")
        target = rel_targets.get(rel_id, "")
        part = posixpath.normpath(posixpath.join("xl", target)) if target else ""
        if not part or part not in names:
            raise RenderCopyError("hidden_sheet_part_missing")
        removed_names.append(sheet.get("name") or "")
        removed_rel_ids.add(rel_id or "")
        removed_parts.add(part)
        sheet_rels = f"xl/worksheets/_rels/{posixpath.basename(part)}.rels"
        if sheet_rels in names:
            removed_parts.add(sheet_rels)
            for rel in ET.fromstring(source.read(sheet_rels)):
                mode = rel.get("TargetMode") or "Internal"
                if _local(rel.tag) == "Relationship" and mode == "Internal":
                    child = posixpath.normpath(
                        posixpath.join(posixpath.dirname(part), rel.get("Target", ""))
                    )
                    # Drawings/comments used only by removed sheets; shared parts like
                    # styles or shared strings are workbook-level rels, never sheet rels.
                    if child in names and child.startswith("xl/"):
                        removed_parts.add(child)
        sheets_parent.remove(sheet)
    if not kept_indexes:
        raise RenderCopyError("no_visible_sheet")

    # The calc chain indexes cells across every sheet; with sheets gone it lies.
    if "xl/calcChain.xml" in names:
        removed_parts.add("xl/calcChain.xml")
        for rel in list(rels_root):
            if _local(rel.tag) == "Relationship" and rel.get("Target") == "calcChain.xml":
                removed_rel_ids.add(rel.get("Id") or "")

    for rel in list(rels_root):
        if _local(rel.tag) == "Relationship" and rel.get("Id") in removed_rel_ids:
            rels_root.remove(rel)

    # Sheet-local defined names (print areas, titles) carry the sheet's position.
    index_map = {old: new for new, old in enumerate(kept_indexes)}
    defined = workbook_root.find(f"{{{MAIN_NS}}}definedNames")
    removed_pattern = (
        re.compile(
            "|".join(
                re.escape(f"'{name}'") + "|" + re.escape(name) for name in removed_names if name
            )
        )
        if removed_names
        else None
    )
    if defined is not None:
        for entry in list(defined):
            local = entry.get("localSheetId")
            if local is not None:
                old_index = int(local)
                if old_index in index_map:
                    entry.set("localSheetId", str(index_map[old_index]))
                else:
                    defined.remove(entry)
                    continue
            elif removed_pattern is not None and removed_pattern.search(entry.text or ""):
                defined.remove(entry)
        if len(defined) == 0:
            workbook_root.remove(defined)

    types_root = ET.fromstring(source.read("[Content_Types].xml"))
    for override in list(types_root):
        if _local(override.tag) == "Override":
            part = (override.get("PartName") or "").lstrip("/")
            if part in removed_parts:
                types_root.remove(override)

    rendered: dict[str, bytes] = {
        "xl/workbook.xml": bytes(
            ET.tostring(workbook_root, xml_declaration=True, encoding="UTF-8")
        ),
        "xl/_rels/workbook.xml.rels": _serialize_rels(rels_root),
        "[Content_Types].xml": _serialize_types(types_root),
    }

    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in source.infolist():
            if item.filename in removed_parts:
                continue
            data = rendered.get(item.filename, source.read(item.filename))
            archive.writestr(item, data)
    return output.getvalue()


def _serialize_rels(root: ET.Element) -> bytes:
    ET.register_namespace("", PKG_REL_NS)
    return bytes(ET.tostring(root, xml_declaration=True, encoding="UTF-8"))


def _serialize_types(root: ET.Element) -> bytes:
    ET.register_namespace("", TYPES_NS)
    return bytes(ET.tostring(root, xml_declaration=True, encoding="UTF-8"))
