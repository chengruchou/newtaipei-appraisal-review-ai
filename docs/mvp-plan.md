# Delivery plan and ownership

Baseline: main `c3687e0cfe16cee0d38fcb4c1780a6092565aaeb`, rechecked 2026-09-09.
PRs #15/#16/#19/#20 are merged. Issue #17 Phases 1–6 extend the shared contracts and
implement policy, selectors, explicitly composed single-decision execution/trace and a
bounded local runner, plus local human tasks, corrections and deterministic re-entry.
The process-local transactional adapter does not provide durable storage. The historical parent
#3 remains closed, not an active catch-all milestone. No role below implies a GitHub
assignee.

## Five workstreams

| Steward | First concrete delivery | Start from / provides | Dependencies and acceptance |
| --- | --- | --- | --- |
| A: project lead | Bounded model action selector with actual execution/rejection records and real-model comparison plan | WorkflowSnapshot, AllowedActionSet, typed ActionProposal, DecisionEvent and Budget; produces trusted execution events and evaluated model/prompt versions | Use E's independently reviewed goldens; reject invalid actions with zero effects, preserve gates, report actual calls/source accuracy/latency/cost when measured |
| B | Authenticated local task/response API that appends a new revision and reruns review | Phase 6 HumanTaskService, MaterialSubjectMap, POSIX principal and non-durable transactional repository; provides remote identity/permission and application assembly | Reuse the local task semantics and M0 core; coordinate durable guarantees with D and real endpoint consumers with C |
| C | Fixture-backed workbench, then real B task API integration | service-v1 schema, public citations/values, findings, task permissions and manifests | Display original/proposed/corrected values and honest statuses; authorized page navigation; require B actual command endpoints before claiming a human-review loop |
| D | Authorized document resolver and local transaction tests for job/outbox/task store, then Runtime wiring | DocumentReference/Resolver, ReviewSubmission, JobRepository, RunReference | Check case access before URI lookup; principal idempotency, leases/fencing/recovery and manifest-only results; consume B human transitions and E output scope |
| E | Formal template/font/map preflight plus independent full-case golden inventory | Existing PDF contract, ArtifactManifest context/field coverage; provides human standard answers and versioned output contract | Coordinate multiple-context migration with B/D/C; preserve original sources, evidence/tolerance/cross-form assertions; no live accuracy claim from small native subsets |

A owns model strategy and comparisons; E owns independent goldens, formal PDF and
complete-case acceptance. B owns human backend and app composition; D owns durable
jobs/documents/deployment; C consumes agreed APIs. Shared contract stewardship and
provider/consumer detail is authoritative in [service contracts](service-contracts.md).

## Milestones

| Milestone | Deliverable and dependencies | Exit evidence |
| --- | --- | --- |
| M0: foundation | Reuse merged review/extraction/writer; shared v1 models/ports/fixtures and real local factory | Roundtrips/digests, permission/version rejection, real parser -> writer -> reopen, actual HTTP/invoke, blocked zero writes and unchanged old API; formal template/account/model tests are subsequent owner work |
| M1: local human loop | Phase 6 local task transaction/re-entry, B actual task/identity/revision API and C workbench; coordinate durable storage with D | Browser -> task -> response -> new immutable revision -> obsolete approval denied -> explicit new authority -> subsequent review; concurrency, duplicate-conflict and source-access tests |
| M2: real document/output acceptance | A controlled model trials using E goldens, complete required fields/rules/cross-form review, E formal font/map and reviewed multiple-context contract | Per-field and source accuracy, wrong/missing/blank comparisons, human corrections and full-case outcomes; insufficient inputs remain needs_review |
| M3: AWS integration | D uploads/IDs/jobs/outbox/queues/Runtime/recovery, B durable human handoff, E validated outputs and A bounded policy | Authorized upload -> fixed job -> Runtime -> persisted human wait -> new run -> fenced manifest/query/download; duplicate, interruption, stale version/attempt and cleanup evidence |

Phase 6 supplies the local application transition and process-local transaction
needed by M1; it does not supply browser/API integration or durable persistence.
M1 can begin from fixtures while D designs durable persistence. It is not complete
until C talks to B's actual API. M2 needs explicit model access and human goldens,
not just mocked Converse. M3 needs designated profile/SSO, Region and expected
account/role, explicit deployment authorization and real cloud evidence. No M0 AWS
call is needed. An attempt ends while awaiting a human; response never revives its
old session/lease. The current single-context writer refuses multiple-context
output until E's contract migration is reviewed and all consumers are updated.

## Current Issue #17 downstream handoff

The 2026-09-09 Phase 7 ownership recheck found existing follow-ups: #24 owns the
authenticated persistent task/revision API, #29 owns durable jobs/waiting/resume,
and #25 owns the browser workflow. The Issue #17 coordination comment explicitly
requires review, CI and merge before those integrations. Do not create duplicates
or implement their deployment scope on this branch. See the
[service handoff](issue17-service-handoff.md) for links, reusable components,
consumer migration and unresolved transport contracts. No new routes are mounted.
The issue-specific assignments supersede the historical five-workstream shorthand
for these downstream deliveries.

## Historical M0 issue handoff prepared locally

At the M0 checkpoint, read-only search found #5/#7/#8/#9/#17 open, with no separate frontend or formal-PDF
follow-up issue. Prepared, unpublished update drafts are kept under ignored
`artifacts/m0/publication/`. They carry no fabricated issue numbers or assignees.

- #5: record merged #19 local writer/S3 wrapper evidence. Keep open pending the
  owner's original acceptance review; formal CJK/maps/multiple-context/snapshot
  and live S3/Runtime work needs explicit follow-up ownership, not automatic closure.
- #7: A/E actual model, complete field/citation and accuracy acceptance after merged
  extraction; do not repeat the historical claim that #16 is awaiting merge.
- #8: E full-case goldens, applicability/cross-form and human acceptance; B integrates
  the unchanged deterministic gate into revision reruns.
- #9: D document access, job/persistence/outbox, Runtime/recovery/publication subwork;
  reuse M0 contracts and coordinate B task transactions.
- #17: A owns the implemented policy/selectors/single-decision coordinator and bounded
  no-progress behavior. Phase 6 adds local human tasks, trusted corrections and full
  deterministic re-entry with process-local atomic handling; B's authenticated API
  can consume those components. No durable executor loop or human API is claimed here.
- New drafts: B authenticated human backend, C workbench, E formal PDF/goldens
  integration. Search again before creating them to prevent duplicate work.

Publish Issue updates/new issues separately after the shared contract review
converges; recheck current issue bodies and duplicates before those later writes.
Publication requires separate approval under AGENTS.md. No issue checkboxes,
assignees, PRs, CI runs or remote state are changed by preparing drafts.

## Historical source observations

On 2026-09-06 the official brief and supplied criteria/forms were read without
modification. Criteria had 9 pages; forms 6 pages with portrait/landscape A4 and
landscape A3 and no AcroForm widgets. Regional main road 18m -> normal; individual
front road 18m versus 6m -> +5% under their respective source rules. Regional totals
are copied into the comparison form; blank comparable columns stay blank. A mixed
unit interval remains unresolved. These earlier observations are not new M0 source
or model validation, universal rules or authorization to approve a real case.

Preserve boundary, reverse-matrix, blank-column, contradictory-unit and mixed-page
regressions. See [traceability](delivery-traceability.md),
[architecture](architecture.md), [local runbook](local-service-runbook.md) and
[cloud smoke plan](aws-smoke-plan.md). Historical delivery/branch migration remains
in [PR migration](pr-migration.md); new work starts from current main and functional
branches without rewriting published history.
