# Shared PDF contract (foundation #6)

Public imports:

```python
from appraisal_review.domain.pdf_models import (
    PDFWriteRequest,
    PDFWriteResult,
    PDFField,
    PDFFieldMap,
    PDFValueRef,
    PDFWriteError,
    UnsupportedDocumentURIError,
    SourceDestinationConflictError,
    PDFReadError,
    PDFFieldPlacementError,
    PDFFontError,
    InvalidPDFResultError,
)
from appraisal_review.ports.pdf import PDFWriter
```

`async write_pdf(request: PDFWriteRequest) -> PDFWriteResult` is the only writer
signature. No PDF bytes/base64 cross this boundary. Factor models and workflow
ports re-export old names as aliases; they do not define a second writer.
Supporting value objects live in pdf_types to keep imports acyclic.

## Request and value lookup

Request fields: source_uri, destination_uri, result (FactorReviewResult),
field_map (PDFFieldMap). Sources and destinations must be separate objects.
At the application boundary, `AgentReviewRequest.pdf_template_uri` supplies this
`source_uri`. `case_document_uri` is the upstream case-data/evaluation-basis
document used for parsing and extraction; the writer must never reopen it to
derive values or use it as a fallback template. Template and data-source page
counts may differ. The local writer validates the output against the template it
actually copied, while the controller validates typed metadata, destination and
the exact field-ID set.
A field map declares template_id, one-based page numbers, PDF bottom-left
coordinates and `page_space=unrotated_crop_box`. Coordinates are points measured
from the unrotated CropBox's lower-left corner. B must incorporate page CropBox
offsets and rotation, using the dimensions of each page, before placement.

Editable-page policy is template-version-specific adapter configuration, not a
universal rule in the shared contract. The local writer must reject fields on
pages that are not explicitly editable. Reference-only map pages are copied
through unchanged and remain available as agent or human context.

Each opaque field_id identifies a destination, with an explicit value_ref:

```json
{
  "field_id": "comparison-1-road-rate",
  "page": 2,
  "bounding_box": [100, 200, 160, 214],
  "operation": "fill_blank",
  "value_ref": {
    "scope": "regional",
    "target_id": "synthetic-target",
    "comparable_id": "synthetic-comparable-1",
    "factor_id": "road.width",
    "value": "adjustment_percent"
  }
}
```

`target_grade`, `comparable_grade` and `adjustment_percent` require exactly one
matching factor_id in result.results. `total_adjustment_percent` omits factor_id
and reads result.summary.total_adjustment_percent. No expression evaluation,
column guessing or closest field match. Grade rendering/localization and numeric
formatting belong to B's deterministic display-value mapper, never to an LLM.

## Local rendering configuration

The local writer receives validated configuration explicitly. No font or file
discovery occurs at import. Configuration declares an absolute font path,
internal font name, positive font size, text color and alignment, labelled
annotation appearance, numeric precision/sign policy, and destination overwrite
policy. The absence of an installed local writer does not make a font path
mandatory for unrelated API or synthetic-demo startup.

The initial policy rejects existing destinations unless overwrite is explicitly
enabled. Text that does not fit its configured bounding box fails rather than
being shrunk, wrapped or truncated. The writer checks the configured font file,
glyph coverage and embedding only when a real write is requested. No system-font
fallback is allowed.

Grade display labels form an exact map over every supported `Grade`; missing or
extra entries are configuration errors. Percentage rendering uses decimal
round-half-up with configured fixed precision, an optional positive sign, no
positive sign for zero, and a trailing percent symbol. Non-finite values fail.
The formatter resolves only the explicit `PDFValueRef`; it does not inspect a
field ID, infer a factor or comparison identity, or read source PDF text.

## Read-only preflight

Before mutation, the local adapter builds an immutable preflight plan for the
entire request. It verifies the template identity and complete editable/reference
page classification, per-page CropBox/rotation/UserUnit geometry, page bounds,
non-overlapping fields, every value reference, font readability, glyph coverage,
`max_characters`, measured width and height, blank destinations, and removable
correction text. Annotation measurement includes its configured label.

Preflight rejects encrypted sources, records the SHA-256 digest of the exact
bytes it inspected, and leaves the source byte-for-byte unchanged. Mutation
refuses a source whose digest differs from that preflight plan. The current
conservative correction inspection supports directly
extractable `Tj`/`TJ` page text. Partial text intersections, ambiguous operator
mapping, and text inside Form XObjects fail explicitly; they never fall back to
painting over source content. This limitation must remain visible until those
content forms have deterministic removal support.

A write requires nonempty fields with value_ref and a single shared
(scope, target_id, comparable_id). Old unbound field maps still load for
compatibility but fail when used for writing. A schema-2 result carries a verified context; every field reference must match
it. Whole-case results live in AgentReviewRun.case_review and contain all
comparisons. The controller passes one result only when the case has exactly one
comparison; multiple contexts explicitly return unsupported_contexts and zero
writer calls. B must not select the first comparison or rename its identities.

PDFWriteResult adds artifact_created (default true for actual adapters). Test
doubles that create no document must return false. The run then returns
verified/simulated and no output_pdf_uri, while preserving diagnostic metadata.
A missing writer returns verified/unavailable. The single PDFWriter protocol is
unchanged; B's real byte validation remains required before returning true.

## Local field mutation

The mutation executor consumes only the preflight plan, rendering configuration,
source path and temporary output path. It clones the source into writer-owned
objects, applies all removals before additions, scales physical-point dimensions
for `UserUnit`, incorporates CropBox and MediaBox offsets, and preserves page
rotation. It writes a temporary artifact but does not publish it.

- `fill_blank` inserts the preformatted value only after preflight proved the
  destination blank.
- `annotate` preserves page content and adds a printable FreeText annotation
  with a labelled, colored, embedded-font Form XObject appearance. Its appearance
  is fixed in the PDF rather than delegated to viewer defaults.
- `correct` removes complete stale text-show operators, rechecks the removed text
  against the preflight plan, preserves non-text drawing operations, and adds the
  replacement with the configured embedded TrueType font. It never paints over
  stale text.

Mutation tests cover all right-angle page rotations, translated CropBoxes,
`UserUnit` scaling, source-byte preservation, real stale-text removal, extractable
replacement text, annotation structure and embedded appearance fonts. Reopen
verification and atomic publication are separate steps; a temporary mutation
result alone is not a successful `PDFWriteResult`.

## Object access and publication

Lexical validation supports absolute local file URIs and literal S3 bucket/key
URIs, without query/fragment. Percent-escaped S3 keys are rejected; they must not
be treated as filesystem paths. B checks resolved paths, symlinks/hardlinks,
readability, output parent policy, page bounds, font glyphs and text fit. Render
into a temporary file, reopen and validate, then atomically publish. S3 upload
happens only after successful local validation. Do not publish partial artifacts.

Local object access resolves platform-native absolute paths, follows source
aliases, and compares filesystem identity to reject normalized paths, symlinks
and hardlinks that refer to the source. The destination parent must already
exist. A same-directory temporary file keeps publication on one filesystem.
Overwrite mode uses atomic replacement; the default no-overwrite mode uses an
atomic destination creation so a racing writer is not overwritten. Temporary
files are removed on failure and are never returned as output artifacts. The
session rechecks both source filesystem identity and its initial SHA-256 digest
immediately before publication.

`LocalPDFWriter` implements the shared asynchronous port as an all-or-nothing
pipeline: stage local paths, preflight all fields, mutate the temporary file,
reopen and verify it, construct the typed result, atomically publish, then return.
Reopen verification checks source digest, page count, exact field-ID metadata,
replacement text origins, stale-text absence, annotation identity/rectangle/
appearance/font embedding, and structural fingerprints of reference-only page
content, resources and annotations. A failed check publishes no new destination;
an existing destination is replaced only after verification when overwrite was
explicitly configured. A generated-PDF integration test explicitly injects this
adapter as `ReviewAdapters.pdf_writer` through `build_controller`. Production
runtime construction remains unconfigured until an approved template policy,
field map and CJK font are available.

## S3 transfer wrapper

`S3ObjectStore` accepts an injected boto3-compatible transfer client and exposes
only PDF download and upload operations. It does not create a client, discover
credentials or import boto3. S3 bucket names and object keys come from the
shared URI identity parser. Keys remain literal: repeated separators, `..`
segments and Unicode characters are not normalized as filesystem paths.

`S3PDFWriter` requires S3 source and destination URIs, downloads the source into
an isolated temporary directory, constructs an equivalent local request, and
waits for the injected local writer to finish mutation, reopen verification and
local atomic publication. It verifies the local typed result and artifact before
calling upload. The uploaded object sets `ContentType=application/pdf`. Default
publication uses conditional `PutObject` with `If-None-Match: *`, so it cannot
overwrite a destination created by a racing writer; explicitly configured
overwrite omits that condition. The
returned result restores the requested S3 destination URI and preserves the
validated page count, field IDs and warnings. Any download, local validation or
result-contract failure suppresses upload. Temporary local files are cleaned on
success and failure.

Current evidence uses an injected in-memory client and real synthetic local PDF
rendering. No AWS credentials are required, and no live S3 transfer or cloud
atomicity claim is made. Runtime client construction and application composition
remain separate work.

The conservative correction, font and publication choices are recorded in
[ADR 0011](adr/0011-conservative-pdf-mutation-and-publication.md).

## Result, warning and error handling

PDFWriteResult contains output_uri, positive page_count, unique written_field_ids
and warnings (nonempty strings). Warnings must be noncritical and free of secrets
or source document text. Anything making output incorrect/unreadable is an error,
not a warning. Writer errors inherit PDFWriteError and identify URI support,
source/destination conflict, read, placement, font, write or invalid-result failure.
The controller returns only stable codes and a sanitized message.

AgentReviewRun.pdf_result preserves all successful PDF metadata/warnings;
output_pdf_uri is its compatibility alias. pdf_error is separate from review and
verification. A checks typed-result schema, destination identity, source page
count and exact written-field set. On any writer failure it preserves findings,
sets failed, and exposes no output URI. Invalid or needs_review verification never
calls the writer. A does not validate PDF bytes: B's reopened-output checks are
required before returning PDFWriteResult.

`adapters.local.fake_pdf.FakePDFWriter` records requests and returns a conspicuous
synthetic warning. It never creates a file and is not production PDF evidence.
A will inject it only through explicitly synthetic setup or tests.

## Ownership and migration

#6 owns shared schemas, pyproject dependency changes, aliases and the minimal
controller migration. #4 owns application entrypoints; #5 owns drawing/storage,
PDF extras and integration tests. Foundation #10, entry #13, review #15 and
extraction #16 are now merged; new writer integration should start from `main`
and use the same public PDFWriter contract. The original stacked foundation
delivery is historical. #8 extended the verifier and its tests under explicit
authorization; the local audit logger remains unchanged.
See ADR 0002. Later contract changes require a coordinated migration, not two
parallel PDFWriter definitions. #9's remote API authorizes document_id/object
references and resolves internal URIs; local file URIs are never a remote API.
