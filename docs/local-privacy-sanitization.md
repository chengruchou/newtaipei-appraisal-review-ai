# Local sanitization and verified bundles

Phase 4 implements a raster redactor/tokenizer, image-only PDF builder and
independent structural/pixel/OCR verification path. Linux is the runtime target.
Actual local OCR assets and Linux execution remain prerequisites for runtime
acceptance. No cloud upload, encryption, refill or case completion is added.

## Local composition

Create `IsolatedPrivacyRasterProcessor(work_directory, limits)` using an existing
trusted absolute local work directory and `ScanLimits`. It requires the existing
PyMuPDF and pypdf document dependencies. Default resolution is 144 DPI; page,
byte, pixel, aggregate memory, CPU, timeout and output limits reject oversized work.
No dependency or model download occurs.

Create `TesseractPrivacyOutputOCR(TesseractOCR(config, work_directory), dpi=...)`
using the same DPI and preapproved executable/model hashes. Both `eng` and
`chi_tra` assets are required. Use that adapter and the processor to construct
`LocalSanitizedVerifier`. Inject the owned source store, the live Phase 3 review
service as authority, the processor and verifier into `LocalSanitizedBundleBuilder`.

After actual human confirmation, call `builder.build(view.command, approval)`.
The builder rechecks the same review at the end. It returns immutable PDF bytes
and a public `PrivacyManifest` only after all checks pass. Serialize the manifest
with `public_manifest_json`; name the PDF `sanitized.pdf`. A carrier constructed
by a caller or returned directly by `processor.build` is merely an unverified
draft. The pure admission guard uses the verifier's live owned record, not a
caller-supplied verification flag. The future exporter must recheck revocation
and atomically send the exact verified bytes; this phase does not upload anything.

## Coordinates and preservation

Approved rectangles are white pixels in the final embedded image, not removable
overlays. The canonical output has no source objects or text layer. The original
file and source snapshot remain unchanged. Independent row-byte comparisons
require every pixel outside the approved mask union to match the original render,
including fee numbers, totals and table borders. Source text within a deliberately
approved rectangle is removed even if that rectangle also contains other values;
the human consumer must choose correct regions.

Full random entity tokens are placed in a new 220-point right column, outside
the original raster. The token font is 9 points with 20-point rows. Too many
occurrences or insufficient page height fails, without overlapping the original
or reducing token readability. Repeated entity IDs still receive distinct random
occurrence IDs and individual placements. The manifest records enlarged sanitized
page dimensions and token boxes, not original source evidence or coordinates.

For local mapping, pair ordered redaction selections with ordered manifest
occurrences from the same confirmed command/build. Source rectangles remain in
that local command; public occurrence regions point to the token column. Do not
copy original excerpts into cloud citations. Future refill must explicitly map
source regions to approved destinations rather than assume old and new PDF
coordinates are identical. Fractional page extents are rounded to the output
pixel grid; masking uses actual raster dimensions and outward rounding.

## Verification and limits

The verifier independently parses the new PDF, demands byte-exact canonical
reserialization, checks decoded pixels and re-renders it for OCR. The profile
rejects all extra metadata, attachments, forms, hidden layers, JavaScript,
annotations, unreachable objects, alternate image streams, incremental history
and trailing payloads. A substituted PDF is rejected even if a caller updates its
manifest digest. Pixel or occurrence tampering also fails.

Every page requires valid OCR observations at confidence 0.85 or greater. Missing
or empty OCR, unreadable full placeholders, out-of-bounds observations and known
source canaries fail verification. The output OCR budget is 30 seconds total,
10,000 observations and 100,000 characters per page. No low-confidence result is
promoted to success. OCR remains imperfect; correct whole-page human review is
required for unknown text, images, signatures and faces. Pixel proof applies to
the selected regions, not to sensitive material that was never selected.

The isolated workers use shared library dependencies but separate build/verify
requests. The verifier's byte-level mask algorithm is independent of the builder's
pixel mutation. The canonical PDF encoder and MuPDF rendering remain shared
dependencies; this is not a formal proof against common library defects.
See [ADR 0017](adr/0017-raster-privacy-bundles.md).

## Synthetic acceptance harness

```bash
python scripts/privacy_sanitize_smoke.py --output artifacts/privacy-sanitize-run-1
python scripts/privacy_sanitize_smoke.py --output artifacts/privacy-sanitize-run-2 --ocr-config artifacts/approved-ocr.json
python -m pytest tests/unit/test_privacy_bundle.py --basetemp=artifacts/privacy-bundle-tests
```

Create the pytest basetemp parent first and keep TEMP/TMP/cache/coverage paths
inside ignored repository artifacts. The smoke output directory must be fresh
and under `artifacts/`. Only generated synthetic input is accepted by this
harness. It writes a clearly named synthetic draft and PNG for local visual QA,
plus aggregate counts/status without source text/hash or credentials.

Without OCR configuration, the smoke reports structure/pixels verified, OCR
unverified and no bundle emitted; its CLI uses nonzero exit status. With approved
assets it also exercises actual OCR, but still never issues human approval or
emits an authorized bundle. Success with OCR doubles in tests is not real OCR
acceptance. The harness has no network fallback or system installation step.

## Development validation, 2026-09-10

Local uncommitted changes on `feat/local-privacy-pipeline`, based on
`8591bddc76584ad630774f214c7452c397c937d5`. Host: Windows / Python 3.12.3.

| Check | Observed result |
| --- | --- |
| Phase 4 focused tests | 24 passed; real PDF subprocesses/pixel comparisons, explicit OCR/authority doubles |
| Full pytest with existing coverage settings | 776 passed, 40 failed, 43 errors, 1 skipped, 2 warnings; 78% aggregate coverage |
| Failure comparison against Phase 3 | Exact same 83 failed/error node IDs; no additions or removals |
| Ruff lint and format | Passed |
| `mypy src --platform linux` | Passed; 92 source files; cross-target analysis only |
| Native Windows mypy | Same three existing POSIX API errors in the business approval adapter |
| Local/mocked cloud tests | 8 passed; no live AWS |
| Synthetic smoke without OCR assets | Structure/pixels verified, original unchanged, OCR unverified, no bundle emitted |
| Synthetic visual QA | Inspected rendered PNG: canary removed, full token in right column, rate/total/table retained |
| Submission/branch checks | Passed; zero outgoing commits |
| Actual Linux / actual OCR / network denial | Not performed; runtime, approved assets and later acceptance remain prerequisites |

The full run used `--basetemp=artifacts/issue-22-p4/full`,
`-o cache_dir=artifacts/issue-22-p4/full-cache` and
`--cov=appraisal_review --cov-config=pyproject.toml --cov-report=term-missing`.
PDF worker processes run with `-I`; their executed lines are not collected by
the parent coverage run. The aggregate decrease from 79% does not mean the new
worker was replaced with a mock. After adding a pre-render pixel-limit check,
the focused Phase 4 suite was rerun separately on the final code.

Ignored full logs, failure comparison, synthetic draft/preview and aggregate smoke
report remain in `artifacts/issue-22-p4/`. No competition originals were modified,
used as fixtures or uploaded. No dependency/system installation, keystore change,
commit, push or remote mutation occurred. CloudFormation lint remains unavailable
from Phase 0; no infrastructure was changed in Phase 4.
