"""Validate an official table mapping against the original workbook it targets.

Run with PYTHONPATH=$PWD/src .venv/bin/python scripts/check_table_mapping.py
  --mapping config/official-tables/table3.json --workbook artifacts/table3.xlsx

The mapping states which workbook digest it was authored against; this command
re-observes the workbook and reports every binding a written copy could not
honor. Exit 0 means no findings, 3 means findings were reported, 2 means the
mapping or the workbook could not be read.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from appraisal_review.adapters.local.workbook_inventory import (
    WorkbookInventoryError,
    inventory_workbook,
)
from appraisal_review.domain.official_table_mapping import TableMapping, validate_mapping


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--mapping", type=Path, required=True)
    cli.add_argument("--workbook", type=Path, required=True)
    cli.add_argument("--output", type=Path)
    cli.add_argument("--max-formulas", type=int, default=2000)
    args = cli.parse_args()

    try:
        mapping = TableMapping.model_validate_json(args.mapping.read_text(encoding="utf-8"))
        inventory = inventory_workbook(args.workbook, max_formulas=args.max_formulas)
    except (ValidationError, WorkbookInventoryError, OSError, ValueError) as error:
        print(f"cannot validate {args.mapping}: {error}")
        return 2

    findings = validate_mapping(mapping, inventory)
    print(f"{mapping.table} sheet={mapping.sheet_name} bindings={len(mapping.bindings)}")
    print(f"  template={inventory.source_name} sha256={inventory.digest}")
    for finding in findings:
        print(f"  finding {finding.code} {finding.cell or '-'}: {finding.detail}")
    if not findings:
        print("  no findings; the mapping addresses cells the template can carry")

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(
                [finding.model_dump(mode="json") for finding in findings],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"report written to {args.output}")
    return 3 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
