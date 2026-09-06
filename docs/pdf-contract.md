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
A field map declares template_id, one-based page numbers, PDF bottom-left
coordinates and `page_space=unrotated_crop_box`. Coordinates are points measured
from the unrotated CropBox's lower-left corner. B must incorporate page CropBox
offsets and rotation, using the dimensions of each page, before placement.

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

## Operations and storage (B implementation required)

- fill_blank: prove the destination is blank before inserting; otherwise fail.
- annotate: add an explicit review annotation, preserving the original value.
- correct: replace old field text in a copy and verify no stale text remains;
  merely overlaying text is not correction. Unsupported operations must fail.

Lexical validation supports absolute local file URIs and literal S3 bucket/key
URIs, without query/fragment. Percent-escaped S3 keys are rejected; they must not
be treated as filesystem paths. B checks resolved paths, symlinks/hardlinks,
readability, output parent policy, page bounds, font glyphs and text fit. Render
into a temporary file, reopen and validate, then atomically publish. S3 upload
happens only after successful local validation. Do not publish partial artifacts.

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
PDF extras and integration tests. A stacks on the foundation commit; B branches
from the same commit. That foundation preserved the verifier/logger files. #8 now extends the verifier
and its tests under explicit authorization; the local audit logger remains unchanged.
See ADR 0002. Later contract changes require a coordinated migration, not two
parallel PDFWriter definitions. #9's remote API authorizes document_id/object
references and resolves internal URIs; local file URIs are never a remote API.
