# Requirements to implementation and acceptance

## Issue 31 acceptance evidence, 2026-09-11

| Requirement | Implementation / test | Missing real evidence |
| --- | --- | --- |
| Complete scenario matrix and exact release | rehearsal-v1; test_rehearsal_evidence.py | 122 measured checks across 16 scenarios |
| Independent source/mode trust | Signed receipt verifier; CLI adversarial fixtures | Protected live collectors and independent reviewer |
| Runtime protocol | Actual #43 loopback process, five HTTP rejection probes | Successful browser/AWS execution |
| Observability | Nine alarms, dashboard, aggregate query; schema tests | Authenticated metric publishing and alarm transitions |
| Safe publication status | Plan/offline/localhost/live separation, synthetic remains Draft | Exact-head CI and all live dependencies |

[Draft PR #44](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/44),
[Issue 31 delivery](issue-31-delivery.md) and [cloud acceptance](cloud-acceptance.md)
retain missing dependencies; a CLI or mocked pass is not substituted for them.


## Issue 30 durable Runtime integration, 2026-09-11

| Requirement | Implementation / regression evidence | Remaining acceptance |
| --- | --- | --- |
| Process-independent authority | DynamoDBJobStore; shared contract, independent-client races and SDK errors | Live table/process replacement and IAM |
| Immutable fenced result | S3ResultStore; stale-first-object and reconstruction tests | Live S3 and publication authority |
| Recovery and bounded worker | runtime_jobs/runtime_worker; duplicate dispatch, lease/cancel/failure tests | Real dispatcher/Runtime/DLQ delivery |
| Current authorized source | runtime_sources; real local C2 admission/revocation tests | Reviewed principal/revision repositories |
| ARM64 and deployment | runtime Dockerfiles, build verifier, IaC tests/cfn-lint | Accepted execution bundle, scan review and create/update/rollback |

[Issue 30 delivery](issue-30-delivery.md) records direct PR #42 dependency and
separate local, emulated-service, container, CI and live scopes.


## Issue 21 integration, 2026-09-11

| Requirement | Implementation and test | Acceptance boundary |
| --- | --- | --- |
| Current authorized immutable source | DocumentSnapshotResolver; test_snapshot_extraction.py | Real local C2/parser; no AWS source access |
| Located low-confidence/missing questions | extraction_handoffs.py; test_extraction_handoffs.py | Candidate requests; human-task persistence pending |
| Bounded provider attempts and safe errors | Existing snapshot backend/preflight/ledger regression suite | SDK doubles; live access/routing pending |
| Frozen independent evaluation | extraction_evaluation.py; 104 scorer/CLI tests | Synthetic #23 source fixtures; real accuracy unmeasured |
| Tiny Chinese live probe | cloud_tests/extraction_smoke.py | Dry by default; explicit environment/budget gate |

[Issue 21 delivery](issue-21-delivery.md) records branch/base and dependency heads.
PR publication supplies the new head and its own CI URL. Historical sections below
are retained as evidence of their original scope, not substituted for current CI.

## Full-case goldens and reviewer acceptance (#23, 2026-09-10)

The [golden acceptance protocol](golden-acceptance.md) and
[ADR 0014](adr/0014-full-case-golden-contract.md) define one acceptance basis shared by the
whole-case review, the independent claim checker, the completion gate and the writer
boundary. Thirteen synthetic cases cover a complete case with cross-page evidence, a derivable
blank, a blank that must stay empty, an incomplete page inventory, an unevidenced field,
contradicting source cells, zero measured confidence, an unsupported criteria rule, two comparison contexts, unapproved
material and a three-step revision chain.

Expected values are authored from citations, rule bands, arithmetic and recorded
adjudications, and CI re-derives each one from its fixture. Reviewed manifests under
`tests/goldens/` are read-only for evaluation runs; `scripts/generate_goldens.py` writes them
only under an explicit flag and reports drift otherwise. Human task expectations are declared
against the frozen service contract; no task producer exists yet, and #24 and #17 own it.

Local checks at this checkpoint: ruff, ruff format, mypy and 756 repository tests pass, and
the golden check reports 14 manifests matching their fixtures. No remote write, commit, merge,
AWS call or real rule approval was performed, and no rule is claimed as formally approved.

## Local privacy integration

PR #37 supplies the reviewed local privacy SDK, encrypted exact-export mapping and local restoration boundary. Its isolated validation remains distinct from combined browser acceptance and real AWS acceptance. See [privacy acceptance](issue-22-acceptance.md).
## Local commit preflight (2026-09-09)

The user authorized categorized local commits after non-cloud verification, with
no push or PR creation. On `feat/controlled_model_selection`, the local suite
reported 926 passing tests, 89% coverage and two existing dependency deprecation
warnings. Ruff, format check, mypy, schema/fixture regeneration checks, 8 mocked
cloud tests, CloudFormation lint and both loopback HTTP/PDF smokes passed.
Live AWS services, browser integration and designated-model/human acceptance were
not exercised; their recorded deferrals remain unchanged. Real source documents,
generated artifacts, credentials and the local planning guide are not commit inputs.

## Phase 10 offline preparation only

`scripts/evaluate_controlled_workflow.py` compares the deterministic baseline and
an injected structured-response simulator using identical synthetic materials,
source/rule versions and budgets. It executes actual local task/correction/reentry
services, reports trace-backed calls, retained blockers and citation resolution,
and probes malformed/out-of-set output with zero tool effects. Costs, live latency,
semantic/full-case accuracy and human acceptance are explicitly unmeasured.

The [offline evaluation guide](offline-evaluation.md) defines metrics and limits;
the [future acceptance template](templates/designated-model-acceptance.md) reserves
the user-designated environment, corpus, independent reviewers and live evidence.
No live model or AWS calls are enabled. Phase 10 and full Issue 17 remain incomplete.

Validation: 9 new offline evaluation cases; full suite 926 passed, 89% coverage,
two existing dependency deprecation warnings. Ruff, formatting, mypy (88 source
files), fixture regeneration, 8 mocked cloud tests, CloudFormation lint and both
existing loopback HTTP/PDF smokes passed. The local comparison produced four
passing paired runs and two rejected pre-tool adversarial probes; those counts
are not designated-model or independent human acceptance.

## Phase 9 local consumer preparation only

`scripts/workbench_fixture.py` produces connected synthetic task, original value,
command, pause trace, accepted response, continuation, superseded task and before/
after review records through actual local services. The consumer tests check
identity joins, original/proposed/corrected/expected distinctions, evidence binding,
unresolved findings and command authority. Exported records retain existing model
versions; no competing frontend, API or DTO is added.

The [workbench handoff](workbench-consumer-handoff.md) supplies field mappings,
coordinate/privacy rules, full-review rerun disclosure and conflict/replay scenarios.
Browser/component tests and real authenticated API/evidence/correction integration
remain deferred to #24/#25. Phase 9 and full Issue 17 acceptance remain incomplete.

Validation: 19 new consumer cases; full suite 917 passed with 89% coverage and two
existing dependency deprecation warnings. Ruff, format check, mypy (88 source
files), fixture/schema regeneration, 8 mocked cloud tests, CloudFormation lint and
both existing loopback HTTP/PDF smokes passed. No browser acceptance, live model
calls, AWS resources or remote mutations were performed.

## Phase 8 local pause/resume preparation only

The local `PauseResumeService` / `PauseResumeRepository` seam reuses existing task,
revision, response and bounded-result contracts. Actual waiting results are bound
to stored task batches and their complete acyclic trace. The existing in-memory
response transaction records a continuation to a fresh pending run; it does not
dispatch, hold a waiting invocation or revive an old attempt. Local tests cover
real correction/recomputation, replay, stale replies, denied access, invalid trace
and task receipt, schema roundtrip and injected response-event failure.

Production persistence (#24/#29), outbox/leases/fencing/Runtime/cloud recovery
(#29), and fenced artifact/manifest publication (#28) are **deferred**, not passed.
Phase 8 and full Issue #17 cloud acceptance remain incomplete. See the
[ownership handoff](issue17-service-handoff.md) and [ADR 0017](adr/0017-local-pause-continuation-seam.md).

Local validation: 10 new pause/continuation cases; full suite 898 passed, 89%
coverage, two existing dependency deprecation warnings. Ruff, format check, mypy
(88 source files), exported-schema roundtrip, 8 mocked cloud tests, CloudFormation
lint and both existing loopback HTTP/PDF smokes passed. No live AWS calls, resource
creation, production recovery test or deployment was performed.

## Issue #17 local audit repairs (2026-09-09)

The audit regressions now cover invalid receipts with terminal snapshots, shared
selection/execution deadlines, in-flight provider exclusion, permanent tool denial,
review-to-human continuation, sanitized failed-selection trace, invented source
regions and genuine source-only preparation. Additional tests cover mixed causal
trace streams, preparation-to-review revision lineage and timeout run quarantine.

`test_controlled_case_integration.py` executes injected model and deterministic
selectors against the same real reviewer and human-task tools. The correction test
preserves original material, replays responses idempotently, creates fresh runs,
performs explicit fact confirmation and separate material signing, then recomputes
the actual verified correction rate. No live provider is contacted. Exported review
receipts now hash real deterministic results instead of a fixed placeholder digest;
their exact-material authority is explicitly synthetic, not a signed approval.

These repairs do not complete raw-document composition or the downstream durable
HTTP/job/browser work. Read [ADR 0016](adr/0016-controlled-execution-failure-boundaries.md)
alongside the earlier phase records; those records are not production acceptance.

Validation after local repair: 888 tests passed, 89% total coverage, with the two
existing Starlette/httpx and AnyIO deprecation warnings. The 19 new cases comprise
14 regression/boundary cases and 5 real-core integration cases. Ruff, format check,
mypy (87 source files), 8 mocked cloud tests, CloudFormation lint, existing HTTP
smoke and configured local-service smoke passed. No live provider or deployment
was used; no commit, push or remote mutation was performed.

## Issue #17 controlled-action phases 1–6 (2026-09-09)

Baseline main is `c3687e0cfe16cee0d38fcb4c1780a6092565aaeb`, the merge of PR #20.
Issue #17 and dependency Issue #9 remain open and unassigned; no open pull request
existed at re-baseline. Main CI run 34121829886 passed for that exact baseline.
The working branch preserves its pre-existing documentation-deletion commit, and
the local `plan.md` remains an untracked implementation guide.

Phase 1 adds ADR 0014 and exact `WorkflowSnapshot`, `AllowedActionSet`, typed action
arguments and causal `DecisionEvent` contracts. Admission now compares trusted
proposer/executor context with exact policy, snapshot, revision, rules, state,
prerequisites, source purpose and budget. Schema and synthetic consumer fixtures are
regenerated together under the explicit `controlled-action-v1` record version. Other
service-v1 records and the legacy synchronous HTTP, invocation, deterministic review,
audit and PDF contracts are unchanged.

Focused tests cover all four valid action argument forms, cross-action confusion,
unknown authority fields, stale state/revision/source bindings, duplicate registries,
actor separation and event state/causality/budget contradictions. These are contract
and pure-guard checks.

Phase 2 adds `application/action_policy.py`. Its trusted, provider-neutral policy derives
actions only from a detached `WorkflowSnapshot`, binds the exact policy/snapshot/revision/
documents/rules, filters source purposes to current versioned documents and accounts for
system versus model selection cost. An explicit state table returns no action for
terminal/waiting states or when a prerequisite, required blocker or step/model/retry/time
budget is missing. The verified outcome deliberately leaves eligible output to the
existing deterministic PDF gate. Focused tests cover every workflow state, deterministic and detached
derivation, wrong-purpose sources, exhausted budgets, duplicate/ambiguous policy rules
and execution-time rejection after the state digest changes. This is not evidence of
model selection, production tool execution, event persistence, human task transactions
or pause/resume.

Local Phase 1 verification used Python 3.13.12: 55 focused service-contract/revision
tests and 662 full repository tests passed with 87% combined coverage; the separate
cloud suite passed 8 tests. Ruff lint/format, strict mypy over 74 source files,
CloudFormation lint, actual localhost HTTP smoke, configured local service smoke and
the submission gate passed. The full suite reported two existing dependency
deprecation warnings from Starlette/httpx and AnyIO. Tests required only synthetic
material; no model, AWS service, deployment, real approval or remote mutation occurred.

After Phase 2, 38 focused policy tests and 700 full repository tests pass with 87%
combined coverage; the policy module has complete line/branch coverage. The separate
cloud suite still passes 8 tests. Ruff lint/format, strict mypy over 75 source files,
schema export checking, CloudFormation lint and both local service smokes pass. The same
two dependency deprecation warnings remain; no model, AWS service, deployment, real
approval or remote mutation was used.

Phase 3 adds the `ActionSelector` port, exact `SelectorInput`, deterministic local
baseline, injected Bedrock Converse selector and versioned selector prompt. Model output
contains only action identity, typed arguments and untrusted rationale; adapter-owned
identity, policy/snapshot binding, latency, attempts and valid paired token usage are
recorded on the proposal. Both selectors perform side-effect-free admission preflight.
Injected-client tests cover two state-dependent admitted branches, deterministic
baseline parity, no-action short circuit, unknown/cross-action output, invented pages,
prose/malformed/extra authority fields, refusal, truncation, timeout, throttling and
sanitized provider failure. No live model or tool executor is used.

Final Phase 3 verification used Python 3.13.12: 35 focused selector tests and 736
full repository tests passed with 88% combined coverage; the new selector modules have
97% combined coverage. The separate cloud suite passed 8 tests. Ruff lint/format,
strict mypy over 78 source files, schema export checking, CloudFormation lint, actual
localhost HTTP smoke, configured local service smoke and the submission gate passed.
The full suite reported the same two existing Starlette/httpx and AnyIO deprecation
warnings. No live model, AWS service, deployment, real approval or remote mutation ran.

Phase 4 adds provider-neutral snapshot/executor/trace ports, an exactly-one-action router,
`ControlledWorkflowCoordinator.decide_once` and the explicitly non-durable
`NonDurableInMemoryDecisionTrace`. The coordinator captures state before selection,
re-captures and re-admits immediately before execution, sanitizes executor/receipt
failures, captures resulting state/blockers, charges actual model attempts and appends an
event at decision time. Causal parents come from the current trace frontier. The executed
fixture is generated by running this coordinator with deterministic synthetic adapters.
Focused tests cover zero-call rejection across parser/extractor/reviewer/approval/writer
spies, exactly-once success, result digest, sanitized failure, invalid evidence receipt,
human waiting/task linkage, actual model-call metadata, causal order and trace integrity.
The legacy controller/audit/HTTP paths remain unchanged and no durable guarantee is made.

Final Phase 4 verification used Python 3.13.12: 11 focused coordinator/trace tests and
749 full repository tests passed with 88% combined coverage; the new coordinator, trace
adapter and execution ports have complete line/branch coverage. The separate cloud suite
passed 8 tests. Ruff lint/format, strict mypy over 81 source files, schema export checking,
CloudFormation lint, actual localhost HTTP smoke, configured local service smoke and the
submission gate passed. The same two dependency deprecation warnings remain. No live
model, AWS service, deployment, real approval, durable write or remote mutation ran.

Phase 5 adds `BoundedWorkflowRunner`, executable budget carry-forward, stable failure
categories, deterministic injected exponential backoff, canonical no-progress
fingerprinting and a concrete non-persisted `HumanReviewHandoff`. Model provider attempts
consume model-call and retry budgets; outer tool/selector retries and backoff are bounded;
snapshot providers cannot increase the current ledger. Permanent failures execute once,
repeated identical tool failures stop as no progress, and exhaustion retains the current
blockers, affected subjects, located evidence and last actual sanitized tool outcome.
Business `needs_review` creates the existing controlled human action and terminates in
`waiting_for_human`; it is not retried as infrastructure failure. Task creation,
response transactions and correction/re-entry follow in the scoped local Phase 6
delivery; durable persistence remains owned by Issue #9.

Final Phase 5 verification used Python 3.13.12: 10 focused bounded-runner tests and
761 full repository tests passed with 88% combined coverage; the bounded runner has
91% line/branch coverage and the complete Phase 1–5 focused set passes 145 tests. The
separate cloud suite passed 8 tests. Ruff lint/format, strict mypy over 82 source files,
schema/fixture export checking, CloudFormation lint, actual localhost HTTP smoke,
configured local-service smoke and the submission gate passed. The full suite reported
the same two existing Starlette/httpx and AnyIO deprecation warnings. No live model, AWS
service, deployment, real approval, durable task/write or remote mutation ran.

Phase 6 adds the purpose-specific `HumanTaskService`, trusted `MaterialSubjectMap`,
POSIX principal resolver and `NonDurableInMemoryHumanTaskRepository`. Local response
transactions recheck permission, task/run/revision/side/result bindings and actor-scoped
replay before atomically consuming a task, recording the accepted response, appending a
revision and queuing full review. Exact replay returns the original event; changed
payloads under the same key and competing stale responses cannot apply another correction. Historical
snapshots and original/proposed/corrected ledgers remain available through authorized reads.

Correction and explicit missing-evidence supply preserve scores, resolve the selected
forms and keep existing source anchors and units. They require subsequent confirmation
and exact-material approval. Sequential confirmation preserves only unchanged assertions
proven by accepted fact-response events for the same actor; imported confirmation labels
are insufficient. Rule changes clear those assertions. Separate material approval verifies
an existing local signed receipt, and publication authorization binds an actual verified
result without writing an artifact. Full local re-entry uses the existing `CaseReviewer`.
The acceptance test changes a proposed value from 9 m to 10 m, preserves the parent,
rejects obsolete authority, confirms, separately signs eligible synthetic material and
recomputes a verified 5 percent-point adjustment through the real deterministic core.

`create_from_handoff` resolves every supplied blocker through trusted finding/purpose/subject
bindings and stores the full task batch atomically. An unsupported or incomplete mapping
fails explicitly. Infrastructure-only handoffs without deterministic finding bindings
remain with the caller; this delivery does not fabricate findings or mount new endpoints.
Tasks, response events and queued work are process-local and lost on exit. Phase 6 uses
the plan's explicitly scoped local transactional-adapter deliverable; Issue #9 durability,
Phase 7 endpoint ownership, browser integration and live-model acceptance remain separate.
The new task/response/revision fixtures are generated by actual synthetic service execution.

Final Phase 6 verification used Python 3.13.12: 108 focused human-task/correction/repository
tests and 869 full repository tests passed with 89% combined coverage. The four new
application/adapter modules have 95% combined coverage. The separate cloud suite passed
8 tests. Ruff lint/format (182 formatted Python files), strict mypy over 86 source files,
schema/fixture export checking, CloudFormation lint, actual localhost HTTP smoke,
configured local-service smoke and the submission gate passed. The same two existing
Starlette/httpx and AnyIO dependency deprecation warnings remain. Synthetic material
and isolated local test signing exercise the existing approval boundary; no live model,
AWS service, deployment, real-case approval, durable write or remote mutation occurred.

## Issue #17 Phase 7 ownership handoff (2026-09-09)

Read-only reinspection of #9/#17 bodies and all comments found the new downstream
coordination: #24 owns authenticated persistent task/revision APIs, #29 owns
durable jobs and waiting/resume integration, and #25 owns the browser workflow.
All three follow-up bodies were inspected; all remain open, and no open PR was
returned at this checkpoint. Their issue numbers replace the historical M0
assumption that no backend/frontend follow-ups existed.

The [service handoff](issue17-service-handoff.md) records exact source links,
component/fixture reuse, migration requirements and unresolved endpoint, collection,
authentication, audit/resume-event and privacy boundaries. Phase 7 follows its
ownership-gated application/schema/fixture delivery path; it adds no runtime code,
new models, HTTP routes, remote OpenAPI or durable-success claims. Existing local
contracts and executable synthetic examples remain the handoff, not a substitute
for #24/#29/#25 integration tests, review, CI or merge.

Phase 7 verification reran all 869 repository tests, including schema/fixture
roundtrips, with the same two dependency warnings. Ruff lint/format, mypy and
actual localhost HTTP smoke passed. The restricted HTTP smoke could not create
a socket, and the restricted full-suite run was interrupted after making no
further progress; both passed when rerun with approved local execution permissions.
This documentation-only phase does not report a new coverage measurement or live
cloud/model acceptance. The submission gate and whitespace check passed; nothing
was staged, committed or published, and `plan.md` remains untracked and unmodified.

## PR #20 local P2 correction (2026-09-07)

The [P2 correction record](m0-p2-review.md) supersedes the earlier review's limited
native-lineage and service-diagnostic conclusions. It fixes method-only restoration
of extraction authority and preserves sanitized verification when preflight has no
case_review. Local checks pass with 655 repository tests, 8 cloud tests, quality
gates, unchanged legacy schemas and actual HTTP/invoke/run smoke. Raw confidence
and legitimate confirmation remain intact. The current published PR head is still
89e4c77eea67337cb94ef7f4f0d9afbf7645cef9; its successful CI precedes these local
changes. Commit/push/PR edits require separate approval and exact new-head CI.

## M0 pre-publication review snapshot (2026-09-07)

At the local review checkpoint, branch `feat/shared-service-contracts` had
base/HEAD and fetched main at `ea55043d90aa21e6f0a7e3fe05aa34ef8a3553d3`, with no
staged changes and zero outgoing commits. No remote write, CI rerun, merge, history
rewrite, AWS/model call or real rule approval had been performed. The original
checkout and its untracked cloud_tests copies were untouched. This dated snapshot
records local validation before publication; the actual commit, PR and latest head
CI must be verified separately after the authorized publication workflow.

The [review report](m0-review.md) covers all five review areas and records three
pre-fix assertion failures: forged system origin bypassing a model budget, stale
Controller result claiming an existing PDF, and same-page replacement passing
manifest validation. Fixes require independent trusted action origin and this
run's observed writer evidence with exact file/review/result/map binding. Original
confidence=0 confirmation, exact approval, core gates, explicit overwrite and all
old HTTP/invocation/PDF contracts remain intact.

Fresh validation under ignored `artifacts/m0-review/`:

- 638 repository tests passed with 7 dependency warnings and branch coverage enabled
  (combined coverage displays 87%); 8 cloud_tests passed without test warnings.
- Ruff/format/mypy passed (157 formatted Python files, 74 typed source files);
  both CloudFormation templates lint locally.
- Actual legacy/configured loopback HTTP smokes, invoke/run CLI, two reopened real
  synthetic PDFs, failed retry and needs_review preservation of existing output pass.
  JSON stdout/stderr and exit semantics are explicitly documented in the runbook.
- Schema/fixture export equality and roundtrips pass. Archived-main comparison
  confirms identical OpenAPI/request/run/InputManifest/PDF request/result schemas.
- The output/manifest.pdf filename denotes the second actual PDF; its machine
  manifest is artifacts[0] inside result-written.json, bound by that result's run.
  smoke-report.json records producer/path/response/hash/size/run mapping.
- Read-only issue recheck confirms #5/#7/#8/#9/#17 open and their latest original
  bodies preserved verbatim in unpublished update drafts. No B/C/E duplicate
  work item was found; proposed new issues remain unnumbered and unassigned.
- 73 relative documentation links/anchors resolve; both Mermaid diagrams parse;
  rendered PDF fields/borders and metadata pass inspection. Full submission checks
  pass for 157 blobs, configured identity, functional branch and all 11 drafts.
  Seven protected/core/gate files, including AGENTS.md, match main byte-for-byte.
  Checkpoint inventory: 13 modified tracked + 31 new = 44 files, no staged changes,
  zero outgoing commits.
- Main's #19 CI 34066702731 was rechecked successful on its old implementation head
  de2f691ba2adaa0733462e80027764303f7d25da. No M0 head CI existed at this pre-publication checkpoint.

Warnings are the existing Starlette/httpx, AnyIO and PyMuPDF SWIG deprecations;
no dependency upgrades, filters, coverage changes or weakened assertions. Earlier
624-test evidence below is retained as historical evidence with its own scope.
Issue publication is a separate later approval group after contract review.

## Historical initial M0 local handoff (2026-09-07)

Re-fetched main and remote refs: `ea55043d90aa21e6f0a7e3fe05aa34ef8a3553d3`.
New isolated worktree branch: `feat/shared-service-contracts`; base and HEAD are
that same full SHA. M0 changes are uncommitted working files at this handoff.
The original checkout's branch, tracked files and untracked cloud_tests copies
are preserved. No commit, push, PR/Issue/comment write, assignment, CI rerun,
merge, history rewrite, AWS call or real rule approval was performed.

Read-only GitHub inspection confirmed #15/#16/#19 merged and #5/#7/#8/#9/#17 open.
An all-issue search found eight existing issues (#3–#9 and #17), with no separate
frontend, human backend or formal-PDF follow-up. New issue drafts have no invented
numbers or assignees. Original issue bodies are retained as history in the five
prepared update bodies under ignored `artifacts/m0/publication/`.

| Merged delivery | Final implementation head | Merge / historical CI |
| --- | --- | --- |
| [Review #15](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/15) | `a303ed5bd869e4b493d4688b47b44bd7e2c69690` | `3cdc33a822be57707416959910e86bac4029e0cc` |
| [Extraction #16](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/16) | `cacb85b8ae587b74167d1717383aa87703eb6bb8` | `fefce2f2bd7f9fe60a8f63422714f5b993365ff9` |
| [Writer #19](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/19) | `de2f691ba2adaa0733462e80027764303f7d25da` | `ea55043d90aa21e6f0a7e3fe05aa34ef8a3553d3`; [CI 34066702731 succeeded](https://github.com/chengruchou/newtaipei-appraisal-review-ai/actions/runs/34066702731) |

#19's CI reports 562 repository tests and 8 cloud tests on its implementation
head. That is baseline evidence, not CI for this M0 change. M0 has no remote head
or new CI until commit/push are explicitly approved. Earlier review discussions
are preserved; they are not approval of new working code.

| M0 requirement | Code and focused evidence | Consumer / unresolved acceptance |
| --- | --- | --- |
| Shared external references/revision/run/task/action/result contracts | domain/service_contracts.py; schemas/service-v1.json; examples/service-v1; test_service_contracts.py roundtrip/schema/null/Decimal/version tests | A–E use one schema; DTO validity grants no authority |
| Immutable local material and old-authority invalidation | application/revisions.py; detached-copy/new-version/changed-native tests and real local receipt rejection | B must provide transactional append and validated correction mapping; continuous source snapshot remains D/E work |
| Trusted permission/version/action/idempotency semantics | application/service_guards.py; denial, stale side/result/version, principal isolation and action/source/budget tests | B/D reserved protocols in ports/service.py; no production repository or auth API |
| Callable real local composition | adapters/local/service.py, local_service.py; test_local_service.py actual parser/controller/writer and HTTP/invoke parity | B web app assembly and D Runtime composition remain open |
| Real artifact scope and blocked output | Real synthetic PDFs, zero raw confidence with explicit test confirmation/receipt; real LocalPDFWriter, reopened content/metadata/hash/field/context manifest; missing evidence zero writer calls | E formal CJK/template/full-case goldens and multiple-context migration; D S3 publication |
| Legacy compatibility | Existing full API/invocation/PDF tests; new factory parity; inspected main OpenAPI/AgentReviewRequest/AgentReviewRun schema equality | No public response change; separate run envelope only |
| Actual localhost behavior | scripts/http_smoke.py and scripts/local_service_smoke.py | 200 normal/written/needs_review, 422 malformed, 503 missing configuration; actual CLI JSON parses; no jobs/task/download routes |
| Import/config/source failures | Subprocess import without document access/SDK; wrong identity/version/hash/URI/template/map/font, stale receipt and machine-error category tests | No fallback, no web identity claim, no AWS calls |
| Documentation/ownership | README, architecture, MVP plan, service authority/runbook, ADR 0013, data/PDF and cloud docs | Corrects stale writer status, current B/E split and historical stacked dependencies |

Local verification uses macOS/Python 3.13.5, Pydantic 2, FastAPI 0.141.1,
pypdf 6.17.0 and ReportLab 4.5.1 in the isolated worktree environment.
Commands and raw evidence stay under ignored `artifacts/m0/`:

- `ruff check .`, `ruff format --check .`, `mypy src`: pass.
- `pytest --cov=appraisal_review --cov-config=pyproject.toml --cov-report=term-missing`:
  624 passed (562 inherited + 62 M0 regressions), 7 dependency warnings;
  combined statement/branch coverage displays 87%. This is not pure branch coverage.
  The branch coverage configuration and all existing assertions are unchanged.
- `python -m pytest cloud_tests`: 8 passed, no test warnings.
- `cfn-lint cloud_tests/image-stack.json cloud_tests/runtime-stack.json`: pass.
  IaC templates were not changed or deployed.
- Existing HTTP smoke: verified/not_requested, verified/simulated, needs_review,
  legacy detail and review error/OpenAPI contract passed.
- New localhost smoke: two real reopened synthetic PDFs, invocation/review parity,
  validation/config failures, unchanged source hashes and blocked no-output path.
  Manifest PDF SHA-256: `080270d6ed2ce7e6df04c6a33501a029082f256006c25b3365438ec363326a29`.
  Rendered inspection confirms readable +5.00% inside the synthetic cell and intact border.
- Mermaid parsed both current/target diagrams; arrows and pending labels inspected.
  Relative Markdown links/anchors resolve. The disposable diagram tools reported a
  whatwg-encoding installation deprecation; it is not a repository runtime dependency.
- Repository tests report seven dependency warnings: Starlette/httpx, AnyIO alias
  and PyMuPDF SWIG type/module deprecations. No warnings, coverage or assertions
  were suppressed to pass tests. Parser package advertising is isolated from CLI
  JSON stdout; that is not a test-warning filter.
- Full submission gate checks HEAD/index/working files, branch, configured identity
  and every draft publication file; there are zero outgoing commits. Core review,
  independent verifier, audit logger and the three protected files remain byte-identical
  to the inspected main. AGENTS.md, submission checker and published history are unchanged.

The test count and combined coverage are engineering evidence, not product completion
or real extraction accuracy. Actual Bedrock/model comparison, formal template/CJK,
full-case human goldens, live S3, source snapshot, multiple-context PDF and AWS jobs/
deployment were not performed: they need subsequent owner work, controlled inputs
and explicit cloud access/authorization. No real cases were authorized here.

See [contracts](service-contracts.md), [local runbook](local-service-runbook.md) and
[A–E milestones](mvp-plan.md). Historical delivery below retains its original named
head metrics and scope; it is not the current working-branch acceptance state.

## Historical pre-writer-merge snapshot (2026-09-06)

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

At that historical checkpoint the writer branch implemented the local writer
core and injected-client S3 wrapper for #5; #19 is now merged. Runtime selection, approved production field maps and
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
| Model-selected allowed actions and decision records | Exact contracts, policy/selectors, single-decision coordinator/router, bounded no-progress runner and non-durable causal trace | #17 | Phases 1–6 local components implemented; endpoint/workbench assembly and durable Issue #9 storage remain |
| Version-bound human tasks and re-entry | Purpose-specific task service, explicit subject correction, POSIX principal, atomic local repository and existing deterministic reviewer | #17/#9 | Correction/confirmation/approval/replay/re-entry exercised locally; B API/D persistence remain future work; local receipt is Linux/macOS only |
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
