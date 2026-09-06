# ADR 0012: Bind PDF inputs and template policy before publication

- Status: Proposed for human review
- Date: 2026-09-06
- Delivery: #5

## Context

An explicitly enabled overwrite can replace a reviewed criteria, forms, or
reference document unless the output is checked against every input identity.
Checking only the copied template is insufficient. A human-readable template ID
and page count also do not prove that the selected PDF bytes and coordinate map
are the approved pair: an unrelated PDF with the same number of pages can satisfy
those checks.

PDF text and visual content introduce a related trust problem. Nominal font
width does not bound text whose horizontal scale, spacing, rise, rendering mode,
`TJ` positioning, or source-font `/Widths` differ. An image, annotation, or
painted vector fill can occupy a field even when text extraction reports an
empty string.

## Decision

`PDFWriteRequest` carries `protected_source_uris`. The controller populates it
from the parsed criteria and forms documents plus every selected policy-registry
document. Contract validation rejects a destination with the same canonical URI
identity as the template or a protected source before calling a writer. Local
object access additionally resolves filesystem identities and rejects normalized
paths, symbolic links, and hard links, both when staging and immediately before
publication. S3 compares literal bucket/key identities before any transfer.

`PDFTemplatePolicy` requires two lowercase SHA-256 values: one for the exact
template bytes and one for a canonical JSON serialization of the complete
`PDFFieldMap`. Preflight validates both before field operations. Template ID and
complete editable/reference page classification remain separate checks, but
neither substitutes for byte and coordinate binding.

Correction accepts only text geometry represented by its bounding calculation.
Non-default horizontal scale, character spacing, word spacing, rise, rendering
mode, non-zero `TJ` adjustments, and fonts with custom or embedded metrics fail
explicitly. The supported correction subset is printable ASCII using a
recognized standard Type 1 Base-14 font without custom width, descriptor,
character-program, descendant-font, or Unicode mapping data. `fill_blank`
treats intersecting inline images, painted XObjects, annotation rectangles,
filled vector paths, and arbitrary stroke-only paint as occupancy. A stroke is
accepted as a table border only when it is a solid rectangle or straight
segment, uses default cap and join behavior, coincides with and covers a complete
field boundary, and remains no wider than two PDF user-space points after the
current transformation. Direct operators and named `ExtGState` resources update
stroke state under `q`/`Q`; device-dependent hairlines, stroke adjustment,
shadings, and unsupported or malformed paint geometry fail before mutation and
no destination is published.

Local file URIs are converted with the platform URI converter exactly once.
Canonical identity parsing and filesystem conversion therefore agree for literal
percent sequences, spaces, and Unicode filenames.

## Consequences

- Overwrite permission cannot authorize replacement of any selected review input.
- Deployments must provision reviewed template and field-map digests alongside
  editable/reference page policy.
- Changing one template byte or field coordinate requires an explicit policy
  version/update, even when template ID and page count remain unchanged.
- Complex PDF text state, custom source-font metrics, and uncertain visual
  occupancy reduce supported-template coverage but cannot silently produce an
  incorrect correction or fill.
- The new request field is provider-neutral metadata; PDF bytes still do not cross
  the writer port.

## Rejected alternatives

- Protecting only the copied template, because case and criteria evidence are at
  least as important to preserve.
- Trusting filename, template ID, or page count, because each can match unrelated
  content.
- Computing trusted digests from the write-time input and approving them
  automatically, because that proves consistency rather than authorization.
- Ignoring non-text content when checking a blank field, because visual occupancy
  is independent of extracted text.
- Estimating unsupported text geometry, because a plausible bound is not a safe
  basis for destructive correction.
