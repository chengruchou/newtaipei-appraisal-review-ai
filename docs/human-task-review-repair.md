# Human-task review repairs

The response ledger keeps the submitted proposal separately and derives its
corrected value from the committed observation and stored citations. A request
cannot raise raw confidence, substitute evidence, erase an authoritative unit,
or reinterpret a numeric value under a different type. Explicit confirmation of
eligible confidence-zero observations remains a separate supported operation.

Collection and task authorization reads the owning job independently of task
rows. An owned queued or successful job can return an empty task list and its
available revision chain. Unknown jobs and foreign jobs remain indistinguishable.

Rejection removes the answered task from the job projection without changing
material or scheduling a run. Other open tasks keep the job waiting. With no
open tasks remaining the job becomes failed with a sanitized conflict problem;
rejection cannot imply completed or verified. Task registration at the unchanged
head is idempotent for identical records. A changed record, replaced digest or
stale head is rejected before any write; repeated registration cannot append r2
twice. Exact snapshot reads verify the complete revision reference.

The local response adapter rolls back task, revision and head changes for all
exceptions, including cancellation while waiting for the reference job write.
This is a single-process reference guarantee. It depends on the in-memory job
adapter having no suspension after acquiring its mutation lock; it does not make
an arbitrary external adapter transactional. Durable composition must commit the
task, revision, job, receipt and outbox in one storage transaction.

## Authoritative correction metadata

GET /v1/review-tasks/{task_id}/subject returns the new TaskSubjectView projection:
task_id, exact revision, server subject_id, stored PublicValue observation,
required_type, required_unit and unit_required. It is read from the exact task
snapshot after job authorization and side-digest validation. Numeric corrections
without an authoritative unit are blocked; the UI must never guess from a name.
TaskView retains its outer task/subject shape and TaskSubjectView stays separate.
The integrated undeployed service-v1 union adds nested HumanTask fields from #36;
all strict consumers must regenerate even when old inputs still parse. Legacy
frozen commands and baseline HTTP/invocation success remain compatibility
obligations. See [canonical migration](service-contracts.md) for the current policy.

## Regression evidence

At fb901866d7c05e43e68ce2dcd214f002f13517f7, six minimal regressions failed:
corrected metadata, sole/multiple-task rejection, owned empty collection,
follow-up registration and cancellation rollback. The corrected suite adds
exact-unit/zero-value tests, strict OpenAPI response validation and foreign-owner
rejection. Tests use synthetic isolated material and do not authorize real rules.

Run the human-task tests, job state/store conformance, schema export checks,
Ruff, format, mypy and the full suite before publication. Later integration and
real cloud/identity acceptance are separate from these reference-adapter results.
