# ADR 0055: Faithful workbook to PDF conversion, or none

## Status

Accepted for implementation and local verification. The conversion has not been
performed on any host: the required converter is not installed where this was
written, and the adapter reports that rather than substituting a fallback.

## Context

The delivery flow ends by rendering the PDF from the same official workbook the
reviewer approved. That makes the converter the last component between an approved
value and a delivered document, and it makes fidelity the whole requirement. The
official forms use merged cells, defined print ranges, hidden worksheets, external
links and Traditional Chinese headings, and one of them has a cell outside its
print range.

Two tempting shortcuts break the requirement. Re-drawing the sheet from extracted
values with a PDF library produces a document that looks similar and is not the
approved workbook: layout, merged anchors and print range are re-invented, and the
output is a second authoring step rather than a rendering of the first. Letting the
converter refresh external links or recalculate formulas lets the delivered PDF
disagree with the approved workbook.

A converter can also fail quietly. Missing fonts for the document's script render
headings as blank boxes, which produces a plausible-looking PDF with no readable
text and no error.

## Decision

Define `ports/workbook_conversion.py`. A converter reports a capability without
attempting a conversion, and converts exact bytes identified by digest. It may not
change values, refresh a link, recalculate a formula or redraw a sheet. A converter
that cannot guarantee that is reported unavailable; no fallback renderer is
substituted.

`adapters/local/workbook_pdf.py` implements it with a local headless office
converter. It runs with `--headless --norestore` in a private
`-env:UserInstallation` profile directory so it cannot join an interactive session,
bounded by a timeout, with arguments passed as a list and never through a shell.

Verification is on the output, not on trust in the converter:

- the input digest must equal the approved digest, and the input must be a package;
- the output must be a bounded, readable, unencrypted PDF;
- the page count must match the caller's expectation when one is given;
- text must be extractable, which catches a blank-box render;
- every string the caller requires must be present, which catches a heading lost to
  a font fallback.

The capability report distinguishes `ready`, `converter_missing`, `fonts_missing`
and `unverified`. Unknown font coverage and an unreportable converter version are
`unverified`, never `ready`. Failure codes carry no path, cell content or converter
diagnostic, because those can quote the document.

## Consequences

The delivered PDF is a rendering of the approved workbook, and a host that cannot
produce one says so before a reviewer relies on it. A silent font failure becomes a
refusal.

The conversion depends on a system package that is outside this repository, so it
is an environment prerequisite rather than something the project can install for
itself. Where it is absent the flow stops at the filled workbook, which is the
honest outcome: the workbook is complete and the PDF step is unavailable.

Font coverage is checked through fontconfig when present. That answers whether a
font for the script exists on the host; it does not prove the converter selected it
for a particular cell, which is why the per-run output text check exists and why a
reviewer still opens the result.
