# Local human review and correction

For the optional local Phase 8 handoff, construct `PauseResumeService` with the
same in-memory repository and principal resolver as `HumanTaskService`. After the
bounded runner returns an actual waiting result, call `pause(result)`. This captures
the existing task batch and complete local trace; it does not dispatch anything.
Submit human commands through `HumanTaskService.respond` as before. When its result
has `next_run`, `continuation(response.event_id)` returns the corresponding local
causal link, and an explicitly invoked new-run reentry can recompute the material.
The paused run is not revived. See [ADR 0017](adr/0017-local-pause-continuation-seam.md)
for the intentionally non-atomic task/checkpoint boundary and deferred durability.

The local `LocalControlledCase` adapter now connects controlled decisions to the
actual `CaseReviewer` and `HumanTaskService`. It accepts prepared, located material
with approved rules, not raw PDF input. Configure exact finding-to-task bindings;
incomplete independent-finding coverage fails closed. Aggregate calculation findings
remain in the stored result and must be recomputed after resolving their mapped
causes. The admitted human action creates the entire authorized batch atomically.
After a response, instantiate a new run adapter with `reentry=True`. Exact material
signing remains a separate operator action, not model authority. See
[ADR 0016](adr/0016-controlled-execution-failure-boundaries.md) for trust boundaries.

Issue #17 Phase 6 provides `HumanTaskService`, explicit material subject bindings,
a POSIX reviewer resolver and `NonDurableInMemoryHumanTaskRepository`. These are
callable application components for one process. Tasks, response events,
revisions and queued re-entry are lost on process exit. No human-task HTTP route,
browser workflow, worker or restart recovery is assembled by this delivery.

## Explicit local composition

Use prepared `ReviewMaterial` with the selected source registry and the existing
deterministic reviewer. The existing [document preparation runbook](member-a-runbook.md)
and [local service runbook](local-service-runbook.md) remain authoritative for
allowlisted source preparation and the local signed receipt workflow.

1. Capture the material with `RevisionSnapshot.capture`; create a `RunReference`
   for that exact revision and calculate an actual `CaseReviewer` result.
2. Initialize the local task repository with `register(snapshot, run, review)`.
   This is a trusted bootstrap operation, not an external import of asserted
   verified status.
3. Configure `MaterialSubjectMap` with application-owned subject IDs and exact
   context/factor/target-or-comparable bindings. Configure
   `LocalReviewerPrincipalResolver` with the expected local reviewer and explicit
   permitted case IDs and permissions.
4. Construct `HumanTaskService` with those dependencies and, when approval is
   needed, the existing `LocalApprovalStore` as its authorization adapter.
5. Call `create_task` using actual finding IDs from the stored review and an
   explicit task purpose. Supply the configured subject ID for a side task.
   Retrieve the returned task with `get_task` through the repository and trusted
   principal when needed.
6. Submit a `HumanResponse` to `respond`, using the task ID, exact current task
   version, revision, side/result digest and an idempotency key. A value command
   additionally supplies the exact current public original and proposed value.
7. Inspect the returned `HumanResponseResult`. If it contains `next_run`, call
   `reenter(next_run)` to run the full deterministic review and store the result.
   Any remaining findings require new tasks or other applicable workflow steps.

To turn a bounded runner handoff into tasks, call
`create_from_handoff(run, handoff, bindings)` explicitly. Each `HumanTaskBinding`
maps one handoff blocker ID to actual current stored finding IDs, a task purpose
and, for a side task, its configured subject ID. Bindings are trusted application
configuration, not model output or a response-body locator. Every blocker must
be represented exactly once. The service checks the exact material revision and
compares each blocker's reason, subjects and evidence with the task derived from
the stored findings. Missing/unknown bindings and conflicting mappings fail.

The repository creates the complete task batch atomically or leaves all task
records unchanged. Keep the original bounded result/handoff alongside the returned
tasks: its last tool outcome, failure category and no-progress fingerprint remain
separate context, not task authority. Infrastructure-only handoffs with no mapped
deterministic blockers currently return `capability_unavailable`; they remain
unresolved for the caller to handle and create no fabricated finding or task.
This method is not automatically invoked by the default bounded runner,
`/v1/reviews` or the local `run` facade.

## Purpose-specific responses

Every task allows its listed positive command and rejection. Rejection records
the answer without a material change or new review run.

| Task kind | Permission | Positive command | Effect |
| --- | --- | --- | --- |
| `fact_confirmation` | `confirm_observation` | `confirm` | New revision with the admitted side confirmed and eligible earlier confirmations rebound; queue full review |
| `material_correction` | `correct_material` | `correct` | Record evidenced value correction in a new revision; queue full review |
| `evidence_supply` | `correct_material` | `supply_evidence` | Supply missing value/evidence on the admitted side; new revision and full review |
| `rule_approval` | `approve_rules` | `approve` | Approve eligible cited candidate rules in a new revision; queue full review |
| `material_approval` | `approve_material` | `approve` | Verify a separately signed receipt for exact current material; record authorization and queue review |
| `publication_authorization` | `publish_artifact` | `authorize_publication` | Record authority for the exact current verified result; no review run or artifact write |

Task questions, reason codes, selected finding IDs, affected subjects, evidence,
required permission and lifecycle state are server-created. A grouped task retains
every selected finding ID. A side task binds one configured observation side and
cannot be used to modify a different factor or comparison context.

## Correction, evidence and authority

The service compares the submitted original against the current stored public
value. A correction may propose a present normalized value and supporting raw
text/evidence, but cannot supply `corrected` or `corrected_by`. Trusted application
code attributes the accepted corrected value to the resolved human principal.

Existing value type and unit remain fixed. Measured observation confidence cannot
change, existing source anchors cannot move, and evidence must resolve to the
selected case forms with supporting text or an image/cell location. Missing
values or evidence require an explicit `evidence_supply` task. Newly supplied
locations have no measured extraction confidence; their score remains zero even
when a separate confirmation later establishes human authority.

Both correction and evidence supply clear previous confirmations through the
existing conservative revision operation. They do not confirm the new value or
approve its material. The parent snapshot, source evidence and cumulative value
ledger remain inspectable through the repository's `read_revision(principal, reference)`
with current case permission and the exact historical revision digest.
Old signed receipts and old publication authorization
cannot be applied to the changed revision.

Confirmation creates another revision, clears old human confirmations and then
binds the selected side. It can also rebind earlier confirmations proven by accepted
fact-response events in this repository, provided the actor and exact side digest
still match. This supports sequential confirmation of several uncertain sides without
trusting confirmation metadata imported at bootstrap or supplied in a response.
Corrections and rule changes clear that proven set. Full review reports any remaining
blockers; confirmation never raises measured confidence or approves material.

To approve material, first use the existing local approval boundary to inspect
and sign the exact current eligible material. The task service only verifies that
receipt; it cannot sign one. Rule approval, material approval and publication
authorization are separate tasks. A publication response requires an actual
verified result and current material authority. Artifact writing still requires
the existing writer and completion gates; remote publication additionally needs
Issue #9's durable authority and fencing.

## Atomicity, replay and re-entry

Within one repository instance, the response lock covers admission, material
transformation, task/version update, response event, revision and new run. A
validation failure leaves those records unchanged. Exact same-principal/key/command
replay returns the original event, task result and run; a changed command under
that key conflicts. The repository still checks current principal permissions
before replay. Stale revisions, consumed tasks, wrong sides/results and
concurrent conflicting responses fail closed.

A material change marks other open tasks for its parent revision as superseded.
A new run on the same material revision also supersedes open tasks from the prior run.
Re-entry uses the resulting revision and the existing `CaseReviewer`, including
approval and deterministic verification gates. Repeated successful local re-entry
returns the stored result. The same lock prevents a newer correction from racing
an in-progress review into storing an obsolete result.

These guarantees stop at the process boundary. There is no database transaction,
durable outbox, resumed Runtime session or cross-process lease. The
[Phase 6 ADR](adr/0015-local-human-task-transactions.md) records this scope and the
[service contract](service-contracts.md) defines the port a durable adapter must
implement. Source PDFs and local approval keys/receipts remain outside submitted
repository material.
