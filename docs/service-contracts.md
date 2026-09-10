# Service v1 contract authority

Status: M0 working-branch implementation based on main
`ea55043d90aa21e6f0a7e3fe05aa34ef8a3553d3`; not a merged or deployed service.
[ADR 0013](adr/0013-service-foundation.md) records the trust boundaries.

## Reuse and delivery inventory

| Responsibility | Directly reused | Added in M0 | Subsequent delivery |
| --- | --- | --- | --- |
| Review and evidence | ReviewMaterial, SourceCitation, ComparisonContext, findings, Controller and independent verifier | Immutable serialized revision helper and public references | Durable revision repository and full-case acceptance |
| Entrypoints | ReviewAdapters, build_controller, execute_review, legacy HTTP/invoke | Configured local factory and separate service envelope | Authenticated web assembly and Runtime wiring |
| Human authority | OS reviewer, side confirmation, exact-material signed receipt | Task/response/authorization DTOs and pure admission checks | B identity, transactional human-task APIs and correction workflow |
| PDF | Single-context PDFWriter, LocalPDFWriter, policy/map hashes, validated output | Explicit local writer injection and manifest projection | E formal CJK/multiple-context contract; D live publication |
| Cloud and action policy | Injected extraction/storage adapters, existing audit events, synthetic smoke | Protocols, allowed actions, decision records, fixtures | A selector/budgets; D jobs/outbox/leases/fencing/recovery |

## Ownership and wire authority

All new wire models are in `domain/service_contracts.py`. The validation source
is Python/Pydantic; `schemas/service-v1.json` is its serialization-schema bundle.
Use `#/$defs/ModelName` from the example index, not an untyped arbitrary object.
Regenerate with `PYTHONPATH=src python scripts/export_service_contracts.py`.
`examples/service-v1/index.json` maps each fixture filename to its model. Fixtures
have synthetic identities and illustrative hashes; they are not authorization,
live task responses, downloadable PDFs or observed execution evidence.

| Types / boundary | Contract steward | Provider -> consumer | Current behavior / reserved scope |
| --- | --- | --- | --- |
| DocumentReference; DocumentResolver | D | Authorized document API -> B/A/runtime | URI-free DTO; resolver protocol only |
| MaterialRevision, RevisionReference, ValueRevision; RevisionSnapshot | B | Trusted material assembly -> A/C/D/E | Detached immutable snapshots and new revisions; no database |
| RunReference, ReviewSubmission; JobRepository | D | Submission/dispatch -> B/C/runtime | Separate run, attempt and session; pure idempotency checks; durable operations reserved |
| JobReference, JobStatusView, JobAcceptance; JobStore, ResultStore | D | Durable job plane -> C/B/runtime | Mounted review-jobs routes over an injected store; in-memory reference adapter only, no cloud persistence |
| HumanTask, HumanResponse, AcceptedResponse; PrincipalResolver, HumanTaskRepository | B | Server task/admission -> C and subsequent run | Strict commands and pure permission/version checks; mounted human-task routes over an injected store |
| TaskListView, RevisionListView, ResponseReceipt; HumanTaskStore | B | Authenticated reviewer plane -> C/D | Authorized task and revision projections and one transactional response; in-memory reference adapter only, no cloud persistence |
| AuthorizationRecord | B, E for publication scope | Trusted authorizer -> job/publisher | Reserved metadata record; existing signed local receipt remains actual local authority |
| AllowedAction, ActionProposal, DecisionEvent, Budget | A | Trusted policy/executor -> C/D/evaluation | Pure admission and truthful event validation; no model selector, retry loop or event store |
| ServiceResult, ServiceVerification, VerificationDiagnostic, ArtifactManifest | B assembly, E coverage, D publication | Local facade / future publisher -> C | Callable local envelope, sanitized verification and real PDF manifest; durable publication reserved |
| ServiceProblem / ServiceFault | B with D | Guards -> transport adapters | Static sanitized codes; old EntryProblem remains the legacy transport boundary |

B coordinates shared changes; each steward reviews its trust boundary. A–E are
responsibility positions, not repository usernames or automatic assignees.

## Versions, serialization and digests

- `schema_version="service-v1"` is explicit on service models. Unknown fields,
  unknown enum values, unsupported versions and nonfinite numbers fail. JSON uses
  enum strings, UUID strings, arrays for tuples and explicit null for absent values.
  Strict integer counters reject booleans. `PublicValue` distinguishes present
  zero from blank/missing/not_present/not_applicable and preserves raw text,
  original confidence, unit and existing SourceCitation geometry.
- Existing review schema `2.0`, Decimal arithmetic, tolerances and rounding remain
  authoritative. Decimal serializes as a string, preserving scale. For example,
  a Decimal `0.0100` serializes as `"0.0100"`; numeric-equivalent presentations are
  not implicitly identical material. NormalizedValue retains its existing shape.
- `review-material-json-v1` means existing `content_digest(ReviewMaterial)`:
  Pydantic JSON-mode dump, `json.dumps(sort_keys=True, separators=(",", ":"))`
  with default ASCII escaping, UTF-8, SHA-256. Array order is significant. Existing
  finite floating-point observations are preserved through Pydantic JSON; never
  hash object repr or build a new float formatter. Consumers must preserve the
  exact JSON-mode material, including Decimal strings and explicit defaults.
- RuleReference hashes the complete existing ScopedRules with that same function.
  A revision identifies input document versions/hashes, rule versions/contexts,
  and exact material. Digests assert identity, not trusted origin.
- Full PDF map hashing remains `field_map_sha256`: JSON-mode full map, sorted
  keys, compact separators, `ensure_ascii=False`, UTF-8 SHA-256. PDF source and
  output hashes are SHA-256 of actual bytes. Do not substitute one canonicalizer
  for another.
- Submission digest excludes the idempotency key by using a fixed canonical
  placeholder and sorts the unique document set by document_id before the same
  content digest. Other fields, version and explicit defaults are included.
- v1 models reject extra fields, so even additive wire fields require coordinated
  schema/fixture and consumer rollout. Preserve v1; incompatible public evolution
  gets a new schema version and explicit adapter. The new InputManifest module
  re-exports through document_cli for compatibility; migrate internal imports now,
  retain that alias through v1 and remove only in a documented major migration.
  ArtifactStatus is the existing five-value type factored into an alias.
- PR #20's pre-merge correction adds nullable `ServiceResult.verification` and its
  finite diagnostic types to the proposed service-v1 bundle. B/C/D must consume
  the regenerated schema and fixtures together; older extra-forbid consumers
  cannot silently accept the added field. This is convergence of the unmerged
  M0 contract, not a change to any legacy HTTP/invocation schema.

## External versus trusted input

External submission contains document ID/version/hash/purpose and revision refs,
not file/S3 URIs. D must authenticate and authorize case/document ownership before
resolving the internal ResolvedDocument.storage_uri; validate content/version and
purpose again at use. A caller's digest, reviewer name, actor, role or verified
flag never grants access. PublicValue is a URI-free presentation of observations,
not a second trusted fact model. Existing SourceCitation supplies one-based page,
region, bottom-left bbox, excerpt and hash/version; legacy EvidenceRef with a local
source_file remains internal to ReviewMaterial.

Principal is trusted adapter output, never a body DTO. The future principal
adapter obtains identity/permissions from server context. M0 local configuration
is an operator-owned absolute-path allowlist with exact hashes. It uses the
existing Linux/macOS OS identity and private receipt store. It is a loopback,
single-operator workflow, not Windows approval, multi-user login or remote IAM.

## Revisions, confirmation, approval and publication

RevisionSnapshot stores serialized immutable material/metadata. Each property
read returns a detached object. `revise` checks case/new revision identity, copies
material, changes both case identity versions and removes every old confirmation.
Native authority survives only if the immediate parent side was `native_numeric`,
the candidate is still `native_numeric`, neither carries a human confirmation,
and the exact side digest is unchanged. That digest includes context, factor,
side, observation, citations and other reliability fields; it deliberately omits
method/confirmation to support explicit human confirmation. Digest equality alone
therefore cannot establish native origin. Changed/new/non-native sides remain
`model_proposed`, even after a later method-only relabeling. Re-extraction must
use the trusted extraction/assembly entry, not a revision label. Raw confidence
is never raised. Original snapshots remain
unchanged. B must populate ValueRevision's original/proposed/corrected views from
trusted old/new material; supplied original values are never authoritative.

This helper neither resolves corrections nor grants approval nor persists data.
The changes ledger is metadata, not an alternate signed fact payload. B must
atomically persist it with the exact material and parent using RevisionRepository;
mutating a detached value does not mutate the old revision. Hashes and serialized
snapshots do not provide an end-to-end immutable source-byte snapshot.

HumanTask binds run/revision, task concurrency version, required permission,
finding IDs and exact side digest for observation confirmation. Publication tasks
also bind an exact result digest. HumanResponse contains expected versions, action,
side/result digest and key, without actor/roles. Only a correction command carries
a proposed ValueRevision; it cannot assert corrected_by or a server correction.
The server must resolve/compare original and proposed evidence with stored material.
Pure admission checks cannot replace those adapters.

Task kinds separate observation confirmation, material correction, rule approval,
material approval and publication authorization. Permission/action mismatches fail.
`admit_response` checks trusted human principal, case permission, task identity,
current revision/state/version, side/result digest and allowed action. It returns
an admission record, not an applied response, approval or persisted transition.
Existing confirm-facts changes reliability with a bound side digest; it does not
alter raw scores. LocalApprovalStore separately verifies exact-material eligibility
and signed receipt. An original receipt is valid only for the unchanged original material. A new
revision must not reuse confirmations or approvals that fail their exact binding. `revise`
starts a new case version and deliberately clears all human confirmations: M0 has
no validated dependency graph for safely carrying human assertions across revisions.
It does not revoke or rewrite the unchanged parent. Only unchanged native lineage
retains native provenance; other sides require explicit confirmation and the new
exact material needs separate approval. This conservative revision operation does
not change the existing eligibility rules for material that was not revised.
AuthorizationRecord is a reserved server record referencing all revision metadata;
it is never accepted in place of a signed local receipt.

## Actions and events

A derives AllowedAction and prerequisites from trusted state. A model may propose
extract_page, inspect_reference, request_human_review or deterministic_review.
Approval, publishing and confidence changes are not model actions. Admission checks
fixed run/revision, allowlist, prerequisites, budget and exact document/purpose.
`admit_action(..., proposer=trusted_origin)` additionally compares the proposal's
claimed actor with the actual origin supplied by the executor. The trusted origin
must come from the executor/model-adapter call context, never the proposal body;
its kind determines model-call budget admission. A proposal cannot claim system
identity to avoid an exhausted model budget. Real system-origin work still needs
steps/prerequisites but does not require a model call.
The actual resolver/tool must validate page existence, citation and access; the
pure guard does not read bytes or execute tools. A must account for budgets and
no-progress retries atomically with action execution in its future implementation.

DecisionEvent records proposal, policy version, optional model/prompt references,
causal parent IDs, checked prerequisites, reason code, actual executor/action,
tool digest or sanitized failure, remaining blockers and budget. Rejected events
cannot claim execution. Executed/failed events must agree with the tool outcome.
Records are not proof until emitted by the trusted executor at execution time;
M0 fixtures are illustrative. No private chain-of-thought is required, and no
retrospective explanation is treated as an execution log. Existing audit remains
unchanged; A/D must connect events to its producer/store explicitly.

## Durable job status

JobStatus records eight control-plane states; ExecutionStatus keeps its four frozen wire
values and is never widened, because every service-v1 model forbids extras. JobStatusView
carries the exact status, so the projection below is a view, not a loss of record.

| Job status | Execution status | Notes |
| --- | --- | --- |
| queued | queued | Job, run and outbox entry committed; nothing dispatched yet |
| dispatched | queued | Handed to the queue; indistinguishable from queued to a caller |
| running | running | One attempt holds a valid lease |
| waiting_for_human | succeeded (needs_review) | The attempt ran and released its lease; the case did not pass |
| retryable_failed | running | Still in progress; an exhausted retry becomes failed instead |
| failed | failed | Terminal; carries a sanitized problem |
| succeeded | succeeded | A result version is committed |
| cancelled | failed | Lossy on the wire; the exact status stays in JobStatusView |

An expired lease returns the job to queued with a new outbox entry rather than to
dispatched, so recovery never depends on the queue redelivering a message a dead worker
may have consumed; the duplicate delivery this can cause is absorbed by the conditional
claim. A lease takeover has its own ceiling and never consumes a business attempt.

Recovery stops at a recorded cancellation. Cancelling a running attempt sets
`cancel_requested` and asks it to stop cooperatively, and every path that would otherwise
carry the job forward from there — a reclaim, a retryable failure, a hand-off to a
reviewer — resolves to cancelled rather than starting work the principal forbade. Only an
attempt that had already finished its work may still publish. `cancel_requested` is part
of JobStatusView so a job under notice is distinguishable from one that is merely running;
it is observable while the job runs and afterwards only on a terminal job.

POST /v1/review-jobs returns 202 with JobAcceptance, whose status is pinned to queued so
the body can never advertise work that has not started. An exact replay returns 200 with
JobStatusView instead, because reporting the pinned acceptance for a job that has already
moved on would be fabricated. GET .../result returns 409 until a result version is
committed and never synthesizes a ServiceResult that no run wrote; a terminal failure is
reported through the status route. Cancel is offered because it binds one principal and
one case; retry and dead-letter redrive stay operator actions in a runbook rather than a
general-purpose administrative endpoint. See
[ADR 0015](adr/0015-durable-review-jobs.md).

## Result and error meanings

| Execution / business / artifact | Meaning |
| --- | --- |
| queued or running / null / not_requested | Reserved job in progress; no terminal result claim |
| succeeded / needs_review / not_requested | Review ran; findings/blockers remain; no completed-form write |
| succeeded / failed / not_requested | Review ran but preflight or verification failed; inspect verification even when findings is empty |
| succeeded / verified / not_requested | No PDF requested |
| succeeded / verified / unavailable | Output requested but no writer assembled |
| succeeded / verified / unsupported_contexts | Existing single-context writer cannot accept the case |
| succeeded / verified / simulated | Test writer returned without creating a PDF |
| succeeded / completed / written | Real single-context PDF and verified local manifest |
| failed / optional failed / not_requested | Sanitized execution or manifest failure; no advertised artifact |

A validation finding can also yield business failed after a successful review
execution. Success of the process is not a claim that the case passed.
ServiceResult retains review findings, sanitized verification, result_version and
durable=false locally. `verification` is null only when the Controller supplied no
report (for example, entry/configuration failure). Otherwise it includes the
existing EvaluationStatus plus `critical_errors` and `warnings`, each an ordered
array of `{schema_version, code, message}` diagnostics. Preserve list membership
and multiplicity as well as status; a warning is not silently promoted to a blocker.
The adapter projects the report even without `case_review`, and retains it if a
subsequent artifact-manifest check fails. `problem` remains reserved for entry or
execution failure: a source-binding rejection has succeeded/failed, empty findings,
no artifacts, a source_binding diagnostic and null problem.

| Diagnostic code | Public meaning / consumer action |
| --- | --- |
| source_binding | Requested documents must match configured review sources; select the authorized configured documents |
| source_registry_required | Supply a current typed source registry through trusted material preparation |
| verification_blocker | An unmapped critical reason remains; inspect available findings or request human review |
| verification_warning | An unmapped warning remains; request human review before proceeding |

Only exact recognized internal messages map to specific codes; all other text
uses fixed generic messages. No raw error, local path, storage URI, input excerpt
or credential is copied into these diagnostics. Generic fallbacks deliberately
do not promise the detailed internal reason. The existing review findings remain
separate evidence-bearing content for authorized consumers, not operational logs.
See [the source-binding fixture](../examples/service-v1/result-source-binding-failed.json).

A written artifact includes one exact context, unique field IDs, page count,
output/template/map hashes, verification=local_writer_reopened and
publication=local_only. The enclosing ServiceResult.run binds every nested artifact
to case/revision/material digest and this invocation's run UUID. Keep that envelope
when storing or handing off the manifest; the standalone artifact DTO is not a
self-authenticating ownership certificate. No byte-size field is declared in v1;
the smoke report additionally measures size for operator inspection. This is local file validation, not an S3 publication grant
or durable download capability. If manifest validation fails, a local file may
exist but is not advertised as completed. The confined local adapter records this call's run UUID, destination, published
file identity, byte hash, review/result digests and full map hash after the real
writer returns. The facade requires that record, reopens the same output and
compares identity/hash and exact result/coverage before projecting a manifest.
Replayed results, no-write returns against an existing inode, and same-page PDF
replacement fail without advertising or deleting that destination. Explicit
operator-configured overwrite still uses the existing atomic staged publication
and can produce a new artifact/run even when the bytes are identical.

These observations rely on the configured LocalPDFWriter's existing preflight,
mutation and content verifier. They are not a signature, protection from malicious
operator-owned code, a cross-process transaction, or a promise that the path cannot
change after return. D/E still own immutable storage and durable publication.
Legacy PDF result warnings remain in
legacy responses; manifest verification does not turn warnings into approval.

ServiceProblem exposes only static `message` and these machine codes:
invalid_request (validation), unauthorized, not_found, version_conflict,
capability_unavailable, execution_failed. Pure guards raise ServiceFault.
Reserved HTTP mapping is respectively 422/403/404/409/503/500; B/D must avoid
cross-case existence leaks and test mapping in their transport implementation.
The review-jobs routes implement this mapping and answer with the ServiceProblem
envelope; an invalid submission returns invalid_request without loc/msg/input, because
a rejected payload can quote document text. Another principal's job and an unknown job
both answer 404: 403 would confirm that the job exists. Other reserved routes remain
unmounted. Legacy HTTP retains its actual EntryProblem
422/503/500 envelope; legacy validation retains sanitized detail/loc/type/msg.

## Durability and callable boundaries

JobStore implements these duties and JobRepository's coarse boundary sits on top.
Submission atomically persists principal/key/payload digest, fixed run input
and outbox before dispatch. Same principal+key+canonical payload returns the same
run; changed payload conflicts. Uniqueness is a conditional write on the idempotency
record's own primary key, never a secondary-index lookup, because an eventually
consistent index lets concurrent submissions both observe an unused key. Distinct principals have separate namespaces and
cannot retrieve each other's run. RevisionRepository uses compare-and-swap on the
expected parent; None means create only if absent. HumanTaskRepository must recheck
identity/permissions, versions and response key inside its transaction, then append
revision/task/outbox together. Its trusted actor must match the current principal.
Publication requires current attempt/lease/fencing token and result-version CAS.
Pure guards and in-process snapshots provide no cross-process guarantee.

When human review is needed, D/B persist findings/tasks and finish the attempt.
A response produces a new revision and necessary approvals, then a subsequent run;
it does not revive the old session or hold its lease. Job run ID, attempt UUID and
Runtime session string are separate; a session requires an attempt.

The human-task plane implements that response. `HumanTaskStore.commit_response` is one
coarse transaction on purpose: marking the task answered, appending the revision,
superseding the siblings bound to the old revision, consuming the response key and
scheduling the follow-up run are inseparable. A store that applied some of them could
leave a revision nothing is scheduled to review, or a scheduled run for a revision that
was never stored. Everything the transaction rechecks is passed into it, because a
decision made from an earlier read is exactly what a conditional write exists to
invalidate; the service's own admission and replay checks are a fast path, never the
decision. The in-memory adapter rolls its writes back when the job plane refuses, which
is the local stand-in for a single TransactWriteItems, not a pattern to reimplement.

Confirmation and correction take deliberately different paths. A confirmation is an
assertion about material that did not change, so it captures a child revision and keeps
the confirmation; routing it through `revise`, which clears every confirmation by design,
would erase the very assertion being recorded. A correction changes an observed value, so
it uses `revise` and every prior confirmation is cleared. A correction carries the
reviewer's proposed value and raw text only: it keeps the stored evidence, must name the
observation its task is about, must be normalized before entering the arithmetic path, and
never raises a raw confidence. Refusing to confirm is a recorded answer that commits no
revision and schedules no run. Rule, material and publication approval are not answered
here; the existing exact-material authority still owns them, and such a task reports
capability_unavailable rather than advertising an authorization the service never wrote.

The task routes return `TaskView`, which is the task plus the `subject_id` its own
correction must carry. That name is published rather than derived by the caller because
deriving it means canonicalizing the comparison context, and two languages do not
canonicalize alike: `ComparisonContext.key()` is `json.dumps`, which escapes non-ASCII by
default, while a browser's `JSON.stringify` does not. A client that rebuilt the name would
compute a different string for any case identified in Chinese, which is every real case
here, and every correction would be refused. Do not substitute one canonicalizer for
another; ask the server for the name.

Reads and writes are separated by permission: `review` is enough to see a task, while
answering additionally requires the task's own permission. A task belonging to another
principal, and a case this principal cannot see, are both reported as not_found; replying
forbidden would confirm the task exists.

Actually callable: GET /health, POST /v1/validate, POST /v1/reviews and existing
invocation, using the configured factory; plus POST /v1/review-jobs,
GET /v1/review-jobs/{job_id}, GET /v1/review-jobs/{job_id}/result and
POST /v1/review-jobs/{job_id}/cancel when a job store and a principal resolver are
both configured, and capability_unavailable otherwise; plus
GET /v1/review-jobs/{job_id}/tasks, GET /v1/review-jobs/{job_id}/revisions,
GET /v1/review-tasks/{task_id} and POST /v1/review-tasks/{task_id}/responses when a
human-task store and a principal resolver are both configured, and
capability_unavailable otherwise; separately `LocalReviewService.run` /
`python -m appraisal_review.local_service run` returns ServiceResult. No legacy
HTTP/invocation shape changed. Reserved groups for B/D: documents and authorized
artifact downloads. They return no fake production success because no routes exist. C can validate fixtures now. See the [local runbook](local-service-runbook.md).
