# Requirements to implementation and acceptance

Review date: 2026-09-05. Original base: b69ed74da950f37add016b5e881a7e0478f9c7d5.
Current revision base: main ff5e9d1d511e015dbc96aa5b380cce1def433421, which already
merged foundation e24265d7c4ef0bb5d2d74d0ed94bffa2052a08ed in PR #10.
Historical delivery: A PR #13 and Runtime PR #14 are merged. [Migration and full original SHAs](pr-migration.md).

| Requirement | Component / implementation | Issue | Evidence / status |
|---|---|---|---|
| Shared typed PDF boundary | domain/pdf_models.py, pdf_types.py, ports/pdf.py | #6 | test_pdf_contract.py; local + CI |
| URI separation, field identity, warnings/errors | PDFWriteRequest/Result, Controller | #6/#4 | Contract and PDF boundary tests; metadata only |
| Shared composition, no import-time clients | application/bootstrap.py | #4 | test_bootstrap.py; local, explicit mocked AWS bundle |
| Sync review HTTP and invocation parity | api/routes/reviews.py, agentcore/runtime.py, application/entrypoint.py | #4 | test_entrypoints.py + scripts/http_smoke.py |
| Legacy behavior | /health, /v1/validate | #4 | Actual HTTP findings equality and wrong-total test |
| Legacy detail and review 422/503/500 schema | EntryProblemResponse, routed validation handler | #4, PR #13 | test_api_error_contract.py; JSON Schema checks on actual responses, invocation parity |
| Candidate/missing/low-confidence/unknown factors block PDF | Existing engine/verifier + Controller | #4 | test_controller_pdf_boundary.py, no verifier changes |
| Write once, malformed writer output cannot complete | Controller + FakePDFWriter | #4/#6 | URI/count/field/exception tests, warnings retained |
| Runnable synthetic fixture | adapters/local/synthetic.py, demo.py, examples/ | #4 | verified/completed/needs_review local invocations |
| Actual PDF correction/storage | B's future PDF adapters | #5 | Planned; no writer or live S3 integration |
| Chinese text/marks/coords and Bedrock extraction | Future parser/extractor/provider | #7 | Source PDFs inspected; automated extraction not implemented |
| Approval provenance/applicability | Trusted provider and semantic resolver | #7/#8 | Planned; model fields alone do not enforce applicability |
| Observed vs expected, sums and copied totals | Complete review model/engine/verifier | #8 | Legacy arithmetic exists separately; new-path integration planned |
| Full coverage and evidence gate | Future verifier expansion | #8 | Known baseline weakness reproduced; protected files unchanged |
| Async jobs and durable Runtime delivery | Cloud API/persistence/dispatcher/reconciler | #9 | Architecture design; no live test |
| Runtime packaging/health smoke | Separate cloud-test PR | #9 | Prepared separately; never conflated with A or full review |
| Attribution and branch naming | AGENTS.md and scripts/check_submission.py | #4, PR #13 | 49 subprocess CLI tests; full snapshots/index/metadata/publication/branch checks |

## Baseline and local evidence

- Initial working tree clean. Fetched origin/main; no remote teammate branches or
  open implementation PRs. #4/#5 had no comments. #3 remains closed.
- gh was absent; the authenticated GitHub connector confirmed repository
  permissions and supplied Issues/PRs. Git fetch/push used existing credentials.
- Baseline: 17 tests passed; Ruff and format passed. With dev+aws, mypy had four
  errors in two existing optional boto3 imports. Foundation adds dev type stubs
  and removes obsolete import-ignore comments; strict mypy remains enabled.
- Foundation: 40 tests passed; Ruff, format and mypy passed. PR #10 CI passed.
- A: 76 tests passed; real localhost HTTP returned 200 for all three synthetic
  scenarios, 422 for malformed input. Ruff, format and mypy passed.
- Review revision: A has 133 tests (76 existing, 8 API schema/error cases and
  49 submission CLI cases). Localhost smoke checks both error contracts and
  unchanged success paths. The Runtime replacement reruns these plus 8 cloud
  tests and CloudFormation lint; final CI/head evidence is attached to PR #14.
- Two dependency deprecation warnings from current Starlette/httpx/AnyIO are
  reported, not suppressed. Tests validate actual response bodies and call counts.
- Local Python 3.13 skipped hidden editable-install .pth files on macOS. Explicit
  PYTHONPATH=$PWD/src selects this checkout; no test expectations were relaxed.
- Protected domain/verification.py, adapters/local/audit.py and
  tests/unit/test_verification.py are byte-identical to origin/main.

## Source and live-test limits

Source PDFs were read directly from the supplied Downloads files/ZIP without
modification or repository copies. The forms include mixed page sizes and no
AcroForm widgets; regional/individual road examples were checked against the
rendered source tables. Only future acceptance descriptions and synthetic data
are committed. No real document extraction, font/rendering, PDF correction,
Bedrock inference, live S3 or deployed AgentCore was validated in A.

## Submission inspection

Git author and committer match the existing user configuration. No AI author,
co-author, committer, signature, badge or attribution footer is included.
No active local commit hooks or configured commit template supplied automatic
attribution. Technical service/model names and the policy itself are retained.
Existing history is retained. This revision performs no PR merge or force-push;
foundation #10 was already merged before the refreshed inspection.

## Complete review revision (#8)

Baseline: b3760b9ae15d97dd16ffdd0da130e564324c71f6 (latest fetched main).
141 baseline tests, Ruff, format, mypy and localhost HTTP passed on Python 3.13.5.
The independent forged-999 regression failed before the verifier change; its
assertion passed after the fix. Local ignored evidence is under artifacts/evidence.

Schema-2 CaseReviewer and Controller now enforce exact applicability, source
registry, trusted material approval, observed values, arithmetic/cross-form
checks and independent inventory coverage. test_case_review.py exercises
adversarial claims, omissions, duplicates, uncertainty, numeric units, boundary
dates, multi-context aggregation and writer gating through the Controller.
Synthetic fixture identity/approval/evidence was strengthened without a gate bypass.
The audit logger is unchanged; verifier changes are explicitly authorized by #8.

B receives the unchanged public writer protocol with bound single-context results
and artifact_created metadata. No real writer, real-document accuracy, live
model acceptance, human rule approval or AWS deployment is claimed by this layer.
The dependent #7 layer supplies parsing/extraction/approval operations and evidence.

## PR #15 review corrections

Review 5121531968 identified three additional cases beyond the green baseline:
an unregistered copied-slot context, runtime confidence ignored by schema 2.0,
and arithmetic tolerance contradicted by final exact comparison. All three new
regressions failed at acf3a454bb7c2e07953f5e49d5fa6c7ae60be176 before the fix.
The unchanged baseline had 213 passing tests. Ignored before/after evidence lives
under artifacts/review-fixes; no real source material is needed for this revision.

The corrected gate validates all slot/factor bindings before arithmetic, passes
one validated runtime threshold through calculation and verification, and retains
all arithmetic constraints through final aggregate comparison. Regressions cover
registered cross-context success, unknown entities, binding mismatches, HTTP
configuration, confidence boundaries, zero-writer failures, Decimal tolerance
boundaries, rounding and multiple constraints in either order. Existing forged
claims, evidence, API and artifact-status tests remain required. Real model and
whole-case human acceptance remain pending; these tests use synthetic material.

The follow-up review 5122010147 is covered by test_review_trust_boundaries.py:
request/parser/source identity, roles/page counts, supplementary sources, complete
value-anchor binding, mutable self-reference, cycles, unrooted aggregates, loaded
and evaluated audit identities, and writer-failure sequencing. Seventeen negative
cases reproduced the earlier gaps before implementation; the distinct-cell DAG
positive case passed. Existing context, runtime-threshold and Decimal regressions
remain required. The dependent #16 must inherit these fixes before re-review.

Confidence regressions in test_confidence_provenance.py cover target/comparable
threshold boundaries, multiple measured anchors, unknown/model provenance,
method-only claims, legitimate low-score confirmation with separate authorization,
and later score/provenance/citation changes. ADR 0008 defines the semantics before
these tests. The #16 parser/extractor/CLI integration must produce these fields
through official normalization rather than test-side repairs.

Review 5557081140: A1/A2/B regressions use distinct actual synthetic cells and empty
raw text/excerpts for blanks; failures were assertions, not import/schema errors.
Review 5557168465: shared wrong-purpose tests cover criteria/reference/brief,
mixed references and another selected forms identity. Valid forms values with
criteria rules and reference procedures remain supported. ADR 0009 records the
requirement/module/gap/test/dependency map. Older real-document subset counts are
historical and were not rerun; no current whole-case accuracy claim follows them.
