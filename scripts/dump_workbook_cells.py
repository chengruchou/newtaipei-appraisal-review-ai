"""Print the populated cells of one sheet, to author a table mapping against.

Run with PYTHONPATH=$PWD/src .venv/bin/python scripts/dump_workbook_cells.py
  --workbook artifacts/table3.xlsx [--sheet '<name>'] [--output cells.tsv]

Without --sheet the single visible sheet is used. Each line gives the cell, its
merged anchor when the cell sits in a merged range, whether the print area
covers it, and the text the original already prints there. That text is the
form's own wording; it is never a case value. Exit 2 means the workbook or the
sheet could not be read.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from appraisal_review.adapters.local.workbook_inventory import (
    WorkbookInventoryError,
    inventory_workbook,
    read_sheet_cells,
)
from appraisal_review.domain.official_workbook import cell_position, within_print_areas


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--workbook", type=Path, required=True)
    cli.add_argument("--sheet")
    cli.add_argument("--output", type=Path)
    args = cli.parse_args()

    try:
        inventory = inventory_workbook(args.workbook)
        visible = inventory.visible_sheets
        if args.sheet is None and len(visible) != 1:
            print(f"{len(visible)} visible sheets; name one with --sheet")
            return 2
        name = args.sheet if args.sheet is not None else visible[0].name
        sheet = next((entry for entry in inventory.sheets if entry.name == name), None)
        if sheet is None:
            print(f"{name} is not a sheet of {inventory.source_name}")
            return 2
        cells = read_sheet_cells(args.workbook, name)
    except (WorkbookInventoryError, OSError) as error:
        print(f"cannot read {args.workbook}: {error}")
        return 2

    anchors: dict[str, str] = {}
    for merged in sheet.merged_anchors:
        start, end = (cell_position(part) for part in merged.range.split(":"))
        for column in range(start[0], end[0] + 1):
            for row in range(start[1], end[1] + 1):
                anchors[f"{column}:{row}"] = merged.anchor

    lines = [f"# {inventory.source_name} sha256={inventory.digest} sheet={name}"]
    lines.append("cell\tmerged_anchor\tin_print_area\ttext")
    for reference, text in cells:
        column, row = cell_position(reference)
        anchor = anchors.get(f"{column}:{row}", "")
        inside = within_print_areas(reference, sheet.print_areas)
        state = "unknown" if inside is None else ("yes" if inside else "NO")
        flat = text.replace("\t", " ").replace("\n", " ").strip()
        lines.append(f"{reference}\t{anchor}\t{state}\t{flat}")

    report = "\n".join(lines)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report + "\n", encoding="utf-8")
        print(f"{len(cells)} populated cells written to {args.output}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
