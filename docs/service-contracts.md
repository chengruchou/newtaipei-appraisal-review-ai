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
| HumanTask, HumanResponse, AcceptedResponse; PrincipalResolver, HumanTaskRepository | B | Server task/admission -> C and subsequent run | Strict commands and pure permission/version checks; no human API mounted |
| AuthorizationRecord | B, E for publication scope | Trusted authorizer -> job/publisher | Reserved metadata record; existing signed local receipt remains actual local authority |
| AllowedAction, ActionProposal, DecisionEvent, Budget | A | Trusted policy/executor -> C/D/evaluation | Pure admission and truthful event validation; no model selector, retry loop or event store |
| ServiceResult, ArtifactManifest | B assembly, E coverage, D publication | Local facade / future publisher -> C | Callable local envelope and real PDF manifest; durable publication reserved |
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
Changed/new sides lose native-extraction authority; unchanged native observations
retain their provenance. Raw confidence is never raised. Original snapshots remain
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
It does not revoke or rewrite the unchanged parent. Unchanged native observations
retain native provenance; changed sides require explicit confirmation and the new
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

## Result and error meanings

| Execution / business / artifact | Meaning |
| --- | --- |
| queued or running / null / not_requested | Reserved job in progress; no terminal result claim |
| succeeded / needs_review / not_requested | Review ran; findings/blockers remain; no completed-form write |
| succeeded / verified / not_requested | No PDF requested |
| succeeded / verified / unavailable | Output requested but no writer assembled |
| succeeded / verified / unsupported_contexts | Existing single-context writer cannot accept the case |
| succeeded / verified / simulated | Test writer returned without creating a PDF |
| succeeded / completed / written | Real single-context PDF and verified local manifest |
| failed / optional failed / not_requested | Sanitized execution or manifest failure; no advertised artifact |

A validation finding can also yield business failed after a successful review
execution. Success of the process is not a claim that the case passed.
ServiceResult retains review findings, result_version and durable=false locally.
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
No reserved routes are mounted. Legacy HTTP retains its actual EntryProblem
422/503/500 envelope; legacy validation retains sanitized detail/loc/type/msg.

## Durability and callable boundaries

JobRepository must atomically persist principal/key/payload digest, fixed run input
and outbox before dispatch. Same principal+key+canonical payload returns the same
run; changed payload conflicts. Distinct principals have separate namespaces and
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

Actually callable: GET /health, POST /v1/validate, POST /v1/reviews and existing
invocation, using the configured factory; separately `LocalReviewService.run` /
`python -m appraisal_review.local_service run` returns ServiceResult. No v1 shape
changed. Reserved groups for B/D: documents, review-jobs, human-tasks/responses,
authorized artifact downloads. They return no fake production success because no
routes exist. C can validate fixtures now. See the [local runbook](local-service-runbook.md).
