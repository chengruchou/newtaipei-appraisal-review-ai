# Service contract authority and migration

This document defines the integration ownership and compatibility rules for the
current source union. **The local composition is being integrated; a complete
consumer migration and deployed release are not accepted.** The Python/Pydantic
models are validation authority. JSON Schema, OpenAPI, fixtures, generated clients
and Runtime adapters must be generated or checked against that same model set.
Earlier conflicting M0/branch claims are preserved in the
[historical contract snapshot](history/2026-09-11-pre-convergence/service-contracts.md).

## Ownership and consumers

| Canonical source / boundary | Steward | Producers and consumers | Current integration duty |
| --- | --- | --- | --- |
| `domain/service_contracts.py`: document/revision/run/value/task/response/result and controlled records | Integration owner; domain stewards review their gates | #36 workflow, #38 API, #39 browser, #42 extraction, #43 Runtime, publication | One current model union; no competing task definition with the same version |
| `domain/task_contracts.py`: TaskView, TaskSubjectView, TaskListView, RevisionListView, ResponseReceipt | Human-task/API owner | #38 routes -> #39 client; #43 resumed execution | Use server-provided subject/type/unit; consume committed receipt and revision chain |
| `domain/job_contracts.py`, `ports/jobs.py`: jobs, claims, outbox and result references | Job/Runtime owner | API submission, dispatcher, worker, reconciler, publication | Retain ownership, cancellation, lease/fence/current-run and result-version CAS |
| `ports/human_tasks.py`, `application/human_tasks.py` | Human-task/API owner | Authenticated task operations -> combined store -> Runtime | Atomic response, actual applied values and empty-job ownership reads |
| `application/workflow_tasks.py`, `ports/service.py` local task/continuation seams | Controlled-workflow owner | Existing local workflow/reference consumers | Explicit adapter to API receipts; local response/re-entry is a different interface |
| `SQLiteReviewStore`, `SQLiteResultStore` | Local durable adapter owner | Same API/jobs/Runtime consumers through existing ports | Actual local transactions/restart, immutable result candidates and committed reads |
| `domain/document_transfer.py` and snapshot resolver | Document/source owner | Local exact export -> C2 -> extraction and Runtime | Authorized immutable versions and refreshed per-run source authority |
| `domain/artifact_publication.py`, manifest/object repositories | Publication owner with PDF/domain approval stewards | Real writer -> publisher -> authorized download | Exact assets/sources/grant plus current attempt; grant revocation and manifest transaction |
| `domain/pdf_models.py`, `pdf_types.py`, `ports/pdf.py` | PDF owner | Controller, local/S3 writer, publication and local backfill | One writer protocol; complete contexts and approved byte-bound assets |
| Schema/OpenAPI/fixtures/CLI/frontend generation | Integration owner, each consumer owner verifies | `schemas/`, `examples/`, CLI, #39, packaged #43 | Generate and validate together; no field dropping to hide incompatibility |

A–E names in older documents are responsibility roles, not usernames or automatic
approvals. The integration owner owns the composition root and cross-module
adapters; individual repairs do not independently redefine shared wire models.

## Canonical #36 and #38 migration

The current merge retains #36's `service_contracts.py` union and #38's
`task_contracts.py` API projections. It preserves `controlled-action-v1` as the
explicit version for controlled snapshot/action/receipt/event/pause records.
Service records continue to declare `service-v1`; that label alone is not proof
that older strict clients accept the expanded task shapes.

Both task extensions originate in open, undeployed PR work. The integration
release requires coordinated regeneration of every v1 task consumer. Older
`extra="forbid"` clients will reject additional serialized fields and enum values,
even when the new fields have defaults. Do not weaken their validation or silently
strip evidence to make them appear compatible.

| Difference | Current source meaning | Required migration |
| --- | --- | --- |
| `HumanTask.reason_code`, `affected_subject_ids` | Shared task extensions; actual handoffs must bind meaningful blockers/subjects | Regenerate task fixtures/schema/client; retain empty/null draft defaults only where the model allows them |
| `TaskKind.EVIDENCE`, `ResponseAction.SUPPLY_EVIDENCE` | `evidence_supply` / `supply_evidence` are the current canonical values | Replace incompatible draft evidence-action spelling explicitly; a model enum does not mean every route implements that action |
| #38 `ResponseReceipt.resumed_run` | Records the revision and newly scheduled run committed with the answer | Browser/Runtime consume this receipt; do not deserialize it as HumanResponseResult |
| #36 `HumanResponseResult.next_run` | Existing controlled local response/re-entry result with different evidence and effects | Keep that internal contract or implement an explicit mapping that checks committed effects; field renaming alone is insufficient |
| `RunReference.runtime_session_id` and other union fields | Runtime session belongs to an attempt, distinct from the durable run | Strict consumers must regenerate and preserve identity separation |
| `TaskSubjectView` | Exact authoritative observation, subject ID, required type/unit and unit-required flag | #39 fetches it for corrections; never derive a unit or subject string from a display name |
| `application/human_tasks.py` API vs `application/workflow_tasks.py` | Authenticated API service vs controlled local reference workflow | Update imports and composition intentionally; do not overwrite one implementation with the other |
| Legacy `ArtifactManifest` and new `FencedArtifactManifest` | Legacy single_context/local_only class stays unchanged; artifact-manifest-v2 binds primary context plus all contexts, review_contexts scope and fenced publication | Projection owner implements the ServiceResult.artifacts union and actual service coverage; all consumers regenerate to accept the new version |

**Selected compatibility policy:** synchronize the undeployed service-v1 task
schema and every compiled consumer against one union. The #39 client is
regenerated against that union; its exact generated artifacts and transport
regressions remain part of the integration gate. Legacy frozen commands and
baseline success responses remain compatible obligations. New serialized nested
`HumanTask` keys require upgraded consumers: an old extra-forbid client is not
compatible merely because old payloads parse with defaults. The integration tree
must not maintain two incompatible v1 task definitions in parallel.

The authenticated API now handles canonical EVIDENCE through the trusted
correction path: resolved citations must be nonempty, original confidence zero
remains zero, and a changed revision resets confirmation. The integration owner
reports 14 focused regressions passing for this path; that is scoped evidence,
not full browser or cloud acceptance. `TaskSubjectView` remains a separate
projection; `TaskView` keeps only its task and subject ID fields.

The historical controlled local `HumanResponseResult` is a distinct internal
event/result contract. `CanonicalResponseReceiptAdapter` maps it only after
loading the actual committed canonical receipt, matching the current job, task,
command, exact revision, superseded tasks and resumed run. It does not write
either store or infer authority from a renamed field; missing/stale effects fail.
Both confirm/correct responses and forbidden mismatches have regression coverage.

The established `/health`, `/v1/validate`, `/v1/reviews` and invocation contracts
remain explicit compatibility obligations. Their AgentReviewRun/EntryProblem
response boundary is separate from the service-v1 job/task/result API. A migration
must preserve promised legacy shapes or introduce a documented versioned adapter,
with actual transport regression coverage.

Migration validation must cover all of these consumers, not only Python imports:

1. Export the shared schemas and deterministic fixtures with
   `PYTHONPATH=src python scripts/export_service_contracts.py`.
2. Regenerate actual mounted OpenAPI and the #39 generated client using the
   frontend's declared generation/formatting workflow; verify clean generation has
   no drift and strict response validation accepts real API output.
3. Validate CLI and fixture parsing, empty task collections, correction subject
   metadata, command/receipt round trips, rejection and r1→r2→r3 transitions.
4. Validate the #43 packaged factory/entrypoint, source-snapshot rebinding and
   resumed-run receipt mapping against those exact models.
5. Test legacy HTTP/invocation shapes against the selected compatibility policy.
   A schema export is not proof that a deployed old client remains compatible.

The integration generation and transport tests exercise these consumers together.
Exact final checkout validation remains distinct from deployed-client acceptance.

## Serialization and identity

- Unknown fields, enum values, versions and nonfinite numbers fail. Integers reject
  booleans where the model requires strict counters. JSON uses UUID/enum strings,
  arrays for tuples and explicit nulls for absent values.
- `PublicValue` distinguishes zero, blank, missing, not_present and not_applicable,
  preserving raw text, authoritative units, measured confidence and citations.
- Review schema `2.0` remains authoritative for deterministic domain material.
  Decimal strings preserve scale. `review-material-json-v1` uses the existing
  Pydantic JSON-mode content digest, sorted compact JSON with default ASCII
  escaping, UTF-8 and SHA-256; array order and explicit defaults matter.
- Rule references digest the complete scoped rules with that same canonicalizer.
  A source-only preparation revision uses `source-documents-json-v1`, empty rules
  and preparation-only states; it does not change review-material digest meaning.
- Full field-map hashing uses `field_map_sha256`, sorted compact JSON with
  `ensure_ascii=False`. Template, font and output hashes identify actual bytes.
  These canonicalizers are not interchangeable.
- Submission identity excludes the caller's idempotency key and sorts its unique
  document set by document ID. Changing a document version/hash or revision still
  changes the submission identity. SQLite's reconstructed worker key is internal.
- Subject IDs come from the server. JavaScript JSON stringification cannot safely
  reproduce Python's context-key ASCII escaping for arbitrary non-ASCII IDs.

Digests establish identity, not trust. No signature, principal, confirmation or
approval is inferred from a digest or a fixture. Existing signed content must not
be silently re-signed after a schema or source change.

## External input and independent authority

HTTP bodies propose authorized document IDs, commands and values. Trusted adapters
supply principals, case permissions, allowed actions, source locations, execution
origin, budgets and approvals. The cloud-facing API never accepts arbitrary local
paths, source PDFs, private maps or credentials. A local bridge is a distinct
Origin/session/source-constrained boundary.

A task answer rechecks its actual owning job, task version/state, exact revision,
side digest, allowed action and principal-scoped idempotency payload. Unknown jobs
and foreign jobs remain indistinguishable. A legitimate job may have zero tasks;
ownership cannot be inferred from its first task row.

Corrections retain proposal and actual applied value separately. A submitted
confidence or citation does not become a committed observation merely because it
was in the request. Human confirmation of eligible confidence-zero observations
remains supported, binds the exact side, and does not grant material approval.
New revisions invalidate mismatching confirmations and independently signed
material/publication grants. Evidence supply, material approval, rule approval
and publication approval are purpose-specific. Canonical EVIDENCE is supported
through the trusted correction path; rule/material/publication approval still
requires its independent purpose-specific authority.

A rejection changes no material and schedules no new run. It removes that task
from the job's open projection; remaining tasks keep the job waiting, otherwise
it fails with a sanitized problem. An accepted changed revision supersedes old
open siblings and schedules a new run with the newly committed document refs.
Replayed registration/response cannot append r2 twice.

## Actions, budgets and durable execution

The selector receives a detached `WorkflowSnapshot`. Trusted policy derives the
allowed actions from exact sources/rules, state, blockers and remaining budget.
Typed action arguments bind page/source or revision/context; a proposal cannot
self-declare approval, switch principal, invent a new action or acquire more budget.
Execution re-admits the selected action against current trusted state.

Action-specific receipt validation is inside the protected execution path. Invalid
receipts, failed trace writes and unknown external results leave failed or
quarantined state, not an untraced verified result that can later replay as success.
Selection failures and decision events are separate streams with a shared causal
frontier; consumers must retain both. Reconstructing a coordinator or retrying the
same run cannot refill model/retry allowance or erase failures. Unknown external
call outcomes retain their reservations until explicit recovery/reconciliation.

`SQLiteReviewStore` atomically persists job/task/revision/outbox/receipt effects.
Its local result store selects immutable candidates through committed references.
The separate workflow run ledger has its own reservation/trace recovery boundary;
it is not automatically atomic with jobs or publication. See
[ADR 0030](adr/0030-sqlite-local-review-transactions.md),
[the workflow ledger](workflow-run-ledger.md) and
[publication integration](artifact-publication-integration.md).

## Job and result status meanings

| Job status | Execution projection | Meaning |
| --- | --- | --- |
| queued | queued | Job/run/outbox persisted; no execution claim |
| dispatched | queued | Queue accepted responsibility; not completed work |
| running | running | Current attempt must still hold its lease |
| waiting_for_human | succeeded with needs_review | Attempt ended; case did not pass |
| retryable_failed | running | Bounded recovery remains; not business success |
| failed | failed | Terminal sanitized failure |
| succeeded | succeeded | Current result reference committed |
| cancelled | failed | Exact cancelled status remains in JobStatusView |

New submission returns 202/JobAcceptance only after durable admission. Exact replay
returns the stored job view, not a fabricated new queued acceptance. Result reads
fail until a committed matching body exists. Retry/redrive are controlled operator
recovery, not unscoped public commands. Recorded cancellation and lost/expired
leases prevent new publication; stale attempts cannot overwrite a new result.

| Execution / business / artifact | Meaning |
| --- | --- |
| queued or running / null / not_requested | No terminal result claim |
| succeeded / needs_review / not_requested | Review ran; evidence/blockers remain |
| succeeded / failed / not_requested | Review ran but validation/verification failed |
| succeeded / verified / not_requested | Verified review; no PDF requested |
| succeeded / verified / unavailable | Required writer/assets not configured |
| succeeded / verified / unsupported_contexts | Writer capability or full map coverage is insufficient |
| succeeded / verified / simulated | Test writer; no real PDF |
| succeeded / completed / written | Actual verified output and matching manifest |
| failed / optional failed / not_requested | Sanitized execution/publication failure |

Process success is not case approval. Real multiple-context writing requires every
comparison and mapped field to be represented in the service/publication manifest.
The selected projection preserves the existing `ArtifactManifest` class and its
explicit `single_context` / `local_only` semantics unchanged. A separate
`FencedArtifactManifest` declares `schema_version="artifact-manifest-v2"`, primary
`context`, complete `contexts`, `review_contexts` scope, fenced publication and
the digest/font/writer bindings. `PublishedArtifact` also retains all contexts.
`ServiceResult.artifacts` accepts the legacy/new union; the legacy fields are not
widened. All undeployed consumers must regenerate to accept that versioned union.
Reading an old payload remains supported; it does not establish that an old
extra-forbid consumer understands the new manifest version or serialized keys.
The projection owner implements and validates the actual producer/service/reader
path. Writer-only `additional_results` support is insufficient acceptance evidence.
`durable=true` requires an actual committed durable result; it is not proof of
cloud deployment or approval.

`ServiceVerification` retains status, ordered critical errors and warnings,
including multiplicity. A warning is not promoted into a blocker. Fixed diagnostic
codes include source_binding, source_registry_required, verification_blocker and
verification_warning. Unknown internal details use generic sanitized messages;
raw paths, storage URIs, excerpts and credentials do not enter operational errors.
Authorized evidence-bearing findings remain separate from those diagnostics.

`ServiceProblem` exposes invalid_request, unauthorized, not_found, version_conflict,
capability_unavailable and execution_failed with static messages. Job/task transport
maps them to 422/403/404/409/503/500 without leaking request content. The legacy
EntryProblem envelope remains a distinct compatibility obligation.

## Independent remaining gates

A real PDF file is not an authorization certificate. Publication requires exact
source versions, template/map/font bytes, complete context/field coverage, writer
version, immutable output digest/version and independently granted authority,
plus the current job/attempt conditions. Downloads reauthorize every request.
Local reveal/backfill stays outside cloud publication and preserves the downloaded
placeholder artifact.

Runtime package/container success, live AWS/IAM/storage/recovery, designated-model
quality and formal human approval remain separate acceptance gates. `rehearsal-v1`
validates independently bound observations; synthetic signatures or a consistent
attestation do not prove that collectors ran or that raw observations are complete.
See [cloud acceptance](cloud-acceptance.md) and [ADR 0031](adr/0031-rehearsal-evidence.md).
