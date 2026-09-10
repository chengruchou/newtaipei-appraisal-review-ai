# Extraction evaluation runner

This is the local Issue 21 evaluation implementation. It consumes the existing
`domain/evaluation_contracts.py:EvaluationManifest`, existing `PageOutcome`, and
the independently authored Issue 23 `GoldenSuite`. It neither modifies goldens
nor constructs expected answers from predictions. Resolver, parser, provider
authorization, live smoke and the top-level architecture remain separate work.

## Interfaces

`application/extraction_evaluation.py` exposes:

```python
score_evaluation(manifest, replay, goldens) -> EvaluationReport
await run_evaluation(manifest, seed, goldens, extract) -> (EvaluationReplay, EvaluationReport)
english_summary(report) -> str
```

`manifest` is the exact existing `EvaluationManifest`, not a replacement class.
`goldens` is an independently loaded `GoldenSuite`. `replay` is the additive
`domain/evaluation_report.py:EvaluationReplay` containing:

| Field | Meaning |
| --- | --- |
| `schema_version` | `evaluation-replay-v1` |
| `manifest_digest` | Canonical digest of the complete existing manifest |
| `versions` | Required parser and normalizer version names and digests |
| `bindings` | Explicit `case_id` to `golden_case_key` bindings, one per scheduled case |
| `outcomes` | Records containing one-based `repeat` and an unchanged `PageOutcome` |
| `execution_errors` | Callback failures without an outcome: repeat, zero-based input index, one-based page, classified failure and measured callback duration |
| `pricing_evidence` | Optional dated `TokenPriceEvidence`; absent means unknown cost |

The manifest already records input versions and hashes, model/API/Region and
configuration, pipeline, prompt, proposal schema, rendering configuration,
dataset/split, scoring policy, repeats, timing scope and budgets. Replay adds the
parser/normalizer identities without changing the existing manifest or schema
bundle. Its full digest pins these additional identities and recorded outcomes.
Versions are captured execution metadata, not independent proof that a named
binary or provider actually executed. The report retains version names and
digests. It uses a digest for the complete model configuration instead of
copying potentially identifying model ARNs or input names into summaries.

### Digest preparation

Use these exported helpers before freezing a manifest:

```python
dataset_digest(inputs)
split_digest(split, bindings)
golden_digest(goldens)
scoring_policy_digest(scoring)
canonical_digest(manifest)
```

Canonical JSON is UTF-8, sorted keys, compact separators, ASCII escaping, no NaN,
and Pydantic JSON-mode serialization for models. Arrays preserve order.
`dataset_digest` hashes the ordered serialized `EvaluationInput` list.
`split_digest` hashes `{"split": split, "bindings": [...]}`. `golden_digest`
hashes `GoldenSuite` with cases sorted by `case_key`; all supplied cases are
included, not just selected cases. `scoring_policy_digest` hashes the scoring
model excluding its own `policy_digest`. `manifest_digest` hashes every manifest
field. Unknown or mismatched digests are rejected, never silently replaced.

An input declaration cannot prove that development and held-out populations
are disjoint across unrelated files. Curators must freeze their bindings before
prompt tuning and maintain that separation. The runner records the chosen split,
checks its digest and never changes it in response to a score. Many current #23
fixtures intentionally reuse `golden-case` and document identities. Evaluate
those variants with separate manifests; do not relabel sources or combine
incompatible revisions under one manifest input identity.

## Replay CLI

Run from the repository with its local environment:

```sh
PYTHONPATH=$PWD/src .venv/bin/python scripts/evaluate_extraction.py \
  --manifest artifacts/evaluation/manifest.json \
  --outcomes artifacts/evaluation/outcomes.json \
  --goldens tests/goldens \
  --output-dir artifacts/evaluation/replay-report
```

`--goldens` accepts the read-only #23 directory (excluding its summary index) or
an exact serialized `GoldenSuite`. The output directory must be new and cannot
be inside the golden directory. Exit 0 means valid inputs were scored, **not**
that extraction passed acceptance. Exit 2 means rejection; stderr contains only
a fixed reason code, never a validation payload, provider message or input path.
Duplicate JSON keys are rejected. JSON and English reports are `report.json` and
`summary.txt`. The additive `schemas/evaluation-report-v1.json` describes the
serialized machine report; the original evaluation/extraction bundles are unchanged.

## Provider orchestration

`run_evaluation` executes all scheduled pages in manifest order for each repeat.
The seed replay must contain no outcomes or execution errors and the manifest
must already declare `mocked` or `live`. There is no implicit provider or cloud
fallback. All input and golden bindings are checked before invocation. Each
repeat/case gets a distinct run UUID bound to the golden revision and material
digest; document requests reuse the exact sanitized source reference. The
context language is `zh-Hant`; source purpose selects the existing task enum.

Supply an async callback `extract(PageRequest) -> PageOutcome`, normally a
closure over the parent composition's authenticated principal and
`AuthorizedExtractionService.extract`. The callback receives no golden values,
review expectations, answers, or summary. It owns resolver authorization,
snapshot verification, rendering, provider configuration and shared execution
budget/ledger. Its result must retain all retries and telemetry. This runner
does not implement a second retry loop or grant data-transmission authority.

The runner is sequential and caps total elapsed time. Before each callback it
requires both `used_calls + max_attempts_per_page <= max_calls` and
`used_output_tokens + max_attempts_per_page * configuration.max_output_tokens
<= budget.max_output_tokens`. These are global manifest counters, shared across
every case and repeat even when the callback creates fresh per-run ledgers.
Exact fits are allowed. The reservation is a pre-call worst-case allowance,
not fabricated measured usage: after complete telemetry returns, only actual
attempts and output tokens consume it, releasing the unused allowance for the
next page. No callback runs when either reservation cannot fit.

Insufficient reservation stops the remaining schedule. Every uncalled page and
case remains in the original scoring denominator; no synthetic zero-token
outcome, failed provider attempt, or execution error is created for those pages.
The current replay contract records them conservatively as unobserved/missing,
so known usage subtotals survive while totals remain null. A budget too small
for the first callback's worst case can yield zero calls and an entirely
unobserved schedule even if it could afford one successful attempt.

Returned attempts beyond the per-page cap, any known per-attempt output beyond
the configured cap, or known aggregate calls/output beyond the manifest cap
fail the page as `budget_exhausted` and stop further execution. All original
attempt records, failures, tokens and durations are retained unchanged, with a
located `page_failed` handoff. The candidate proposal is withdrawn. This is a
failed `PageOutcome`, not an `execution_errors` entry with lost telemetry.
Unknown tokens remain unknown even when a separate known limit violation is
detected. Replay scoring applies the same declared-limit checks in manifest
page/repeat order without mutating the supplied replay; its scoring projection
rejects over-budget candidate pages while keeping their measured usage.

Providers must still enforce their per-call/per-attempt limits inside the
execution boundary. A callback that violates its declared limits can already
have incurred charges; post-call failure reporting cannot undo that spending.
The runner also stops on uncertain completion, unknown usage, or a callback
exception. A callback exception produces a safe `execution_errors` record, never
a fabricated zero-token `PageOutcome`. Its attempts and tokens remain unknown.
Callback errors count in failure rates and retain measured callback duration
separately from provider/page timing. They also remain missing page outcomes
for completeness and usage accounting. External cancellation propagates.

The CLI can invoke the parent composition directly:

```sh
PYTHONPATH=$PWD/src .venv/bin/python scripts/evaluate_extraction.py \
  --manifest artifacts/evaluation/live-manifest.json \
  --outcomes artifacts/evaluation/empty-seed.json \
  --goldens tests/goldens \
  --execute --provider-factory project_composition:make_evaluation_provider \
  --output-dir artifacts/evaluation/live-report
```

`module:function` must accept the exact manifest and return the async page
callback. It is an explicitly selected, trusted local composition, not a
provider plugin inferred from document content. No factory is imported without
`--execute`. No AWS SDK is imported by the scorer or CLI. Live execution requires
the parent's existing authorization, capability and spending preflight. This
implementation has been tested with local callbacks; it supplies no live
acceptance evidence. The CLI also writes `private-outcomes.json` when executing,
so recorded results can be replayed against the same manifest. Keep that file
local; it includes raw proposals and source evidence.

## Scoring and denominators

The supported #23 `golden-1` origin is synthetic. A `sanitized` real-case dataset
is explicitly rejected until an independent compatible golden contract exists.
Metrics cover **observed form slots**, including target/comparable grades,
correction values, subtotals, totals and cross-page copies. A printed conflict
is compared to `expected_slots[].observed`, not to the reviewer's derived
`independent` answer. Blank derivation authority never changes the printed state.
Rules, measurements, checks and empty-column proposals have separate unscored
counts; these goldens do not supply a complete extraction benchmark for them.

Every scheduled source must match golden case/document/version/hash/purpose/page
count. Every forms document in a bound case must be scheduled in full, and
every cited golden field must resolve to the scheduled document/page/version.
Partial page subsets cannot shrink a whole-case benchmark. Missing page outcomes
and wholly absent cases still contribute all their expected fields, pages and
repeats. Golden fields with no citation remain in the field denominator but not
the localization denominator. Duplicate page outcomes and off-schedule records
are invalid inputs. A source-bound prediction in the wrong field's page or
region instead receives an incorrect evidence score.

| Metric | Numerator / denominator |
| --- | --- |
| `precision` | Jointly correct fields / all predicted observations, including duplicates and unknown identities |
| `recall` | Jointly correct fields / all expected slots across scheduled cases and repeats |
| `identity_precision`, `identity_recall` | One-to-one identity matches / predictions or expected slots |
| `type_accuracy` | Correct semantic slot kind, context, factor, unit and value representation / all expected slots |
| `value_accuracy` | Correct state, unit and value / all expected slots |
| `state_accuracy` | Correct explicit state / all expected slots |
| `source_accuracy` | Correct document/hash/version/page/region identity with valid geometry / all expected cited slots |
| `localization_accuracy` | Source-correct citations meeting IoU threshold / all expected cited slots |
| `page_completeness` | Recorded `PageOutcome`s / scheduled pages |
| `case_completeness` | Cases with all scheduled outcomes / scheduled cases |
| `case_accuracy` | Nonempty expected field sets entirely correct with all pages candidate / scheduled cases |
| `error_rate`, `refusal_rate` | Observed failed/refused pages, including callback errors / scheduled pages |
| `human_intervention_rate` | Pages with actual handoff requests / scheduled pages |

Every metric publishes numerator, denominator and nullable value. An empty
denominator yields null. `not_measured` localization also yields null and retains
the eligible cited-field denominator. Error/refusal/handoff rates are observed
lower bounds when outcomes are absent; review-required rates explicitly retain
missing pages/cases. Handoffs are requests, not completed human decisions.

Matching is by case and slot identity, in manifest page order and proposal list
order. The first prediction consumes that identity; later duplicates are false
positives even if they have a better value. A unique `ReviewSlot` declaration
on that page is required for semantic type credit. Missing declarations do not
obtain type credit by copying the expected type. Context and factor bindings
prevent matching the right number to the wrong context or grade side. Extra
unrelated citations cannot hide behind one good citation. Source localization
does not establish truth of an excerpt's prose or confidence calibration.

Blank, missing, not-present, not-applicable and present-zero are distinct states.
No prediction is an additional confusion-matrix column, not an inferred
`missing` answer. Numeric strings/Decimals must be finite. Numeric comparison is
`abs(actual-expected) <= max(abs_tolerance, rel_tolerance*abs(expected))`;
grades use exact strings. Exact units are default. The only supported conversion
policy is exported `UNIT_CONVERSION_POLICY`, pinned by its digest, converting
ratio and percent points by 100. Unknown conversion digests fail explicitly.

## Usage, cost, comparison and privacy

Usage sums every recorded attempt, including failed attempts and retries. It
reports known subtotals, unknown-attempt counts, unobserved-page counts, nullable
totals, retry tokens, attempt/backoff/retry time, page time and observed page
latency mean/p50/p95 (nearest-rank percentiles). Page sums are not parallel-run
wall time. Explicit zero-attempt preflight outcomes contribute measured zero;
an absent outcome does not. Unknown tokens and callback attempts are never
converted into known zeros.

Cost is null without both a manifest `CostPolicy` and matching local
`TokenPriceEvidence`. The evidence digest must equal `rates_digest`; exact
configuration digest, date, source label, currency and both rates must match.
It is a supplied dated rate record, not independently fetched current pricing.
Estimates use Decimal over all input/output tokens divided by one million.
Unknown usage makes total cost null while preserving a known-usage subtotal.
No taxes, discounts, image/storage charges or non-token fees are implied. Rate
source text and private URLs are omitted from reports; their digest stays local
in the manifest/evidence chain.

Each pair of repeats reports pages observed in both, missing on each side,
changed-page counts and changes in field identity/state/type/value/evidence,
handoffs, status, other proposals, usage and timing, plus metric deltas. Field
projection comparison is insensitive to observation ordering but preserves
multiplicity. Text formatting can still differ. Run UUIDs and raw excerpts are
excluded. There is no bitwise determinism claim, no acceptance-threshold gate,
and no promotion of development results into held-out evidence.

Reports omit extracted values, raw text, excerpts, case/slot/context/region
identifiers, unresolved prose and provider exception messages. Technical version
names are caller-supplied metadata and must not contain private case content.
Private inputs and captured outcomes belong under ignored local `artifacts/` or
`outputs/`, never Git or publication. New report files use mode 0600 and the new
report directory uses mode 0700. Only publish separately reviewed value-free
`report.json` and `summary.txt` when authorized. The source PDFs remain untouched.

## Offline verification

```sh
PYTHONPATH=$PWD/src .venv/bin/pytest tests/unit/test_extraction_evaluation.py
PYTHONPATH=$PWD/src .venv/bin/mypy src/appraisal_review scripts/evaluate_extraction.py
```

Tests read all thirteen #23 cases without rewriting them. Synthetic proposals
come from their source fixtures; expectations are loaded separately from the
reviewed JSON. Real subprocess CLI tests cover successful replay and provider
orchestration, opt-in, corrupt provenance, wrong case/page/version/evidence,
complete-case denominators, unknown usage, rate evidence and raw-content
canaries. These are local engineering checks, not cloud or Chinese accuracy
acceptance. The additive boundary decisions here should be incorporated into
the parent's integration ADR; existing ADRs and top-level documents are not
edited by this implementation.
