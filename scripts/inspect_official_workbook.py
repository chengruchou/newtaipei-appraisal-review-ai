"""Inventory the organizer's original .xlsx workbooks without modifying them.

Run with PYTHONPATH=$PWD/src .venv/bin/python scripts/inspect_official_workbook.py
  --workbook artifacts/originals/table3.xlsx --output artifacts/workbook-inventory.json

Each workbook is opened read-only. The report records the file digest, sheet
visibility, merged anchors, print areas, formulas and external links, plus the
findings a human reviewer must resolve before any copy is written. Exit 0 means
the inventory completed, never that a workbook was approved for writing.
Exit 2 means a workbook could not be read; no partial report is written.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from appraisal_review.adapters.local.workbook_inventory import (
    WorkbookInventoryError,
    inventory_workbook,
)
from appraisal_review.domain.official_workbook import review_inventory


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--workbook", type=Path, action="append", required=True)
    cli.add_argument("--output", type=Path)
    cli.add_argument("--max-formulas", type=int, default=200)
    args = cli.parse_args()

    reports: list[dict[str, Any]] = []
    for path in args.workbook:
        try:
            inventory = inventory_workbook(path, max_formulas=args.max_formulas)
        except (WorkbookInventoryError, OSError) as error:
            print(f"unreadable workbook {path}: {error}")
            return 2
        findings = review_inventory(inventory)
        reports.append(
            {
                "inventory": inventory.model_dump(mode="json"),
                "findings": [finding.model_dump(mode="json") for finding in findings],
            }
        )
        visible = inventory.visible_sheets
        print(f"{inventory.source_name} sha256={inventory.digest} bytes={inventory.byte_size}")
        print(
            f"  sheets={len(inventory.sheets)} visible={len(visible)} "
            f"hidden={len(inventory.hidden_sheets)} "
            f"external_links={len(inventory.external_references)} "
            f"macros={inventory.has_macros}"
        )
        for sheet in inventory.sheets:
            print(
                f"  [{sheet.index}] {sheet.name} {sheet.visibility} "
                f"dimension={sheet.dimension or '-'} merged={len(sheet.merged_anchors)} "
                f"formulas={sheet.formula_count} external={sheet.external_formula_count} "
                f"values={sheet.value_cell_count} print_areas={';'.join(sheet.print_areas) or '-'}"
            )
        for finding in findings:
            print(f"  finding {finding.code} {finding.sheet or '-'}: {finding.detail}")

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"report written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
