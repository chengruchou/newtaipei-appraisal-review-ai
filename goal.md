## Goal
Implement deterministic completed-form writing and storage while preserving the original PDF. Consume the shared #6 contract; do not create a competing writer interface.
## Dependencies / ownership
#6 is the prerequisite foundation PR/commit. Start from its SHA; no need to wait for A (#4) to merge. A consumes typed results and injects B's writer through its composition root.
B owns adapters/local/pdf_overlay.py, local object access, adapters/aws/storage/s3_object_store.py, adapters/aws/pdf/s3_pdf_writer.py, field-map configuration and PDF/storage tests. Shared pyproject.toml PDF extras and schema changes must be coordinated through #6; B does not independently redefine domain models or ports.
No B implementation or remote branch was present at the 2026-09-05 checkpoint. Status remains planned, not completed.
## Authoritative contract
Public imports from domain.pdf_models: PDFWriteRequest(source_uri, destination_uri, result: FactorReviewResult, field_map: PDFFieldMap), PDFWriteResult(output_uri, page_count, written_field_ids, warnings), PDFField/PDFValueRef and typed errors.
ports.pdf.PDFWriter is the only protocol: async write_pdf(request) -> PDFWriteResult.
Errors: UnsupportedDocumentURIError, SourceDestinationConflictError, PDFReadError, PDFFieldPlacementError, PDFFontError, PDFWriteError, InvalidPDFResultError. Return noncritical warnings; blocking placement/font/partial-write problems are errors.
Field IDs are opaque placement IDs with an explicit value_ref: scope, target_id, comparable_id, factor_id (omitted for total) and target_grade/comparable_grade/adjustment_percent/total_adjustment_percent. Resolve exactly one value. The initial FactorReviewResult supports one comparison context; reject mixed contexts and ambiguous/missing factor results. #8 must add validated multi-comparable identities/applicability before supporting those cases; never guess an identity from column position.
## Scope
- Deterministic local rendering with optional isolated PDF libraries (e.g. pypdf/reportlab) and a narrow object-store adapter. No model redraw.
- One-based pages, PDF bottom-left points relative to unrotated CropBox; translate rotation and CropBox offsets explicitly. Inspect each page's own dimensions.
- file:// requires an absolute local path; resolve aliases, symlinks and hardlinks to reject source/output conflicts. S3 object keys remain literal (not path-normalized); reject unsupported schemes, queries/fragments and percent-escaped S3 keys. No file access at import.
- Validate source/readability, parent policy, pages, duplicate fields, bounds, result lookup, font glyph coverage and measured overflow.
- operation=fill_blank: verify the destination is blank or fail. annotate: deliberately add a labelled review annotation without replacing the source value. correct: remove/replace the original field text in a copy while preserving borders; painting new text over old text is not a valid correction. Fail explicitly for unsupported operations.
- Chinese fonts must be explicitly configured/redistributable and embedded where needed. Never silently output missing glyphs; document deterministic overflow policy.
- Write to a temporary destination, reopen and verify readability/page count/content; atomically publish only successful output. S3 wrapper uploads only after local validation and returns the published URI and all metadata. Failed writes never publish a partial artifact.
- Object-store seam: download URI to scoped local temp, upload validated output with application/pdf; injected S3 client, mocked tests, no provisioning.
## Non-goals
Do not decide case validity, extend verifier semantics, implement OCR, discover official coordinates automatically, implement API/AgentCore, or deploy AWS. Do not modify domain/verification.py, adapters/local/audit.py or tests/unit/test_verification.py. No proprietary/system fonts or competition PDFs committed.
Failed/needs_review cases remain inspectable as findings but must not call the completed-form writer. If a review-report PDF is needed, open a separate report-artifact contract issue.
## Acceptance / tests
- [ ] Synthetic mixed-size/rotated/CropBox PDFs: exact correct page/placement, four corners and center conversion.
- [ ] Source hash unchanged, readable output with same page count, no partial destination after any failure.
- [ ] Duplicate/unknown/ambiguous value_ref, invalid page/bbox, off-page/overflow/missing CJK glyphs fail explicitly.
- [ ] fill_blank rejects occupied cells; annotate visibly distinct; correct removes stale source text and verifies re-extracted replacement without losing grid lines.
- [ ] Controller integration after verification only; invalid/needs_review zero writer calls.
- [ ] Typed result includes exact fields, URI, page count and warnings; no bare string URI.
- [ ] S3 mocked download -> local verification -> upload ordering; upload suppressed on failure, correct content type and source/output separation.
- [ ] Local tests need no AWS credentials and local PDF module has no boto3 imports.
- [ ] Ruff/format/mypy/pytest pass; protected files unchanged.
## Handoff
Use a functional work branch (feat/, fix/, test/ or docs/); follow the word-boundary attribution policy in AGENTS.md and the current gate in A PR #13.
Foundation supplies adapters.local.fake_pdf.FakePDFWriter (explicitly creates no file). Replace it with the real B writer via #4's injected adapters and run a real local PDF integration test. Until that passes, A's synthetic completed status is entry/gate evidence only. #9 separately owns authorized real S3/cloud tests.
## Review gate
Open a separate B PR referencing #6 and #4, include exact foundation SHA and validation evidence. Human review required; coding agents must not merge.

## Active delivery and review correction evidence
Foundation PR #10 was already merged at ff5e9d1d511e015dbc96aa5b380cce1def433421; its commit e24265d7c4ef0bb5d2d74d0ed94bffa2052a08ed is retained on feat/shared-pdf-contract. B can use main or that exact foundation without waiting for A.
A replacement PR #13: feat/member-a-entrypoint, head a7b4553eba9f22aa5a3a3828320f9daf997713ea, base main ff5e9d1d511e015dbc96aa5b380cce1def433421. It preserves original #11 head 2e3d265fbc3e64bd843ebb608deb37c49da5ffab.
Runtime replacement PR #14: test/runtime-smoke, head 50387b90fa306998baf3b71d95ff58164b6f7cce, base A a7b4553eba9f22aa5a3a3828320f9daf997713ea. It preserves original #12 head c91230a3974006acced6f754377bf49dadac9e90 and contains all A fixes through normal merges.
API review r3939585201 is addressed by 5fea6df91fad79be77c285eb46d1cd0816b15fd7; submission review r3939585204 by 59f62d4388fd35e4765440a475f63017e789000c. A's follow-up a7b4553eba9f22aa5a3a3828320f9daf997713ea propagates the coverage config to subprocesses without disabling coverage/assertions.
Evidence: A 133 tests (76 existing + 8 API error/schema + 49 actual submission CLI); Runtime adds 8 for 141 combined. Ruff, format, mypy, localhost HTTP smoke, CloudFormation lint, protected-file equality and full attribution/branch checks passed. Latest CI: #13 run 33948739833 and #14 run 33948794394 both successful.
Authoritative imports remain appraisal_review.domain.pdf_models and appraisal_review.ports.pdf. Inject B's real implementation as ReviewAdapters.pdf_writer via appraisal_review.application.bootstrap; FakePDFWriter creates no file.
Review order: merged #10, then #13, then #14. Prior COMMENT reviews and original discussions are preserved; they do not approve replacement heads. This revision performs no PR merge, force-push, history rewrite or AWS deployment. B and full review semantics remain open. See docs/pr-migration.md and docs/submission-checks.md.
