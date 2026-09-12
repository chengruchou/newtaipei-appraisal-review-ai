# Official table mapping

A mapping says which snapshot value goes in which cell of an official table. It
is data, validated against the original workbook before anything is written.
It computes nothing, decides no applicability and approves no output.

## Authoring

Dump the visible sheet first. Each line gives the cell, its merged anchor when
the cell sits inside a merged range, whether the print area covers it, and the
wording the original already prints there:

```sh
PYTHONPATH="$PWD/src" .venv/bin/python scripts/dump_workbook_cells.py \
  --workbook artifacts/table3.xlsx --output artifacts/table3-cells.tsv
```

The printed wording is the form's own label. It is never a case value, and a
label being present is not a statement that this case fills the cell beside it.
Which cell each snapshot value belongs in is a human decision; nothing in this
repository infers it from a nearby label.

## Shape

`TableMapping` pins the table (`table_3`/`table_4`/`table_5`), the template
file name and digest it was authored against, the visible sheet name, and one
`CellBinding` per cell. A binding carries the target cell, the snapshot source
path, a human label, the value kind, an explicit unit for percentages, optional
precision, and an `AbsencePolicy`.

`AbsencePolicy` keeps the three absences apart, because collapsing them is how a
form ends up asserting a zero nobody confirmed:

- missing always renders blank, never zero, and this is not configurable;
- not applicable renders blank or an explicit wording the mapping states;
- a confirmed zero renders zero, or an explicit wording.

## Percentages

Five percentage points is `5` in table 5 and `0.05` in table 4's percent-format
cells. The mapping binds that rule to the table: a `table_5` mapping rejects a
`percentage_fraction` binding and a `table_4` mapping rejects
`percentage_points`. Every percentage binding must also name its unit.

## Validation

```sh
PYTHONPATH="$PWD/src" .venv/bin/python scripts/check_table_mapping.py \
  --mapping config/official-tables/table3.json --workbook artifacts/table3.xlsx
```

Exit 0 means no findings, 3 means findings were reported, 2 means the mapping or
workbook could not be read. `validate_mapping` re-observes the workbook and
reports: a template digest other than the one the mapping was authored against,
an unknown or non-visible target sheet, a cell outside the sheet's used range, a
cell the print area does not cover, a cell inside a merged range (naming the
anchor to use instead), a cell that carries a formula in the original, and a
formula sample too truncated to support that last check.

Passing validation means the template can carry those cells. It is not a
statement that the values are right, that the mapping is complete, or that the
output may be published. The cell-by-cell human check against the snapshot
stays separate, as does the [converted PDF check](official-table-conversion.md).
