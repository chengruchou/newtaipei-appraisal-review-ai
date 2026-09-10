# Service v1 contract authority

Local Phase 8 preparation adds `WorkflowPause` (actual waiting result and stored
task IDs) and `WorkflowContinuation` (pause/response/task/old-run/new-run linkage).
Both use controlled-action-v1 and are exported additively. There is no new task,
revision, lease, job or outbox wire model. Consumers must update their schema before
using the new records. They are internal evidence-bearing records, not authorized
cloud payloads. `PauseResumeRepository` is implemented only by the existing
non-durable local task store. See [ADR 0017](adr/0017-local-pause-continuation-seam.md)
for replay, transaction boundaries and deferred #24/#28/#29 requirements.

Issue 17 audit repair: see [ADR 0016](adr/0016-controlled-execution-failure-boundaries.md).
`BoundedWorkflowResult.selection_failures` contains sanitized pre-proposal failures;
trace adapters now implement `append_failure` and `read_failures` in addition to
decision append/read. Consumers must merge causal IDs from both streams.
`unresolved_execution` prohibits automatic timeout retry. Source-only revisions use
`source-documents-json-v1`, empty rules and preparation-only states; existing
review-material digests retain their previous meaning and nonempty-rule requirement.

Status: M0 foundation merged in PR #20 at
`c3687e0cfe16cee0d38fcb4c1780a6092565aaeb`; this remains a local, non-durable
service boundary rather than a deployed service. [ADR 0013](adr/0013-service-foundation.md)
records the foundation trust boundaries, and
[ADR 0014](adr/0014-controlled-action-policy.md) defines the Issue #17 extension.
[ADR 0015](adr/0015-local-human-task-transactions.md) records Phase 6's local
human-task transaction and revision re-entry boundary.

Phase 7's [service handoff](issue17-service-handoff.md) records the current
#24 authenticated task API, #29 durable job and #25 browser ownership. These
downstream issue assignments supersede historical role shorthand below; remote
endpoint names, collection shapes and resume-event transport are not frozen here.

## Reuse and delivery inventory

| Responsibility | Directly reused | Added in M0 | Subsequent delivery |
| --- | --- | --- | --- |
| Review and evidence | ReviewMaterial, SourceCitation, ComparisonContext, findings, Controller and independent verifier | Immutable serialized revision helper and public references | Durable revision repository and full-case acceptance |
| Entrypoints | ReviewAdapters, build_controller, execute_review, legacy HTTP/invoke | Configured local factory and separate service envelope | Authenticated web assembly and Runtime wiring |
| Human authority | OS reviewer, side confirmation, exact-material signed receipt | Task/response/authorization DTOs and pure admission checks | Phase 6 local task/correction/re-entry implemented; B authenticated API and D durable storage remain |
| PDF | Single-context PDFWriter, LocalPDFWriter, policy/map hashes, validated output | Explicit local writer injection and manifest projection | E formal CJK/multiple-context contract; D live publication |
| Cloud and action policy | Injected extraction/storage adapters, existing audit events, synthetic smoke | Contracts, policy/selectors, one-decision coordinator, bounded runner and non-durable trace | D durable trace/jobs/outbox/leases/fencing/recovery |

## Ownership and wire authority

Shared wire models are in `domain/service_contracts.py`. The validation source is
Python/Pydantic; `schemas/service-v1.json` is the serialization-schema bundle. Existing
service records retain `schema_version="service-v1"`. The strengthened Issue #17 action
records use `schema_version="controlled-action-v1"` as an explicit migration from their
incompatible illustrative draft shapes.
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
| HumanTask, HumanResponse, AcceptedResponse, HumanResponseResult; PrincipalResolver, HumanTaskRepository | B | Server task/admission -> C and subsequent run | Purpose-specific local service, POSIX principal, atomic process-local task/revision/replay/re-entry; no human API mounted |
| AuthorizationRecord | B, E for publication scope | Trusted authorizer -> job/publisher | Attributed local response record; signed local receipt remains material authority and durable publication is reserved |
| WorkflowSnapshot, AllowedActionSet, SelectorInput, ActionProposal, ControlledToolReceipt, DecisionEvent, Budget, BoundedWorkflowResult, HumanReviewHandoff | A | Trusted policy/selector/executor -> C/D/evaluation | Exact validation, single-decision execution/trace and bounded local coordination; no durable event/task store |
| JobReference, JobStatusView, JobAcceptance; JobStore, ResultStore | D | Durable job plane -> C/B/runtime | Mounted review-jobs routes over an injected store; in-memory reference adapter only, no cloud persistence |
| HumanTask, HumanResponse, AcceptedResponse; PrincipalResolver, HumanTaskRepository | B | Server task/admission -> C and subsequent run | Strict commands and pure permission/version checks; no human API mounted |
| AuthorizationRecord | B, E for publication scope | Trusted authorizer -> job/publisher | Reserved metadata record; existing signed local receipt remains actual local authority |
| AllowedAction, ActionProposal, DecisionEvent, Budget | A | Trusted policy/executor -> C/D/evaluation | Pure admission and truthful event validation; no model selector, retry loop or event store |
| ServiceResult, ServiceVerification, VerificationDiagnostic, ArtifactManifest | B assembly, E coverage, D publication | Local facade / future publisher -> C | Callable local envelope, sanitized verification and real PDF manifest; durable publication reserved |
| ServiceProblem / ServiceFault | B with D | Guards -> transport adapters | Static sanitized codes; old EntryProblem remains the legacy transport boundary |

B coordinates shared changes; each steward reviews its trust boundary. A–E are
responsibility positions, not repository usernames or automatic assignees.

## Versions, serialization and digests

- `schema_version` is explicit on every model. Existing service records use `service-v1`;
  exact Issue #17 action records use `controlled-action-v1`. Unknown fields, unknown
  enum values, unsupported versions and nonfinite numbers fail. JSON uses
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
- PR #20 added nullable `ServiceResult.verification` and its finite diagnostic types
  to service-v1. B/C/D consume the regenerated schema and fixtures together; older
  extra-forbid consumers cannot silently accept the added field. Issue #17 action
  consumers must migrate explicitly from the old illustrative service-v1 records to
  controlled-action-v1 and adopt the regenerated fixtures together. This does not
  change any legacy HTTP/invocation schema.
- Phase 6 adds `HumanResponseResult`, `TaskKind.EVIDENCE="evidence_supply"`,
  `ResponseAction.SUPPLY_EVIDENCE="supply_evidence"`, and `HumanTask.reason_code`
  / `affected_subject_ids`. The two task fields retain null/empty defaults for
  existing draft records; actual service-created tasks require meaningful values.
  New enum values and serialized fields require coordinated consumer/schema/fixture
  adoption even though these models remain service-v1. `HumanTaskRepository`
  now specifies context, task retrieval, atomic batch creation, response transition and local
  re-entry; durable adapters must implement the full contract explicitly.

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
adapter obtains remote identity/permissions from server context. Phase 6's
`LocalReviewerPrincipalResolver` checks the actual POSIX reviewer against the
configured reviewer and supplies explicit case IDs and permissions. M0 local configuration
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
Phase 6's `MaterialSubjectMap` separately resolves application-configured subject IDs
to exact context/factor/sides. It validates the submitted original against stored
material, preserves value type/unit and existing source anchors, accepts only case-form
evidence and records the actual human-attributed corrected value. Missing value/evidence
requires an explicit evidence-supply task. Correction and evidence supply do not imply
confirmation. Confirmation creates a new revision before binding the selected side;
it may rebind earlier unchanged sides only when the repository proves accepted fact
responses for the same actor. Bootstrap/caller metadata is not that proof. Correction
and rule changes clear the proven set; measured confidence never changes.

The changes ledger is cumulative metadata, not an alternate signed fact payload.
The local task repository appends it atomically with the exact material and parent
inside its process-local transaction. B/D must provide durable revision storage;
mutating a detached value does not mutate the old revision. Hashes and serialized
snapshots do not provide an end-to-end immutable source-byte snapshot.

HumanTask binds run/revision, task concurrency version, required permission,
finding IDs and exact side digest for observation confirmation. The actual local
service also binds corrections/evidence supply to one configured side and creates
concrete questions, reason codes, affected subjects and located citations from stored
review findings. Publication tasks
also bind an exact result digest. HumanResponse contains expected versions, action,
side/result digest and key, without actor/roles. Only correction and evidence-supply
commands carry a proposed ValueRevision; neither can assert corrected_by or a server correction.
The server must resolve/compare original and proposed evidence with stored material.
Pure admission checks cannot replace those adapters.

Task kinds separate observation confirmation, material correction, explicit missing
evidence supply, rule approval, material approval and publication authorization.
Permission/action mismatches fail.
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
exact material needs separate approval. The Phase 6 confirmation service may explicitly
rebind only the repository-proven unchanged assertions described above, after this
base operation clears them. This conservative revision operation does
not change the existing eligibility rules for material that was not revised.
`HumanTaskService` applies admitted commands through the task repository. A changed
revision supersedes other open tasks bound to its parent, preserves the original
snapshot and queues a new local run. Rejection consumes the task without changing
material or queuing work. Material approval requires approved rules and verifies
a pre-existing signed receipt for the exact current material through injected
`ReviewAuthorization`; the service does not sign a receipt. Publication requires
current material authority plus an actual verified result and records that exact
result digest, without writing an artifact or queuing review.

`HumanResponseResult` records the actual atomic response: event ID, admitted command
and trusted actor, answered task at the next task version, resulting material revision,
optional new run and optional authorization record. It asserts no execution/business/
artifact status. `reenter` runs the existing `CaseReviewer` for queued work and stores
the actual full review result. `AuthorizationRecord` references the exact revision
metadata and actor; it is never accepted in place of a signed local receipt or durable
publication fencing. See [local human review](local-human-review.md) for composition,
purpose-specific commands and conservative confirmation limits.

## Actions and events

`WorkflowSnapshot` is the exact trusted selector input. It binds one run and material
revision, whose `MaterialRevision` carries the source purposes, versions, hashes and
rule references. It also contains a finite `WorkflowState`, typed satisfied
prerequisites, located blockers with affected subjects and the remaining step,
model-call, retry and optional time budget. It has no caller-supplied URI, actor,
approval, verification or completion flag. Its canonical `content_digest` identifies
the complete snapshot; a snapshot ID or state label alone is insufficient.

A's `ControlledActionPolicy` derives `AllowedActionSet` from a detached, revalidated
copy of that trusted snapshot without document, model or tool I/O. The system and model
policy identities are distinct so their different model-call costs cannot be confused.
The set binds the policy version, snapshot digest and revision. Each `AllowedAction` has a stable ID,
permitted states and document purposes, exact revision and rule references, permitted
proposer/executor kinds, typed prerequisites and explicit cost. Duplicate action IDs,
revision mismatches, ambiguous duplicate values and invalid source-purpose combinations
fail validation. Source actions expose only policy-authorized purposes actually present
in the revision; templates are never extraction sources.

The current state table is explicit: criteria/forms pending permit their corresponding
extraction after prerequisites; retryable extraction additionally consumes retry budget;
available registered references permit reference inspection; rule/evidence/review-failure
and unsupported-context blockers permit human review; material-ready permits deterministic
review. `verified` and `waiting_for_human` permit no action. Missing prerequisites,
required blockers or remaining step/model/retry/time budget produce no action.
For `verified`, the empty controlled-action set hands the result back to the existing
deterministic PDF eligibility/writer path; it does not create a second PDF action.

An `ActionProposal` selects one advertised action ID and binds the same policy, snapshot,
run and revision. Its discriminated argument is exactly one of:

- `ExtractPageArguments`: an authorized non-template document and page, optionally an
  exact matching cited region;
- `InspectReferenceArguments`: a versioned reference or brief page and optional section;
- `RequestHumanReviewArguments`: a stable reason, concrete question, unique affected
  subjects and available citations;
- `DeterministicReviewArguments`: the exact current revision and rule references.

`SelectorInput` is the complete provider-neutral selector request. It repeats the
current budget deliberately and validates that it equals the snapshot budget, that the
allowed-action digest/revision/documents/rules match the snapshot, and that every
sanitized citation resolves to a versioned document in that revision. It contains no
storage URI, credential or caller-authored actor.

`ActionSelector` is the single async port. `DeterministicActionSelector` is a no-model
local baseline: it selects only a sole allowed action and derives source pages only from
located input evidence. `BedrockActionSelector` receives its client and configuration
through its constructor and performs no import-time client or credential discovery. It
advertises the exact allowed set under the versioned
[`controlled-action-selector-v1`](prompts/controlled-action-selector-v1.md) prompt and
accepts one strict JSON object containing only action identity, typed arguments and an
optional rationale. Prose, extra fields, multiple blocks, truncation/refusal, malformed
arguments, unadvertised actions/source pages and provider failures produce typed,
sanitized errors. Only throttling/service-unavailable errors receive bounded retries;
there is no silent fallback action.

Cross-action arguments and extra fields fail. A model proposal requires model/prompt
identity and adapter-recorded attempt/latency metadata; valid input/output token usage is
recorded as a pair when the provider supplies it. A system proposal cannot carry any of
those fields. Model rationale is stored as an
untrusted proposal field, separate from the event's reviewer-facing summary. Approval,
publishing, confidence changes, budget changes and asserted executor/human authority are
not proposal fields.

`admit_action` revalidates detached copies and compares the trusted proposer and executor,
proposal/action identity, exact snapshot digest, policy, run/revision/rules, current state,
prerequisites, source purpose and budget. The actual resolver/tool must still authorize
access and validate page existence immediately before I/O. The guard reads no documents
and executes no tools. The Phase 2 policy output can be connected directly to this guard,
which rejects a proposal if a newly captured snapshot has changed. Both Phase 3 selectors
use it as a side-effect-free preflight; the Phase 4 coordinator calls it again against fresh state
immediately before actual invocation and event production.

`DecisionEvent` records state before/after, proposal and policy identity, causal parent
event IDs, checked prerequisites, reason and reviewer summary, affected subjects and
evidence, actual executor/action/outcome, blockers, task/response links and budget before,
after and consumed. Budget arithmetic must reconcile. Rejected events cannot claim tool
execution or a workflow-state change. Executed/failed events must agree with the typed
tool outcome. Records are not proof until emitted at execution time; checked-in fixtures
remain illustrative. No private chain-of-thought is required, and no retrospective
explanation is an execution log. Existing audit remains unchanged; A/D must connect the
new producer and store explicitly.

`ControlledWorkflowCoordinator.decide_once` is that explicit local producer. It captures
trusted state, derives policy, selects one proposal, captures state again and re-admits
before routing exactly one action to an injected `ControlledActionExecutor`. A rejected
proposal appends an event with no executor/tool result and no step consumption. An
admitted action records the detached `ControlledToolReceipt`, freshly captured resulting
state/blockers, actual result digest or sanitized failure, and exact charged budget. Tool
receipt evidence must resolve in the resulting revision. Model-call consumption uses the
adapter-recorded attempt count rather than the policy's minimum cost.

`DecisionTrace` provides append/read ports. `NonDurableInMemoryDecisionTrace` stores
detached events, rejects duplicate IDs and unknown parents, and is only a process-local
test adapter. The coordinator derives parent IDs from the current causal frontier rather
than an assumed sequence number. It cannot provide cross-process atomicity, recovery or
publication fencing; those remain Issue #9 responsibilities. Selection failures that do
not produce a valid `ActionProposal` propagate as typed selector failures and cannot be
misrepresented as a proposal decision event.

`BoundedWorkflowRunner` carries the remaining budget between decisions and stops on
verified, waiting-for-human, exhaustion, permanent failure or repeated no progress.
Provider attempts consume model-call budget; provider and outer retries consume retry
budget; tool actions consume steps; measured decision time and deterministic injected
backoff consume available time. The runner never accepts budget from selector output and
rejects a snapshot provider that attempts to increase its current ledger.

Stable failure classes separate retryable provider/tool failure, malformed proposals,
unauthorized or wrong-purpose sources, missing evidence, unsupported rule/context and
deterministic review blockers. The no-progress SHA-256 fingerprint covers action, exact
typed arguments, material revision, current document versions/hashes and stable failure
code; proposal/event UUIDs and raw exception text are excluded. Permanent failures execute
at most once, while identical retryable failures hand off after the repeated result.
`needs_review` transitions to a human action/waiting state and is never treated as an
infrastructure retry.

`HumanReviewHandoff` is a concrete Phase 5 result, not a persisted `HumanTask`. It retains
the exact revision, all current blockers, affected subjects, deduplicated located evidence,
the last actual sanitized tool outcome and any no-progress fingerprint. Phase 6 provides
the local task service and transactional adapter. Its explicit `create_from_handoff`
method requires one trusted `HumanTaskBinding` per blocker, mapping current actual
findings, purpose and subject. It validates exact revision, reason, subjects and evidence;
incomplete, unknown or conflicting mappings fail, and task batches are atomic. The caller
retains the full handoff separately. Infrastructure-only handoffs without deterministic
blockers remain unresolved with `capability_unavailable`; no finding is fabricated.
The default runner does not automatically create tasks, grant human authority or keep
a runtime invocation open.

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

Phase 6's `NonDurableInMemoryHumanTaskRepository` implements one-process task
transactions with a lock covering admission, transformation, event serialization,
task consumption, revision append and local re-entry work. Current principal
permissions are checked before replay; exact actor/key/payload replay returns the
original `HumanResponseResult`, while changed payload conflicts. Re-entry runs and
saves a full `CaseReviewer` result under the same lock; pending review is not a new
verified result. A newer revision cannot interleave and commit obsolete review work.
This adapter keeps detached records and loses them on process exit. It provides
no database, durable outbox, restart recovery or cross-process claim.

When human review is needed, D/B persist findings/tasks and finish the attempt.
A response produces a new revision and necessary approvals, then a subsequent run;
it does not revive the old session or hold its lease. Job run ID, attempt UUID and
Runtime session string are separate; a session requires an attempt.

Actually callable: GET /health, POST /v1/validate, POST /v1/reviews and existing
invocation, using the configured factory; plus POST /v1/review-jobs,
GET /v1/review-jobs/{job_id}, GET /v1/review-jobs/{job_id}/result and
POST /v1/review-jobs/{job_id}/cancel when a job store and a principal resolver are
both configured, and capability_unavailable otherwise; separately `LocalReviewService.run` /
`python -m appraisal_review.local_service run` returns ServiceResult. No legacy
HTTP/invocation shape changed. Reserved groups for B/D: documents, review-jobs, human-tasks/responses,
authorized artifact downloads. They return no fake production success because no
routes exist. C can validate fixtures now. See the [local runbook](local-service-runbook.md).
