# Local persistent review storage

Status: optional implemented adapters for the existing job/human-task/result ports.
No default API entrypoint or worker deployment is changed. See
[ADR 0053](adr/0053-local-review-transactions.md) for transaction and trust boundaries.

## Composition for the integration owner

Use a dedicated database under a private directory configured by the operator.
Do not reuse the document-object SQLite database or a database belonging to another
runtime. The adapter rejects unrelated schemas and conflicting job policies.

```python
from pathlib import Path

from appraisal_review.adapters.local.review_database import SQLiteReviewDatabase
from appraisal_review.adapters.local.sqlite_human_task_store import SQLiteHumanTaskStore
from appraisal_review.adapters.local.sqlite_job_store import SQLiteJobStore, SQLiteResultStore
from appraisal_review.application.human_tasks import HumanTaskService
from appraisal_review.application.review_jobs import ReviewJobService

state_directory = Path("artifacts/review-state").resolve()
state_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
database = SQLiteReviewDatabase(state_directory / "review.sqlite")
jobs = SQLiteJobStore(database)
tasks = SQLiteHumanTaskStore(jobs)
results = SQLiteResultStore(database)

job_service = ReviewJobService(jobs, results, policy=jobs.policy)
human_task_service = HumanTaskService(tasks)
```

A injects these services and a trusted PrincipalResolver into the existing
create_app factory, together with the already selected controller/adapters.
This snippet does not provide authentication, start a server, or execute a worker.
Each API/worker process constructs its own database/adapters against the same
private local path. Restarting those processes does not reset durable state.

The default lock wait is 0.25 seconds; configured values must be positive and no
more than 5 seconds. Lock contention reports capability_unavailable through the
existing application error envelope. Keep the original response key/payload when
recovering an unknown result. Permission changes still apply before replay.

## Worker handoff and human responses

1. Submit the existing ReviewSubmission, binding the actual revision and document
   references. The adapter persists job, run, idempotency record and initial outbox.
2. Dispatch and claim through the existing ReviewJobService/OutboxDispatcher.
3. When a claimed worker produces human tasks, call
   `await tasks.finish_with_tasks(attempt, snapshot=snapshot, tasks=task_tuple, now=now)`.
   This validates the attempt, source references, current revision/run and complete
   task set, and commits waiting state/material/tasks atomically. Do not call a
   separate wait_for_human followed by ad hoc task writes in the worker path.
4. Route human commands through HumanTaskService.respond. It derives changes using
   the existing contracts. SQLiteHumanTaskStore.commit_response commits all effects
   and the full accepted response in one transaction.
5. Reconcile the continuation through the existing outbox. Duplicate delivery or
   a stale attempt cannot claim or publish over a newer run.

register_snapshot is an adapter method for trusted bootstrap/recovery against an
already matching job head. It is not a public task-creation endpoint and does not
replace the atomic worker handoff. A reference-only fixture whose submission uses
a placeholder material digest must be rebuilt with the actual snapshot reference;
the persistent adapter intentionally rejects inconsistent bindings.

## Receipt recovery and state interpretation

The existing port method read_receipt(principal_id, key) provides exact durable
lookup for trusted application code. A/D must still agree the public authorized
lookup contract. Existing response POST replay remains available through the
unchanged HTTP service and rechecks the caller's current task permission.

- A committed receipt replays unchanged, even though the first call consumed the
  task version. A changed payload under that key conflicts.
- A missing receipt does not prove an in-flight request cannot commit later. Reuse
  the same key and payload or reconcile; do not invent a replacement operation ID.
- Rejection stores no revision or continuation. Other tasks keep the job waiting;
  rejecting its final open task leaves it failed, not completed.
- Manual corrections preserve raw confidence but still use the current legacy
  provenance model. The pending manual/adopted-value contract is not implemented.
- Results remain governed by the application's business/publication checks; a
  durable job or response does not make the valuation formally complete.

## Scope and operational limits

### Configured standard formula batch

SQLiteJobStore accepts optional trusted `source_scopes` configuration. Each
SourceProcessingScope binds an exact complete source set to a versioned
`privacy_handling="not_applicable"` setting. This is not a scan result or approval.
Submission and same-source correction bind it atomically; replay preserves the
original scope. Previously stored definitions do not enable exemptions on new
jobs unless explicitly supplied in server composition.

The internal `read_source_processing` accessor requires owning-job/run authorization
before public projection. See the [batch integration guide](formula-batch-integration.md)
for composition and outstanding A/C source-contract changes. The source exemption
does not relax database permissions: stored case material and accepted responses
still use the private storage boundary below.

### Storage limits

This is single-host local persistence with private filesystem access, short
synchronous transactions and namespace scans. Multi-host/multi-tenant deployment,
encrypted backups, bulk migration and high-volume indexing are not delivered.
The operating-system owner remains trusted. Use a consistent SQLite backup process
if retaining state; copying a live database file alone is not a backup protocol
provided by this implementation.

Schema version 1 is created only in a new/empty dedicated database. Unknown versions,
nonprivate paths and incompatible existing tables fail explicitly. There is no
automatic migration or deletion of earlier state. Job policy is fixed when the
database is initialized; reopening with a different policy is refused.

The database contains accepted proposals and case material, including citations.
Keep it out of commits, logs and submission artifacts. Tests create synthetic data
only in isolated temporary directories and remove no user's existing database.

## Focused validation

```bash
.venv/bin/python -m pytest \
  tests/unit/test_review_database.py \
  tests/integration/test_sqlite_job_store.py \
  tests/integration/test_sqlite_human_tasks.py \
  tests/integration/test_source_processing_scope.py
```

The suite includes the existing 31 job-store contract checks, SQLite lock conflicts,
immutable result bodies, source bindings, stale attempts, full response rollback,
fresh adapter/application instances, and real subprocess exits/races. Synthetic
HTTP tests may require the same unrestricted local test environment used for the
existing API suite. This is not real-case, cloud or deployed-runtime acceptance.
