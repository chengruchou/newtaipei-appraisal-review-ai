# Local SQLite review transactions

Status: accepted for an optional local storage adapter; runtime composition remains
with the integration owner.
Date: 2026-09-12.

## Context

The reference job and human-task adapters retain state in memory. Their rollback
behavior expresses the transaction contract but cannot recover after a process
restart. Persisting a task separately from its job, receipt and outbox would leave
partially applied human responses and risk duplicate continuations.

The existing job/task/result ports already describe the required operations. A
local storage implementation can be added without changing public DTOs, endpoints
or the application state machine. New condition/candidate/Decimal contracts remain
separate integration work.

## Decision

Implement SQLiteReviewDatabase, SQLiteJobStore, SQLiteHumanTaskStore and
SQLiteResultStore behind the existing ports. Each adapter reads durable rows on
every operation. No in-memory task or job store is composed inside this adapter.
All job transitions continue to use application/job_state.py.

Use a dedicated private database with schema version 1 and typed JSON records in
namespaces for jobs, runs, attempts, outbox, submissions, material, revision chain,
head, tasks, accepted responses and result references/bodies. A unique
(kind, key, subkey) constraint enforces identities. Material, accepted responses,
result versions and revision-chain entries are append-only through the adapters.
Private model records are adapter persistence details, not a new public schema.

Each write acquires BEGIN IMMEDIATE before reading admission state and commits
with synchronous=FULL. Responses commit all task, material, head, sibling-task,
run, outbox and receipt effects together. The full AcceptedResponse is stored
alongside its payload digest, resulting receipt and commit timestamp. Rejection
records the answer and updates the waiting/failed projection without a new run.

The transaction rechecks task identity/version, revision/material/side binding,
current job run, open task membership, actor ownership, human actor kind, payload
digest and replay identity. Current permission checks remain the application
service's responsibility using its trusted request Principal. This adapter does
not introduce a persisted grant store or claim atomic in-flight revocation.

Worker material/task registration uses finish_with_tasks to commit the fenced
waiting transition and all tasks in the same transaction. Material document
references must equal the fixed run source references. Expired leases cannot
heartbeat or finish even before a reclaimer runs. Stale outbox acknowledgements
cannot transition a newer run's queued state.

Result bodies use a separate record namespace and ResultStore adapter. The existing
ReviewJobService writes bodies before fenced reference publication and authorizes
reads; unreferenced bodies are not exposed through that service. The local database
contains private material as well as control-plane metadata and must not be logged,
exported as diagnostics, or committed.

## Recovery and concurrency

Independent connections/processes coordinate through SQLite, with a bounded lock
timeout (default 250 ms, configurable up to 5 seconds). A timeout becomes the existing
capability_unavailable fault; a uniqueness/state race becomes ConditionFailed or
version_conflict as appropriate. No caller should replace an unknown operation's
idempotency key simply because an operation timed out.

Connections always close. Exceptions and cancellation before commit roll back the
whole transaction. A process exiting after the response row is inserted but before
commit recovers without the response or its continuation. A process exiting after
commit but before returning the receipt recovers the original receipt and exactly
one continuation. Database commit is the authority for that distinction.

The adapter uses short synchronous transactions inside its async port methods,
without await points while a transaction is open. Task cancellation is observed
outside that synchronous interval; a caller cancelled after commit must treat the
outcome as unknown and recover by the original operation key. Injected cancellation
inside transaction code rolls back like any BaseException. This is a bounded local
execution choice, not a high-throughput async database design.

## Filesystem and compatibility

Require an operator-owned private parent directory and a regular, owner-only
database file with one hard link. Reject symlinked parents/files and recheck
permissions on every connection. Never derive the database location from a request.
SQLite journal files stay within the private directory. Do not place this database
on S3, a shared network filesystem or untested multi-host replicas.

Persist the job policy on initialization and reject a conflicting policy on reopen.
Refuse unknown schema versions and unrelated pre-existing databases without changing
their schema. There is no automatic migration from the document-object database,
memory state, or another team's runtime database. Changing private filesystem
contents by the trusted operating-system owner is outside the adapter's threat
boundary; this is not encrypted or tamper-proof storage against that owner.

## Limits and validation

These adapters support the current factor-side response contracts. They do not
implement canonical condition tasks, manual provenance migration, system-candidate
receipts, a complete valuation/export snapshot, shared identity infrastructure or
the actual worker loop. Revision heads follow the existing job-scoped port; this
does not add a cross-job case-wide editing authority. The integration owner must
define case-to-job routing and the canonical runtime before real-case use.

Scans deserialize the selected namespace and are intended for a small controlled
local workload. Large case inventories need indexed projections and measured
capacity work. The database can contain sensitive source excerpts and must stay in
private local state, not a Git change or public artifact.

Validation includes the unchanged job-store contract suite, independent-connection
lock contention, real subprocess crashes before/after commit, competing subprocess
responses, failure/cancellation after each response write stage, replay permissions,
stale task/run/lease rejection, fixed source mismatch, immutable revision identity,
result publication/reopen and synthetic HTTP application replacement. Real Shulin
acceptance, deployment and cloud integration are separate and not claimed here.
