# ADR 0026: Transactional DynamoDB job persistence

Status: implemented with offline SDK and emulated persistence validation for Issue #30.
Live DynamoDB and complete Runtime acceptance remain pending.

## Context

ADR 0015 defines the durable job port and shared state machine. Its local adapter loses
all state with the process. Runtime admission, queue dispatch and worker replacement
need committed control-plane records and database-enforced execution authority.

This change implements `ports.jobs.JobStore` directly. It neither inherits from nor
uses the in-memory implementation. `application.job_state` remains the only authority
for status transitions; the adapter supplies atomic persistence and lease conditions.

## Interface and composition

Construct `DynamoDBJobStore(client, table_name=..., index_name="job-recovery", policy=...)`
with a **low-level boto3 DynamoDB client**, not a resource's auto-marshalling client.
The SDK `TypeSerializer` and `TypeDeserializer` handle boundary conversion. Conditions
use `Attr`/`Key` and `ConditionExpressionBuilder`, including transaction expressions.
No client, credential, table or index is created by this adapter. SDK calls execute in
worker threads so they do not block the asynchronous application event loop.

The composition owner must supply region, table and index configuration, bounded SDK
timeouts/retries and least-privilege permissions for `GetItem`, `TransactGetItems`,
`TransactWriteItems` and `Query`. Queries address the configured index. The adapter
does not require `Scan`, table creation or administrative permissions. There is no
fallback store. The current Cases table is not silently reused with an incompatible key.

The concrete `read_submission(run_id=...)` extension returns pinned revision/document
metadata with a strong read. It validates the canonical payload digest before returning
`ReviewSubmission`. The original idempotency key is not stored; the loader uses the
internal opaque key `run-<uuid>`, which canonical payload hashing excludes. This loader
does not grant execution or source access. A worker must first claim its current run,
then recheck current source authorization through the document service.

## Storage schema

The table has string partition/sort keys `pk` and `sk`. Every row carries
`storage_schema=job-store-v1`, a finite `kind` and a positive optimistic `version`.
Unrecognized schemas, fields, inconsistent keys/index attributes and invalid data fail
closed. Identifiers are bounded opaque strings or UUIDs; counters use bounded integers.

| Entity | Partition key | Sort key | Content |
| --- | --- | --- | --- |
| Job | `JOB#<job UUID>` | `META` | Principal/case IDs, current run, state, counters, task IDs, finite problem |
| Run | `RUN#<run UUID>` | `META` | Owning job, revision, document references, canonical digest, fence, result version, active lease |
| Attempt | `RUN#<run UUID>` | `ATTEMPT#<attempt UUID>` | Owner UUID, fence, claim/close times, finite outcome |
| Result reference | `RUN#<run UUID>` | `RESULT#<20-digit version>` | Digest, statuses, artifact IDs and finding count |
| Outbox | `JOB#<job UUID>` | `OUTBOX#<20-digit sequence>` | Run, random dispatch token, schedule, retry count, pending/sent/abandoned |
| Idempotency | `IDEM#<namespace digest>` | `META` | Principal, canonical payload digest, initial job/run IDs |

The idempotency namespace is SHA-256 of a JSON array containing principal ID and caller
key. This avoids delimiter ambiguity and stores neither the raw key nor any credentials.
Conditional uniqueness applies to this primary key, never a secondary index. Run IDs
are globally unique in this table, including across jobs. Results are append-only.

Job, run, attempt, outbox and result history are separate rows, not an expanding job
blob. A run holds at most 64 document references. A job holds at most 256 open task IDs;
a result reference holds at most 256 artifact IDs. Serialization caps each row's JSON
representation at 128 KiB, with bounded field lengths and numbers. This stays below
DynamoDB's item and transaction limits for the two-to-four-row writes used here.

No source contents, extracted text, private filenames/URLs, storage locators, result
bodies, mapping data or secrets belong in these rows. Admission supplies trusted opaque
principal/case/document identities; syntactic identifier validation cannot detect an
identity deliberately populated with private information. Source snapshot authorization
and full result-body storage remain separate adapters.

## Atomic writes and authority

Admission commits job, run, idempotency and first outbox in one conditional transaction.
A replay rechecks case permission and canonical digest. A losing create or lost SDK
response performs a strong idempotency read to resolve a committed admission, without
creating another job or outbox entry. A successful replay returns the current job view.

Mutations use transactional compare-and-swap on every changed row's previous version.
Fencing increments are computed inside the store from a serializable snapshot and
committed under the run version condition, so two readers cannot commit the same next
token. Claim also checks the current run and absence/expiry of a lease and creates an
attempt record atomically. No application-process lock provides production authority.

Heartbeat and finish require the current job/run, attempt UUID, owner UUID, fencing
token, expected result version and **stored deadline strictly greater than `now`**.
The lease conditions also appear in the transaction. An expired worker cannot revive
its lease or publish even before a reconciler takes over. Heartbeat never shortens a
stored deadline. Reclamation requires an exact candidate deadline and fence, and a
deadline at or before `now`; a heartbeat invalidates the old candidate. Callers supply
trusted epoch seconds from synchronized clocks. DynamoDB does not supply a server-clock
predicate: callers must refresh time promptly and bound SDK latency near lease expiry.

Finish commits state, lease release, attempt outcome and optional append-only result
reference together. Reclamation closes the dead attempt and enqueues its replacement
in the same transaction, subject to the independent takeover ceiling. Retry finish and
retry scheduling are separate port operations; the recovery index finds stranded retries.
Cooperative cancellation and cancellation priority follow the unchanged state machine.

Human resume creates a new run and outbox with the job pointer change. Old run/attempt
history remains intact, and fencing restarts in the new run. The new payload digest
includes the new revision. Document references are inherited because the existing port
only supplies `RunReference`; replacing sources requires an independently authorized
admission flow. A late dispatch confirmation for an old run marks that round sent but
cannot move the current run to dispatched. Rescheduling rechecks the exact dispatch
token, run, attempt count and schedule; finished jobs abandon the pending round.

Job reads first locate the run and then use `TransactGetItems` to return a consistent
job/run pair. A concurrent run-pointer change permits at most three snapshot attempts.
Every mutating decision is protected again by database conditions when it commits.

## Sparse recovery index

The configured GSI uses string `recovery_pk`, numeric `recovery_at` and a `KEYS_ONLY`
projection (additional projected fields are unnecessary). The three partitions are:

| Partition | Rows present | Timestamp in epoch seconds |
| --- | --- | --- |
| `PENDING` | Pending outbox entries | `available_at` |
| `LEASE` | Runs with active lease attributes | `lease_expires_at` |
| `RETRY` | Jobs in `retryable_failed` | `updated_at` |

Sent/abandoned outboxes, released runs and jobs leaving retryable failure omit both
index attributes. Recovery uses ascending, bounded Query pagination (1-1000 candidates,
at most 100 per page), never a table scan. Each candidate is reread strongly from the
base table and every action remains conditional. Eventual GSI lag can return fewer than
the requested limit; another reconciler pass supplies progress. These three partitions
are intentionally unsharded for the bounded deployment; sustained high volume needs a
versioned sharding strategy and capacity measurements. No TTL is applied to authority,
idempotency or history records; retention/deletion requires a separate policy.

## Errors and retries

Failed conditions raise `ConditionFailed` with fixed text and are not retried here.
`DynamoDBJobStoreError` subclasses `ServiceFault`, preserving the public finite
`capability_unavailable` problem. Its internal `reason` is one of `throttled`,
`unavailable`, `denied`, `invalid` or `corrupt`; `retryable` is explicit. Transaction
conflict and throttling remain transient infrastructure failures, distinct from a lost
condition. Invalid/corrupt rows and denied operations fail without transient retry.
SDK messages, request IDs, cancellation items and endpoint details are not emitted;
exception chaining is suppressed at the boundary.

Each transaction supplies one random `ClientRequestToken`, reused by retries of that
SDK call. There is no unbounded adapter retry loop. A cancelled await or lost response
may still have committed: queue/reconciler retry must read current state rather than
assume rollback. Idempotent admission resolves this window as described above.

## Validation and remaining limits

`tests/integration/test_dynamodb_job_store.py` runs every unchanged shared
`JobStoreContract` check with Moto and adds SDK Stubber failures, separate-client/store
reconstruction, forced concurrent claims, rollback, lease-boundary and mid-transaction
races, stale dispatch/index candidates, pagination, bounded data and privacy checks.
Tests require the development dependency `moto[dynamodb]>=5,<6` and use explicit
synthetic credentials with intercepted SDK calls. Moto lacks transaction isolation
across threads, so the fixture serializes each individual SDK operation to emulate the
service guarantee; it does not lock whole store operations. Race tests deliberately
interleave reads and transactions across independent adapters.

These are offline service emulation and SDK request-shape results, not AWS durability,
IAM, capacity, throttle-backoff, cross-process server restart or GSI propagation evidence.
The production adapter has no resident job cache; all reconstructed state comes from
DynamoDB requests. Actual process replacement against a live table remains a live gate.

This subtask does not create infrastructure, wire SQS or Runtime, publish artifacts,
authorize human responses or perform AWS calls. The unchanged JobStore port has no
external transaction-enlistment parameter: `resume_after_human` makes job/run/outbox
atomic, but cannot also commit another repository's revision/task response. That
cross-store atomicity remains an explicit integration blocker; it is not claimed here.
Result publication fencing applies to the committed reference; the body store must use
digest-addressed or attempt-scoped immutable objects so a stale first write cannot block
the legitimate winner. PR #34-#39 dependencies and complete production execution remain
subject to their separate integration and review gates.
