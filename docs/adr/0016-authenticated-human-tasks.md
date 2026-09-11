# ADR 0016: Authenticated human tasks and transactional revision submission

Original branch status: proposed on `feat/human-task-api` for #24, stacked on
`feat/durable-review-jobs` (#32), whose job identity and transport this builds on.
Current integration: included in main through PR #45, including the durable local
SQLite composition. #24 now tracks the remaining cloud transaction implementation;
#30 tracks its deployment composition.

Gives an implementation to the human-task duties reserved by
[ADR 0013](0013-service-foundation.md), and consumes the resume seam introduced by the
durable-jobs ADR on that branch.

Registry note: [the consolidated index](README.md) preserves main's full-case
golden decision as 0014 and durable jobs as 0015. This decision remains 0016;
other independently allocated branch IDs were moved to unique numbers. The
originating-branch status above is retained as decision provenance, not a claim
about the current integration worktree or deployed approval.

## Context

M0 froze `HumanTask`, `HumanResponse` and `AcceptedResponse`, and shipped `admit_response`
as a pure check with no API and no persistence. The durable job plane can now reach
`waiting_for_human` and list `open_task_ids`, but nothing could read those tasks or answer
them, so a job that needed a reviewer stayed stuck by construction.

#24 requires that answering be authenticated, idempotent, version-bound and transactional,
and that a job be safely reschedulable afterwards with parent and child runs traceable.

## Decision

### One coarse transaction, against the grain of the job store port

`ports/jobs.py` is deliberately fine-grained: each method is one conditional write with one
meaning. `ports/human_tasks.py` deliberately is not. Answering a task marks the task
answered, appends an immutable revision, supersedes the sibling tasks bound to the old
revision, consumes the idempotency key and schedules the follow-up run. Those five effects
have no useful partial state. A store that could apply four of them would produce either a
revision nothing is scheduled to review, or a scheduled run for a revision that was never
stored — and both are silent, because each individual write succeeded.

So `commit_response` is one method. A DynamoDB adapter implements it as one
TransactWriteItems. The in-memory adapter implements it under one lock and rolls its writes
back when the job plane refuses, which is a local stand-in for a transaction that would
simply not have committed, not a pattern for a cloud adapter to reimplement.

### The store rechecks everything the service already checked

The service authorizes the principal, admits the response and derives the next revision
from a read. By the time it calls the store, another writer may have invalidated that read.
Every condition is therefore passed into `commit_response` and rechecked there: task state,
task version, the task's binding to the current head revision, the actor's ownership, the
new revision's parent, and the idempotency key.

This looks redundant and is not. Mutation-testing the service-only tests showed every one
of those store conditions could be deleted with the whole suite still green, because the
service caught each case first. `tests/unit/test_human_task_store.py` drives the store
directly for exactly that reason; each condition now fails a test when removed.

### Replay is checked before admission, not after

An exact retry names the task version its own first attempt already consumed, so running it
through admission would reject a response that in fact succeeded. The service reads the
stored receipt first and returns it unchanged. That read is a fast path only; two concurrent
retries can both miss it, so the authoritative key check is inside the transaction. The
receipt is the idempotent body: same principal, key and payload returns the same value, and
a changed payload under the same key conflicts.

### Confirmation captures, correction revises

`RevisionSnapshot.revise` clears every human confirmation by design, because M0 has no
validated dependency graph for carrying assertions across a material change. That makes it
exactly wrong for a confirmation, which asserts something about material that did not
change: routing a confirmation through `revise` would erase the assertion being recorded.
A confirmation therefore captures a child revision directly and keeps it.

A correction does change an observed value, so it uses `revise` and every prior confirmation
is cleared, including on the side that was not corrected. The reviewer's payload contributes
a value and raw text only. It keeps the stored typed citations rather than the caller's
evidence, must name the observation its own task is about, must already be normalized, and
can never raise a raw confidence — human authority is recorded as a confirmation on the next
round, not by inflating an extractor's score.

### Refusal and approval are not the same as silence

Refusing to confirm is a recorded answer that commits no revision and schedules no run. The
receipt's validator enforces that a rejection can never present itself as having produced a
revision, and that a revision and its scheduled run are only ever advertised together.

Rule, material and publication approval are admitted by the frozen contract but are not
answered here: the existing exact-material authority owns them. Such a task reports
capability_unavailable rather than advertising an authorization the service never wrote.

### The server names the subject; the client never re-derives it

A correction names the observation it changes, and that name canonicalizes the comparison
context through `json.dumps`, which escapes non-ASCII by default. A browser's
`JSON.stringify` does not, so a client computing the same name would produce a different
string for any case identified in Chinese — which is every real case in this project — and
every correction would be rejected as invalid. The task routes therefore return `TaskView`,
carrying the server's own `subject_id`. This is the repository's existing rule about not
substituting one canonicalizer for another, applied across a language boundary.

### Reading and answering are separate permissions

`review` is enough to see a task; answering additionally requires the task's own permission.
A task belonging to another principal, and a case this principal cannot see, are both
reported as not_found, because replying forbidden would confirm the task exists.

### No route creates, reassigns, reopens or deletes a task

Tasks are raised by a review attempt. An endpoint able to mint one would let a caller invent
the very question whose answer authorizes a change to the material.

## Consequences

The review repair adds authoritative job-based collection reads, reconciles
rejected task projections, retains applied evidence in the correction ledger,
and rolls back cancelled local transitions. TaskSubjectView is an opt-in
authoritative value/unit endpoint preserving the existing TaskView wire shape.
See [human-task review repair](../human-task-review-repair.md) for reference
adapter limits and the durable transaction requirement.

The composition guard in `create_app` becomes an implication rather than a biconditional.
Two authenticated planes now share one principal resolver, so requiring a job store
alongside it would refuse both a human-task-only composition and the deliberately
unconfigured one whose capability_unavailable the contract promises. The safety direction
is unchanged and still tested: neither plane mounts without an authenticator.

Three wire models are added rather than widening frozen v1 types, which forbid extra
fields. Consumers must take the regenerated schema and the task-list, revision-list and
response-receipt fixtures together.

The in-memory store is not durable and its rollback is not a transaction. Nothing here
provides a cross-process guarantee, and the cloud adapter, its conformance suite and the
DynamoDB transaction remain outstanding.
