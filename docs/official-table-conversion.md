# Official table conversion

Role E delivers the filled official tables as Excel and as PDF. The PDF must be
the delivered Excel copy converted, not a separately produced document. This
page covers what is checked after a conversion runs.

## This is not the template PDF writer

The existing PDF path in `adapters/local/pdf_writer.py` fills an existing PDF
template at configured coordinates, and its verification assumes a printable
ASCII subset of the standard Type 1 Base-14 fonts with a field map and an
approved font digest (see [the PDF contract](pdf-contract.md)). A converted
official table has embedded CJK fonts and no field map, so it satisfies none of
those preconditions and must never be described as having passed that gate. The
artifact contract for a converted output is role A's to define.

## Provenance is asserted, not proven

No PDF proves which workbook produced it. `ConversionRecord.source_digest`
records which bytes the operator says were converted; an independent check means
reconverting from the digest-pinned source and comparing. The contracts and the
command below keep that distinction rather than implying the PDF was verified
against the workbook.

## Environment

Conversion itself needs a converter and Chinese fonts, which belong to role C.
Neither is present on the checked machine: `soffice`, `libreoffice` and
`unoconv` are all absent, and the repository contains no conversion code. The
checks here run on whatever PDF is produced, by any converter.

The font requirement is not cosmetic. A converted table whose fonts are not
embedded renders with substitute glyphs on the reviewer's machine, which is how
a visually correct local check turns into an unreadable delivered file.

## Run it

```sh
PYTHONPATH="$PWD/src" .venv/bin/python scripts/check_converted_pdf.py \
  --pdf artifacts/table4.pdf \
  --source artifacts/table4-filled.xlsx \
  --converter 'soffice 25.2' \
  --expected-pages 1 \
  --require-text '<closing note from the form>' \
  --forbid-text '<legacy example marker>'
```

Exit 0 means no findings, 3 means findings were reported, 2 means the output
could not be inspected.

## What it checks

- The record converted the same workbook digest the delivery expects.
- The output is not encrypted and has pages.
- The page count matches what the reviewer declared.
- No page is free of extractable text; a silently blank page is a failed
  conversion, not a converted page.
- Every font is embedded, with subset fonts identified.
- Required content is present. This is where the table 4 `A37` note belongs:
  content outside the print area is dropped by conversion, so name it as
  required text and the check fails instead of the reviewer missing it.
- Forbidden content is absent. Markers from the hidden legacy examples belong
  here, so a leaked example is caught rather than shipped.

## What it does not check

Layout fidelity, column widths, page breaks, merged-cell rendering and visual
correctness are not checked. Extractable text is not proof of correct
appearance. The page-by-page human review stays a human step, and the
cell-by-cell check against the source snapshot is separate again.
