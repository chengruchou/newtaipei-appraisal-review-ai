# ADR 0020: Local human-task transactions and revision re-entry

Status: proposed for Issue #17 Phase 6 review, 2026-09-09.

## Context

The bounded workflow can stop with located findings and a concrete human-review
handoff. The service foundation has task commands, pure admission guards, immutable
material snapshots and the existing local reviewer/receipt boundary. Those pieces
alone cannot consume a response once, apply a correction or queue review of the
resulting revision. Issue #17 needs an executable local transition before the
durable repositories and authenticated remote API are assembled.

## Decision

`HumanTaskService` creates purpose-specific tasks from actual stored review
findings. Tasks bind all selected finding IDs, a concrete question, reason,
affected subjects, located evidence, exact run/material revision, task version
and required permission. Application configuration maps a subject ID to one
comparison context, factor and observation side; response bodies cannot supply
their own material locator. The service reuses `CaseReviewer` for full re-entry.

`create_from_handoff` accepts an explicit trusted `HumanTaskBinding` for every
blocker. Each binding selects current stored deterministic findings, task purpose
and subject; the service checks exact revision, reason, affected subjects and
evidence. Incomplete, unknown or conflicting mappings fail. The repository's
`create_tasks` operation commits the complete batch or none of it. The caller
retains the full handoff separately, including the last actual tool outcome and
no-progress context. Infrastructure-only handoffs without deterministic blockers
return `capability_unavailable` and remain unresolved for caller follow-up; the
service does not invent a review finding to make them into a task.

`LocalReviewerPrincipalResolver` checks the current POSIX reviewer against an
explicitly configured reviewer and supplies explicit case IDs and permissions.
Only a trusted human principal may respond. Identity, roles and completion flags
are not response fields; proposed values cannot change measured confidence.
This preserves the existing local
single-operator boundary; it does not establish Internet identity.

Task purposes remain separate: confirm an observation, correct a value, supply
missing evidence, approve rules, approve material and authorize publication.
Each purpose permits its specific command or rejection. Corrections and supplied
evidence create a new immutable revision and do not implicitly confirm it.
Confirmation first creates a new revision and binds the selected side. Sequential
confirmation may also rebind earlier unchanged sides proven by accepted confirmation
events for the same actor in this repository. Caller-supplied or bootstrap confirmation
metadata supplies no such proof. Corrections and rule changes clear the retained set.
All original extraction confidence is preserved. Existing value types, units and
source anchors cannot be silently reinterpreted. Supplying missing evidence requires an explicit evidence task and
citations in the selected case forms.

The revision retains its parent and cumulative original/proposed/corrected value
ledger. A changed revision invalidates old exact-material authority and supersedes
other open tasks on the old revision. The base revision operation clears human
confirmations; the narrow confirmation transition above rebinds only proven assertions.
Only unchanged native provenance meeting the existing revision rules survives. A new
task and full deterministic review establish the next result; a response never
sets `verified`, `completed` or `written`.

Material approval verifies a pre-existing signed receipt through the injected
`ReviewAuthorization` boundary. For local use this is `LocalApprovalStore`; the
operator signs the exact current eligible material separately through the
existing approval workflow. The task service does not create that signature.
Rule approval does not approve material. Publication authorization binds both
the current revision and an actual verified result digest, and does not write
or publish an artifact. `AuthorizationRecord` remains an attributed record,
not a substitute for a signed receipt or publication fencing.

`NonDurableInMemoryHumanTaskRepository` implements a deliberately scoped
transaction. One process-local lock covers current-version and permission
checks, actor/key/payload replay handling, the synchronous material transition,
task consumption, sibling supersession, response event, revision append and
queued local re-entry. Potentially failing transformations and serialization
complete before store mutation. Exact replay returns the original response
event and run; a different payload under the same actor/key conflicts. Concurrent
responses cannot both consume one open task. Re-entry uses the same lock to
prevent a concurrent correction from committing an obsolete review result.

## Consequences and limits

This adapter supports local transaction and concurrency tests. Its state is lost
when the process exits. It has no durable event store, outbox, cross-process CAS,
leases, restart recovery or artifact publication fencing; those remain Issue #9.
No HTTP route, background worker or automatic wiring from the default bounded
runner is introduced. Application composition must explicitly call task creation
or `create_from_handoff`, submit the response and invoke re-entry.

The same-process lock includes deterministic review, so a long review serializes
other local operations. A durable adapter must implement equivalent admission
and atomic append guarantees with its own work claims and recovery, rather than
treat this lock as a distributed guarantee.

The service-v1 schema adds `HumanResponseResult`, the `evidence_supply` task kind,
the `supply_evidence` command and task reason/subject fields. Missing reason and
subject fields remain valid for old draft DTOs, but the actual task repository
requires both. Consumers must adopt the regenerated schema and fixtures together
because strict older consumers reject new fields and enum values. Existing
HTTP/invocation, deterministic review and PDF contracts remain unchanged.

See [local human review](../local-human-review.md) for composition and operation,
and [service contracts](../service-contracts.md) for wire semantics.
