# Official workbook inventory

Role E owns the official table 3/5/4 mapping, the exporter and the independent
human check. This inventory is the first step of that work: it records what the
organizer's original `.xlsx` files actually contain, before any copy is written.
It produces observations and reviewer findings, never an approval and never a
writable-cell decision.

## Scope and boundaries

- Originals stay read-only in an ignored `artifacts/` directory. The inventory
  opens them read-only and never writes, refreshes or resolves an external link.
- The reader is `adapters/local/workbook_inventory.py`. It parses the OPC package
  with the standard library, so it needs no spreadsheet dependency and does not
  depend on a writer library's normalization. `openpyxl` is not a dependency of
  this repository; adding one for the writer side is a shared `pyproject.toml`
  change that belongs to role A.
- Contracts are in `domain/official_workbook.py`. `WorkbookInventory` is an
  observation record. It is not the published-artifact contract: the existing
  publication boundary in `domain/artifact_publication.py` keys objects as
  `.pdf` and carries PDF-only fields, so an XLSX artifact type must be defined
  by role A rather than forced into those fields.

## Run it

```sh
PYTHONPATH="$PWD/src" .venv/bin/python scripts/inspect_official_workbook.py \
  --workbook artifacts/originals/table3.xlsx \
  --workbook artifacts/originals/table5.xlsx \
  --workbook artifacts/originals/table4.xlsx \
  --output artifacts/workbook-inventory.json
```

Exit 0 means the inventory completed. Exit 2 means a workbook could not be read
and no report is written. The report and the originals both stay local.

## What it records

Per workbook: file name, SHA-256 digest, byte size, defined-name count, macro
part presence and every declared external link with its raw target.

Per sheet: workbook order, name, `visible`/`hidden`/`veryHidden` state, declared
dimension, print areas, merged ranges with the top-left anchor a writer must
address, formula count, how many of those formulas reference another workbook,
a capped formula sample, how many cells carry a value, and which of those cells
fall outside the print area. A print area that is not a plain cell range, such
as a whole-column reference, is reported as unresolved rather than treated as
excluding content.

## Findings

`review_inventory` reports what a human must resolve: no visible sheet, more
than one visible sheet, formulas or external formulas in a visible sheet, any
external link, macros, a visible sheet with no print area, an unresolved print
area, and any cell that carries a value outside the print area. Formulas in a
hidden sheet are recorded but are not findings; the hidden legacy examples are
expected and are never this case's truth.

## What this does not do

It does not produce the writable-cell whitelist, does not map snapshot values to
cells, does not write a copy, and does not open a file in Excel. Those stay
separate steps, and the visual and cell-by-cell check remains a human one.
Checks on a converted PDF live in [official table conversion](official-table-conversion.md).

## Observed originals

Running this against the organizer's three workbooks confirms 24 sheets each,
one visible form sheet and 23 hidden legacy examples, no formulas in any visible
sheet, and two external links per workbook pointing at 2012-era file paths that
must never be refreshed.

Table 4 is the exception that changes delivery: its visible sheet spans `A1:S37`
while its print area stops at `$A$1:$R$36`, so the note in `A37` is real content
that no printed or converted page will show. Column S is empty and harmless.
That note is the organizer's own text, so the print area is not something to
quietly widen; record it and ask the organizer.
