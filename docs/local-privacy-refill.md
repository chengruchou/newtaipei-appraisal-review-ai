# Local privacy refill

Phase 6 implements local validation, isolated deterministic PDF refill and a
fresh-file sink. Production publisher authentication, output-plan approval
integration and actual OCR acceptance remain pending. Linux is the runtime
baseline; Windows test execution does not establish Linux acceptance.

## Authority and consumer handoff

`LocalPrivacyRefillExecutor` receives trusted mapping, source, publisher,
approval, processor and OCR ports, plus an optional local file sink. The mapping
reader must authenticate and enforce expiry as described in
[encrypted mapping](local-privacy-mapping.md). `PrivacyRefillPublisher.current`
must obtain the authenticated current artifact; `permits` must check that it is
still current. Neither a caller-supplied digest nor a structurally valid
`PublishedRefillDescriptor` grants authority. `RehydrationAuthority` separately
approves the exact plan. These production publisher/approval adapters are not
implemented by this phase.

The validator binds case/document/mapping IDs, run/revision, template digest,
base sanitized digest, artifact digest and actual PDF bytes. It checks mapping
expiry, approval and publication again after processing, and rereads the owned
source and mapping. The result does not advance business verification or approval.

The proposed #26/#28 handoff is in
[synthetic fixtures](../examples/privacy-refill-v1/README.md) and
`schemas/local-privacy-refill-v1.json`. It supplements the local contracts;
public privacy-v1 and service-v1 are unchanged. No HTTP endpoint, uploader or
remote publisher integration is added.

## Occurrences and PDF behavior

Every mapped occurrence must appear exactly once in the published target list
and approved plan. Output field IDs are unique, regions do not overlap and there
are at most 1,000 targets. Restores use the exact published destination region.
Repeated entities remain separate occurrences. An omitted field requires an
explicit `omit` operation, whether its placeholder is still present or has been
removed by the approved layout. Unknown, duplicate, missing or misplaced tokens
fail. No inferred destination or arbitrary document-wide replacement is allowed.

Before processing, OCR inventories all pages at confidence of at least 0.85.
A complete placeholder must be contained in one observation inside its field;
split words are not guessed or reassembled. Malformed reserved `PT_` text fails.
Native PDF words receive an independent inventory check. After processing, all
pages undergo OCR again and no placeholder may remain. Empty or low-confidence
OCR output fails. The existing configured OCR adapter can be injected; this
phase's tests explicitly use synthetic OCR observations.

The isolated worker rasterizes the downloaded artifact, clears only present
approved target regions and builds a clean PDF. It restores text from the local
mapping or renders exactly the approved original crop. Published pages currently
require zero rotation and a CropBox starting at zero. Original crop/rotation
metadata must match the owned source snapshot before crop rendering.

Text restoration requires an explicitly pinned SHA-256 digest of the embedded
bundled CJK font. Tests use PyMuPDF 1.28.2's Droid Sans Fallback Regular; this
test configuration is not production asset approval. No system font fallback or
download occurs. Missing glyphs, missing pin and textbox overflow fail; the
writer does not shrink or truncate values. Raw mapping text remains unchanged.

The worker reopens the result, verifies restored text and embedded crop pixels,
rejects attachments/widgets/annotations and compares pixels outside the approved
fields to the cloud artifact at the configured DPI (144 by default). Cloud
corrections remain visually intact at that resolution. Raster rebuilding removes
selectable cloud text and does not promise identical antialiasing at other DPI.
Signature crops are visual only, never a digital signature.

## Local output and operation

`LocalRefillFileSink` requires an existing absolute directory inside the configured
workspace and rejects symlink paths. It reuses `LocalObjectAccess.staged_write`
with overwrite disabled, checks the owned temporary file identity before writing,
flushes and verifies its digest, and checks the original file's identity/digest
before publication. A changed original or existing `final-local.pdf` fails.
The original and downloaded cloud bytes remain unchanged.

The returned `FinalLocalManifest` binds exact final bytes and restored/omitted
field IDs. Its state is `refilled_local`, business authority is `unchanged`, and
signature effect is `visual_only_no_digital_signature`. The file sink writes the
PDF; the manifest is returned to the local caller, not automatically persisted.
The final PDF, plan, mapping handle and final manifest remain local and must not
be uploaded. Secure persistent source/key provisioning remains separate work.

```bash
python scripts/export_privacy_refill_contracts.py
python -m pytest tests/unit/test_privacy_refill.py --basetemp=artifacts/privacy-refill-tests
```

Keep temporary, cache and coverage paths under ignored repository artifacts.
Actual publisher credentials, real cases and private fonts are not test fixtures.

## Development validation, 2026-09-10

Local uncommitted work on `feat/local-privacy-pipeline`, based on
`8591bddc76584ad630774f214c7452c397c937d5`, on Windows / Python 3.12.3.

| Check | Observed result |
| --- | --- |
| Phase 6 focused tests | 30 passed; real isolated PDF worker and local file publication, explicit synthetic publisher/mapping/approval/OCR fixtures |
| Synthetic PDF QA | Reopened final PDF, extracted Chinese text and inspected rendered Chinese; protected cloud rate and total passed pixel comparison at 144 DPI |
| Full pytest with existing coverage settings | 838 passed, 40 failed, 43 errors, 9 skipped, 2 warnings; 75% aggregate coverage |
| Failure comparison with Phase 5 | Exact same 83 failed/error node IDs; no additions or removals |
| Ruff lint and formatting | Passed |
| Linux-target mypy | Passed, 101 source files; static analysis only |
| Native Windows mypy | Same three pre-existing POSIX API errors in the business approval adapter |
| Local/mocked cloud tests | 8 passed; no live AWS |
| Submission, branch and whitespace checks | Passed; zero outgoing commits, configured Git identity |
| Actual Linux / production publisher / real OCR | Pending; fixture tests do not establish these capabilities |

Ignored logs, coverage and synthetic visual artifacts are under
`artifacts/issue-22-p6/`. Aggregate coverage includes isolated worker lines and
unexecuted Linux storage code; no exclusions were introduced. No production
documents were modified or uploaded, and no tools or fonts were installed in
this phase. No commit, push or remote mutation occurred.

Phase 7 remains the export gate and network-denial/leak regression work. Existing
upload paths are not approved for sensitive cases by this local writer.
