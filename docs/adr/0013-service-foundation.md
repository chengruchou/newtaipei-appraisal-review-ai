# ADR 0013: Shared service contracts and explicit local composition

Status: proposed on the M0 working branch, 2026-09-07; not merged.

## Context

Main includes source-bound review (#15), extraction/local reviewer controls (#16)
and the real local writer/injected S3 wrapper (#19). Five workstreams need stable
service identities and handoffs. The public synchronous review API already has a
compatible result and error contract. No durable jobs or multi-user human service
exists; the Runtime smoke stores only session-local state.

## Decision

Reuse the existing review, citation, confidence, PDF and composition types. Add
service-v1 wire projections and reserved ports rather than a second review engine.
Use immutable serialized material snapshots for local revision handling, existing
canonical digest functions, explicit parent/version binding and no automatic reuse
of confirmation/approval after revision. Original/proposed/corrected views remain
separate, and raw confidence remains unchanged. The original receipt is valid only
for unchanged original material; a new revision cannot reuse confirmations or
approvals that fail exact binding. Native authority persists only for an unchanged
side that was native in the immediate parent and remains native, without human
confirmation fields. The side digest intentionally omits method/confirmation;
compare lineage explicitly rather than treating equal digests or a new method
label as extraction authority. All other revised sides need explicit confirmation;
trusted re-extraction is a separate entry, not a revision label.

Treat external IDs, digests and DTO validity as untrusted assertions. Internal
resolvers must authenticate case access before storage URI resolution. The local
operator-owned allowlist is a separate boundary, supported by the existing
Linux/macOS reviewer and exact-material receipt. No HTTP body can create a trusted
principal or approval. Confirmation, material/rule approval and publication remain
separate operations. Publication binds the exact result in addition to revision.

Use pure admission checks for task/version/permission/action/idempotency semantics.
Protocols specify atomic conditional append/outbox and fenced publication duties.
The executor supplies the trusted proposal origin independently; action admission
compares it with the claimed actor before choosing model-budget rules. A body
claim of system identity cannot bypass a model budget.
M0 has no persistent implementation; do not mount jobs/task/download endpoints or
claim transactions/leases from in-memory checks. Human waiting ends an attempt;
new material and required approvals start a subsequent run.

Expose explicit local configuration through a factory using ReviewAdapters,
build_controller and execute_review. HTTP/invocation return their original schema;
a separate run facade returns service-v1. Configure the real parser, prepared
material and optional authorizer; compose the real writer only with a trusted
hash-bound template, complete field map, explicit font and output directory.
Importing modules does not read case files or create SDK clients. Missing or invalid
configuration fails; there is no synthetic fallback. Fixtures generate and authorize
only their own fixed synthetic case in a newly created isolated directory.

Project the Controller's verification report into ServiceResult even when preflight
ends before case_review. Preserve status and ordered critical/warning diagnostics
with finite codes and fixed public messages; unknown internal reasons receive
generic diagnostics, never raw text or paths. Keep execution success separate from
business failure and leave ServiceProblem for entry/execution errors. Update the
proposed, unmerged service-v1 schema and its consumer fixtures together; existing
HTTP/invocation payloads and their OpenAPI remain unchanged. This pre-merge change
requires coordinated B/C/D consumer adoption because service models forbid extras.

Keep existing single-context completion limits. A manifest projects actual reopened
output bytes and exact field/context coverage; it is not proof of full-case template
coverage, a durable download or publication permission. The local wrapper records
per-call run identity and actual published-file identity/hash with exact review,
result and map digests. Manifest projection requires that evidence and a matching
reopen; existing files or old Controller results alone cannot prove a new write.
Explicit overwrite remains available through the existing atomic writer policy.
This local observation is not a durable receipt or a new storage transaction.
Preserve writer preflight,
source alias protections and independent review gates without changing the core.

## Consequences and limits

B/C can share versioned commands and fixture views; A can implement a constrained
selector; D can implement durable ports; E can extend coverage through a reviewed
version migration. This adds no model selector, full browser workflow, cloud
identity, persistent store, formal CJK template, multi-context writer or deployment.
Existing audit producers remain authoritative for executed core behavior; illustrative
event fixtures must not be published as actual execution evidence.

Snapshots protect in-process material copies, not source bytes across the whole
pipeline. Operator-owned local paths can change; parser rechecks hashes and writer
rechecks its template/protected sources. End-to-end source snapshots and race-resistant
remote artifact publication remain explicit D/E work. Review data can contain raw
source text; expose it only through authorized product routes, not operational logs.

See [service contracts](../service-contracts.md) for canonicalization, enum/null,
compatibility and error rules, and [local runbook](../local-service-runbook.md) for
real synthetic parser/writer, HTTP, invocation and blocked-case evidence.
