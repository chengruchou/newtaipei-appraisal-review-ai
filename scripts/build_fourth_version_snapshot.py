"""Render the fourth-version Shulin results through the existing official writers.

Reads the three already-produced workbooks, maps them into snapshot entries with the
existing table mappings, then draws the official templates with
``workbook_writer.fill_workbook``. Table 3 carries four sections (P001-00..P004-00),
so its template is drawn once per section rather than silently keeping only the first.

The output is the operator's evidence that the import wires into the real renderer:
six .xlsx files plus a report of what mapped, what stayed absent, and why.
"""

from __future__ import annotations

# The assignment's own fullwidth punctuation appears in labels and sheet names.
# ruff: noqa: RUF001
import argparse
import hashlib
import json
from decimal import Decimal
from pathlib import Path

from appraisal_review.adapters.local.fourth_version_import import (
    IMPORT_LABEL,
    SOURCE_COMMIT,
    ImportedTable,
    import_table,
    section_mapping,
)
from appraisal_review.adapters.local.workbook_writer import fill_workbook
from appraisal_review.domain.calculation_snapshot import CalculationSnapshot
from appraisal_review.domain.official_table_mapping import TableMapping

#: (source file, source sheet) for each official table. Table 5's two names differ.
TABLE3_SECTIONS = ("P001-00", "P002-00", "P003-00", "P004-00")
SOURCE_FILES = {
    "table_3": "表3_地價區段勘查表_已填.xlsx",
    "table_4": "表4_比較法調查估價表_已填.xlsx",
    "table_5": "表5-1_影響地價區域因素分析明細表_已填.xlsx",
}
SOURCE_SHEETS = {
    "table_4": "表4比較法調查估價表",
    "table_5": "表5區域因素明細表＿一般住宅",
}
MAPPING_FILES = {
    "table_3": "table3-v1.json",
    "table_4": "table4-v1.json",
    "table_5": "table5-v1.json",
}


def load_mapping(mappings_dir: Path, table: str) -> TableMapping:
    return TableMapping.model_validate(
        json.loads((mappings_dir / MAPPING_FILES[table]).read_text())
    )


def template_for(templates_dir: Path, mapping: TableMapping) -> Path:
    for candidate in sorted(templates_dir.glob("*.xlsx")):
        if hashlib.sha256(candidate.read_bytes()).hexdigest() == mapping.template_digest:
            return candidate
    raise SystemExit(f"no template matching {mapping.table} digest {mapping.template_digest[:12]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="fourthVersion directory")
    parser.add_argument("--templates", type=Path, required=True)
    parser.add_argument("--mappings", type=Path, default=Path("configs/mappings"))
    parser.add_argument("--out", type=Path, required=True)
    arguments = parser.parse_args()
    arguments.out.mkdir(parents=True, exist_ok=True)

    imported: list[ImportedTable] = []
    entries: dict[str, object] = {}
    gaps: dict[str, str] = {}

    # Table 3: one read per section, into that section's own keys.
    mapping3 = load_mapping(arguments.mappings, "table_3")
    source3 = (arguments.source / SOURCE_FILES["table_3"]).read_bytes()
    for section in TABLE3_SECTIONS:
        prefix = section.split("-")[0]
        result = import_table(
            source_bytes=source3,
            mapping=mapping3,
            source_sheet=section,
            section=section,
            file_name=SOURCE_FILES["table_3"],
            source_prefix=prefix,
        )
        imported.append(result)
        entries.update(result.entries)
        gaps.update(result.gaps)

    # The unprefixed table_3.case.* keys the standard export reads are the base
    # parcel's section (P001-00), stated openly in each entry's trace; the other
    # three sections remain fully present under their own prefixes above.
    base = import_table(
        source_bytes=source3,
        mapping=mapping3,
        source_sheet=TABLE3_SECTIONS[0],
        section=TABLE3_SECTIONS[0],
        file_name=SOURCE_FILES["table_3"],
    )
    entries.update(base.entries)
    gaps.update(base.gaps)

    for table in ("table_4", "table_5"):
        mapping = load_mapping(arguments.mappings, table)
        result = import_table(
            source_bytes=(arguments.source / SOURCE_FILES[table]).read_bytes(),
            mapping=mapping,
            source_sheet=SOURCE_SHEETS[table],
            section="全表",
            file_name=SOURCE_FILES[table],
        )
        imported.append(result)
        entries.update(result.entries)
        gaps.update(result.gaps)

    # Draw each output with the existing writer. Table 3 is drawn per section by
    # rebasing the mapping onto that section's keys - the same renderer, four times.
    produced: list[dict[str, object]] = []
    for result in imported:
        table = result.table
        mapping = load_mapping(arguments.mappings, table)
        if table == "table_3":
            prefix = result.section.split("-")[0]
            mapping = section_mapping(mapping, prefix)
            name = f"table_3_{result.section}.xlsx"
        else:
            name = f"{table}.xlsx"
        template = template_for(arguments.templates, mapping)
        snapshot = _snapshot_for(mapping, entries, gaps)
        filled = fill_workbook(template.read_bytes(), mapping, snapshot)
        (arguments.out / name).write_bytes(filled.content)
        produced.append(
            {
                "file": name,
                "table": table,
                "section": result.section,
                "source_file": result.file_name,
                "source_sheet": result.sheet_name,
                "source_sha256": result.file_digest,
                "bytes": len(filled.content),
                "cells_written": len(filled.written_cells),
                "cells_skipped": len(filled.skipped),
                "entries_imported": len(result.entries),
                "gaps": len(result.gaps),
                "unmapped": result.unmapped,
            }
        )

    # The registrable snapshot: all tables, all sections, gaps preserved. The
    # revision reference is rebound by register_prepared_snapshot.py at hook-up.
    combined = _snapshot_all(entries, gaps)
    (arguments.out / "snapshot.json").write_text(combined.model_dump_json(indent=2))

    report = {
        "label": IMPORT_LABEL,
        "source_commit": SOURCE_COMMIT,
        "total_entries": len(entries),
        "total_gaps": len(gaps),
        "outputs": produced,
    }
    (arguments.out / "import-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str)
    )
    for row in produced:
        print(
            f"{row['file']:<28} written={row['cells_written']:>3} "
            f"skipped={row['cells_skipped']:>3} imported={row['entries_imported']:>3} "
            f"gaps={row['gaps']:>3} bytes={row['bytes']}"
        )
    print(f"\nentries={len(entries)} gaps={len(gaps)} -> {arguments.out}/import-report.json")
    return 0


def _snapshot_all(entries: dict[str, object], gaps: dict[str, str]) -> CalculationSnapshot:
    """Every imported key in one registrable snapshot."""
    from datetime import date

    from appraisal_review.domain.service_contracts import RevisionReference

    return CalculationSnapshot(
        revision=RevisionReference(
            case_id="55642f92-90ca-499c-bfb7-773f9eb03062",
            revision_id="f30bfabc-1096-40a9-a293-04a61c3f6633",
            material_digest="0" * 64,
        ),
        district="新北市樹林區",
        valuation_date=date(2022, 9, 1),
        rule_bundle_id="fourth-version-import",
        rule_bundle_version=SOURCE_COMMIT[:12],
        subjects=_subjects(),
        entries=entries,  # type: ignore[arg-type]
        gaps=gaps,
    )


def _snapshot_for(
    mapping: TableMapping, entries: dict[str, object], gaps: dict[str, str]
) -> CalculationSnapshot:
    """A snapshot carrying exactly the keys this mapping reads, plus their gaps."""
    from datetime import date

    from appraisal_review.domain.service_contracts import RevisionReference

    wanted = {binding.source for binding in mapping.bindings}
    present = {key: value for key, value in entries.items() if key in wanted}
    missing = {key: reason for key, reason in gaps.items() if key in wanted}
    if not present:
        # fill_workbook still needs a snapshot; an all-absent table renders blanks.
        present = {
            next(iter(wanted)): __import__(
                "appraisal_review.domain.calculation_snapshot", fromlist=["SnapshotEntry"]
            ).SnapshotEntry(state="missing")
        }
    return CalculationSnapshot(
        revision=RevisionReference(
            case_id="55642f92-90ca-499c-bfb7-773f9eb03062",
            revision_id="f30bfabc-1096-40a9-a293-04a61c3f6633",
            material_digest="0" * 64,
        ),
        district="新北市樹林區",
        valuation_date=date(2022, 9, 1),
        rule_bundle_id="fourth-version-import",
        rule_bundle_version=SOURCE_COMMIT[:12],
        subjects=_subjects(),
        entries=present,  # type: ignore[arg-type]
        gaps=missing,
    )


def _subjects() -> tuple[object, ...]:
    from appraisal_review.domain.calculation_snapshot import SnapshotSubject

    return (
        SnapshotSubject(
            subject_id="P001", role="comparison_base", label="比準地 P001-00 樹德段1415"
        ),
        SnapshotSubject(subject_id="P002", role="comparable", label="比較標的1 P002-00"),
        SnapshotSubject(subject_id="P003", role="comparable", label="比較標的2 P003-00"),
        SnapshotSubject(subject_id="P004", role="comparable", label="比較標的3 P004-00"),
    )


assert Decimal  # keep the import meaningful for readers scanning conversions

if __name__ == "__main__":
    raise SystemExit(main())
