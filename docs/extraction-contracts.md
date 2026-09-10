# Extraction and evaluation contract authority

Phase 3 update: [authorized assembly](extraction-preflight.md) now returns these
outcomes with [bounded execution and telemetry](extraction-execution.md). The
Phase 1 contracts and historical verification below remain unchanged; actual
privacy/source resolution remains a dependency.

Status: Issue 21 Phase 1 local implementation. These types and validation helpers
are available; actual privacy/source resolution and evaluation scoring remain
pending. See [ADR 0021](adr/0021-extraction-evaluation-contracts.md)
and the [Phase 0 baseline](issue-21-baseline.md).

## Wire roots and consumers

| Root / port | Producer -> consumer | Meaning and required checks |
| --- | --- | --- |
| `PageRequest` | Trusted executor -> resolver/extractor | Existing run/revision and document references; one page; explicit task/language; matching case and purpose |
| `SanitizedSourceReference` | Privacy/storage integration -> extraction request | URI-free sanitized identity and external privacy-v1 manifest reference; not privacy approval |
| `AuthorizedSanitizedSnapshot` | Reserved authenticated resolver -> extraction adapter | Internal immutable bytes/parser JSON; digest, identity, role and page count checks; detached parser data |
| `PageOutcome` | Extraction adapter -> existing candidate assembly bridge | Candidate or failed execution, nullable proposal, safe failure code, telemetry and located handoffs |
| `HandoffRequest` / `ExtractionHandoffSink` | Extraction -> Issue 17/24 integration | Exact run/revision/source/page binding; no task store, accepted response or approval |
| `ProviderTelemetry` / `ExtractionTelemetrySink` | Executor -> safe metrics consumer | Explicit configuration/prompt identity, ordered attempts, nullable usage and elapsed/backoff accounting |
| `EvaluationManifest` | Frozen evaluation configuration -> future runner/scorer | Dataset/split/golden identities, ordered inputs, versions, model settings, budgets, scoring and optional dated cost policy |

Python/Pydantic in `domain/extraction_contracts.py` and
`domain/evaluation_contracts.py` is the validation authority. Export with:

```text
python scripts/export_extraction_contracts.py
```

`schemas/extraction-v1.json` and `schemas/evaluation-v1.json` are serialization
bundles; choose a root using `#/$defs/ModelName`. The
[example index](../examples/extraction-v1/index.json) maps each fixture to its root.
Tests compare exports, roundtrip fixtures and validate them against JSON Schema.
Pydantic also enforces cross-field/source invariants that JSON Schema alone does
not encode. Every consumer must run the typed validators and applicable trusted
boundary checks; schema validation alone is insufficient.

Unknown fields and unsupported envelope versions fail. Shared extraction
components carry `extraction-v1`; the evaluation root carries `evaluation-v1`.
Inherited service references retain `service-v1`. Decimal tolerances/rates use
strings in serialization schemas. Counters reject booleans; finite values and
positive limits are required. Optional unknown usage, thresholds and cost policy
are explicit null, not omitted guesses. Tuples preserve declared input/page order.
Existing exported service/local-service and legacy proposal/result schemas remain
unchanged. Frozen outer models do not deeply freeze legacy mutable proposals;
`validate_outcome` revalidates them before checking snapshot citations.

## Privacy and ownership handoff

Issue 22's local branch defines `PrivacyManifest` with UUID case/document IDs,
sanitized digest, byte size, page metadata and placeholder occurrences. This
branch does not copy those models. The future integration must validate the real
manifest using its owner module, verify local provenance/export eligibility,
and bind that exact manifest to the sanitized bytes. The manifest digest must
use the agreed producer serialization; Phase 1 intentionally does not invent a
second canonicalizer or silently equate it with a document digest.

Issue 27 must authorize the trusted principal and map privacy-issued IDs to
service `DocumentReference` identities and immutable storage versions. The
service revision digest must refer to the sanitized revision; old original-byte
citations/confirmations cannot be relabelled. Purpose comes from the trusted
document registry, not a body claim. Both dependencies must agree on page
geometry, identity mapping and manifest-byte binding before production use.

The internal snapshot constructor proves only internal consistency, not that a
parser truly derived its text from those bytes or that a document is anonymous.
`SanitizedSnapshotResolver.resolve` is the required provenance/access boundary;
there is no default resolver implementation. Its future production composition
must reject unavailable integration before transmitting documents. Mapping files,
original hashes/filenames, paths and raw case prose are not request fields.
Opaque strings still need trusted provenance; a regex cannot detect personal data.

`PageOutcome` checks page/source identity and source-purpose usage.
`AuthorizedSanitizedSnapshot.validate_outcome` additionally resolves exact region,
geometry and excerpt against a detached canonical `SourceRegistry`. Preserve the
existing extractor's stricter proposal canonicalization and confidence demotion;
these envelopes do not replace it. Domain factor validation, table completeness,
rule applicability and approval remain in existing assembly/review gates.

## Telemetry, evaluation and failure semantics

No raw provider response, prompt text, exception message or proposal belongs in
telemetry. A returned response can still carry a failure such as refusal or
malformed output. Failed/unknown attempts require a static reason. Timeout with
unknown completion is terminal for that sequence: recording cancellation is not
evidence the SDK request stopped. Failed pages require a `page_failed` handoff;
they must not later be marked inspected merely because the outcome exists.

The legacy `PageExtraction` remains a success-only structure with required integer
usage. Phase 5 must bridge candidate data into `assemble` while preserving unknown
usage separately; Phase 1 provides no conversion that substitutes zero. An empty
candidate fixture tests wire shape, not inventory completeness or extraction quality.

Evaluation freezes document/page order and repeats. Duplicate documents/pages,
out-of-range pages and impossible minimum call/page schedules fail. Runtime
enforcement of call, concurrency, byte, character, token and elapsed limits is
later work. A character limit is not a tokenizer measurement. Cost policy records
currency, rate date/source/digest, rates and an estimated ceiling; missing policy
means unavailable estimate. This is not an enforceable provider billing limit.

The scoring contract fixes one-to-one identity matching, distinct missing states,
all scheduled eligible cases as denominator and null for zero denominators.
Numeric tolerances, optional versioned unit conversion and optional region IoU
must be explicit. A policy digest can bind more detailed per-field rules.
No acceptance-threshold digest means no production quality pass can be claimed.
Field-key matching, false-positive counting, split separation against other runs,
actual label adjudication and scoring remain Phase 6/Issue 23 responsibilities.

## Compatibility and remaining work

Phase 1 adds no HTTP route, AWS client, production resolver, task database, model
selector, scorer or PDF writer. Existing `PageProposal`, `PageExtraction`,
`SourceRegistry`, `assemble`, service v1, HTTP/invocation and PDF contracts retain
their original behavior. Legacy CLI/explanation paths still need Phase 2 gating;
importing these types does not make those paths privacy-qualified.

Continue with explicit source/client preflight and safe request projection in
Phase 2. Actual #22/#27 wiring, live authorization and later acceptance remain
open; mock or synthetic evidence cannot close those gates.

## Phase 1 verification (2026-09-10)

- New offline contract suite: 55 passed. Coverage includes schema export equality,
  JSON Schema/typed roundtrips, strict versions and fields, byte/source/purpose/page
  binding, canonical geometry/excerpts, detached snapshots, changed nested
  proposals, located handoffs, timeout accounting, null usage, evaluation page
  ordering, budget/scoring constraints and SDK-blocked imports.
- Existing extraction/source-purpose/service/CLI/reviewer/integration regressions:
  81 passed, 20 failed on Windows. Eighteen reproduce the Phase 0 sandbox/POSIX
  failures. Two additional service fixture tests reject POSIX absolute paths on
  Windows. Their code and expected schemas are unchanged. The new suite checks
  exact service/local-service schema equality without executing POSIX paths.
- Ruff check and format check passed. Linux-target mypy passed for 77 source
  files. Host mypy retains three existing POSIX API errors in local approval.
- Submission checks passed for complete snapshots, index and working files,
  configured identity and functional branch; zero outgoing commits.

Commands and actual exit codes are retained in ignored
`artifacts/issue-21-p1/validation.json`, with individual logs. The validation
process removed inherited AWS variables, disabled metadata lookup and used
nonexistent local SDK configuration paths. No real provider client or AWS call
was used. These are focused offline results, not full CI or Linux runtime evidence.
