# ADR 0017: Local pause and continuation integration seam

Status: Accepted for local preparation only. Phase 8 cloud acceptance is deferred.

## Decision

Reuse `BoundedWorkflowResult`, `HumanTaskService`, `HumanResponseResult`,
`RunReference` and the existing transactional in-memory repository. Do not create
a second task/revision/job implementation or introduce cloud infrastructure.

`WorkflowPause` retains the actual waiting result, complete acyclic local decision
and selection-failure trace, final budget and current open task IDs. The repository
requires the successful human action's receipt digest to match that exact stored
task batch. Duplicate identical pause requests return the original checkpoint;
changed requests conflict. It does not infer waiting from arbitrary needs_review
results or use DTO validity as approval.

`PauseResumeService` accepts results from trusted local composition only, resolves
the current principal through its injected port, and returns promptly. No new HTTP
body, queue consumer, heartbeat, lease or background polling task is introduced.
The old paused run cannot use the repository's reentry operation.

When the existing task response transaction authorizes subsequent work, it prepares
and serializes a `WorkflowContinuation` before any store changes. The link records
pause ID, response event ID, task ID, previous run and exact next run. Correction
creates a new revision; some authorization responses legitimately keep the same
revision but always use a fresh run. New work has no attempt or Runtime session.
Exact admitted replay retains the same response/link. Rejection creates no new run
or continuation. A caller cannot submit a continuation as an execution command.

The `PauseResumeRepository` port specifies the handoff seam, not a replacement for
#29's scheduler. No continuation read dispatches or reviews work. Explicit local
`HumanTaskService.reenter(next_run)` still performs actual deterministic review.

## Limits and downstream ownership

- #24 owns authenticated persistent tasks/revisions and response transactions.
- #29 owns checkpoints/trace persistence, atomic waiting plus attempt release,
  durable response/outbox integration, dispatch, leases, fencing, Runtime resource
  lifecycle and cloud/process recovery acceptance.
- #28 owns attempt-scoped artifact and fenced manifest publication acceptance.

Local task creation and checkpoint capture are separate calls. They are **not** a
durable atomic task/checkpoint/attempt-release commit. The continuation is atomic
with the response only inside the existing single-process lock; no outbox delivery,
database rollback, process-restart recovery or exactly-once external effect is
claimed. A newly constructed repository loses every record. Runtime/lease release
must be implemented and tested by the downstream owner, not inferred from a pause.

Pause results contain evidence-bearing material. They are internal local records,
not a privacy attestation or safe cloud/log payload. Downstream authorization and
privacy projection must precede persistence and exposure. Operational IDs and
contracts do not grant publication authority or waive deterministic verification.

## Validation

Synthetic tests execute real local review, task creation, pause, admitted correction
and new-run recomputation. They cover schema roundtrip, duplicate replies, stale
commands, invalid checkpoints, access denial and injected pre-commit failure.
These are local integration tests, not crash-recovery or live Runtime evidence.
