# Requirements to implementation and acceptance

Review date: 2026-09-05. Base: main b69ed74da950f37add016b5e881a7e0478f9c7d5.
Foundation: e24265d7c4ef0bb5d2d74d0ed94bffa2052a08ed, PR #10.

| Requirement | Component / implementation | Issue | Evidence / status |
|---|---|---|---|
| Shared typed PDF boundary | domain/pdf_models.py, pdf_types.py, ports/pdf.py | #6 | test_pdf_contract.py; local + CI |
| URI separation, field identity, warnings/errors | PDFWriteRequest/Result, Controller | #6/#4 | Contract and PDF boundary tests; metadata only |
| Shared composition, no import-time clients | application/bootstrap.py | #4 | test_bootstrap.py; local, explicit mocked AWS bundle |
| Sync review HTTP and invocation parity | api/routes/reviews.py, agentcore/runtime.py, application/entrypoint.py | #4 | test_entrypoints.py + scripts/http_smoke.py |
| Legacy behavior | /health, /v1/validate | #4 | Actual HTTP findings equality and wrong-total test |
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
| No AI attribution | AGENTS.md and scripts/check_submission.py | #4 | Content + outgoing Git metadata checked before publication |

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
No existing history was rewritten; no PR was merged or force-pushed.
