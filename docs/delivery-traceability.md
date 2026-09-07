# Requirements to implementation and acceptance

## Merged delivery snapshot (2026-09-06)

Inspected `origin/main`: `0e9a5832a84c95fd03f1084847e3b6181ca93f03`.
PRs #15, #16 and #18 are merged, alongside the earlier foundation #10, entry
#13 and Runtime preparation #14. The PDF writer commits are rebased onto that
snapshot; the original stacked heads below remain historical verification references.

| Delivery | Final implementation head | Merge commit |
|---|---|---|
| [Review #15](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/15) | `a303ed5bd869e4b493d4688b47b44bd7e2c69690` | `3cdc33a822be57707416959910e86bac4029e0cc` |
| [Extraction #16](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/16) | `cacb85b8ae587b74167d1717383aa87703eb6bb8` | `fefce2f2bd7f9fe60a8f63422714f5b993365ff9` |
| Architecture alignment #18 | `33531c7` | `0e9a5832a84c95fd03f1084847e3b6181ca93f03` |

Historical head validation: #15 had 323 tests plus 8 cloud tests
([CI](https://github.com/chengruchou/newtaipei-appraisal-review-ai/actions/runs/34019136768));
#16 had 402 tests plus 8 cloud tests
([CI](https://github.com/chengruchou/newtaipei-appraisal-review-ai/actions/runs/34019738902)).
These counts are previous implementation evidence, not new live-model, complete
human-case or cloud acceptance.

The branch implements the provider-local formal writer core and an injected-client
S3 transfer wrapper for #5. Runtime selection, approved production field maps and
fonts, live S3 acceptance and production deployment remain open. Durable cloud
jobs #9, controlled actions/decision traces/human tasks #17 and the web workbench
remain planned. See the [delivery plan](mvp-plan.md) for dependencies.

## Requirement map

Original review date: 2026-09-05. Original base: b69ed74da950f37add016b5e881a7e0478f9c7d5.
Historical A revision base: main ff5e9d1d511e015dbc96aa5b380cce1def433421, which already
merged foundation e24265d7c4ef0bb5d2d74d0ed94bffa2052a08ed in PR #10.
Historical delivery: A PR #13 and Runtime PR #14 are merged. [Migration and full original SHAs](pr-migration.md).
The #8/#7 implementation and correction evidence is recorded below as history.

| Requirement | Component / implementation | Issue | Evidence / status |
|---|---|---|---|
| Shared typed PDF boundary | domain/pdf_models.py, pdf_types.py, ports/pdf.py | #6 | test_pdf_contract.py; local + CI |
| URI separation, field identity, warnings/errors | PDFWriteRequest/Result, Controller | #6/#4 | Contract and PDF boundary tests; metadata only |
| Shared composition, no import-time clients | application/bootstrap.py | #4 | test_bootstrap.py; local, explicit mocked AWS bundle |
| Sync review HTTP and invocation parity | api/routes/reviews.py, adapters/aws/agentcore/runtime.py, application/entrypoint.py | #4 | test_entrypoints.py + scripts/http_smoke.py |
| Legacy behavior | /health, /v1/validate | #4 | Actual HTTP findings equality and wrong-total test |
| Legacy detail and review 422/503/500 schema | EntryProblemResponse, routed validation handler | #4, PR #13 | test_api_error_contract.py; JSON Schema checks on actual responses, invocation parity |
| Candidate/missing/low-confidence/unknown factors block PDF | Engine/verifier + Controller | #4/#8 | PDF boundary and complete-review regressions; #8 extends independent verification |
| Write once, malformed writer output cannot complete | Controller + PDFWriter | #4/#5/#6 | Boundary tests plus real LocalPDFWriter composition integration; URI/field/error behavior and warnings retained |
| Runnable synthetic fixture | adapters/local/synthetic.py, demo.py, examples/ | #4 | verified/completed/needs_review scenario names; completed scenario yields verified/simulated, no file |
| Local PDF correction/storage | adapters/local/pdf_*.py and object_access.py | #5 | Synthetic PDFs prove real removal, embedded-font output, reopen verification, protected reference pages and atomic publication; explicit controller injection tested, automatic production selection remains unconfigured |
| S3 PDF transfer | adapters/aws/storage/s3_object_store.py and adapters/aws/pdf/s3_pdf_writer.py | #5 | Injected-client tests prove literal keys, ordered transfer, conditional no-overwrite, content type, suppression and cleanup; no live S3 claim |
| PDF source/mutation/publication decisions | ADR 0010 and ADR 0011 | #5/#6 | Separate data/template identities; conservative correction, explicit fonts and atomic/conditional publication documented for human review |
| Chinese text/marks/coords and Bedrock extraction | Native parser, bounded Converse adapter, material provider | #7 | Implemented; native subset checked, live model accuracy pending |
| Approval provenance/applicability | Signed local receipts and exact case resolver | #7/#8 | Implemented and synthetic tested; real approval pending |
| Observed vs expected, sums and copied totals | CaseReviewer and shared arithmetic | #8 | Controller regression tests; complete live case acceptance pending |
| Full coverage and evidence gate | Independent source/page/table inventory and recalculation | #8 | Forged-result regression fixed; source omissions block completion |
| Async jobs and durable Runtime delivery | Cloud API/persistence/dispatcher/reconciler | #9 | Architecture design; no live test |
| Runtime packaging/health smoke | cloud_tests/, merged PR #14 | #9 | Local/mocked preparation; live invocation and durable production jobs pending |
| Model-selected allowed actions and decision records | Future policy/tool/trace contracts | #17 | Planned; current Controller is a fixed gated workflow |
| Version-bound human tasks and re-entry | Future task/revision API and persistent store | #17/#9 | Planned; current confirmation/approval store is local Linux/macOS only |
| Browser review workbench | Future source/evidence/task UI | New work item required | Planned; full UI implementation is separate from #17 contracts |
| Attribution and branch naming | AGENTS.md and scripts/check_submission.py | #4, PR #13 | 49 subprocess CLI tests; full snapshots/index/metadata/publication/branch checks |

## Historical foundation and Member A evidence

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
- At the Member A delivery, domain/verification.py, adapters/local/audit.py and
  tests/unit/test_verification.py were byte-identical to its origin/main baseline.
  The later #8 revision explicitly changes the verifier and its tests.
- The PDF writer commit range does not modify domain/verification.py,
  adapters/local/audit.py or tests/unit/test_verification.py relative to the
  rebased origin/main.
- Post-rebase controller/PDF integration and boundary tests pass against the
  schema-2 source, authorization and coverage gates. The complete suite and
  platform checks are rerun before publication.
- The submission-check tests now write Unicode negative fixtures as UTF-8 and compare the
  checker's portable POSIX path labels on Windows. All 49 submission-check tests pass. The
  pending-snapshot gate passes for `feat/pdf_writer`, configured Git identity, HEAD, index,
  and every tracked or untracked non-ignored working file. The gate is rerun over each
  categorized outgoing commit before publication.

## Historical Member A source and live-test limits

Source PDFs were read directly from the supplied Downloads files/ZIP without
modification or repository copies. The forms include mixed page sizes and no
AcroForm widgets; regional/individual road examples were checked against the
rendered source tables. Only future acceptance descriptions and synthetic data
are committed. A did not validate real document extraction or PDF mutation. The
current local-writer branch validates rendering, correction and publication only
against generated synthetic PDFs with ReportLab's redistributable Vera test font
and an original test-time-generated minimal TrueType font that covers one CJK glyph;
it has not validated the ignored real template, a production CJK font, Bedrock
inference, live S3 or deployed AgentCore. S3 wrapper evidence uses an injected
in-memory client and does not require or discover AWS credentials.

## Historical submission inspection (foundation and Member A)

Git author and committer match the existing user configuration. No AI author,
co-author, committer, signature, badge or attribution footer is included.
No active local commit hooks or configured commit template supplied automatic
attribution. Technical service/model names and the policy itself are retained.
Existing history was retained. That revision performed no PR merge or force-push;
foundation #10 was already merged before its refreshed inspection. Later merges
are recorded in the current snapshot above.

## Complete review revision (#8)

Historical implementation base: b3760b9ae15d97dd16ffdd0da130e564324c71f6.
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

## Document understanding revision (#7)

The #16 implementation adds LocalPDFParser, conservative native rule candidates,
BedrockDocumentExtractor, PageProposal assembly, MaterialProvider, LocalApprovalStore,
ReferenceCatalog, golden subset comparisons and the opt-in document CLI. It uses
existing Controller/HTTP/invocation paths. Dependency setup stays in the project;
no cloud resource or real approval receipt was created.

Local source inspection covered the supplied 9-page criteria, 6-page forms,
169-page March 2015 manual and 1-page brief. Native text/regions were parsed for
all pages; representative source pages were also rendered and visually inspected.
The two road matrices matched all 50 manually checked cells. Ten local golden
fields matched, including two explicit selection symbols. This subset does not
measure all semantic mappings, source geometry accuracy or whole-case accuracy.

Native detection found 47 matrix blocks: 37 produced supported candidate shapes
and 10 remained unresolved. These are format-support counts, not a validation
rate. A source unit conflict remained unresolved. The prepared source-bound review
contained two contexts but incomplete facts, source coverage and no trusted approval;
its actual Controller result was needs_review, without any PDF URI. The regional
comparable fact was not invented from the target's value. The manual's dated
regional-total transfer section produced a separate candidate equals check.

Synthetic tests include actual Chinese PDF text, table alignment, selection glyphs,
mixed sizes/CropBox/rotation and render ink positioning; invalid model output,
source citations, fake approval, throttling, timeout and event-loop responsiveness;
receipt tampering, other-case/version reuse, private permissions and actual CLI
execution; and prepared PDF material through existing HTTP/invocation. Native
matrix header reversal remains unresolved instead of silently transposing rates.

Live Bedrock calls, account/model availability, full-page semantic goldens and real
human rule/fact approval remain unchecked. No mock or native-parser statistic is
used to mark live accuracy complete. Source hashes and actual private artifacts
are recorded only under ignored artifacts paths; none are included in Git.

## PR #16 evidence integration correction

Review 5121532233 exposed the gap between valid SourceCitations and the legacy
evidence needed by CaseReviewer. A new synthetic-PDF integration test omitted all
legacy evidence from mocked Converse responses, ran actual parsing, extraction,
assembly, confirm-facts CLI and an isolated test approval store. Before the fix,
the final approved result remained needs_review. It now transitions from
unconfirmed needs_review, through confirmed/unapproved needs_review, to verified
after exact-material approval, without any writer or PDF URI. No test-side
EvidenceRef construction repairs this path.

The adapter now derives each side's local evidence only after canonical citation
validation, rejects explicit contradictory legacy metadata, and preserves zero
confidence/model_proposed state. Negative tests cover missing refs, forged
identity/version/page/box/excerpt, contradictory source metadata and receipt
invalidation after normalized evidence changes. #15 corrections were inherited
through a normal merge retaining both histories and both documentation sections.
This revision uses synthetic PDFs, mocked responses and isolated test approvals;
no AWS call, real approval or new real-data accuracy claim is made.

The follow-up review 5122010147 is covered by test_review_trust_boundaries.py:
request/parser/source identity, roles/page counts, supplementary sources, complete
value-anchor binding, mutable self-reference, cycles, unrooted aggregates, loaded
and evaluated audit identities, and writer-failure sequencing. Seventeen negative
cases reproduced the earlier gaps before implementation; the distinct-cell DAG
positive case passed. Existing context, runtime-threshold and Decimal regressions
remain required. #16 inherited these fixes before the final merged delivery.

Confidence regressions in test_confidence_provenance.py cover target/comparable
threshold boundaries, multiple measured anchors, unknown/model provenance,
method-only claims, legitimate low-score confirmation with separate authorization,
and later score/provenance/citation changes. ADR 0008 defines the semantics before
these tests. The following #16 integration correction produces these fields
through official normalization rather than test-side repairs.

The review 5122010245 integration correction follows the normal merge of #15.
The full synthetic PDF/parser -> mocked Converse without local URI or legacy
evidence -> canonical normalization/assembly -> real confirm-facts subprocess ->
isolated approval store -> Controller path passes with raw scores unchanged at
zero. Unconfirmed, confirmed/unapproved and post-approval score/provenance/citation
changes stay blocked. A model cannot preserve forged measured provenance or
confirmation through normalization. Historical receipt tests prove no automatic
reuse, mutation or re-signing after the material extension. Native candidate
adapters remain candidate-only; no live accuracy or real approval is claimed.

Review 5557081140: A1/A2/B regressions use distinct actual synthetic cells and empty
raw text/excerpts for blanks; failures were assertions, not import/schema errors.
Review 5557168465: shared wrong-purpose tests cover criteria/reference/brief,
mixed references and another selected forms identity. Valid forms values with
criteria rules and reference procedures remain supported. ADR 0009 records the
requirement/module/gap/test/dependency map. Older real-document subset counts are
historical and were not rerun; no current whole-case accuracy claim follows them.

The dependent corrections reproduce C through actual synthetic PDFs/parser,
mocked Converse without local URI/legacy evidence, assembly, real confirmation CLI,
isolated approval and Controller. Wrong-role proposals remain visible and unresolved.
D tests cover every-side eligibility, multi-pair omissions and previously admissible
signed receipts using isolated test keys. E tests simulate missing pwd/getuid and
an unsupported platform in subprocesses; POSIX permissions are tested on the host
and Linux CI. No actual Windows runner or native Windows support is claimed.
All five initial dependent regressions failed through assertions before correction.

## Historical verification metrics for the final correction

Tests and cloud_tests are separate collection scopes. The former 305 and 358
totals meant 297+8 and 350+8, respectively, not additional cloud suites. The
final lower layer had 323 tests + 8 cloud_tests; the dependent layer had 402 + 8.
All passed locally on macOS/Python 3.13.5. The CI Test step collected tests only on
Ubuntu/Python 3.11; its following cloud step separately runs the eight cloud_tests.
Exact-head CI links and results belong in the PR delivery record.

With branch=true, coverage.py's Cover column combines executed statements and
branches: (covered_lines + covered_branches) / (num_statements + num_branches).
It is not pure branch coverage. Local lower-layer totals are 1544/1686 statements
and 341/424 branches, giving 89.34% combined (display 89%), 91.58% statements and
80.42% branches. Dependent totals are 2195/2527 statements and 523/718 branches,
giving 83.76% combined (display 84%), 86.86% statements and 72.84% branches.
The local tests-only and tests+cloud_tests JSON reports have identical file/hit
data in each layer, so adding cloud_tests does not explain the CI difference.

Historical CI at 8061a14/721d6a1 reported 90%/86% combined versus local 89%/84%.
The logs have different missing-statement counts (CI 117/268; local 136/316),
not merely different rounding labels. Runtime/platform and measured hits differ;
no isolated experiment establishes the cause. Final lower-layer CI at a303ed5
likewise reports 90% with 123 missing statements versus local 142. Preserve each
report's actual scope and environment rather than relabeling a percentage.
Ignored artifacts/fill-purpose-corrections contains before/after logs and the
coverage-tests.json / coverage-combined.json reports. No coverage setting,
assertion, exclusion or collection scope was weakened for these results.

Local lower/dependent runs report two/seven dependency deprecation warnings:
Starlette/httpx, AnyIO, and additionally PyMuPDF SWIG types in the dependent suite.
CI warnings and their exact counts are recorded from the latest job logs. These
warnings remain visible; no source-PDF subset or live service test was rerun.
