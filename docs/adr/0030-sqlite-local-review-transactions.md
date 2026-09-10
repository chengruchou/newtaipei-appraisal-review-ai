# ADR 0030: Atomic local review state and result storage with SQLite

- Status: Implemented locally; integration and publication acceptance are separate
- Scope: `adapters/local/sqlite_review_store.py`
- Storage format: `sqlite-review-v1`

## Decision

Use one private, file-backed SQLite database for local jobs, runs, attempts,
leases, dispatch outbox, result references, human tasks, material snapshots,
revision chains and response receipts. Each operation opens a new connection,
executes `BEGIN IMMEDIATE`, loads typed JSON into fresh reference job/task state
machines, applies their conditions, serializes all resulting state and commits.
No authoritative in-memory object survives the operation. The reference stores'
asyncio locks are not the cross-process transaction boundary: SQLite is.

Exceptions, including cancellation raised inside the operation, roll back the
whole transaction. A process exiting after its state UPDATE and before COMMIT
recovers the previous state through SQLite's rollback journal. SQLite uses
`synchronous=FULL`. No remote call, model execution or queue send occurs inside
these transactions. Dispatch delivery remains outside the transaction and uses
the existing outbox token protocol.

Database work runs in `asyncio.to_thread`, keeping SQLite lock acquisition off
the service event loop. Cancelling the awaiting coroutine does not kill its
worker thread: the outcome can be unknown, but the transaction still commits
all effects or none. Callers recover response outcomes using the identical
idempotency key and command. They must not infer rollback from a lost response.

## Composition API

```python
store = SQLiteReviewStore(database_path, policy=job_policy, clock=clock)
jobs = ReviewJobService(store, store.results, policy=job_policy, clock=clock)
tasks = HumanTaskService(store, clock=clock)
```

`SQLiteReviewStore` satisfies both `JobStore` and `HumanTaskStore`; their
`read_job` signatures agree. It additionally provides:

- `await register_tasks(job_id=..., principal_id=..., snapshot=..., tasks=...)`.
  `await seed(...)` is an identical async alias. Initial snapshots may be seeded
  with an empty task tuple. Task registration verifies the actual job principal,
  exact current run and revision. It supports RUNNING before the worker calls
  `wait_for_human`, and QUEUED after a response has committed a resumed run.
  In WAITING_FOR_HUMAN, the registered IDs must exactly match `open_task_ids`.
  Repeating the same registration does not append a duplicate revision or task.
- `await read_submission(run_id=...)` reconstructs the pinned metadata and checks
  its canonical digest. Its internal idempotency key is `run-<run UUID>`;
  that key is excluded from the canonical submission digest.
- `store.results`, also constructible as `SQLiteResultStore(store)`, implements
  `ResultStore.put/get` plus `get_committed(reference)`.

The clock defaults to `time.time`. Result staging uses that injected trusted
clock, never a timestamp from a result body. Tests inject the same clock into
both the service and store. Existing JobStore operations retain their explicit
trusted `now` parameter.

A response rechecks the current job principal, waiting state, task identity,
version, run, revision head, side digest, allowed action and idempotency payload.
Its task answer, superseded siblings, new snapshot, head, job transition,
resumed run, dispatch entry and receipt share one commit. Resumed run documents
come from the newly committed snapshot; the new submission digest is computed
in that same transaction. The previous run keeps its original references.
Source authorization and new C2 execution snapshots remain composition duties.

Cancellation and terminal job transitions supersede remaining open local tasks
without changing their original material. A cancellation that wins before a
response leaves no response receipt, revision or extra outbox entry. A response
that wins first commits all its effects before cancellation acts on the new run.
Replay returns its original receipt even if the job later changes.

## Results and publication authority

`review_results` holds typed immutable candidates by `(run_id, version,
attempt_id)`, with each row's canonical content digest. Reusing that exact
candidate key with changed bytes conflicts. A new current attempt can stage its
own digest, so an orphan from an expired attempt cannot occupy its result slot.
A new write verifies the current run, attempt, revision, expected result version,
active lease deadline and cancellation flag. Exact existing-byte replay is safe
without renewing any execution authority.

`get` returns no body until a result reference is committed. `get_committed`
requires the supplied reference to equal the authoritative reference and selects
only its exact digest, validating the stored body again. Uncommitted candidates
are never used as a fallback. Lease-bound `finish` additionally checks stored
current run, deadline and result version; a cancelled attempt cannot publish.
When locally staged candidates exist, a reference must select the publishing
attempt's own candidate, not an older attempt's digest. The JobStore port can
still commit references for an independently configured external ResultStore;
a missing local body then fails local result retrieval instead of being invented.

The coordinated publication adapter may inject this store and use its internal
`_connect()` and `_decode(payload)` seam to perform its own `BEGIN IMMEDIATE`,
read typed authoritative state, verify source grants/epochs and insert a
manifest atomically. It must commit or roll back and close its own connection.
Alternatively `_transaction(async_callback)` supplies fresh job/task state and
the open connection. Calling the outer store methods from inside that callback
would start a nested transaction and must be avoided. No transaction callback
may perform network I/O. Publication-owned tables and source authorization
remain separate from this adapter's ownership.

## Storage and operational limits

The immediate parent directory must be owned by the current OS user, mode 0700,
and free of symlink aliases. A missing immediate directory is created with 0700.
The database must be a regular, singly linked, current-user-owned 0600 file.
Existing permissions are rejected instead of silently changed. Connections
recheck directory and database inode identity. Keep this directory on a local
filesystem with SQLite-compatible locking; this is not an NFS or multi-host
service design. The directory must also protect journals and future backups.

`review_state(singleton, payload)` has exactly one versioned JSON snapshot;
`review_results(run_id, version, attempt_id, digest, payload)` stores result
bodies separately. JSON uses explicit typed records and material models, never
pickle. Snapshot digests are recomputed when loading. Persisted execution policy
must match construction policy on every operation. Schema/policy mismatch fails
closed; migrations must be explicit. The reference stores' internal dataclasses
are intentionally coupled to this storage version and require migration review
when they change.

All operations serialize behind one SQLite writer and load the complete control
and revision state. This is appropriate for the isolated local rehearsal mode,
not a claim of production scale or AWS DynamoDB transactions. Bodies, local
material and mappings are not automatically encrypted by SQLite; the directory
is private local storage and must never be published or committed. This adapter
does not implement cloud deployment, login, source authorization, artifact object
storage, grant revocation, model budgets or independent approval.

## Executed local validation

The focused suite exercises every existing `JobStoreContract` check plus actual
file-backed operations through independent instances and subprocesses:

- Competing workers in two processes yield exactly one claim and one persisted
  attempt, without a timing sleep.
- A separate process commits a human response, exits, and the original instance
  observes and replays the exact receipt, revision and outbox.
- `os._exit(73)` after writing the full response state but before COMMIT leaves
  the prior task, revision, job, outbox and receipt state intact after reopening.
- Concurrent response/cancel and an injected cancellation after job resume
  preserve whole-transaction outcomes.
- Two response rounds yield exactly r1, r2, r3 with proper parents; repeated
  registration and response replay do not duplicate history.
- New revision documents are moved atomically into the resumed submission;
  source-run metadata is unchanged.
- Result bodies survive process exit; same-attempt byte conflicts, old-attempt
  candidates, stale result CAS, lease expiry, cancellation and stored-body
  tampering fail closed.

Run from the integration checkout using its declared project environment:

```sh
mkdir -p artifacts/sqlite-review/tmp
PYTHONPATH="$PWD/src" TMPDIR="$PWD/artifacts/sqlite-review/tmp" python -m pytest \
  tests/unit/test_sqlite_review_store.py \
  --basetemp=artifacts/sqlite-review/focused
python -m ruff check src/appraisal_review/adapters/local/sqlite_review_store.py \
  tests/unit/test_sqlite_review_store.py
python -m ruff format --check src/appraisal_review/adapters/local/sqlite_review_store.py \
  tests/unit/test_sqlite_review_store.py
PYTHONPATH="$PWD/src" python -m mypy \
  src/appraisal_review/adapters/local/sqlite_review_store.py
```

The repair session uses the sibling runtime-deployment project-local virtual
environment and retains evidence under ignored `artifacts/sqlite-review/`.
These are actual SQLite process/restart results. They do not establish AWS,
browser integration, live model behavior or formal business acceptance.
