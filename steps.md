# Deterministic PDF Writer Implementation Steps

## Purpose

Implement the PDF writing and storage work described in `goal.md` while preserving the
original template PDF and consuming the existing shared contract. The implementation must use
`appraisal_review.ports.pdf.PDFWriter` and the public models and errors exported by
`appraisal_review.domain.pdf_models`. It must not introduce a competing writer protocol or
change valuation verification semantics.

The implementation is complete only when it produces and validates a real PDF artifact.
The existing `FakePDFWriter` remains a clearly labelled synthetic test/demo adapter and is
not evidence of successful PDF generation.

## Implementation Progress

Steps 1 through 11 are implemented on `feat/pdf_writer`. Step 9 has generated-PDF
integration coverage that injects `LocalPDFWriter` through `ReviewAdapters.pdf_writer` and
`build_controller`; this proves the controller gate and a real local publication without
claiming automatic production runtime selection. Step 10 covers the synthetic acceptance
matrix, including an original test-time-generated TrueType font with an extractable embedded
CJK glyph. Step 11 records the durable template-identity, correction, font and publication
decisions in ADRs and reconciles implementation-status documentation. Pre-rebase Step 12
validation passed Ruff, format, mypy, all 250 tests and the complete pending-snapshot gate.
The categorized commits are rebased onto `origin/main` at 0e9a583; the generated-PDF
integration now uses the merged schema-2 source, authorization and coverage contracts, and
the focused controller/PDF tests pass. On the current Windows host, the merged POSIX-only
reviewer tests, multiprocessing restrictions and related mypy platform checks still require a
supported-host validation run. Push, pull-request creation and human review remain pending
explicit authorization.
Production CJK font and approved template-map inputs remain explicit deployment prerequisites
outside this repository change.

## Local Reference Document Roles

The ignored PDF files currently stored at the repository root have distinct roles in the
intended workflow:

- `評價基準明細表範例.pdf` is the example data source from which the upstream document
  understanding and deterministic review stages obtain the values that are eligible for
  writing.
- `查估書表範本.pdf` is the target form layout. A preserved copy of this template is the PDF
  represented by `PDFWriteRequest.source_uri`, and the writer publishes a separately named
  completed copy at `PDFWriteRequest.destination_uri`.

The writer must not read values directly from the evaluation-basis PDF or bypass the typed
`FactorReviewResult`. It consumes only verified typed results and a versioned field map. The
evaluation-basis example is case-specific source material, not universal policy, while the
form template defines the initial placement target for field-map development.

Both PDFs are local, ignored, read-only reference materials. Do not add them to Git, copy them
into test fixtures, embed their contents in tests, or publish generated derivatives. Automated
tests must continue to use programmatically generated synthetic PDFs. Manual local validation
against the form template may supplement, but never replace, reproducible synthetic tests.

The following sections of `查估書表範本.pdf` are reference-only material and are outside the
PDF writer's editing and drawing scope:

- `土地徵收市價查估地價區段略圖`;
- `土地徵收市價查估地價使用分區圖`;
- `土地徵收市價查估地價區段圖`.

These maps may be useful evidence or context for an agent or a human reviewer, but the writer
must not generate, redraw, annotate, correct, or populate them. They must remain present and
unchanged when the form template is copied to the completed output. Their exact page numbers
must be identified from the applicable template version instead of being guessed or hard-coded
as universal page numbers.

A read-only inspection of the currently supplied six-page local template found these sections
on pages 4, 5, and 6 respectively. This is template-specific evidence for the current local
file, not a universal page-number rule; production configuration must still bind the editable
and reference-only page policy to an identified template version.

## Constraints

- Do not modify `src/appraisal_review/domain/verification.py`,
  `src/appraisal_review/adapters/local/audit.py`, or
  `tests/unit/test_verification.py`.
- Do not commit competition PDFs, real cases, OCR output, generated PDFs, proprietary or
  system fonts, credentials, or cloud artifacts.
- Keep PDF rendering and storage provider-specific behavior behind adapters.
- Do not access files, initialize cloud clients, or discover credentials at module import.
- Treat field IDs as opaque placement identifiers. Resolve values only through the explicit
  `PDFValueRef` in the shared contract.
- Restrict edits to explicitly approved form pages and regions. Reference-only map pages are
  copied through unchanged and are never rendering targets.
- Fail explicitly when placement, identity, glyph coverage, correction, or validation cannot
  be proven safe.
- Preserve all review findings when PDF writing fails, and never publish a partial artifact.

## Proposed Modules

- `src/appraisal_review/adapters/local/pdf_overlay.py`
  - Implement `LocalPDFWriter`.
  - Resolve deterministic display values.
  - Validate page geometry, text placement, glyph coverage, and operations.
  - Apply PDF content changes and reopen the result for verification.
- `src/appraisal_review/adapters/local/object_access.py`
  - Parse and resolve local `file://` URIs.
  - Detect source/destination aliases, symlinks, and hardlinks.
  - Manage scoped temporary files and atomic publication.
- `src/appraisal_review/adapters/aws/storage/s3_object_store.py`
  - Provide narrow download and upload operations around an injected S3 client.
  - Keep S3 object keys literal and set `application/pdf` on upload.
- `src/appraisal_review/adapters/aws/pdf/s3_pdf_writer.py`
  - Download the source to scoped local storage.
  - Delegate rendering and validation to `LocalPDFWriter`.
  - Upload only a successfully validated output.
- `configs/pdf_fields/`
  - Store synthetic, versioned field-map examples only.
  - Do not store coordinates or data taken from protected case files unless they are approved
    for publication.
  - Represent template-specific editable-page policy as versioned configuration rather than
    hard-coded page logic. Reference-only pages must not contain writable field mappings.

Package directories should include the required `__init__.py` files without exporting cloud
implementations through local modules.

## Step 1: Prove the High-Risk PDF Operations

Before building storage integration, create focused synthetic tests that generate their PDF
inputs inside pytest temporary directories. Verify that the selected libraries can:

1. Read and write PDFs with pages rotated by 0, 90, 180, and 270 degrees.
2. Handle CropBoxes whose lower-left corner is not `(0, 0)`.
3. Handle mixed portrait, landscape, A4-like, and A3-like page dimensions in one document.
4. Convert all four bounding-box corners and the center from the contract's one-based,
   unrotated CropBox coordinate space to the page content space.
5. Identify text-show operations intersecting a correction rectangle.
6. Remove a complete text operation without removing nearby line or border drawing
   operations.
7. Reopen the result and prove that replacement text exists and stale text does not.

Prefer `pypdf` for PDF structure/content operations and `reportlab` for deterministic text
appearance and TrueType font embedding. Do not adopt a library with incompatible licensing.
If a correction intersects only part of a text operation, uses an undecodable font, or cannot
be mapped reliably, fail instead of covering the old text with a painted rectangle.

## Step 2: Add Explicit PDF Dependencies and Configuration

Coordinate the shared `pyproject.toml` change and add PDF dependencies as an optional extra.
Pin compatible version ranges and keep AWS dependencies separate from local PDF dependencies.

Define explicit rendering configuration, injected into the local writer, for at least:

- font file path;
- internal font name;
- font size;
- text color and alignment;
- annotation label and appearance;
- deterministic numeric precision and sign policy;
- destination overwrite policy.

The font path must never default silently to a system font. Document the configuration in
`.env.example` and the PDF contract documentation without committing a font file.

Recommended initial policies:

- reject an existing destination unless overwrite is explicitly enabled;
- fail on overflow instead of shrinking, wrapping, or truncating text;
- render adjustment percentages with an explicit, tested sign and decimal policy;
- require an explicit grade display-name mapping when output labels differ from enum values.

## Step 3: Implement Deterministic Value Resolution

Implement an internal display-value mapper that accepts a `FactorReviewResult` and one
`PDFValueRef`. It must resolve exactly one of:

- `target_grade`;
- `comparable_grade`;
- `adjustment_percent`;
- `total_adjustment_percent`.

Requirements:

- A factor value must match exactly one `factor_id`.
- A total must omit `factor_id` and come from the result summary.
- Missing, duplicate, ambiguous, or unsupported values raise `PDFFieldPlacementError`.
- The mapper must not evaluate expressions, parse `field_id`, infer a comparable from column
  position, or invoke an LLM.
- Grade localization and numeric formatting must be deterministic and independently tested.
- Mixed comparison contexts remain unsupported until the shared multi-comparable contract is
  introduced.

## Step 4: Implement Local Object Access

For a `file://` source and destination:

1. Require an absolute path and reject unsupported authorities, queries, and fragments.
2. Decode the URI once using platform-correct path conversion.
3. Require the source to exist, be a regular readable file, and contain a readable PDF.
4. Require the destination parent to exist and satisfy the configured write policy; do not
   create an unexpected directory tree implicitly.
5. Resolve path aliases and symlinks.
6. Compare filesystem identity where available to detect hardlinks.
7. Reject any source/destination conflict with `SourceDestinationConflictError`.
8. Create temporary output in the destination filesystem so final publication can be atomic.

Map URI failures to `UnsupportedDocumentURIError`, source read failures to `PDFReadError`, and
publication failures to `PDFWriteError`. Do not include sensitive paths or document text in
externally visible warnings.

## Step 5: Validate the Entire Write Before Mutation

Validate every mapped field before publishing or applying any irreversible operation:

- page number is within the source page count;
- page is explicitly editable for the selected template version and is not reference-only;
- bounding box is completely inside that page's unrotated CropBox;
- page-specific dimensions, CropBox offsets, and rotation are valid;
- field IDs are unique;
- every `value_ref` resolves exactly once;
- the operation is supported;
- `max_characters`, when present, is respected;
- the configured font exists and is readable;
- every character has a glyph in the configured font;
- measured rendered width and height fit inside the bounding box;
- a `fill_blank` region contains no existing text;
- a `correct` region has text that can be removed unambiguously.

Use `PDFFieldPlacementError` for invalid geometry, lookup, occupation, or overflow and
`PDFFontError` for missing fonts or glyphs. Unsupported operations are already rejected by the
shared Pydantic model, but the adapter must still use an exhaustive operation dispatch.

## Step 6: Implement Field Operations

### `fill_blank`

- Inspect text intersecting the field bounding box.
- Require the destination region to be blank within a documented tolerance.
- Add the formatted value with the configured embedded font.
- Fail if existing content makes blankness ambiguous.

### `annotate`

- Preserve the original value and content stream.
- Add a visibly distinct, labelled review annotation.
- Give the annotation a deterministic appearance rather than relying on viewer defaults.
- Verify the annotation object, label, target page, and placement after reopening the file.

### `correct`

- Locate complete text-show operations contained by the correction region.
- Remove only the stale text operations from the page content stream.
- Preserve paths, borders, images, and unrelated text.
- Add the replacement text using the configured embedded font.
- Reject partial intersections, ambiguous text transforms, and undecodable content.
- Reopen the output and verify that stale regional text is absent and the replacement is
  extractable.
- Never treat painting new text or a white rectangle over old text as correction.

Apply removals before additions when several fields affect the same page. Reject overlapping
field operations unless a deterministic ordering rule is explicitly approved and tested.

## Step 7: Write, Reopen, Verify, and Atomically Publish

Use one all-or-nothing pipeline:

```text
validate source and all fields
-> copy source to a scoped temporary destination
-> apply corrections
-> apply blank fills and annotations
-> write temporary PDF
-> reopen temporary PDF
-> verify readability, page count, field effects, and expected metadata
-> atomically publish destination
-> return PDFWriteResult
```

Verification must include:

- source hash is unchanged;
- output opens successfully;
- output page count equals source page count;
- all expected field IDs were written exactly once;
- correction checks pass after extraction;
- reference-only map pages retain their original page content, resources, and annotations;
- no temporary or destination artifact remains after a failed write.

Return `PDFWriteResult` only after publication succeeds. Include only noncritical, sanitized
warnings. Any problem that can make the document wrong or unreadable is an error.

## Step 8: Implement the S3 Object-Store Seam

Implement `S3ObjectStore` around an injected client. It must not create a client at import and
must not normalize S3 keys as filesystem paths.

The S3 writer flow is:

1. Validate literal source and destination `s3://bucket/key` identities.
2. Create a scoped temporary directory.
3. Download the source object to a local source path.
4. Build an equivalent local `PDFWriteRequest` without changing the review result or field map.
5. Invoke `LocalPDFWriter` and wait for full local validation.
6. Upload the validated file with `ContentType=application/pdf`.
7. Return a `PDFWriteResult` whose output URI is the requested S3 URI and whose page count,
   written field IDs, and warnings come from the validated local result.
8. Clean up all temporary files.

Tests must prove download -> local validation -> upload ordering, suppress upload after any
local failure, preserve literal S3 keys, set the content type, and keep source and destination
objects separate. Local PDF tests must run without AWS credentials, and local PDF modules must
not import `boto3`.

## Step 9: Integrate Through the Existing Controller Boundary

Inject the real writer through `ReviewAdapters.pdf_writer` and the existing composition root.
Do not add a direct rendering call to the controller.

In the intended real-document flow, parsing and rule evaluation use the evaluation-basis
example upstream, while the write request uses the form template as its source PDF. The
controller must pass only the resulting verified `FactorReviewResult` to the writer; the writer
must not reopen the evaluation-basis document to derive or reinterpret values.

Add a real local PDF integration test proving:

- candidate rules stop before case extraction and writing;
- failed, missing-evidence, and `needs_review` results make zero writer calls;
- a verified result invokes the writer once;
- a successful real output permits `completed`;
- a writer error retains review findings and verification, returns a stable `pdf_error`, and
  publishes no output URI;
- returned URI, page count, written field IDs, and warnings match the request and artifact.

Keep `FakePDFWriter` for explicitly synthetic demos. Its results must continue to state that no
file was created.

## Step 10: Test Matrix

Create focused tests for the following groups.

### Geometry and PDF structure

- rotation at 0, 90, 180, and 270 degrees;
- translated CropBox origin;
- mixed page sizes and orientations;
- four corners and center coordinate conversion;
- invalid page, zero-area box, off-page box, and overlapping operations;
- rejection of a field mapping that targets a reference-only map page;
- unchanged content, resources, and annotations on copied reference-only pages;
- unchanged page count and readable output.

### Values, fonts, and layout

- every supported `PDFValueRef` value;
- duplicate, unknown, missing, and ambiguous references;
- deterministic grade and percentage formatting;
- `max_characters` failure;
- measured width and height overflow;
- missing font and missing CJK glyph failure;
- successful embedded CJK glyph rendering using a test-provided redistributable font.

### Operations

- blank and occupied `fill_blank` regions;
- visibly distinct `annotate` output that preserves source text;
- `correct` removal and replacement;
- partial text intersection failure;
- grid-line preservation;
- stale-text absence after re-extraction;
- unsupported or ambiguous content failure.

### Filesystem safety

- source hash unchanged;
- same path, normalized alias, symlink, and hardlink conflict;
- unreadable source and invalid parent policy;
- existing destination policy;
- atomic replacement;
- no partial destination or temporary residue after each failure phase.

### S3 and controller behavior

- mocked download/validate/upload order;
- upload suppression on failure;
- correct `application/pdf` content type;
- literal object keys and source/output separation;
- no AWS credential requirement for local tests;
- verification gate and exact writer call count;
- exact typed result metadata and sanitized errors.

All PDF fixtures must be generated during tests under `tmp_path`; do not commit generated PDF
files.

## Step 11: Documentation and Durable Decisions

Update the relevant documentation when implementation begins:

- `docs/pdf-contract.md` for concrete rendering, font, overflow, correction, and publication
  behavior;
- `docs/architecture.md` and `docs/delivery-traceability.md` to distinguish implemented local
  writing, S3 scaffolding, mocked evidence, and any remaining work;
- `.env.example` for non-secret font and writer settings;
- `README.md` only when real behavior and its tests exist.

Add an ADR if the chosen correction algorithm, overwrite policy, or font distribution strategy
is a durable trust-boundary decision. Do not claim real cloud validation from mocked S3 tests.

## Step 12: Final Validation and Review Gate

Run, when the development dependencies are available:

```text
ruff check .
ruff format --check .
mypy src
pytest
```

Also run the submission checker over the complete intended snapshot, Git metadata, publication
text, and `feat/pdf_writer` branch name. Confirm that protected files are unchanged and that no
PDF, font, generated output, secret, cache, or prohibited attribution is included.

Open a separate PDF-writer pull request referencing the shared foundation and controller work.
Include exact commit/base information, local test evidence, mocked S3 evidence, source-hash and
atomic-failure evidence, and known limitations. Human review is required; do not merge the pull
request automatically.

## Completion Criteria

The goal is complete only when all of the following are true:

- A real local writer consumes the shared request and returns the shared typed result.
- The original template PDF remains byte-for-byte unchanged.
- Geometry is correct for mixed page sizes, CropBoxes, and rotations.
- Blank fill, labelled annotation, and genuine text correction have distinct verified behavior.
- CJK font configuration, glyph coverage, embedding, and overflow are deterministic.
- Output is reopened and validated before atomic publication.
- Failed writes never publish partial local or S3 artifacts.
- The S3 wrapper uploads only locally validated output through an injected client.
- Verification failures and `needs_review` cases make zero writer calls.
- The full static analysis and test suite passes.
- Documentation reports implemented behavior, scaffolding, mocked tests, and future work
  accurately.
