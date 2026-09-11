# Workflow run authority and reservation integration

The controlled coordinator accepts `ledger: WorkflowRunLedger`. Its default is
`NonDurableInMemoryWorkflowRunLedger`, owned by the coordinator. Every bounded
runner using that coordinator shares the same run authority. A composition that
reconstructs coordinators must inject the same ledger instance (or the same durable
store). Creating a new independent in-memory ledger loses history and is not a
supported recovery mechanism. The local adapter is thread-safe across event loops,
but it does not persist across processes.

## Admission and completion

The port is `ports/workflow_run_ledger.py`; the memory reference is
`adapters/local/workflow_run_ledger.py`. The persistent implementation is
`adapters/local/sqlite_workflow_run_ledger.py`. It changes no canonical service DTO
or human-task service interface. The integration owner supplies the configured
composition and canonical human-task adapter separately.

| Operation | Required atomic behavior |
| --- | --- |
| `acquire(run, initial_budget)` | Bind the full RunReference to the unique run ID. Insert the initial budget only once. Reserve all remaining allowance for one owner before any selector or tool call. Return a detached terminal result on replay; reject active, quarantined or mismatched runs. |
| `assert_active(owner)` | Check the exact run and token and reject quarantine immediately before external call admission. |
| `remaining(owner)` | Return a detached authoritative budget; reject stale tokens. Quarantined owners can read their accounting to report failure. |
| `checkpoint(owner, budget)` | Reject budget increases and changes between known and unknown time allowance. Atomically retain decreasing budget with the reservation still active. |
| `quarantine(owner)` | Prohibit further calls while preserving the reservation. The owner can still write accounting and a failure result. |
| `release(owner)` | Release a settled direct decision while retaining consumed budget. Quarantine remains inadmissible. Bounded runs use `complete` instead. |
| `complete(owner, result)` | Check exact identity and fencing; atomically commit the terminal result, remaining budget and terminal reservation state. Reject successful results for quarantined attempts. Preserve the complete failure history in the cached result. |
| `abandon(owner)` | Fence the owner and retain quarantine after cancellation, failed tracing, or other uncertain interruption. Never make the allowance available again. |

Reservation is deliberately conservative: the entire remaining step/model/retry/time
allowance belongs exclusively to the active invocation. Checkpoints record actual
known consumption. They do not mean the rest is free for another worker. If an
external result is unknown, the remaining allowance stays reserved, rather than
being reported as observed usage or refunded. Tokens are opaque internal capabilities,
not client body fields. All calls within one reservation belong to one sequential
runner. Direct `decide_once` calls acquire the same authority independently.

A bounded invocation stores its exact terminal result, including selection failures,
final budget and handoff. Repeating the same run returns that result without invoking
selectors or tools. This also applies to permanent failures, no-progress termination
and budget exhaustion. New authorized work needs a distinct RunReference. Concurrent
bounded admission returns `ServiceFault(CONFLICT)`; direct decision admission raises
`ActionSelectionError(IN_FLIGHT)`. These are fail-closed internal results, not new
HTTP routes. The caller still needs current run/source authorization before lookup.

## Receipt and external-result failures

Receipt structure, source evidence and the complete action-specific DecisionEvent
validation are protected. A review receipt with a task link, or a successful human
receipt without its waiting task, produces a sanitized failed decision with
`invalid-tool-receipt`. The ledger is quarantined before tracing that failure. A tool
having already moved its snapshot to VERIFIED cannot turn this into clean replay.
The existing generic structural-failure reason remains `tool-execution-failed` for
consumer compatibility; that invalid receipt also quarantines the reservation.

Adapters that cannot determine whether an external action took effect must raise
`WorkflowExternalResultUnknown`. It yields a sanitized `tool-result-unknown` failed
decision and unresolved-execution handoff, without retry. Timeouts, cancellation and
unexpected failures outside accepted tool results also retain quarantine. Existing
known retryable failures keep their bounded retry semantics: adapters must not
report an unknown transport outcome as a known retryable failure. Neither the ledger
nor a Python cancellation can stop a provider thread or roll back a tool side effect.

Selector identity, proposal admission, action arguments and executor origin remain
validated by the existing trusted boundaries. Ledger injection does not give a model
system authority or permit it to select outside the advertised action set.

## SQLite implementation and integration requirements

Inject `SqliteWorkflowRunLedger(database_path)` into every coordinator serving the
same runs. It uses the independent `workflow_run_ledger_v1` table and can use a
separate configured database file from the combined job/human store. The parent
directory must already exist. The adapter refuses `:memory:` and has no in-memory
fallback. Database access is synchronous local I/O behind the async port.

Each operation uses a fresh connection, `BEGIN IMMEDIATE`, full synchronous writes
and an atomic commit/rollback; initialization enables WAL. SQLite serializes
competing processes. The unique run ID is permanently bound to the complete
serialized RunReference, including exact case/revision/digest. A conflicting binding
is rejected before any terminal replay. Unknown schema versions or malformed stored
records fail validation rather than being reset.

`owner_token` is the current non-expiring execution lease; `reservation_token`
retains the last reservation identity after release, abandonment or completion.
No automatic lease renewal, expiry takeover or scheduler is provided. An owner is
checked under the same write transaction as every mutation. This makes stale owner
writes fail even through new adapter instances/connections. Restart does not change
any row, owner token or remaining budget.


Persist exact run/revision binding, owner/fencing token, active or quarantined
reservation state, authoritative budget and terminal result. An active reservation
must be committed before external work. Use transactions and conditional owner/state
updates for every transition; a Python lock alone is not the durable implementation.
Do not silently fall back to a new local ledger if the configured store is unavailable.

An active row found after restart is not free capacity. Reject duplicate admission
until recovery has established the prior owner's status. If its external result
cannot be reconciled, persist quarantine, fence that owner, retain its reservation
identity and allowance, and require explicitly authorized fresh work. Merely expiring
a lease cannot prove that a model call or tool side effect never occurred.

Trace append precedes budget checkpoint and final-result commit. An interruption
between those writes leaves the reservation active/quarantined, not replayable as
success. A durable composition should transactionally couple trace/checkpoint/outbox
writes where possible; otherwise it must reconcile trace IDs and reservation tokens
before any recovery. Completion with an unknown storage acknowledgment is recovered
by reading the committed terminal row, never by issuing the external work again.
The workflow ledger does not replace the job store's lease, attempt fencing,
authorization or artifact-publication transaction.

Actual subprocess tests now cover two processes contending on one database;
restart with permanent failure and exhausted model allowance; crash after reservation
before a call; crash after a tool side effect; crash after trace append before budget
checkpoint; successful-result replay after restart; and replay of quarantined invalid
receipts and unknown external results. These run with fresh coordinators/runners and
an injected model client, real SQLite files, and side-effect marker files. Abrupt
crash probes terminate the worker process without cleanup. Contract tests also
exercise stale tokens and budget rollback through independent SQLite connections.

Combined job lease/attempt fencing, task/revision transactions, persistent trace and
outbox reconciliation, actual runtime recovery and artifact publication remain
integration-owner acceptance. The standalone ledger cannot make those separate
stores one atomic transaction.

## Local regression evidence

Direct-to-bounded integration regressions cover switching after review, after
verification and after human task creation, with deterministic and injected model
selectors. Results must retain the complete ordered decision trace without duplicate
IDs, preserve the consumed budget and replay identically through a new runner without
additional model calls. Human handoffs must pass the actual pause service and retain
both open tasks. These are local composition checks, not crash recovery acceptance.

On the original PR head, the five initial regression cases failed: three invalid
action-specific receipt cases, permanent selection failure replay using the actual
LocalControlledCase with an injected Bedrock selector client, and concurrent new-runner
admission. The repaired cases preserve the assertions. Additional tests exercise
shared-ledger coordinator reconstruction, actual thread/event-loop contention,
cancellation and trace-write quarantine, budget monotonicity and stale tokens.
No AWS resources or model service calls are used. Run from this worktree with the
existing adjacent runtime environment:

```sh
PYTHONPATH=$PWD/src ../runtime-deployment/.venv/bin/python -m pytest tests/unit/test_workflow_run_authority.py tests/unit/test_workflow_run_ledger.py tests/unit/test_sqlite_workflow_run_ledger.py
```

See [ADR 0028](adr/0028-controlled-execution-failure-boundaries.md) for the governing
failure-boundary decision. Canonical task/schema migration and live deployment
acceptance remain outside this bounded change.
