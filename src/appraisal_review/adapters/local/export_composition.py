"""Discover the export ingredients a local composition can honestly offer.

Templates are operator-supplied files that never enter Git, so composition finds them
by content: each committed mapping pins the sha256 of the workbook it was authored
against, and a template directory is scanned for files whose bytes match. A missing
template, mapping or writer simply yields None and the export plane stays unwired -
the routes then answer capability_unavailable instead of pretending.

The converter is looked up the same way the rest of the host tooling is: an explicit
environment override first, then PATH, then the standard macOS application path.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

from appraisal_review.adapters.local.workbook_pdf import LocalWorkbookConverter
from appraisal_review.adapters.local.workbook_render import visible_only_copy
from appraisal_review.application.exports import ExportAssets
from appraisal_review.domain.official_table_mapping import OfficialTable, TableMapping
from appraisal_review.ports.workbook_conversion import ConvertedWorkbook

TEMPLATES_DIR_VARIABLE = "REVIEW_TEMPLATES_DIR"
CONVERTER_VARIABLE = "REVIEW_SOFFICE"
_MAC_SOFFICE = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
_MAPPING_FILES: dict[OfficialTable, str] = {
    "table_3": "table3-v1.json",
    "table_4": "table4-v1.json",
    "table_5": "table5-v1.json",
}
BUNDLE_ID = "official-forms-ntpc"
BUNDLE_VERSION = "20260912"


def discover_export_assets(
    templates_dir: Path | None = None,
    mappings_dir: Path = Path("configs/mappings"),
) -> ExportAssets | None:
    if templates_dir is None:
        templates_dir = Path(os.environ.get(TEMPLATES_DIR_VARIABLE, "artifacts/official-templates"))
    if not templates_dir.is_dir() or not mappings_dir.is_dir():
        return None
    mappings: dict[OfficialTable, TableMapping] = {}
    for table, name in _MAPPING_FILES.items():
        path = mappings_dir / name
        if not path.is_file():
            return None
        mappings[table] = TableMapping.model_validate(json.loads(path.read_text()))
    by_digest: dict[str, Path] = {}
    for candidate in sorted(templates_dir.glob("*.xlsx")):
        by_digest[hashlib.sha256(candidate.read_bytes()).hexdigest()] = candidate
    templates: dict[OfficialTable, Path] = {}
    for table, mapping in mappings.items():
        template = by_digest.get(mapping.template_digest)
        if template is None:
            # The operator's template does not match what the mapping was authored
            # against; filling it anyway would write values into unknown cells.
            return None
        templates[table] = template
    return ExportAssets.load(
        bundle_id=BUNDLE_ID, version=BUNDLE_VERSION, templates=templates, mappings=mappings
    )


class RenderPreparedConverter:
    """Rasterize a visibility-pruned derived copy instead of the full workbook.

    The installed headless converter renders hidden sheets, which would leak the
    template's 23 legacy example sheets into every delivered PDF. The wrapper checks
    the caller's digest against the true filled workbook, derives the render copy,
    and delegates with the copy's own digest, so the inner converter's guarantee
    still binds the exact bytes it rasterized. The returned workbook_sha256 is the
    render copy's hash; the operation records both.
    """

    def __init__(self, inner: LocalWorkbookConverter) -> None:
        self.inner = inner

    def convert(
        self,
        workbook: bytes,
        *,
        expected_sha256: str,
        verify_text: tuple[str, ...] = (),
        expected_page_count: int | None = None,
    ) -> ConvertedWorkbook:
        if hashlib.sha256(workbook).hexdigest() != expected_sha256:
            from appraisal_review.ports.workbook_conversion import ConversionUnavailable

            raise ConversionUnavailable("workbook_digest_mismatch")
        prepared = visible_only_copy(workbook)
        return self.inner.convert(
            prepared,
            expected_sha256=hashlib.sha256(prepared).hexdigest(),
            verify_text=verify_text,
            expected_page_count=expected_page_count,
        )


def discover_converter() -> LocalWorkbookConverter | None:
    override = os.environ.get(CONVERTER_VARIABLE)
    if override:
        path = Path(override)
        return LocalWorkbookConverter(executable=path) if path.is_file() else None
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return LocalWorkbookConverter(executable=Path(found))
    if _MAC_SOFFICE.is_file():
        return LocalWorkbookConverter(executable=_MAC_SOFFICE)
    return None


def discover_render_converter() -> RenderPreparedConverter | None:
    inner = discover_converter()
    return None if inner is None else RenderPreparedConverter(inner)
