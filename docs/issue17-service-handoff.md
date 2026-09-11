# Issue #17 service-boundary handoff

Integration scope note: this document records the controlled local reference
workflow and its originating delivery. Its human service now lives in
`application/workflow_tasks.py`; the authenticated API service is separately
`application/human_tasks.py`. The internal HumanResponseResult/next_run contract
requires an explicit adapter to API ResponseReceipt/resumed_run. For current
ownership, the synchronized service-v1 consumer migration and actual SQLite
durability, use [service contracts](service-contracts.md) and
[project progress](project-progress.md). Historical phase/status statements below
do not describe the combined integration's current acceptance.

Phase 9 local consumer preparation is described in the
[workbench handoff](workbench-consumer-handoff.md). Connected synthetic records and
consumer tests are available; real API and browser acceptance remain with #24/#25.
No workbench, endpoint or cloud acceptance is claimed complete.

## Phase 8 local preparation update

Only the local pause/continuation seam is implemented; **Phase 8 is not complete**.
Use `PauseResumeService` with `PauseResumeRepository` after an actual bounded
workflow returns waiting_for_human. The existing in-memory task repository also
implements that port. Its response transaction links the pause, admitted response
and fresh pending run. No dispatch occurs on pause, reply or continuation lookup.
`WorkflowPause` and `WorkflowContinuation` are additive controlled-action-v1
records in the exported service schema. See [ADR 0017](adr/0017-local-pause-continuation-seam.md).

| Deferred production acceptance | Owning dependency |
| --- | --- |
| Persistent authenticated task/revision/response transactions | [#24](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/24) |
| Checkpoint/trace persistence, outbox, duplicate dispatch, leases, fencing, Runtime release, restart/cloud recovery | [#29](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/29) |
| Stale-attempt artifact rejection, object/manifest commit recovery and fenced publication | [#28](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/28) |

No AWS resources, durable adapters, lease/token state machine or replacement job
contract is added to this branch. Local success cannot close these acceptance
items or establish full Issue #17 cloud acceptance.

Status: Phase 7 ownership reconciliation, 2026-09-09. Local handoff only;
no new HTTP routes, frozen remote OpenAPI, durable service or integration acceptance.

## Confirmed delivery boundaries

Read-only inspection of the current issues and comments supersedes the older M0
assumption that task/backend/frontend follow-up issues had not been created.
The [Issue #17 coordination comment](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/17#issuecomment-5600848420)
requires its domain contracts and workflow to pass review, CI and merge before
downstream integration. No open pull requests were returned at this checkpoint.
Local working-tree tests are not that acceptance.

| Work item | Owned delivery | Issue #17 handoff |
| --- | --- | --- |
| [#24, B2](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/24) | Authenticated, persistent HumanTask/Revision API | Reuse task service, commands, admission, correction and response semantics; coordinate transactions with #29 |
| [#29, D1](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/29) | Durable jobs, outbox, leases, recovery and waiting/resume integration | Persist exact task/revision/run/event identities and atomically dispatch authorized subsequent work |
| [#25, B3](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/25) | Complete browser workflow | Consume #24's agreed API and the existing synthetic fixtures; browser acceptance remains separate |
| [#9 umbrella](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/9#issuecomment-5600845890) | Cloud integration and related document/artifact/Runtime deliveries | No deployment, document upload, download or publication authority is added here |

These issue-specific B2/B3/D1 assignments take precedence over the historical
A–E workstream shorthand for downstream delivery. They are not GitHub assignees.
Issue #24 lists candidate task/revision URLs, but explicitly reserves their final
shape for frozen OpenAPI. Issue #17 therefore supplies its existing application
services, schema and consumer fixtures without mounting competing endpoints.
An authenticated trace-query endpoint still needs an explicit #24/#29 ownership
agreement; the internal trace reader is not an authorized remote query service.

## Reuse inventory and consumer migration

The validation source remains `domain/service_contracts.py`, exported to
`schemas/service-v1.json`; `examples/service-v1/index.json` maps each fixture to its
model. Follow [service contracts](service-contracts.md) for canonical digests,
strict fields, enum values, nulls and error semantics. No new wire version or model
is invented by this handoff.

- Adopt `HumanTask`, `HumanResponse` and `HumanResponseResult` together. Phase 6
  adds task reason/affected subjects, explicit evidence supply and the attributed
  response result. Strict older consumers need coordinated migration.
- Adopt `controlled-action-v1` for exact action/state/decision records; do not
  parse those as the earlier illustrative `service-v1` action drafts.
- Reuse `HumanTaskService.create_task`, `create_from_handoff`, `respond` and
  `reenter`. Subject/finding bindings are trusted application configuration,
  never request-body locators. See [local composition](local-human-review.md).
- `HumanTaskRepository` requires context, task/batch creation, authorized task
  lookup, response transaction and re-entry; the old reserved `accept` operation
  is not the implemented interface. Atomic transitions must preserve admission
  and replay semantics when implemented with a durable store.
- `read_revision(principal, reference)` returns an internal `RevisionSnapshot`.
  Project its public `MaterialRevision` for an authorized consumer; do not
  serialize the internal material/source registry as an HTTP response.
- `DecisionTrace.read(run_id)` is an internal, unauthenticated port. A remote
  adapter must establish case/run access before lookup and preserve causal
  identity. Raw decision rationale and citation excerpts are evidence-bearing
  content, not safe general operational logs.

The `human-task-created`, `human-response-accepted` and `human-material-revised`
fixtures come from actual synthetic local task service execution. The
`decision-executed` fixture comes from the local coordinator. Neither fixture set
proves deployed HTTP behavior, durable persistence or live model execution.

## Requirements before mounting downstream routes

1. Freeze endpoint names, path/body task-ID consistency, collection/pagination
   shapes, job-to-run mapping, trace-query ownership and transport errors with
   #24/#29/#25. Task listing and revision-history listing are not implemented
   collection APIs merely because single-record lookup exists.
2. Resolve a trusted authenticated principal from request context. The local POSIX
   reviewer is not a web user's identity. Never use the server's OS identity for
   every browser caller, or accept actor/role claims from JSON.
3. Check case/job/task/document access before internal resolution. An inaccessible
   record and an unknown record must not disclose cross-case existence. Define
   that masking at the new transport boundary without changing legacy errors.
4. Accept structured commands and authorized document IDs/citations only. A
   URI-free schema does not sanitize raw text, excerpts or original/proposed
   values. #9/#29 require sanitized material: original documents, sensitive
   values, re-identification maps and locally rehydrated final PDFs must stay
   local. Enforce the upstream privacy boundary before cloud persistence/logging.
5. Persist accepted response, consumed task/version, new revision where needed,
   subsequent run and dispatch outbox atomically. Exact same-principal/key/payload
   replay returns the original result; permission revocation is still checked.
   Conflicting payloads and stale concurrent replies cannot produce another run.
6. Keep fact confirmation, rule approval, material approval and exact-result
   publication separate. The local task service verifies an existing signed
   material receipt; a deployment needs its own reviewed authorizer adapter.
   Do not upload local signing keys or treat an authorization DTO as a signature.
7. #29 must persist waiting state and finish the active attempt, then claim new
   work with new attempt/lease/fencing authority after an admitted response.
   `HumanResponseResult.next_run` is only an identity for pending work, not an
   outbox delivery receipt. Do not keep a Runtime session alive or implement
   durable dispatch by awaiting the in-memory repository's locked `reenter`.
8. Agree a timestamped durable human audit/resume event envelope and links to
   prior decisions. The local response event ID and parent revision are inputs,
   not a complete cross-run trace or a frozen #29 event bus contract.

## Downstream acceptance, not current claims

Preserve `/health`, `/v1/validate`, synchronous `/v1/reviews`, their response/error
schemas and invocation parity. Validate actual new HTTP responses against frozen
OpenAPI and the exported models. Test inaccessible/missing indistinguishability,
wrong path/body task IDs, stale sides/results/revisions, replay, competing human
responses, rollback, revoked permissions and source/privacy rejection.

#24/#29 must additionally test crashes around task/revision/outbox commits,
duplicate dispatch, waiting without infrastructure retry, lease takeover and
stale artifact publication. #25 must exercise actual browser correction and
evidence navigation against those endpoints. Local stores lose all state on
process exit; neither asyncio locks nor fixture roundtrips satisfy those tests.

The Phase 7 ownership gate is reconciled by this handoff. Endpoint implementation
and its acceptance remain with #24, coordinated with #29. Phase 8 durability and
Phase 9 browser integration remain dependency work, not completed Issue #17
acceptance. Do not close #17 or mark its integration checkboxes from this document.
