# Local privacy review SDK: Issue 25 handoff

Phase 3 implements local list/add/edit/remove/page-review/confirm operations and
ephemeral approval validation. Linux is the runtime target. Browser frontend,
HTTP/session transport and durable receipts are unimplemented. The later
[export gate](local-privacy-export.md) implements exact-output handoff through
trusted confirmation/sink ports; production UI and transfer integration remain pending.
The SDK and synthetic harness do not establish frontend completion.

## Trusted composition

Create one `LocalPrivacyReviewService` per trusted local review session with:

- `LocalSnapshotStore` and `LocalPrivacyScanner`, including explicitly configured
  approved OCR assets when image pages require them;
- `LocalPrivacyPreviews(store)` for isolated rendering of owned originals;
- `LinuxTerminalConfirmation(owner_uid=...)`, with UID configured by trusted
  operator startup, never a request parameter;
- the default trusted UTC clock, never consumer timestamps.

Do not register the human adapter as a model tool or give untrusted model code
the owning account, controlling terminal or Python composition access. The local
consumer and OS account are trusted; this is not a sandbox against same-account
code. The adapter creates no files and does not initialize the business reviewer
store. On Windows it rejects issuance; actual interactive acceptance needs Linux.

Capture a source with `store.capture(relative_path, case_id=...)`, then call
`await service.rescan(snapshot, policy_digest=...)`. The source path uses the
existing confined acquisition adapter. A source/policy change uses rescan and
invalidates approval before processing starts. OCR failures remain blocked even
if the reviewer adds regions and attests every page.

## Consumer operations

All DTOs are local-only. JSON parsing uses `model_validate_json`; strict Python
constructors require native UUID, enum and tuple values. Catch parsing errors
locally and display a fixed `PrivacyProblem`, never raw Pydantic input excerpts.
Do not log or publicly serialize views, scans, previews, source hashes or local
review digests. Service validation failures use fixed faults.

| Operation | Input and behavior |
| --- | --- |
| `list()` | Returns `PrivacyReviewView` with command, original scan, current-scan history and state. Contains raw text. |
| `preview(ReviewPrivacyPage)` | Returns PNG bytes/dimensions of the owned original page and records preview delivery; no path/URL input. |
| `add(AddPrivacyRegion)` | Adds a manual crop with unknown confidence and service-issued candidate/crop/entity IDs. |
| `edit(EditPrivacyRegion)` | Changes geometry/category/grouping. Original detection remains in scan/events; the selection becomes manual crop evidence. Supply an existing entity ID to retain/join a group, or null to issue a new group. |
| `remove(RemovePrivacyRegion)` | Dismisses with a nonblank reason, retaining the selection and evidence. Edit restores redaction. |
| `crop(PrivacyCropRequest)` | Resolves a current crop ID to owned source/region. Fetch that page preview and apply the region locally; this is a reference, not stored raster bytes. |
| `review_page(ReviewPrivacyPage)` | Records a page attestation after preview delivery. Advances revision and revokes approval. |
| `confirm(ConfirmPrivacyReview)` | Requests independent human interaction with exact digest/version. No actor/approval/state input. Returns a local receipt only after all gates pass. |
| `permits(approval, command, now=...)` | Approval authority for the pure admission guard; rechecks live issuance, source, command and expiry. |
| `close()` | Revokes and releases review references. Release/forget owned sources separately. |

Every request uses the latest case ID, snapshot ID and `selection_revision` as
`revision`. Refresh after each mutation; stale/cross-case requests fail. Compute
the confirm digest with `privacy_review_digest(view.command)` after the final
page attestation. The local digest binds evidence and decisions; it is not authority.

Coordinates are bottom-left points relative to the unrotated original CropBox.
For raster width W/height H and source page width PW/height PH, region
`(x0,y0,x1,y1)` maps to top-left pixel bounds
`(x0*W/PW, (PH-y1)*H/PH, x1*W/PW, (PH-y0)*H/PH)`. Use actual rounded raster
dimensions and do not apply the original page rotation again.

The consumer must show every original page and all `manual_categories`, including
signatures, stamps and faces. Display original observations/confidence separately
from manual proposals, plus explicit grouping and every dismissal reason.
Add/edit/remove clear all page marks and preview markers: fetch previews and
review all pages again. A page attestation preserves other page marks. The final
terminal prompt attests to this exact completed view.

States are limited to `blocked`, `awaiting_confirmation` and `confirmed`.
Receipts expire after 15 minutes or immediately on relevant changes/close.
Restart does not restore authority. `confirmed` still needs the later sanitizer
and independent verifier before export. No request can set verified/exportable
or business completed state.

## Schemas and harness

`schemas/local-privacy-review-v1.json` collects definitions under
`#/$defs/ModelName`; DTOs extend the existing `local-privacy-v1` envelope. Public
privacy-v1 and cloud service-v1 schemas are unchanged. Independent examples in
`examples/privacy-review-v1/` are synthetic parsing fixtures, not a replayable
transaction or valid approval. Regenerate and test using local temporary paths:

```bash
python scripts/export_privacy_review_contracts.py
python scripts/privacy_review_harness.py --output artifacts/privacy-review-run-1
python -m pytest tests/unit/test_privacy_review.py --basetemp=artifacts/privacy-review-tests
```

The harness requires a fresh repository artifacts subdirectory. It generates a
native PDF, captures/scans/renders it, records a page attestation, then verifies
confirmation denial without a human. Aggregate JSON contains no source text/hash,
preview data or credential. It does not simulate successful human authorization
or test actual OCR accuracy.

If #25 later needs HTTP, first agree on loopback binding, allowed Origin/Host,
per-session authentication, CSRF, DNS rebinding resistance and request limits.
Do not mount these methods in AWS FastAPI or assume localhost protects originals.
No listener is added by Phase 3.

## Remaining acceptance

Terminal tests use explicit OS/terminal doubles. A real Linux controlling-terminal
interaction in an isolated trusted operator session remains required. Approved
OCR assets and actual recognition, Linux security checks and later sanitization,
encryption and export acceptance are also pending. See
[ADR 0016](adr/0016-local-privacy-human-review.md).

## Development validation, 2026-09-10

Uncommitted local work on `feat/local-privacy-pipeline`, based on
`8591bddc76584ad630774f214c7452c397c937d5`. Host: Windows / Python 3.12.3.

| Check | Observed result |
| --- | --- |
| Phase 3 focused tests | 49 passed; synthetic human/OS/terminal doubles |
| Generated-PDF SDK harness | Passed: actual capture/scan/render, original unchanged, confirmation denied without human |
| Full pytest with repository coverage settings | 752 passed, 40 failed, 43 errors, 1 skipped, 2 warnings; 79% aggregate coverage |
| Comparison with Phase 2 | Exact same 83 failed/error node IDs; no new or removed failure/error nodes |
| Ruff lint and format | Passed |
| `mypy src --platform linux` | Passed, 88 source files; cross-target analysis only |
| Native Windows mypy | Same three existing POSIX API errors in the business approval adapter |
| Local/mocked cloud tests | 8 passed; no live AWS |
| Submission/branch gate | Passed; zero outgoing commits |
| Actual Linux terminal and OCR acceptance | Not performed; runtime/assets remain prerequisites |

The full run used `--basetemp=artifacts/issue-22-p3/full`,
`-o cache_dir=artifacts/issue-22-p3/full-cache` and
`--cov=appraisal_review --cov-config=pyproject.toml --cov-report=term-missing`.
Ignored logs, exact failure comparison and the aggregate harness report are under
`artifacts/issue-22-p3/`; temporary and coverage paths were confined there too.
The existing failures remain the Windows POSIX/path/named-pipe groups described
in the scan runbook. The Linux acquisition test remains skipped on Windows.
No original competition PDFs were modified or uploaded. No dependency/system
installation, commit, push or remote mutation occurred. CloudFormation lint
remains unavailable from Phase 0; no infrastructure was changed in Phase 3.
