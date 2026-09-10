# Bounded extraction execution

Status: Issue 21 Phase 3 local implementation. See
[ADR 0023](adr/0023-bounded-extraction-execution.md) and the
[authorized preflight runbook](extraction-preflight.md).

## Results and telemetry

`AuthorizedExtractionService` and `BedrockSnapshotBackend.extract` now return the
Phase 1 `PageOutcome`. A provider success creates a candidate, never approval or
verified/completed review. Failures during an admitted page retain a page-bound
handoff and any recorded attempts. Authorization/configuration rejection before
page admission still raises a static `ExtractionBoundaryError`; it is not a
fabricated provider attempt. Existing DTO field/version rules are unchanged.

`ProviderTelemetry` includes exact configuration, prompt version/digest, ordered
attempts and total page elapsed time. Per-attempt timing includes provider wait
and response validation. Backoff timing is separate. Page total additionally
includes rendering, local validation and preflight; resolver latency is outside
this backend interval. Prompt hashes use exact UTF-8 system-prompt bytes.

Read input/output tokens independently before validating model output. Unknown
counts remain null; do not sum null as zero in later reports. A refusal or malformed
proposal can still have known billable usage. The private legacy success adapter
cannot represent unknown usage and raises `usage_unavailable`; the public outcome
keeps the candidate with null usage. A timeout records unknown completion and
does not pretend the provider was cancelled or unbilled.

An optional `on_attempt(request, attempt)` trusted synchronous observer receives
safe metadata as soon as an attempt ends, before sleeping for a retry. Its backoff
value is zero at that moment; the final outcome contains measured backoff. This
observer must be fast and nonblocking. It receives no prompt, image, source JSON,
raw exception or proposal. Cancellation emits an unknown-completion observation
when configured, preserves reservations and propagates cancellation. Observer
errors stop further calls; they do not cause a second paid request. Durable
publication, delivery retries and human-task persistence are not implemented.

## Budget scope and retries

Create one backend per run and reuse it for every page. Its ledger binds the full
`RunReference`, including revision/attempt/session fields. Repeated page requests
consume the page budget. Counters and model concurrency use a thread lock; a full
model slot returns budget exhaustion rather than building an unbounded queue.
Each permitted attempt reserves its full output-token maximum. Known usage
reconciles that reservation; unavailable usage keeps it reserved. Input tokens
are recorded when available; the byte/character caps are not precise token caps.

Page count, total submitted model attempts, per-page attempts, output reservations
and model concurrency are run-wide limits. Snapshot bytes, image dimensions/pixels,
request characters and response JSON characters have separate per-input limits.
The global deadline is the smaller of `total_timeout_seconds` and
`ExecutionBudget.max_elapsed_seconds`, starting at first page admission. It covers
rendering and preflight as well as model work/backoff. The SDK's connect/read
timeouts share the per-call timeout and SDK retries remain disabled.

| Condition | Automatic retry / result |
| --- | --- |
| Throttling, service unavailable, internal service error, model not ready | Retry only within attempts/calls/tokens/deadline, with capped jitter |
| Wrong identity, missing credentials, invalid model/configuration | Safe access/configuration failure; no retry |
| SDK/caller timeout or connection loss with uncertain completion | Stop the run ledger; retain active worker slot until actual finish; no replacement call |
| Refusal, truncation, invalid JSON/citations, unsupported image | Explicit failure; no fallback, confidence promotion or retry |
| Exhausted slots/pages/calls/tokens/time | Budget failure; no additional model request |

The budget is local admission/accounting, not hard AWS billing control. Metadata
calls do not transmit document contents and are not model invocations; preflight
is serialized on the backend. A hung native renderer or SDK thread may continue
after its caller's deadline. The ledger prevents subsequent model calls, but
does not claim server-side cancellation or an exact process shutdown duration.
Creating another backend would create another local budget; trusted composition
must not use that as a retry mechanism. Durable cross-process enforcement belongs
to the job/runtime integration.

## Images and reproducibility

The supported raster subset is non-interlaced, 8-bit grayscale/gray-alpha/RGB/RGBA
PNG with valid CRCs, exact bounded decompressed scanlines and filters. Optional
valid density metadata is allowed before image data. Other metadata/encodings,
truncation, trailing content, invalid filters and decompression bombs fail before
AWS preflight. This is a renderer contract, not a general-purpose PNG repair tool.
The supported stream structure follows the [W3C PNG specification](https://www.w3.org/TR/png-3/);
bounded inflation uses Python's [zlib decompressor output limit](https://docs.python.org/3/library/zlib.html#zlib.Decompress.decompress).

`BedrockAccessPolicy` now requires reviewed image byte/width/height/pixel ceilings
alongside its model/API evidence. `ExtractionConfig` limits cannot exceed them.
The implementation hard ceilings remain 3,750,000 bytes, 8,000 per dimension and
16,000,000 pixels; a selected model can require smaller values. No current model
is inferred from those ceilings. Evaluation must bind actual model capability and
renderer/library versions independently.

`snapshot_renderer.RENDERING_CONFIGURATION` pins 1.5 scale, rotation zero, opaque
PNG and dimension/pixel ceilings; `RENDERING_CONFIGURATION_DIGEST` hashes canonical
sorted compact JSON. Pin that digest and `PROMPT_DIGEST` in the evaluation
manifest. Existing source coordinates, confidence demotion and exact authority
remain unchanged. Real Chinese extraction quality and live integration are still
unmeasured; mocks and synthetic images prove the listed code paths only.

## Phase 3 verification (2026-09-10)

- Execution/preflight/extractor/contract suites: 175 passed, including 49 new
  execution tests. Evidence covers late SDK completion, cancellation observation,
  retained slots and reservations, concurrency, cumulative budgets, jitter/deadline
  accounting, safe errors, refusal/malformed-output usage, nullable counts,
  prompt identity, bounded raster decoding and selected image ceilings.
- Other CLI/reviewer/source-purpose/service/integration regressions: 55 passed,
  20 failures matching the prior Windows/sandbox baseline. A separate rerun of
  the four process-dependent synthetic PDF tests outside the sandbox yielded
  1 passed and 3 failures at the existing POSIX reviewer gate. Actual PDF rendering
  and extraction reached confirmation; no new PNG failure was hidden by the sandbox.
- Ruff and format checks passed. Linux-target mypy passed for 85 source files.
  Host mypy retains the three existing POSIX API errors in local approval.
- Full submission checks passed with configured identity, functional branch and
  zero outgoing commits. Existing citation, confidence and service-schema
  regressions remain intact.

Ignored `artifacts/issue-21-p3/validation.json` contains commands and actual exit
codes with separate logs; `process-recheck.json` records the outside-sandbox
diagnostic. AWS environment variables were removed, metadata lookup disabled and
SDK config/credential paths pointed to nonexistent local files for these tests.
No real AWS call, install, commit, push or remote mutation occurred. This remains
focused offline verification, not full CI, Linux runtime or live acceptance.
