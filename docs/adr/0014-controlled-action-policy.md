# ADR 0014: Controlled action selection and execution authority

Status: proposed for Issue #17 review, 2026-09-09.

## Context

The existing `ReviewAgentController` safely executes a fixed synchronous sequence and
keeps deterministic review and PDF completion gates authoritative. PR #20 added shared
service identities, pure admission guards, immutable local revision snapshots and
reserved persistence ports. It did not add a model selector, an execution-time decision
producer, retry policy, human-task lifecycle or durable pause/resume behavior.

Issue #17 requires model-assisted next-action selection to affect a permitted branch
without allowing model output to create authority. It also requires actual causal traces,
bounded execution and version-bound human handoff. These capabilities must extend the
existing review core rather than create a second review engine or writer path.

## Decision

Trusted application code owns workflow state and action authority. It captures an exact
`WorkflowSnapshot` containing the run, immutable material revision, source and rule
bindings, workflow state, satisfied prerequisites, unresolved blockers and remaining
budget. The canonical snapshot digest binds every derived allowed-action set and action
proposal to that exact state.

A versioned policy derives an `AllowedActionSet` from the trusted snapshot. Each
`AllowedAction` has a stable identity and binds permitted workflow states, document
purposes, material revision, rule references, proposer and executor kinds, typed
prerequisites and declared step/model/retry cost. The policy is provider-neutral and does
not evaluate factors, calculate correction rates or authorize approval, verification,
completion or publication.

Selectors receive only the exact snapshot, its trusted allowed actions, sanitized
evidence and current budget. A selector returns one `ActionProposal` with a discriminated,
action-specific argument object:

- `extract_page` identifies an authorized document and exact page, optionally narrowed
  to a matching cited region;
- `inspect_reference` identifies an exact versioned reference or brief page and optional
  section;
- `request_human_review` identifies a reason, concrete question, affected subjects and
  available citations;
- `deterministic_review` binds the exact material revision and rule references.

Model identity, prompt version and model rationale exist only on a model-authored
proposal. They are untrusted records, not deterministic evidence or permission. Human
identity and roles never come from a proposal or external response body.

Immediately before tool invocation, trusted application code captures fresh state and
calls admission again. Admission checks the trusted proposer and executor, proposal run,
policy and snapshot digest, stable action identity, current revision and rules, workflow
state, typed prerequisites, source purpose and exact resource budget. Rejection invokes
no tool and is never replaced silently with another action.

The executor records a `DecisionEvent` when the decision occurs. The event distinguishes
the untrusted proposer rationale from a concise reviewer-facing summary and records state
before and after, checked prerequisites, affected subjects, evidence, admitted execution
or rejection, actual typed outcome, causal parent event IDs, linked task/response where
applicable and exact budget consumption. A rejected event cannot claim execution or a
state transition. Executed and failed dispositions must match the recorded tool outcome.
Existing `AuditEvent` producers retain their current meaning and are not relabelled as
Issue #17 decision events.

Execution is bounded locally by step, model-call, retry and available time budgets. A stable
failure fingerprint includes action identity, exact arguments, material revision,
relevant source versions and a finite failure code. Permanent or identical no-progress
failures are not retried. Retryable failures use deterministic bounded policy. Business
`needs_review` is not an infrastructure retry.

Missing or uncertain critical material creates a task bound to the exact run, task
version, material revision and required permission, then ends the active attempt. A
trusted response may perform only the command allowed by that task. Corrections preserve
original observations, create a new immutable revision, invalidate affected confirmation
and approval authority and trigger a newly authorized deterministic review. Waiting for a
person never keeps a Runtime invocation or lease alive.

The existing synchronous controller and `/v1/reviews` contract remain unchanged. The new
controlled coordinator will be introduced through explicit composition and reuse the
existing parsers, extractors, reviewer, verifier and PDF writer. Only the existing
deterministic completion and writer gates can produce a completed artifact.

## Dependency and ownership boundaries

- Issue #17 owns the local policy, selector, controlled coordinator, decision producer,
  bounded-loop behavior and local human correction/re-entry flow.
- Issue #9 owns durable repositories, checkpoints, outbox, leases, fencing, recovery and
  stale-publication prevention. No local Issue #17 component may claim those guarantees.
- The future human-service/API owner authenticates principals and exposes only authorized
  task and trace projections. Routes are not mounted until real application behavior and
  endpoint ownership are agreed.
- The future workbench consumes real task/trace APIs and must keep model rationale,
  deterministic findings and human assertions visibly distinct.
- The existing PDF contract and deterministic writer gate remain the sole artifact
  authority.

## Consequences

The earlier service-v1 action records were illustrative and are incompatible with the
required exact bindings. They therefore migrate explicitly to
`schema_version="controlled-action-v1"`: exact snapshots, registries, typed arguments and
causal budget records replace the loose draft fields. Python model names and imports
already used by the repository remain available, while schema, fixtures and consumer
documentation change together. Other service-v1 records and existing HTTP, invocation,
review, evidence and PDF contracts do not change.

Phase 2 implements the provider-neutral policy derivation described here. It explicitly
maps workflow states to prerequisite-, blocker-, source- and budget-gated actions, returns
no action for terminal/waiting states and performs no document or tool I/O. The contracts
and policy are consumed in Phase 3 through one provider-neutral selector port, a
deterministic local baseline and an injected Bedrock adapter. The adapter accepts only a
minimal structured selection; trusted code supplies proposal identity, state bindings and
telemetry, then performs a side-effect-free admission preflight. It never falls back to a
different action after malformed, refused, truncated, out-of-set or provider output.

Phases 1–3 do not establish tool execution or execution-time event persistence, and do
not establish task transactions, pause/resume durability, browser integration or
live-model acceptance.
Each requires its own implementation and tests before Issue #17 can be proposed for closure.

Phase 4 adds an explicitly composed, single-decision coordinator. It captures state before
selection, re-captures and re-admits immediately before routing one typed action, captures
resulting state, and appends the actual execution/rejection event. Executor exceptions and
invalid receipts become sanitized failed outcomes; rejections call no tool. A local trace
adapter preserves detached causal-parent records but is explicitly non-durable. It does
not change the legacy controller or claim Issue #9 transactions, leases or recovery.

Phase 5 adds a bounded local runner around that one-decision primitive. It accounts for
actual model attempts, provider/outer retries, tool steps, elapsed execution and injected
deterministic backoff without accepting selector-authored budget. Stable failure classes
and a canonical no-progress fingerprint prevent permanent and repeated identical work.
Terminal exhaustion retains blockers, subjects, located evidence and the last sanitized
tool outcome in a concrete non-persisted human-review handoff. The handoff itself grants
no human authority. Phase 6 implements local task and response behavior; durable
pause/resume remains Issue #9 work.

Phase 6 adds purpose-specific tasks from stored findings, a trusted POSIX principal,
explicit subject corrections and full `CaseReviewer` re-entry. A local repository uses
one lock to atomically validate and consume responses, preserve exact replay, append
revisions and queue subsequent review. Corrections preserve evidence and measured
confidence while invalidating obsolete human authority. Material approval checks a
separately signed current receipt; task responses never sign receipts or set completion
status. This is an explicitly non-durable, single-process adapter, with no human HTTP
routes, durable outbox or restart recovery. [ADR 0015](0015-local-human-task-transactions.md)
records the detailed transaction and confirmation boundaries.
