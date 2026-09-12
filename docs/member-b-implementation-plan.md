# Member B implementation plan

Status: implementation started; see sections 7–9 for delivered changes and remaining dependencies.
Assessment date: 2026-09-12 (Asia/Taipei).
Sections 1–6 retain the original inspected baseline and implementation requirements.

## 1. Objective and inspected baseline

Deliver reliable case corrections, source-candidate ingestion, durable transactions,
and evidence-grounded calculations for the shared workflow. Provide immutable
calculation snapshots to A, D, and E. Official workbook rendering belongs to E;
API composition, shared contracts, authentication, and generated clients belong to A.

Requirements: the local five-person architecture plan, sections 3–5, 7–11, and
[repository instructions](../AGENTS.md). The source plan is currently an untracked
root file named `claude-five-person-architecture-plan.md`; preserve it. This document
records its requirements without treating its implementation assumptions as evidence.

| Item | Observed state |
| --- | --- |
| Branch | `feat/reviewer-workbench` |
| HEAD | `774ff6cba719e0235d95a103972f80293d46fdf3` |
| Last commit | `merge: preserve concurrent gateway recovery and strict problem matching` |
| Tracking comparison | `HEAD...origin/feat/reviewer-workbench`: 0 ahead / 0 behind, using locally cached refs |
| Initial working tree | No tracked modifications; only the untracked source plan |
| Runtime used for checks | Existing `.venv`, Python 3.13.12; imports verified to resolve to this checkout |
| Tool versions | pytest 9.1.1, Ruff 0.16.6, mypy 1.20.2 |
| Project baseline | Python 3.11+, Pydantic 2, FastAPI; CI selects Python 3.11 |
| Remote evidence | No fetch, live PR inspection, or CI inspection performed; cached equality does not establish current remote status |
| Running services | Not established: process visibility is restricted and socket inspection reported a netlink permission error |

Do not assume the runtime described in the five-person plan is present in this
branch. Only the existing source files and checks below establish this baseline.
Cached bytecode and ignored artifacts bearing newer module names are not source
implementation evidence. No service was restarted or stopped during inspection.

## 2. Source availability and real-case acceptance blocker

The three root PDFs were opened read-only and inspected through native text:

| Local source | Observation | Consequence |
| --- | --- | --- |
| Competition brief | One page; describes evidence-based appraisal review and case-specific criteria | It is not the six-page case source described by the team plan |
| Sample valuation forms | Six pages; Jinshan example | Cannot stand in for the Shulin four-subject acceptance case |
| Sample evaluation criteria | Nine pages; includes Jinshan commercial-land criteria | Cannot authorize Shulin residential rules |

A workspace inventory excluding dependency directories, caches, and container build
snapshots found no XLSX files. The three PDFs above were the only PDFs outside
`artifacts/`. Generated artifact PDFs were not treated as official source bundles.
No validated Shulin case/rule catalog was found in tracked source, tests, or docs.
This is a scoped availability finding, not proof that no other teammate has the files.

C must deliver the authoritative Shulin materials and source/version manifest;
A must identify the corresponding canonical case and runtime. Until then:

- Synthetic tests may validate transactions and arithmetic behavior.
- The local Jinshan documents may support parser inspection, not Shulin acceptance.
- Real correction, four-subject calculations, and official three-workbook acceptance
  remain blocked by source availability and human review.
- Preserve originals; do not commit PDFs, XLSX sources, real case JSON, raw OCR,
  generated exports, private locations, credentials, or cloud artifacts.

## 3. Requirement-to-code assessment

“Implemented” below describes core code, not production deployment or real-case acceptance.

| Requirement | Evidence in this branch | Assessment / next action |
| --- | --- | --- |
| Canonical response and server-named factor subject | `application/human_tasks.py`: `respond`, `side_subject_id`, `read_subject`; `domain/task_contracts.py` | Implemented for factor sides; preserve canonical subject naming |
| Exact replay before stale-version admission | `HumanTaskService.respond`; `LocalHumanTaskStore.commit_response` | Implemented, including authoritative store replay check; current permission gap below |
| Different payload under the same key conflicts | Same service/store; direct store and service tests | Implemented in the reference adapter |
| Rejection creates no revision/run | `_next_revision`, store `_apply`, receipt validator | Implemented; sole rejected task yields failed job, remaining open tasks keep it waiting |
| Correction and confirmation lineage | `_correct`, `_confirm`, `RevisionSnapshot.revise` | Partial: old snapshots survive and confirmation clears on revision, but new manual values are mislabeled |
| Current permission checked before replay | `_authorized` checks ownership and `REVIEW`; `admit_response` checks task permission after replay | Confirmed gap: revoking `CONFIRM` while retaining `REVIEW` still returns a successful replay receipt |
| Persistent task/revision/job/receipt/outbox transaction | `adapters/local/human_task_store.py`, `adapters/local/job_store.py` explicitly use in-memory state | Missing durable case transaction adapter; reference rollback is not cross-process durability |
| Existing SQLite capability | `adapters/local/document_storage.py` | Durable document-object storage exists, but does not implement the case/job transaction |
| Worker fencing and outbox semantics | `application/review_jobs.py`, `application/job_state.py`, `application/outbox.py`, `ports/jobs.py`, job-store contract suite | Reusable control-plane core; do not equate it with an assembled persistent worker runtime |
| District/use/date condition tasks | `HumanTaskService` handles FACT/CORRECTION through `FactSideReference`; `_next_revision` requires `task.side` | No canonical condition-subject write path found; A must extend contracts |
| Source-bound system candidate transaction | No case-ingestion operation/receipt implementation found in inspected tracked code | Missing; document transfer and extraction candidates are not this transaction |
| Exact receipt lookup after unknown outcome | Store has `read_receipt`; HTTP routes provide task/revision reads and response POST | No dedicated human-response receipt lookup route found; existing browser recovery uses the response path; agree recovery contract with A/D |
| Rule applicability | `domain/case_review.py` matches context, zone, district, land use, effective-date bounds | Implemented generic matching; Shulin approved rule data and condition-change integration missing |
| Interval/category/distance/matrix review | `domain/factor_engine.py`, `domain/factor_models.py` | Implemented generic engine and tests; matrix row is target grade, column is comparable grade |
| Decimal throughout rates and totals | Matrix cells and factor results use `float`; factor engine converts summed Decimal back to float | Does not meet the required decimal-string exchange contract |
| Whole-case coverage and completion gating | `CaseReviewer`, verification and approval adapters | Existing inventory, evidence, exact-material and missing-input gates must be retained |
| Official table 5/4 calculation pipeline | Arithmetic contract supports `sum` and `equals`; slots cover grades/rates/subtotals/totals | Not a complete price, weighting, and cross-table snapshot pipeline |
| Immutable calculation snapshot | `RevisionSnapshot` serializes material/revision; `CaseReviewResult` provides findings/coverage | Useful components exist; no combined persisted export snapshot with all required run/bundle/units/approval metadata found |
| Missing / not applicable / confirmed zero | `ObservedValue` separates missing and not-applicable; zero can be present | Partial: no explicit confirmed-zero semantic found; coordinate representation with A without conflating presence and confirmation |
| Error compatibility | `ServiceProblem`, FastAPI mapping | Existing error codes exist, but `ServiceProblem` has no request ID; A owns any compatible extension |

### Confirmed defects to address first

**B-01: revoked write permission bypasses replay authorization.**
Using the existing synthetic `Harness`, confirm a fact with the authorized principal,
then repeat the same command with the same actor/case but permissions reduced to
`{Permission.REVIEW}`. The second call returns the original receipt. This is a
successful replay, not a second mutation, but violates the team's explicit
“revoked permission still rejects” rule. Check the current action/task permission
and human actor before the replay lookup without moving stale-version checks ahead
of replay. Test revoked case access, REVIEW, CONFIRM/CORRECT, and nonhuman actors.

**B-02: manual correction has no truthful provenance representation.**
`Reliability.method` permits only `native_numeric`, `reviewer_confirmed`, and
`model_proposed`. `RevisionSnapshot.revise` assigns `model_proposed` whenever native
lineage cannot be retained, including human corrections. Existing
`test_revision_authority.py` assertions encode this behavior. A must introduce a
compatible provenance/confirmation representation; B must update revision logic
and tests while preserving protection against fabricated native authority.

**B-03: raw confidence and adopted-value state are not fully separated.**
`_correct` overwrites the child observation's value/raw text and can lower its
confidence with `min(original, proposed)`. Parent snapshots and the change ledger
retain prior data, but the new material does not expose a separate unchanged raw
observation alongside the adopted value. Preserve raw confidence exactly, including
zero, and record manual adoption separately. Do not simply raise confidence or
rename the current observation without checking downstream trust digests.

**B-04: floating-point rates cross the calculation boundary.**
`CorrectionMatrix.values`, `FactorEvaluationResult.adjustment_percent`, and
`EvaluationSummary.total_adjustment_percent` are floats. Merely serializing those
floats as strings later does not restore source decimal precision. Migrate the
calculation path with A's compatibility decision and round-trip tests.

## 4. Implementation order and ownership

The identifiers below are proposed work items, not existing issue numbers. B must
not create remote issues or PRs without explicit authorization.

### P0 — Confirm the integration baseline and fix replay authorization

1. Obtain A's `TEAM_BASE_SHA`, `CONTRACT_VERSION`, file ownership map, runtime
   entrypoint, and this round's stop time. Do not reset to a historical SHA.
2. Use a separate functional branch/worktree for implementation after A assigns
   the baseline, for example `fix/case-response-transactions`. Do not rename or
   overwrite the current shared workbench branch.
3. Add focused regressions for B-01; fix permission ordering in the existing service.
4. Preserve exact replay, different-payload conflict, rejection, and stale-parent behavior.

Deliverable: a small reviewable patch with regression results. This can proceed
without Shulin data or cloud access; do not wait on those blockers.

### P1 — Agree minimal shared-contract changes with A

Send A a concrete schema change proposal covering these semantics; A owns the edits:

| Contract topic | Required decision |
| --- | --- |
| Manual adoption | Separate original extraction, adopted typed value, and confirmation; preserve existing serialized records or specify migration |
| Conditions | Server-created subjects for district, legal zoning, actual use, rule-use category, and effective date; no fabricated factor ID |
| Candidate operation | Case/source bundle, expected revision/run/fence as applicable, operation ID, canonical payload digest, system-origin receipt and unknown outcome |
| Receipt recovery | Authorized exact lookup/replay behavior, key namespace, payload mismatch, and unknown vs known-not-committed distinction |
| Decimal and zero semantics | Exact decimal-string values, explicit units, missing/not-applicable/confirmed-zero semantics, compatibility with legacy clients |
| Calculation snapshot | Case/run/revision, source bundle and hashes, rules, adopted inputs, per-row operations/units, coverage, blockers, approval scope, immutable identity |
| Draft output | Explicit opt-in, independent draft/ready gate, authorized snapshot access; do not overload PDF-only artifact fields |

Update contract documentation and add a durable ADR for changed trust boundaries
with A. Regenerate OpenAPI/TypeScript through A's existing pipeline. B must not
create a parallel DTO family to bypass shared ownership.

### P2 — Correct provenance and implement condition revisions

Likely B-owned edits: `application/human_tasks.py`, `application/revisions.py`,
related application services and tests. Domain contract edits remain A-owned.

- Apply the agreed raw/adopted/confirmation model; explicitly support
  native → confirmed → corrected → confirmed without restoring native trust.
- Keep citations server-validated. The current path intentionally rejects citation
  substitution and requires existing type/unit metadata; adding missing values or
  new citations needs authoritative metadata, not a relaxed client-controlled check.
- Apply district/use/date changes atomically with the new material revision.
- Re-select the complete applicable rule bundle; unsupported scope remains blocked.
- Invalidate affected calculations, tasks, confirmations, and approval applicability.
  Keep historical snapshots/receipts readable under authorization. Existing exact-material
  approvals may remain valid for historical material; never apply them to the new head.
- Retain conservative confirmation invalidation until dependencies justify narrower reuse.

Deliverable: one real human correction/confirmation, original observations intact,
new revision visible, same-payload replay returning the same receipt. If real sources
are unavailable, explicitly report only synthetic regression completion.

### P3 — Implement persistent transactions using the existing ports

The choice of storage is a proposed integration decision, not an existing capability.
For the controlled single-host workflow, propose a SQLite adapter unless A supplies
an already verified durable case store. Keep domain/application logic provider-neutral.

- Implement the existing human-task and job-store contracts against a shared
  transactional database; do not wrap an in-memory job store in a durable task store.
- Commit task response, material snapshot, head CAS, sibling supersession, next run,
  receipt and outbox effect together. Rejection has no next revision/run/outbox.
- Persist accepted response/audit information sufficient to reconstruct the operation;
  a receipt containing only result metadata is not the complete response ledger.
- Recheck key/digest, task state/version, head/material, actor binding and run/fence
  inside the transaction. Define how current authorization is revalidated at admission
  and, where authoritative grants are persisted, at the commit boundary.
- Persist immutable result references and candidate receipts with explicit uniqueness
  constraints; specify schema version, initialization/migration and rollback behavior.
- Test two connections/processes, lock contention, cancellation, crash boundaries,
  outbox recovery, and fresh-process reopen. Retain existing store conformance tests.
- Use a private persistent local disk. No S3-hosted SQLite file or untested replicas.

A wires the adapter into the API and actual worker; C prepares any deployment
environment. Do not build a second queue or replace the existing state machine.

Deliverable: restart evidence for task/head/revision/receipt/outbox consistency,
plus adapter and failure-injection tests. In-memory tests alone cannot satisfy this gate.

### P4 — Add the sole source-candidate ingestion transaction

- Consume C's source/rule catalog through A's agreed contract.
- Bind case, document versions/hashes, subject roles, bundle version and operation ID.
- Canonicalize the payload before hashing; an exact replay returns the same system
  receipt with no duplicate revision or task. Changed payload under the same ID conflicts.
- Validate current head/attempt before accepting worker results; stale workers cannot
  publish into a newer case. A candidate is not a verified fact or approved rule.
- Persist known failure and recover unknown outcomes by exact operation lookup.
  Never convert a timeout into success or retry with a new operation ID blindly.
- Use distinct system actor/receipt semantics, never a forged human ResponseReceipt.

Deliverable: one source-bound batch committed once, replay/conflict/unknown/stale
cases demonstrated. Local real-source operation is sufficient for this transaction;
AWS end-to-end acceptance remains a separate team gate.

### P5 — Produce a minimal snapshot, then complete grounded calculations

Provide the minimum immutable snapshot as soon as P1–P3 permit D/E integration;
do not wait for every missing price input. P4 can be integrated incrementally.

1. Reuse `CaseReviewer` and `FactorRuleEngine`; consume C's reviewed rule data.
2. Preserve Decimal from rule ingestion through arithmetic and storage. Add exact
   serialization, round-trip, rounding and unit tests; A manages compatibility.
3. Bind all four Shulin subjects by explicit role. Do not derive roles from page order.
4. Calculate only source-supported table 5/4 dependencies. Existing `sum`/`equals`
   do not define a complete valuation formula. Add versioned supported operations
   only when C supplies reviewed formula, weighting, rounding and source references.
5. Record each input, normalized unit, rule/version, matrix axes, operation and result.
   Define duplicate-factor ownership so zoning/BCR/FAR cannot count twice.
6. Missing parcel data, weights or FAR methodology produces blockers, not zero,
   equal weights, an inferred ratio, or an invented price.
7. Keep 5 percentage points semantically explicit: E renders table 5 as `5` and a
   table 4 percentage-format cell as `0.05`. Rendering does not recompute business logic.
8. Bind draft/ready and approval scope to the exact snapshot; preserve formal PDF gates.

Deliverable: E consumes the snapshot directly for all three tables; no exporter
calculation or approval is needed. Insufficient data permits a grounded draft only.

### P6 — Real-case acceptance and freeze

With A/C/D/E and a human reviewer, demonstrate:

- Shulin case with correct target/comparable roles and fixed source/rule versions.
- Human confirmation and correction with truthful original/adopted-value provenance.
- Exact retries, changed-payload conflict, revoked permissions, and unknown recovery.
- Condition changes invalidate current approval applicability and stale worker output.
- Missing/unsupported input blocks formal completion; supported calculations have traces.
- A persistent snapshot survives API/worker restart without duplicate execution/publication.
- E's three official exports use that same snapshot and pass human value/unit checks.

Record tested SHA/patch, commands, exact results, private evidence references, remaining
blockers and the owner of each blocker. Preserve distinct statuses for B core delivery,
real-case acceptance, formal valuation, AWS, deployment, and platform submission.

## 5. Validation performed during this assessment

These checks examine the existing code. Passing tests do not implement the missing
features, and current provenance tests intentionally assert behavior that must change.

| Check | Result |
| --- | --- |
| `.venv/bin/python -m ruff check src tests` | Passed |
| `.venv/bin/python -m ruff format --check src tests` | Passed; 149 files already formatted |
| `.venv/bin/python -m mypy src/appraisal_review` | Passed; 102 source files |
| Focused non-HTTP baseline below | 330 passed |
| Case-review and repair subset excluding the identified HTTP cases | 113 passed, 3 deselected |
| Synthetic revoked-CONFIRM replay reproduction | Returned the original receipt; requirement violation reproduced |
| Broader 455-item selection including human-task HTTP tests | Incomplete: stalled at `test_subject_endpoint_is_opt_in_and_exactly_matches_openapi`; bounded diagnostic run exited 124 |
| Live browser/API/worker, restart, real Shulin, official Excel, AWS, remote CI | Not verified |

The initial broad run was interrupted after it stopped making progress. A second
run used a 45-second timeout and `faulthandler_timeout=15`; the stack showed AnyIO
worker/asyncio waiting in the HTTP regression. This does not establish whether the
cause is the constrained execution environment or the application. A further subset
including `test_case_review.py` also timed out (exit 124). Isolating case review and
repair tests with `-k 'not http_composition and not subject_endpoint_is_opt_in'`
completed: 113 passed, 3 deselected. These exclusions are diagnostic, not waived gates.
Do not label these runs passed or silently omit them from final integration acceptance.

Reproducible passing baseline command:

```bash
timeout 30s .venv/bin/python -m pytest -o addopts='' -v -o faulthandler_timeout=10 \
  tests/unit/test_human_task_service.py \
  tests/unit/test_human_task_store.py \
  tests/unit/test_revision_authority.py \
  tests/unit/test_factor_engine.py \
  tests/unit/test_rule_engine.py \
  tests/unit/test_job_state.py \
  tests/unit/test_job_failure_injection.py \
  tests/integration/test_job_store_contract.py
```

Additional completed diagnostic command:

```bash
timeout 30s .venv/bin/python -m pytest \
  tests/unit/test_case_review.py tests/unit/test_human_task_review_regressions.py \
  -k 'not http_composition and not subject_endpoint_is_opt_in'
```

For implementation, add tests for every new rule/state transition. In addition to
the existing suite, cover manual lineage, unchanged raw confidence zero, canonical
condition subjects, stale source batches, receipt unknown recovery, true persistence,
Decimal boundary/round-trip behavior, missing versus confirmed zero, double-count
prevention, snapshot identity, and formal-completion gating. A runs final integrated
checks on the frozen version, including Python 3.11 and generated-client consistency.

## 6. Handoff, schedule and publication boundaries

Every 45 minutes send A a small reviewable increment using the source plan's format:

```text
Role: B
Team baseline SHA / branch head or patch:
Contract version / changed files:
Usable behavior:
Real sources and human verification scope:
Commands / test results / output hashes:
Untested items / blockers / next owner:
One objective for the next 45 minutes:
Running processes / stop instructions:
```

The source plan records September 13 milestones in Asia/Taipei: feature freeze
10:30, fixed demo 11:00, report 12:00, initial upload 13:00, read-back 13:30,
submission before 14:00. A must confirm this round's T0 and stop time; an earlier
agreed stop remains binding. These are coordination deadlines, not evidence that
the missing durable runtime and real-case calculations fit a 4–6 hour estimate.

B's final package is the implementation patch, focused test evidence, durable
transaction/recovery evidence, snapshot contract/consumer handoff, and explicit
unresolved source/formula inputs. E owns final workbook files and human workbook QA.

Repository documents, code, comments, schema keys and new filenames must be English.
Preserve useful existing modules and other contributors' changes. Do not modify
global tools or files outside the repository. Shared-contract changes need updated
documentation and an ADR when the decision is durable.

Local implementation authorization does not authorize commit, push, PR mutation,
merge or publication. Obtain separate explicit approvals for those actions. Use the
configured human Git identity, functional branch names, and no automated authorship
attribution in submitted material. Before any approved submission inspect complete
outgoing content/metadata using [submission checks](submission-checks.md).

The original assessment created only this planning document. It did not claim a commit,
remote update, production deployment, formal valuation, or competition submission.

## 7. First implementation increment — 2026-09-12

Working branch: `fix/case-response-transactions`, created locally from the inspected
HEAD `774ff6cba719e0235d95a103972f80293d46fdf3`. No commit, push or PR action.
The source plan and prior local changes were preserved. A's team baseline, contract
version, ownership confirmation and round stop time have been requested but not
provided; this local baseline is not represented as A's approved integration base.

| Work item | Current status |
| --- | --- |
| P0 / B-01 | Implemented: current task write permission and human actor checked before receipt replay |
| B-03 raw score preservation | Implemented within the existing contract: the stored confidence is never lowered or raised by a proposal |
| P1 contract proposal | Written in `member-b-contract-handoff.md`; ready for A to review, not applied to shared schemas |
| B-02 manual provenance | Pending A's compatible raw/adopted/confirmation contract; corrected values still use the existing model_proposed representation |
| Full B-03 raw/adopted separation | Pending A's model decision; immutable parent snapshots and ledger continue preserving originals |
| B-04 Decimal migration | Pending A's shared-model and compatibility decision |
| P2 condition revisions | Pending canonical condition-subject contract |
| P3 durable transactions | Design handoff prepared; no durable case adapter implemented or runtime selected with A |
| P4 candidate ingestion / P5 export snapshot | Pending operation/snapshot contracts and reviewed rule/source bundle |
| P6 real-case acceptance | Not performed; verified Shulin materials and human review remain unavailable |

Code changes are confined to `application/human_tasks.py`. New focused regressions
are `tests/unit/test_response_replay_authorization.py` and
`tests/unit/test_correction_raw_confidence.py`. The existing correction service test
now asserts exact raw score preservation. Documentation includes
[ADR 0052](adr/0052-response-replay-authority-and-raw-confidence.md), the updated
[human-task behavior](human-task-review-repair.md), and
[the contract handoff](member-b-contract-handoff.md).

Red/green evidence: before the fixes, the new service authorization matrix produced
6 failures / 8 passes, and the raw-confidence matrix produced 5 failures / 10 passes.
After the fixes, both pass. HTTP assertions were corrected to include the existing
service-v1 schema field; the application error contract was not changed. An additional
trust regression proves that preserved high raw confidence does not authorize a
corrected value or transfer the old material approval.

Final validation:

- Ruff check over `src tests`: passed.
- Ruff format check over `src tests`: passed, 151 files.
- mypy over `src/appraisal_review`: passed, 102 source files.
- Focused transaction, API, revision, rules, case-review and job suite: 487 passed,
  one upstream Starlette/AnyIO deprecation warning, no deselections.
- The earlier HTTP stalls did not reproduce outside the restricted sandbox: the
  125-item HTTP/case subset and final focused suite completed. This establishes
  test execution in that environment, not a live production/runtime acceptance.
- No live cloud requests, real human confirmations, workbook exports or full
  repository/remote CI acceptance were performed.

Exact final test command (executed outside the restricted sandbox):

```bash
timeout 45s .venv/bin/python -m pytest \
  tests/unit/test_response_replay_authorization.py \
  tests/unit/test_correction_raw_confidence.py \
  tests/unit/test_human_task_service.py \
  tests/unit/test_human_task_store.py \
  tests/unit/test_human_task_review_regressions.py \
  tests/unit/test_human_task_api.py \
  tests/unit/test_revision_authority.py \
  tests/unit/test_factor_engine.py \
  tests/unit/test_rule_engine.py \
  tests/unit/test_case_review.py \
  tests/unit/test_job_state.py \
  tests/unit/test_job_failure_injection.py \
  tests/integration/test_job_store_contract.py
```

Next owner: A to settle the handoff contracts and shared storage/runtime choice;
C to supply verified case/rule sources. Next B implementation target: canonical
manual adoption and condition revisions after A's compatible contract is available.
No application services or test processes were left running by this increment.

## 8. Second implementation increment — local persistence

Branch and HEAD remain `fix/case-response-transactions` at
`774ff6cba719e0235d95a103972f80293d46fdf3`, with local working-tree changes only.
The user's continuation authorizes this adapter implementation; no shared schemas,
ports, API routes or default runtime entrypoints were changed.

P3 now has an implemented optional SQLite adapter for the existing contracts.
This supersedes section 7's “no durable case adapter implemented” status, while
runtime selection and real-case integration remain with A. Delivered files:

- `adapters/local/review_database.py`: private database, versioned typed rows,
  BEGIN IMMEDIATE transactions, bounded lock waiting and sanitized failures.
- `adapters/local/sqlite_job_store.py`: JobStore and ResultStore implementations,
  existing state-machine transitions, persisted job policy, immutable result bodies,
  leases, attempts, outbox and submission receipts.
- `adapters/local/sqlite_human_task_store.py`: canonical human-task storage,
  immutable material/revisions, full accepted-response ledger, exact receipts,
  atomic revision/run/outbox effects and atomic worker waiting/task registration.
- [ADR 0053](adr/0053-local-review-transactions.md) and
  [composition guide](local-review-storage.md).

Each operation reads database state; neither reference in-memory store is used as
the implementation's source of truth. Worker handoff uses `finish_with_tasks` in
one transaction and validates fixed document versions/hashes. A response checks
the current job run/open-task membership as well as task version, material/side
digests, ownership and replay key. Expired attempts cannot write before or after
reclamation. The adapter retains job-scoped heads under the existing ports; a
cross-job case-wide editing policy is not introduced implicitly.

Evidence from new tests:

- All 31 existing JobStore conformance checks pass against SQLite.
- Independent API/service/store instances reopen the same receipts and revisions.
- Two subprocesses answering one task create one new revision; exact same-key
  operations recover one identical receipt and different-key races conflict.
- A subprocess exit after response insertion but before commit leaves no partial
  answer/run/outbox. Exit after commit but before delivery recovers the exact receipt.
- Exceptions and injected cancellation after each response write stage roll back
  all record groups; the original request can subsequently succeed once.
- Fixed source mismatches, stale task/run/version/digest, missing/extra revision
  effects, expired leases and revision replacement are refused without partial writes.
- Result bodies/reference publication, authorized reads and outbox dispatch survive
  reopen. Private paths, incompatible schema, persisted policy and lock contention
  have focused coverage.

Final combined B regression command extends section 7's command with:

```text
tests/unit/test_review_database.py
tests/integration/test_sqlite_job_store.py
tests/integration/test_sqlite_human_tasks.py
```

Result: **571 passed, 1 upstream Starlette/AnyIO deprecation warning**, no exclusions.
The final combined run used a 60-second bound outside the restricted HTTP test
sandbox and completed in 2.96 seconds. Ruff check passed; Ruff format check passed
for 158 files; mypy passed for 105 source files. Database tests use synthetic data
in temporary paths and include real local subprocess exits. Initial test-only
issues (a subprocess using the real clock instead of the fixture clock, and an
incorrect service method name in a test) were corrected before the final run.

Remaining limits: no default runtime was configured, no actual worker loop was
deployed, no new condition/manual/Decimal/candidate/export contract was introduced,
and no real Shulin case or official workbook was accepted. Storage targets a small
single-host private workload with synchronous transactions and namespace scans.
There is no automatic migration from another runtime's state, cloud deployment,
case-wide multi-job editing authority or production capacity claim.

Next increment: A can compose the existing services with these adapters using
`local-review-storage.md`, or select the already tested team runtime if one exists.
B can then implement the agreed condition/manual/candidate contracts without a
second database or API architecture. C's verified Shulin materials and human
review remain prerequisites for real-case acceptance. No commit, push or PR action
has been performed, and no background process was left running.

## 9. Current batch scope — privacy processing is not applicable

The user's latest batch instruction supersedes earlier privacy prerequisites for
these configured standard formula documents. The workflow is login, direct
upload/import, parsing, data/rule verification, calculation, official Excel filling,
conversion of that same Excel to PDF, then preview/download. There is no additional
user privacy confirmation, masking, scan wait or restoration for this batch.
Existing work and iteration history remain intact.

B inspected job transitions, response handling and CaseReviewer: none has a privacy
prerequisite. NEEDS_HUMAN retains its real data/rule review meaning. The actual
integration blocker resides in A/C's document-transfer service and shared types.
See the [affected files and cross-role handoff](formula-batch-integration.md).

B delivered internal SourceProcessingScope configuration and SQLite run bindings:

- Exact case/document/version/hash/purpose and the complete source set determine
  applicability. The recorded value is not_applicable, never privacy passed.
- Scope definitions are versioned and immutable. Submission binds them atomically;
  replay retains its original setting and correction continuations inherit it in
  the same revision/run/outbox transaction.
- Historical definitions are not active defaults for future submissions. A/C must
  configure the actual resolved sources; no production identities were invented.
- Case permissions, correction versions, payload replay, formula/source validation
  and content reapproval retain their existing checks. No calculation is approved
  by the scope record and no public request exemption flag is added.

See [ADR 0054](adr/0054-scoped-source-processing.md). The focused B regression
command from section 8 now also includes:

```text
tests/integration/test_source_processing_scope.py
tests/integration/test_document_transfer.py
```

Result: **699 passed, 1 upstream Starlette/AnyIO deprecation warning** in 3.59
seconds. The run used the same bounded local HTTP test environment as section 8.
Ruff and format checks passed for 160 files; mypy passed for 106 source files.
Tests cover submission/continuation rollback, replay/restart, future-source
isolation and real deterministic review with missing, low-confidence, unsupported,
incorrect and unapproved inputs. All inputs are synthetic.

Remaining integration: A/C must remove this configured batch's attestation and
sanitized-only source-contract prerequisites; A/D must synchronize direct upload
and its UI projection; E/A must implement/verify same-workbook PDF conversion.
B's optional adapter is still not the default runtime. No complete batch upload,
real-case calculation or official Excel/PDF acceptance is claimed. Changes remain
local; no commit, push, PR action, work reset or timer reset was performed.

## 10. Publication verification against the requested base

The user explicitly authorized commit, push and a PR targeting
`feat/local-review-workbench`. Its fetched tip is
`728f40c3086adaac549bb90e2166a9d90dd7760c`, which descends from the original
implementation baseline and includes substantial later integration work, including
`adapters/local/sqlite_review_store.py` and an expanded HumanTaskService.
The 699-test result above applies to this implementation checkout, not to the
combined target branch. Runtime convergence with that existing SQLite adapter and
regression testing against the updated base remain integration work. Publish as a
draft for review; do not claim readiness to merge or duplicate-runtime deployment.
The new ADRs use 0052–0054 to avoid the target's existing 0046–0048 decisions.
The unrelated local .gitignore edit and original source plan are not submitted.
