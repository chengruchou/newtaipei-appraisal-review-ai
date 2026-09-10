# ADR 0023: Bounded attempts, complete raster validation and truthful page outcomes

Status: implemented locally for Issue 21 Phase 3, 2026-09-10.

## Decision

The authorized application now consumes the existing `PageExtractionProvider`
port and returns `PageOutcome`, including candidate or failed status, safe errors,
located failed-page handoffs and available per-attempt usage. Existing strict
wire models and legacy HTTP/PDF schemas do not gain fields. The private legacy
helper still returns `PageExtraction` for existing regression/assembly consumers;
unknown usage raises `usage_unavailable` rather than inventing zero.

Use one `ExtractionLedger` per backend/run. Bind the complete run reference on
first admitted page; reject other run references on that ledger. Count page
admissions, model-call reservations, simultaneous SDK calls and total output token
reservations. A page re-request consumes another admission. Reserve each request's
maximum output tokens before submission, refund only known unused tokens, and
retain the full reservation when usage is unknown. Observed output exceeding its
reservation stops the run. This is a conservative execution limit, not a billing
measurement or dollar cap. Input byte/image/context limits apply per input.

The run deadline starts at first backend page admission and includes render,
preflight, calls, validation and backoff. Source resolution happens before backend
admission and is owned by the resolver. Use the smaller configured total timeout
and run budget. Check remaining time at each admission and worker start. The
SDK receives one attempt, with connect/read timeouts sharing the configured
per-call timeout. Trusted Runtime factories must honor that same contract.

Only known throttling and transient service failures retry with bounded jittered
backoff. Access/authentication, configuration, refusal, truncation, malformed
output, invalid citations and transport loss do not automatically retry. A
caller timeout does not cancel an SDK thread: shield the worker, stop new calls
on that ledger and retain its concurrency slot until its actual completion.
Cancellation similarly stops the ledger. The worker releases its own slot in a
finally block. Safe error classification never copies provider exception text.

Record usage before proposal validation so refusal, truncation and invalid
citations retain reported tokens. Missing, negative, boolean or incorrectly typed
counts are unknown, not zero. Attempt timing includes response validation; backoff
is separate. Total page timing also includes preparation/preflight overhead.
The optional trusted attempt observer receives only request references and typed
attempt metadata before backoff; it never receives source JSON, prompts or model
output. Final outcome records include actual backoff. Observer failure stops the
ledger without retrying billed work; cancellation remains authoritative even if
the cancellation observer fails.

Validate the actual supported PNG stream: chunk checksums/order/end, bounded zlib
inflation, exact raster length and row filters. Accept non-interlaced 8-bit gray,
gray-alpha, RGB and RGBA plus optional valid pre-data density metadata. Reject
palettes, text/EXIF/unknown chunks and other unsupported encodings explicitly.
This narrow input contract matches the deterministic snapshot renderer. Enforce
configured byte/dimension/pixel ceilings against operator-reviewed model limits
before provider metadata or billing. Cap response JSON size before parsing and
include the system prompt in the request text-character cap; neither character
cap claims to measure tokenizer usage.

Expose exact `PROMPT_VERSION`/`PROMPT_DIGEST` and deterministic rendering
configuration/version/digest. The evaluation manifest can pin them separately;
model/library versions remain additional reproducibility inputs.

## Consequences and limits

The former tiny synthetic PNG had an invalid IDAT checksum and damaged ending.
Replace it with a valid synthetic raster while retaining the original bytes as
an explicit rejection test. All prior citation/confidence/retry assertions remain.

The ledger and observers are process-local, not durable job/fencing infrastructure.
Use the same backend throughout one approved run; do not construct a fresh budget
for each page. Metadata preflight is serialized locally and is not included in
model-call counts. A timed-out render or metadata worker can also finish later;
it cannot subsequently invoke a document model through the stopped ledger.
Python loop shutdown can wait for SDK threads, and CPU/native-library work is
not forcibly terminated. A recorded deadline is not a server-side cancellation
receipt. Runtime/job recovery remains with its designated owner.

No real resolver, approved model selection, live invocation, scorer, human-task
store or PDF side effect is introduced. Phase 4 owns the scoped Textract decision;
Phase 5 owns complete assembly/handoff integration and Phase 6 scoring. All live
privacy, source, budget and authority gates remain required.
