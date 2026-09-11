# ADR 0019: Multiple-context output, template registry and opaque placeholders

- Status: Proposed for human review
- Date: 2026-09-10
- Delivery: formal PDF output follow-up to #5; coordinates with #8 contexts

## Context

Case review verifies every comparison context, but the writer contract carried
one `FactorReviewResult`, so a verified multiple-context case could only return
`verified/unsupported_contexts`. Formal Traditional Chinese output additionally
needs versioned template registration with approved fonts, and cloud execution
must never hold the sensitive original values that identify a case: cloud
artifacts may carry only opaque tokens that are re-identified locally.

## Decision

`PDFWriteRequest` gains `additional_results`. The sole writer protocol,
`async write_pdf(request) -> PDFWriteResult`, is unchanged. Every comparison in
a multiple-context request must bind a unique context in one case, and every
field reference resolves in exactly one bound comparison. A field map that
leaves any verified comparison unwritten fails validation: a partial
first-context report is never produced. Field-map order fixes output order, so
identical inputs, template version and writer version reproduce identical
output bytes and manifest; the embedded writer version is now `2`. The
controller attempts multiple-context output only when the composed writer
declares `supports_multiple_contexts` as the literal `True` and the approved
field map covers every verified comparison; otherwise the run keeps the honest
`verified/unsupported_contexts` status. Legacy single-context requests are
unchanged, and writers without the capability fail closed.

A versioned `TemplateRegistry` binds each (template, version) pair to its
template byte digest, complete field-map digest, per-page unrotated-CropBox
geometry, page classification and approved fonts pinned by file digest.
Registration validates field/page/geometry coherence without opening a PDF;
preflight now also verifies the loaded font file against the approved digest.
An absent digest rejects multiple-context and placeholder requests, including
local backfill, before rendering. Only legacy single-context requests without
placeholders may omit it. A readable or cached font is not approval; the digest
must come from trusted composition, never be inferred from write-time bytes.

`PDFField` alternatively carries an opaque `placeholder_token` (never both a
token and a value reference). A cloud writer renders the token verbatim; it has
no mapping to resolve, so it cannot obtain or backfill original values.
`LocalPlaceholderBackfill` performs re-identification as a fresh, fully
validated local write from the pristine template using an operator-supplied
private mapping, verifies the downloaded cloud artifact by digest without
editing it, and only accepts local file destinations. Publication wrappers
refuse any writer that reveals placeholders, so a re-identified PDF cannot be
uploaded by the artifact publisher.

The downloaded artifact participates in the existing protected-source identity
checks through atomic publication, including explicit overwrite. Font approval,
measurement and embedding use one immutable byte snapshot; a registration-cache
substitution fails closed. See the scoped
[review repair and composition notes](../formal-pdf-review-repair.md).

## Consequences

- A verified multiple-context case can produce one complete formal PDF; the
  local service facade still reports `unsupported_contexts` until its manifest
  evidence migrates to multiple contexts.
- Template, field-map and font approval are one versioned registration; a
  changed byte anywhere requires an explicit new version.
- Cloud PDFs are de-identified by construction; sensitive values exist only in
  local backfill configuration and their revealed output stays local.
- Fake writers and mocks are not multiple-context capable, preserving existing
  synthetic and demo statuses.

## Rejected alternatives

- A second writer protocol or a `CaseReviewResult` request field, because #5
  forbids competing writer interfaces and consumers already bind comparisons.
- Writing the subset of contexts a map covers, because a partial report is the
  exact failure `unsupported_contexts` exists to prevent.
- Backfilling by editing the downloaded artifact, because correction is
  restricted to Base-14 geometry and edited artifacts would bypass template,
  preflight and reopen verification.
- A truthy capability probe, because mocks and wrappers must not accidentally
  enable multiple-context output.
