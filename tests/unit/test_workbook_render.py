"""The render-only derived copy: hidden sheets gone, everything visible untouched.

Uses the real official templates when the operator has staged them; skips cleanly on a
checkout without materials so CI stays honest about what it verified.
"""

from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from appraisal_review.adapters.local.workbook_render import RenderCopyError, visible_only_copy

TEMPLATES = Path("artifacts/official-templates")
MAIN_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


def templates() -> list[Path]:
    return sorted(TEMPLATES.glob("*.xlsx")) if TEMPLATES.is_dir() else []


def sheet_entries(data: bytes) -> list[tuple[str, str]]:
    with zipfile.ZipFile(BytesIO(data)) as archive:
        root = ET.fromstring(archive.read("xl/workbook.xml"))
    return [
        (sheet.get("name") or "", sheet.get("state") or "visible")
        for sheet in root.findall(".//m:sheets/m:sheet", MAIN_NS)
    ]


def visible_sheet_part(data: bytes) -> tuple[str, bytes]:
    with zipfile.ZipFile(BytesIO(data)) as archive:
        root = ET.fromstring(archive.read("xl/workbook.xml"))
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {rel.get("Id"): rel.get("Target", "") for rel in rels}
        visible = [
            sheet
            for sheet in root.findall(".//m:sheets/m:sheet", MAIN_NS)
            if (sheet.get("state") or "visible") == "visible"
        ]
        assert len(visible) == 1
        part = "xl/" + targets[visible[0].get(REL_NS)]
        return part, archive.read(part)


@pytest.mark.parametrize("template", templates(), ids=lambda p: p.name[:14])
def test_render_copy_keeps_only_the_visible_sheet(template: Path) -> None:
    if not templates():
        pytest.skip("official templates are not staged on this checkout")
    original = template.read_bytes()
    derived = visible_only_copy(original)
    assert [state for _, state in sheet_entries(derived)] == ["visible"]
    # The one sheet that renders is byte-identical to the original's visible sheet.
    original_part, original_bytes = visible_sheet_part(original)
    derived_part, derived_bytes = visible_sheet_part(derived)
    assert original_part == derived_part
    assert original_bytes == derived_bytes
    with zipfile.ZipFile(BytesIO(derived)) as archive:
        names = set(archive.namelist())
        assert "xl/calcChain.xml" not in names
        assert archive.testzip() is None
    # Kept sheet-local defined names, if any, point at index 0 after re-basing.
    with zipfile.ZipFile(BytesIO(derived)) as archive:
        root = ET.fromstring(archive.read("xl/workbook.xml"))
    for entry in root.findall(".//m:definedNames/m:definedName", MAIN_NS):
        local = entry.get("localSheetId")
        assert local in (None, "0")


def test_openpyxl_reopens_the_derived_copy() -> None:
    if not templates():
        pytest.skip("official templates are not staged on this checkout")
    openpyxl = pytest.importorskip("openpyxl")
    for template in templates():
        derived = visible_only_copy(template.read_bytes())
        workbook = openpyxl.load_workbook(BytesIO(derived), read_only=True)
        assert len(workbook.sheetnames) == 1


def test_non_package_input_is_refused() -> None:
    with pytest.raises((RenderCopyError, zipfile.BadZipFile)):
        visible_only_copy(b"%PDF-1.7 not a workbook")
