# Local OCR review for exact PDF restoration

The existing automatic restoration path still refuses low-confidence OCR and
invalid placeholder inventory. A configured `LocalRestoreCoordinator` may
supply `ocr_identity`, a trusted callback that revalidates the actual pinned
local engine and assets and returns their digest. Without this callback the
bridge offers no visual-review authority. The combined local rehearsal uses
`TesseractPrivacyOutputOCR.identity`; no model is called remotely.

The bridge exposes these session-authenticated loopback routes:

| Route | Result |
| --- | --- |
| `POST /local-privacy/restore/{result_id}` | Existing success, or HTTP 409 with `code=local_privacy_review_required` and `review_id` |
| `GET /local-privacy/restore-reviews/{review_id}` | Current published/restored stage, digest, page hashes, all raw measurements, required review items and receipts |
| `GET /local-privacy/restore-reviews/{review_id}/pages/{page}` | Actual rendered PNG; records that this exact page was retrieved |
| `POST /local-privacy/restore-reviews/{review_id}/items/{item_id}/confirm` | Confirms one reading against `review_digest` and `page_image_sha256`; returns the current view |
| `GET /local-privacy/restore-reviews/{review_id}/stages/published` | Retained published-stage measurements and review state |
| `POST /local-privacy/restore-reviews/{review_id}/restart` | Empty command archives unfinished review evidence, revokes old confirmations and returns `state=restarted` with the same `result_id` |
| `GET /local-privacy/restored/{local_id}` | Final authorized PDF after both stage requirements have passed |

A bbox is `[x0, y0, x1, y1]` in PDF bottom-left coordinates. Page dimensions use
the same point space. `observations` preserve the complete original text,
confidence and geometry. `items` list only the required individual reviews.
A placeholder item references all contained observation IDs and the exact token
that the reviewer must visually transcribe. A missing token cell remains an
explicit item with no invented OCR text or score. Normal observation items
require the actual visible reading; confidence cannot be edited.

Confirmation requires a viewed page, exact current-stage hashes and a nonempty
reading. No client-supplied identity, generic approval, uploaded receipt or
bulk-approval command is accepted. Confirmed readings are immutable. A stage
receipt is created only after all required readings are confirmed and the
resulting token inventory validates. Receipts bind principal, full session scope,
input, engine, all measurements, all page hashes, exact readings and expiry.
The `business_authority` field remains `unchanged`.

The original restore command resumes after input review. It may return another
409 for the final candidate stage; the candidate PDF remains private in memory.
After final-stage review, success also returns `ocr_review_receipts` alongside
the existing local ID and manifest. Repeating this command reuses the same final
file. Permission, publication, mapping, engine, original/download bytes, receipt
or evidence changes block subsequent access. In-memory receipts cannot survive
process restart or be imported as authorization.

An expired or incorrectly transcribed unfinished review requires the explicit
restart command followed by a separate restore command. Restart revalidates
current source, publication, mapping, download and permissions, including when
the old receipt has expired. Every old stage image, raw observation, reading and
receipt is archived privately before invalidation. Fresh identifiers and all
new confirmations are required. A completed review cannot restart.

Each session retains at most two reviews of 64 MiB each, with at most 10,000
observations per stage. The workspace retains at most four private restart
archives of 64 MiB each. Full capacity refuses new work; evidence is never
silently discarded. A lease lasts at most 15 minutes, bounded by mapping expiry,
and is checked again after slow trust checks as well as before them.

The local schema is generated with:

```bash
python scripts/export_privacy_refill_review_contracts.py
```

This schema is separate from the central/cloud OpenAPI document. The review
route is a local display transformation; it does not approve real materials,
competition upload, model quality, business policy or deployment. Any automated
synthetic UI confirmations must be identified as such in validation reports.
See [ADR 0046](adr/0046-local-ocr-visual-review.md) for the trust boundary.

## Bounded local diagnostics

Authenticated restoration, staged-review and final-download requests generate a
server-owned UUID correlation in `X-Privacy-Request-Id`. Observed failures also
carry `X-Privacy-Failure-Stage` and `X-Privacy-Failure-Code`; these headers are
exposed only to the configured origin. Existing JSON response bodies and HTTP
statuses remain unchanged. A review-required 409 still enters the explicit
review flow; it is never an authorization receipt or successful OCR result.
Unrecognized or absent diagnostic headers require a generic client message.
Unauthenticated, invalid-origin and malformed boundary requests receive no
detailed stage/code record. Caller-provided correlations are never reused.

The finite stage vocabulary is `request`, `source`, `mapping`, `publication`,
`authority`, `plan`, `render`, `ocr`, `ocr_validation`, `automatic_restore`,
`review`, `review_lifetime`, `engine`, `review_authority`, `review_evidence`,
`candidate_write`, `final_write`, `final_read` and `file_read`. A stage identifies
the operation which observed a failure, not an inferred historical root cause.

| Diagnostic code | Meaning |
| --- | --- |
| `request_rejected` | Existing bridge guard refused the request |
| `validation_failed` | A validation callback raised `ValueError`; its text is not exported |
| `authority_denied` | A current authority predicate returned false |
| `review_expired`, `review_revoked` | The review's actual lifetime or revocation guard failed |
| `engine_changed`, `evidence_changed` | A pinned engine or retained review-evidence comparison failed |
| `local_io_failed`, `operation_timed_out`, `operation_failed` | Local I/O, timeout or otherwise unclassified operation failure |
| Existing `MappingError` values | Exact typed mapping refusal, including locked, expired, corrupt/authentication and storage failures |
| Existing `PrivacyErrorCode` values | Exact typed privacy refusal, including uncertain automatic OCR |

The optional `PrivacyBridgeConfig.diagnostic` callback receives one bounded
terminal record per authenticated operation, including success. Records contain
the server correlation, operation, observed HTTP response-start status, overall
elapsed milliseconds, first observed failure, and aggregate phase call counts,
failed-call counts and elapsed milliseconds. At most 19 phase entries are
retained; counts saturate at 65,535 and durations at 86,400,000 milliseconds.
The first inner fault survives publishers that catch it and return false.
Generic outer refusals cannot erase the recorded mapping or lifetime failure.

OCR refusals retain the existing allowlisted `stage`, `reason`, page number and
observation/low-confidence counts. The full raw measurements remain unchanged
in their existing private evidence store; records do not copy them. No exception
text, traceback, credential, filename, path, document/result identifier, mapping
contents, engine digest, transcription or PDF bytes enters these diagnostics.
Malformed adapter exception attributes fall back to a finite generic code.
Diagnostic callback exceptions cannot alter access, response bytes or status.

Phase durations are inclusive and may nest, so they must not be summed as an
operation's total. Overall time begins after authentication/body validation and
includes subsequent lock waiting and response handoff. A response-start status
is not proof that the client received or reopened the PDF. Client transport and
UI/download timings require separate bounded observations. Cancellation remains
cancellation, retains the session lock until its worker finishes, and cannot
become a successful download through a diagnostic record.

Final reads retain both complete current-authority passes around the actual
file read, including current publication, selected mapping, plan, pinned engine,
original/download bytes, retained measurements/images, receipts and expiry.
Review lifetime is checked before and after slow callbacks. No check result is
cached by diagnostics, and no lease, OCR threshold or observation is changed.

## Regression evidence and limits

The focused tests use real local PDF bytes and isolated writing with explicitly
synthetic OCR measurements and confirmations. `test_privacy_restore_diagnostics`
exercises restore/GET fault classification, swallowed mapping faults, completed
download reopening, slow stream reads followed by wall/monotonic expiry or
revocation, I/O refusal, boundary confidentiality and failing diagnostic sinks.
`test_privacy_diagnostic_isolation` exercises concurrent request isolation,
malformed fault attributes, bounded aggregation and unchanged measurement
evidence. Existing refill, review lifetime, resolver and HTTP tests remain the
authority and source/download immutability regressions.

Here, "original inputs" means the original failing synthetic rehearsal PDFs,
authored by `write_privacy_sources` in `scripts/privacy_raster_fixture.py` and
created by `create_privacy_rehearsal` in `scripts/privacy_browser_rehearsal.py`.
The paired inputs are a synthetic criteria page and a two-page synthetic form
with eight appraisal output slots. They visibly identify themselves as synthetic
sources. Fresh runs preserve the exact earlier failing inputs' hashes; they do
not substitute a simpler case. Private provenance records retain the exact old
and new source paths, generator hashes and input hashes. These inputs are not
the user's official competition PDF attachments or real valuation forms. Real
local OCR means execution of the pinned OCR engine, not real-case acceptance.

Historical automatic restoration of this synthetic case was correctly refused
for uncertain local OCR, including low-confidence placeholder observations. A later
download attempt exceeded its browser wait; a subsequent 409 lacks the phase
and monotonic evidence needed to determine its cause. A separate fresh local
rehearsal subsequently completed an actual download and PDF reopen, with a
measured final GET of approximately 14.3 seconds and repeated current-authority
checks. That success does not explain the historical 409 or establish a general
latency bound. The new diagnostics provide prospective evidence only.

These changes do not complete the broader operator installer, operating-system
key provisioning, real-material admission, retention/recovery/deletion policy,
multi-case corpus acceptance or cloud deployment work in issue 22. Real OCR
browser rehearsals and independent visual inspection remain distinct from the
focused synthetic regression suite. See
[ADR 0049](adr/0049-local-restoration-diagnostics.md).
