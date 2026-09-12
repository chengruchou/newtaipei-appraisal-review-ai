"""Fill an official workbook copy by surgical XML replacement, never a rebuild.

The organizer's template is the fidelity baseline: 24 sheets (23 hidden legacy
examples), external-link parts and all styling must survive a write untouched.
So the writer never round-trips the package through a spreadsheet library. It
rewrites exactly one zip entry - the visible sheet's worksheet part - by
replacing the ``<c>`` elements the mapping binds, and copies every other part
byte for byte. Values come from a frozen :class:`CalculationSnapshot`, keyed by
each binding's ``source``; the writer renders, it never computes.

Rendering rules the tests pin down:

- Numbers are written as exact decimal text (``str`` of the ``Decimal``), never
  through a float. ``percentage_fraction`` cells scale points to a fraction by
  a Decimal exponent shift (5 points -> 0.05); ``percentage_points`` cells keep
  points as points (5 stays 5).
- Text is written as an inline string (``t="inlineStr"``), so a value beginning
  with ``=`` stays literal text and can never become a formula.
- Absence is never zero: a missing value blanks the cell and is reported in
  ``skipped``; ``not_applicable`` and ``confirmed_zero`` render per the
  binding's :class:`AbsencePolicy`.

After writing, the writer re-opens its own output and refuses to return bytes
it cannot verify: every untouched part must be byte-identical to the template,
every written cell must read back exactly, and openpyxl (when installed) must
agree on values, sheet count and sheet visibility.
"""

from __future__ import annotations

import hashlib
import io
import re
import warnings
import zipfile
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from xml.etree import ElementTree
from xml.sax.saxutils import escape

from appraisal_review.domain.calculation_snapshot import CalculationSnapshot, SnapshotEntry
from appraisal_review.domain.official_table_mapping import CellBinding, TableMapping

_MAIN_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL_ATTR = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
_PERCENT_KINDS = frozenset({"percentage_points", "percentage_fraction"})
_TEXT_KINDS = frozenset({"text", "date"})
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


class WorkbookWriteError(Exception):
    """Stable code plus detail; template internals never cross this boundary."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True)
class FilledWorkbook:
    """One verified output: the bytes plus an account of every bound cell."""

    content: bytes
    written_cells: tuple[str, ...]
    sheet_name: str
    source_digest: str
    content_digest: str
    skipped: dict[str, str]


@dataclass(frozen=True)
class _Render:
    """What one cell becomes: a number, an inline string, or blank."""

    mode: str  # "number" | "inline" | "blank"
    text: str = ""
    reason: str = ""


def _decimal(binding: CellBinding, value: Decimal | str | None) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, str):
        try:
            return Decimal(value.strip())
        except InvalidOperation as error:
            raise WorkbookWriteError(
                "value_shape",
                f"{binding.cell} ({binding.source}) needs a decimal, got {value!r}",
            ) from error
    raise WorkbookWriteError(
        "value_shape", f"{binding.cell} ({binding.source}) needs a decimal value"
    )


def _number_text(binding: CellBinding, value: Decimal) -> str:
    if binding.value_kind == "percentage_fraction":
        value = value.scaleb(-2)
    if binding.precision is not None:
        value = value.quantize(Decimal(1).scaleb(-binding.precision), rounding=ROUND_HALF_UP)
    if value == 0:
        value = abs(value)
    if binding.value_kind == "integer":
        if value != value.to_integral_value():
            raise WorkbookWriteError(
                "value_shape",
                f"{binding.cell} ({binding.source}) needs an integer, got {value}",
            )
        return str(int(value))
    return format(value, "f")


def _inline_text(binding: CellBinding, value: Decimal | str) -> str:
    text = format(value, "f") if isinstance(value, Decimal) else value
    if _CONTROL.search(text):
        raise WorkbookWriteError(
            "value_shape", f"{binding.cell} ({binding.source}) carries control characters"
        )
    return text


def _check_unit(binding: CellBinding, entry: SnapshotEntry) -> None:
    if binding.value_kind in _PERCENT_KINDS:
        if entry.unit != binding.unit:
            raise WorkbookWriteError(
                "unit_mismatch",
                f"{binding.cell} ({binding.source}) expects unit {binding.unit}, "
                f"snapshot says {entry.unit}",
            )
        return
    if binding.unit is not None and entry.unit is not None and entry.unit != binding.unit:
        raise WorkbookWriteError(
            "unit_mismatch",
            f"{binding.cell} ({binding.source}) expects unit {binding.unit}, "
            f"snapshot says {entry.unit}",
        )


def _render_present(binding: CellBinding, entry: SnapshotEntry) -> _Render:
    _check_unit(binding, entry)
    value = entry.value
    if binding.value_kind in _TEXT_KINDS:
        if binding.value_kind == "date" and not isinstance(value, str):
            raise WorkbookWriteError(
                "value_shape", f"{binding.cell} ({binding.source}) needs a date string"
            )
        assert value is not None  # present entries always carry a value
        return _Render("inline", _inline_text(binding, value))
    return _Render("number", _number_text(binding, _decimal(binding, value)))


def _render_zero(binding: CellBinding) -> _Render:
    if binding.value_kind in _TEXT_KINDS:
        return _Render("inline", "0")
    return _Render("number", _number_text(binding, Decimal(0)))


def _render(binding: CellBinding, snapshot: CalculationSnapshot) -> _Render:
    entry = snapshot.entries.get(binding.source)
    if entry is None:
        reason = snapshot.gaps.get(binding.source, "not provided by the snapshot")
        return _Render("blank", reason=f"missing: {reason}")
    if entry.state == "missing":
        return _Render("blank", reason="missing: snapshot marks the value missing")
    if entry.state == "not_applicable":
        policy = binding.absence
        if policy.not_applicable == "text":
            assert policy.not_applicable_text is not None
            return _Render("inline", _inline_text(binding, policy.not_applicable_text))
        return _Render("blank", reason="not_applicable: rendered blank per absence policy")
    if entry.state == "confirmed_zero":
        policy = binding.absence
        if policy.confirmed_zero == "text":
            assert policy.confirmed_zero_text is not None
            return _Render("inline", _inline_text(binding, policy.confirmed_zero_text))
        return _render_zero(binding)
    return _render_present(binding, entry)


def _sheet_part_name(archive: zipfile.ZipFile, sheet_name: str) -> str:
    try:
        workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        relationships = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    except (KeyError, ElementTree.ParseError) as error:
        raise WorkbookWriteError("unreadable_package", "workbook parts") from error
    targets = {
        element.get("Id"): element.get("Target", "")
        for element in relationships
        if element.tag.endswith("Relationship")
    }
    sheets = workbook.find(f"{_MAIN_NS}sheets")
    if sheets is None:
        raise WorkbookWriteError("unreadable_package", "workbook.xml has no sheets element")
    for sheet in sheets:
        if sheet.get("name") != sheet_name:
            continue
        if sheet.get("state", "visible") != "visible":
            raise WorkbookWriteError("sheet_not_visible", sheet_name)
        target = targets.get(sheet.get(_REL_ATTR, ""), "")
        if not target:
            raise WorkbookWriteError("unreadable_package", f"{sheet_name} has no part target")
        return target.lstrip("/") if target.startswith("/") else f"xl/{target}"
    raise WorkbookWriteError("sheet_not_found", sheet_name)


def _cell_xml(reference: str, style: str | None, render: _Render) -> str:
    style_attr = f' s="{style}"' if style is not None else ""
    if render.mode == "blank":
        return f'<c r="{reference}"{style_attr}/>'
    if render.mode == "number":
        return f'<c r="{reference}"{style_attr}><v>{render.text}</v></c>'
    body = escape(render.text)
    return (
        f'<c r="{reference}"{style_attr} t="inlineStr">'
        f'<is><t xml:space="preserve">{body}</t></is></c>'
    )


def _rewrite_sheet(xml: str, renders: dict[str, _Render]) -> str:
    for reference, render in renders.items():
        pattern = re.compile(
            rf'<c r="{reference}"(?P<attrs>[^>/]*)(?:/>|>.*?</c>)', flags=re.DOTALL
        )
        match = pattern.search(xml)
        if match is None:
            raise WorkbookWriteError(
                "cell_not_in_sheet", f"{reference} has no cell element in the visible sheet"
            )
        style_match = re.search(r'\ss="([^"]+)"', match.group("attrs"))
        style = style_match.group(1) if style_match else None
        replacement = _cell_xml(reference, style, render)
        xml = f"{xml[: match.start()]}{replacement}{xml[match.end() :]}"
    return xml


def _repack(template_bytes: bytes, part_name: str, part_bytes: bytes) -> bytes:
    buffer = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(template_bytes)) as source,
        zipfile.ZipFile(buffer, "w") as target,
    ):
        for info in source.infolist():
            data = part_bytes if info.filename == part_name else source.read(info)
            clone = zipfile.ZipInfo(filename=info.filename, date_time=info.date_time)
            clone.compress_type = info.compress_type
            clone.external_attr = info.external_attr
            clone.internal_attr = info.internal_attr
            clone.create_system = info.create_system
            target.writestr(clone, data)
    return buffer.getvalue()


def _read_back(sheet_xml: bytes, renders: dict[str, _Render]) -> None:
    root = ElementTree.fromstring(sheet_xml)
    observed: dict[str, tuple[str | None, str | None]] = {}
    for cell in root.iter(f"{_MAIN_NS}c"):
        reference = cell.get("r")
        if reference not in renders:
            continue
        value = cell.find(f"{_MAIN_NS}v")
        inline = cell.find(f"{_MAIN_NS}is/{_MAIN_NS}t")
        content = inline.text if inline is not None else value.text if value is not None else None
        observed[reference] = (cell.get("t"), content or ("" if content == "" else content))
    for reference, render in renders.items():
        if reference not in observed:
            raise WorkbookWriteError("verification_failed", f"{reference} vanished from the sheet")
        type_attr, content = observed[reference]
        if render.mode == "blank":
            if content is not None:
                raise WorkbookWriteError(
                    "verification_failed", f"{reference} should be blank, holds {content!r}"
                )
        elif render.mode == "number":
            if type_attr not in (None, "n") or content != render.text:
                raise WorkbookWriteError(
                    "verification_failed",
                    f"{reference} reads back {content!r}, not {render.text!r}",
                )
        elif type_attr != "inlineStr" or (content or "") != render.text:
            raise WorkbookWriteError(
                "verification_failed", f"{reference} reads back {content!r}, not {render.text!r}"
            )


def _verify_parts(template_bytes: bytes, produced: bytes, part_name: str) -> None:
    with (
        zipfile.ZipFile(io.BytesIO(template_bytes)) as before,
        zipfile.ZipFile(io.BytesIO(produced)) as after,
    ):
        if before.namelist() != after.namelist():
            raise WorkbookWriteError("verification_failed", "the part list changed")
        for name in before.namelist():
            if name == part_name:
                continue
            if before.read(name) != after.read(name):
                raise WorkbookWriteError(
                    "verification_failed", f"untouched part {name} is no longer byte-identical"
                )


def _verify_with_openpyxl(
    template_bytes: bytes, produced: bytes, sheet_name: str, renders: dict[str, _Render]
) -> None:
    try:
        from openpyxl import load_workbook
    except ImportError:  # pragma: no cover - openpyxl is an optional second opinion
        return
    with warnings.catch_warnings():
        # The hidden legacy sheets carry comments on merged cells; openpyxl
        # warns while reading them, which is noise for a read-only check.
        warnings.simplefilter("ignore", UserWarning)
        original = load_workbook(io.BytesIO(template_bytes), keep_links=True)
        written = load_workbook(io.BytesIO(produced), keep_links=True)
    if written.sheetnames != original.sheetnames:
        raise WorkbookWriteError("verification_failed", "sheet names or count changed")
    states_before = [original[name].sheet_state for name in original.sheetnames]
    states_after = [written[name].sheet_state for name in written.sheetnames]
    if states_after != states_before:
        raise WorkbookWriteError("verification_failed", "sheet visibility changed")
    sheet = written[sheet_name]
    for reference, render in renders.items():
        value = sheet[reference].value
        if render.mode == "blank":
            if value is not None:
                raise WorkbookWriteError(
                    "verification_failed", f"openpyxl reads {reference} as {value!r}, not blank"
                )
        elif render.mode == "inline":
            if value != render.text:
                raise WorkbookWriteError(
                    "verification_failed", f"openpyxl reads {reference} as {value!r}"
                )
        elif value is None or Decimal(str(value)) != Decimal(render.text):
            raise WorkbookWriteError(
                "verification_failed", f"openpyxl reads {reference} as {value!r}"
            )


def fill_workbook(
    template_bytes: bytes, mapping: TableMapping, snapshot: CalculationSnapshot
) -> FilledWorkbook:
    """Write the mapping's cells from the snapshot into a verified template copy."""

    source_digest = hashlib.sha256(template_bytes).hexdigest()
    if source_digest != mapping.template_digest:
        raise WorkbookWriteError(
            "template_digest_mismatch",
            f"mapping was authored against {mapping.template_digest}, got {source_digest}",
        )
    renders = {binding.cell: _render(binding, snapshot) for binding in mapping.bindings}
    try:
        with zipfile.ZipFile(io.BytesIO(template_bytes)) as archive:
            part_name = _sheet_part_name(archive, mapping.sheet_name)
            sheet_xml = archive.read(part_name).decode("utf-8")
    except zipfile.BadZipFile as error:
        raise WorkbookWriteError("unreadable_package", "template is not a zip package") from error
    rewritten = _rewrite_sheet(sheet_xml, renders).encode("utf-8")
    produced = _repack(template_bytes, part_name, rewritten)

    _verify_parts(template_bytes, produced, part_name)
    with zipfile.ZipFile(io.BytesIO(produced)) as archive:
        _read_back(archive.read(part_name), renders)
    _verify_with_openpyxl(template_bytes, produced, mapping.sheet_name, renders)

    written = tuple(
        binding.cell for binding in mapping.bindings if renders[binding.cell].mode != "blank"
    )
    skipped = {
        binding.cell: renders[binding.cell].reason
        for binding in mapping.bindings
        if renders[binding.cell].mode == "blank"
    }
    return FilledWorkbook(
        content=produced,
        written_cells=written,
        sheet_name=mapping.sheet_name,
        source_digest=source_digest,
        content_digest=hashlib.sha256(produced).hexdigest(),
        skipped=skipped,
    )
