# ADR 0014: Durable review jobs, outbox dispatch and lease fencing

Status: proposed on the `feat/durable-review-jobs` branch for #29; not merged.
Supersedes the reserved job duties described in
[ADR 0003](0003-entry-and-cloud-job-boundaries.md) and
[ADR 0013](0013-service-foundation.md) by giving them an implementation.

## Context

M0 froze `RunReference`, `ReviewSubmission`, `ServiceResult` and the `JobRepository`
protocol, and deliberately shipped no persistence: `service_guards` holds pure
idempotency checks, and `LocalReviewService.run` returns `durable=false`. The only
review path is synchronous `POST /v1/reviews`, which executes in the request.

A real review reads documents, calls a model and writes a PDF. That cannot run inside an
HTTP request, and it cannot be made reliable by retrying a queue message: acknowledging a
message says the work was accepted, never that a review succeeded. #29 requires a control
plane that survives a restart at any point, refuses duplicate work, recovers a worker that
died mid-review, and treats a reviewer's involvement as neither progress nor failure.

## Decision

### Three identities, not one

`job_id` is the durable delegation a caller polls. `run_id` binds one immutable revision
and rule version. `attempt_id` is one execution of a run. A human correction produces a
new revision, which is by definition a new run, so a job spans runs. Collapsing job into
run would change a caller's `job_id` after every correction.

`fencing_token` and `result_version` increase within a run, never across runs: fencing
protects one run's publication slot, and a new run has its own.

### Eight durable statuses, four frozen wire values

The control plane distinguishes `queued`, `dispatched`, `running`, `waiting_for_human`,
`retryable_failed`, `failed`, `succeeded` and `cancelled`. `ExecutionStatus` keeps its
four v1 values and is not widened, because every service-v1 model forbids extra fields
and widening an enum breaks existing consumers. New models — `JobReference`,
`JobStatusView`, `JobAcceptance` — carry the finer status; adding models to the bundle is
compatible, changing one is not.

`waiting_for_human` projects to `succeeded` with a `needs_review` business status. The
attempt ran and produced findings; the case did not pass. Reporting `running` would also
be refused by `ServiceResult`'s own validator, which forbids findings on active
execution. `cancelled` projects to `failed` with a sanitized problem, which is lossy;
the exact status stays in `JobStatusView.job_status`.

### One state machine, no clock

`application/job_state.next_state` is the only place a status changes. It is pure: no
I/O, no clock, no randomness, so the full status-by-event matrix is enumerable in tests.
Lease expiry is decided by the store's conditional write and reported back as an event,
because a pure function that reads a clock cannot be exhaustively tested. Both the
in-memory adapter and the future DynamoDB adapter call it, which is how #29's requirement
that local and cloud tests share one state machine is satisfied structurally.

### Separate ceilings for attempts and takeovers

A retryable error consumes an attempt (ceiling 5). An expired lease does not: a dead
worker is infrastructure, and counting it would let one Runtime restart wave push every
in-flight job to the dead-letter queue. Takeovers instead have their own ceiling (3), or
a crash loop would be reclaimed forever.

### Reclaiming re-enqueues

An expired lease returns the job to `queued` with a fresh outbox entry, not to
`dispatched`. The round that carried the run was already marked sent, so leaving the job
dispatched would make recovery depend entirely on the queue redelivering a message that
the crashed worker may already have received and deleted. Re-enqueueing costs at most one
duplicate delivery, which the conditional claim already makes harmless; a lost message
without it would strand the job until its retention expired. This is a deliberate change
from the first sketch of the transition table, which assumed redelivery.

### Outbox before dispatch

A submission commits the job, its run, its first outbox entry and its idempotency record
in one transaction, then dispatches. The window between commit and send always exists, so
`OutboxDispatcher` and `JobReconciler` close it: a crash after the commit is recovered by
a later pass, and a failed send moves only the outbox schedule, never the job status —
the work is still durably owned.

Backoff caps at 900 seconds because that is the queue's maximum delivery delay. Longer
backoff comes from the outbox entry's `available_at`, which makes the outbox a schedule as
well as a recovery log.

### Idempotency on a primary key, never on an index

Uniqueness is a conditional write on the idempotency record's own key,
`IDEM#<principal>#<sha256(key)>`. A secondary index is eventually consistent, so two
concurrent submissions would both observe an unused key and create two jobs. The caller's
key is hashed so its content and length never shape a stored key. The payload digest
excludes the key itself and sorts documents, so re-ordering documents is a replay, not a
conflict.

### Authority comes only from a conditional claim

The queue message carries `job_id`, `run_id`, `outbox_seq` and a dispatch token — no
payload. Messages are read repeatedly by monitoring, dead-letter tooling and redrive
operators, so every byte in one is exposure; a message is also a snapshot that can
disagree with the store. The worker re-reads the run under strong consistency. The
dispatch token identifies the round for observability and never authorizes work.

`ConditionFailed` is not a retry signal. It means another writer moved first, and
retrying it as if it were throttling is how durable state gets corrupted.

### Results are referenced, not stored

The control plane keeps a result's identity, statuses, digest, artifact ids and finding
count. The body goes to an attempt-scoped object first, and only then is the reference
committed under the publishing attempt's fencing token. A crash between the two leaves an
orphan body nothing references. `ServiceResult.findings` carry `SourceCitation` excerpts,
which are document text: they exceed a DynamoDB item and breach the payload boundary.

### Cancel only, no retry or redrive endpoint

Cancel is bound to one principal and one case, so it is safe to expose. Retry and
dead-letter redrive are operator actions with explicit conditions and an audit trail, and
belong in a runbook. An HTTP route able to reschedule arbitrary jobs is exactly the
unauthenticated general-purpose admin surface #29 forbids. Cancelling a running attempt
raises a flag the worker observes on its next heartbeat; it never kills a mid-write
attempt.

### Existence is not leaked

Another principal's job and a job that does not exist both answer 404. Answering 403
would confirm that the job exists.

## Consequences and limits

`POST /v1/reviews` and the existing invocation path are unchanged, as #9 requires. The
new routes are mounted but report `capability_unavailable` until a store and an
authenticator are both configured, so an undeployed durable plane cannot answer as if
work had been accepted.

This ADR delivers the state machine, the port, an in-memory adapter, the application
services and the HTTP boundary. It delivers **no** DynamoDB adapter, no SQS wiring, no
dead-letter queue, no alarms and no infrastructure code; the in-memory store is a
reference implementation whose state dies with the process, and a single lock is not a
cross-process transaction. Those remain the cloud phase of #29, with deployment in #30
and live acceptance in #31.

The privacy canary here scans the in-process control plane only. Scanning DynamoDB, the
queue, the dead-letter queue and CloudWatch belongs to #31.

Contracts still open with other workstreams: the privacy attestation type that #27 will
issue and this boundary will verify; whether #24 commits a revision and its new run in one
transaction through `resume_after_human`; and where #17 stores decision events, which this
plane binds to an attempt but does not own.

See [service contracts](../service-contracts.md) for the status projection, error mapping
and canonicalization rules.
