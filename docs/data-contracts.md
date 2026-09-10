# Data contracts

M0 adds [service-v1 contracts](service-contracts.md), a separate local result
envelope and configured [local factory](local-service-runbook.md) on its working
branch. Existing review schema 2.0 and public HTTP/invocation JSON remain unchanged.
Document/task/job IDs and ports are reserved for B/D; they are not mounted routes.
The real local writer and S3 wrapper are already merged in #19.

All boundaries reject unknown fields and serialize to JSON. Policy is versioned
data, not district-specific branching. Implemented review schema 2.0 is separate
from the reserved service persistence/API capabilities described below.

## Complete review schema 2.0

`domain.factor_models` owns ReviewPolicy, CaseFacts, ReviewMaterial and
CaseReviewResult together with AgentReviewRequest, AgentReviewRun, observations,
rule sets and factor results. `domain.review_contracts` owns explicit
case/comparison identity, review inventory, original ObservedValue, reliability,
findings and coverage. `domain.document_models` owns the typed source registry
and exact citations. AgentReviewRequest has nonempty case_id,
criteria_document_uri and case_document_uri, plus optional pdf_template_uri,
output_pdf_uri and field_map. `case_document_uri` is parsed for case facts;
`pdf_template_uri` is a separate, preserved form template used only as the PDF
writer source. The controller never substitutes the case-data document for a
missing template.
It describes an internal/local invocation; the future cloud API authorizes
external document IDs before resolving these URIs (#9).

CaseReviewResult contains every scoped comparison; AgentReviewRun.review is the
compatibility projection only when exactly one comparison exists. Its
case_review field is authoritative for case findings and completion. A
FactorReviewResult now includes context, case_version and source_hashes.

ReviewPolicy binds the inventory and scoped candidate rules to district, zone,
land-use, date, case and source versions. The server-injected ReviewAuthorization
must authorize the exact ReviewMaterial, including facts and original values.
A model or caller cannot authorize itself with approved=true. The parser's
current registry must equal the reviewed registry. Source citations resolve exact
document/hash/version/page/region/box/excerpt; location and semantic reliability
remain separate checks. Low-confidence or model-only facts remain unresolved.

Inventory contexts/factors/slots/checks are required independently of successful
extractions. Blank columns have source evidence but no comparable entity.
Observed states preserve blank/missing/not_present/not_applicable/present zero.
Expected grades/rates never replace observed values. Sum/equals definitions bind
explicit slot IDs and evidence, Decimal rounding quantum and tolerance.

Factor-only adapter returns remain accepted for migration, but now yield
needs_review because they lack a complete trusted case. The synthetic bundle
uses a fixed complete schema-2 fixture, with no production bypass.
See [ADR 0005](adr/0005-complete-case-review.md) for semantics and migration.

## Entry response and errors (#4)

POST /v1/reviews returns AgentReviewRun synchronously, identical to the successful
JSON result of adapters.aws.agentcore.runtime.invoke. Both preserve enum string
values and None as JSON null. Transport code has no valuation rules.

```json
{
  "case_id": "synthetic-case",
  "status": "needs_review",
  "review": null,
  "verification": null,
  "output_pdf_uri": null,
  "pdf_result": null,
  "pdf_error": null,
  "audit_events": []
}
```

This minimal shape illustrates the unapproved-rule branch. Actual evaluated cases
include review and verification, including unresolved factors and calculation
traces. The controller's audit events record its decisions using the unchanged
AuditLogger interface. The final status requires complete approved inventory coverage and independent
calculation. Actual PDF bytes remain the writer adapter's responsibility.

Malformed payloads return `{"error":{"code":"invalid_request","message":"Invalid review request."}}`
(HTTP 422). Configuration errors use codes invalid_configuration,
missing_local_adapters, missing_aws_configuration, missing_aws_adapters,
synthetic_aws_forbidden, adapter_mode_mismatch or invalid_adapter (HTTP 503).
Unexpected execution errors return review_execution_failed (HTTP 500). Messages
omit raw validation input, secrets, stack traces and document text.

Legacy /v1/validate still returns its existing case/schema/rule-version/findings
shape. Its Python overall_status property is not a serialized JSON field.

## Shared PDF contract (#6)

See the authoritative [PDF contract](pdf-contract.md) and
[ADR 0002](adr/0002-shared-pdf-contract.md). Public module domain.pdf_models exports
PDFWriteRequest, PDFWriteResult, PDFField/Map, PDFValueRef and typed errors.
ports.pdf contains the only PDFWriter protocol. Existing factor_models and
ports.workflow imports are compatibility aliases. pdf_types holds shared value
objects below factor_models in the import graph, preventing circular imports.

The writer request carries source/destination URI, protected review-source URIs,
one bound FactorReviewResult and field map. The destination cannot share a
canonical or provider-level identity with the template or any selected criteria,
forms, reference, or brief input.
The controller maps `AgentReviewRequest.pdf_template_uri` to the writer's
`source_uri`; `case_document_uri` remains upstream extraction evidence and is not
opened by the writer. Output page count therefore belongs to the template artifact,
not the parsed case-data document. The provider-local template policy binds exact
template bytes and the canonical complete field map with SHA-256 values. See
[ADR 0010](adr/0010-separate-pdf-template-source.md) and
[ADR 0012](adr/0012-bind-pdf-inputs-and-template-policy.md).
Only after verification.can_complete may the controller construct it. Every
written field has an explicit value_ref; mixed comparison contexts, duplicate
IDs, missing values, invalid bounds or conflicting URIs fail explicitly.

Successful PDF metadata lives in AgentReviewRun.pdf_result (URI, page count,
written IDs and noncritical warnings), with output_pdf_uri retained as the URI
alias. PDF warnings are not review warnings or audit-schema additions. Errors
produce a stable pdf_error code, failed workflow, retained review/verification,
and no published output URI. The Controller validates the interface; the concrete
writer validates file contents. Current B owns app assembly and E owns formal PDF.

## Reserved service and cloud contracts (M0 / #9)

A job includes principal, case_id, run_id, input document/version references,
rule version, idempotency key + payload hash, execution status, business status,
active attempt/session_id, lease expiry/fencing token and optional result manifest.
Public responses omit raw internal storage URIs. The target authoritative execution
state belongs in durable DynamoDB storage, not SQS receipt or invocation response.
This store and its jobs API are not implemented in M0; use the reserved ports in
[service contracts](service-contracts.md) for conditional/idempotency semantics.

## HTTP error compatibility

POST /v1/validate retains HTTPValidationError with a detail array of loc, type
and msg; raw input/context is omitted and custom value/assertion messages are
sanitized. POST /v1/reviews returns EntryProblemResponse containing error with
code/message for 422 (invalid input), 503 (configuration) and 500 (execution).
OpenAPI declares that envelope for all three statuses. Direct invocation uses
EntryProblem.response() and the same serialization, with no HTTP status wrapper.
Legacy success responses and all error envelopes remain unchanged. See ADR 0004 and
api error contract tests for runtime JSON validation against the published schema.

## Review binding and comparison corrections

Every ReviewSlot.context must exist in ReviewInventory.contexts. Factor grades
and adjustment_percent require that context's factor_id; subtotal/total have no
factor_id. Both ends of ArithmeticCheck must reference structurally valid slots.
An invalid binding yields an observed/<slot-id> slot_binding finding and missing
coverage. Cross-context checks are valid when both contexts are registered.

The runtime confidence threshold is shared by calculation and independent
recalculation. It defaults to 0.85, is finite and within [0, 1], and is not replaced
by model confidence or exact-material approval alone. Unconfirmed measured
values below threshold need review; controlled confirmation is specified below.

Aggregate observed comparisons preserve every arithmetic constraint's Decimal
expected value, ROUND_HALF_UP quantum and inclusive tolerance. All constraints
must pass, including the independent total when available. Grade and factor-rate
comparisons remain exact. Arithmetic findings retain each check ID, version,
source, original value, expected value and comparison trace. A final aggregate
finding may list multiple expected values by check ID; none is silently selected.
No PDFWriter request/result fields or public protocol change in this correction.

## Document candidates and reviewer operations (#7)

`SourceDocument` records immutable source identity, role/version/date and every
page's geometry/regions. `ParsedDocument.source` is typed; the old content dict
remains for compatibility, not as the source of new critical facts. SourceCitation
binds an exact region and excerpt; an empty cell/image can have an empty excerpt.
Individual selection glyphs carry checked/unchecked state, while mixed or unclear
selections stay ambiguous. No page location implies semantic correctness.

`PageProposal` contains candidate rules, accounted table IDs, context inventory,
evidenced facts, observed slots/values, arithmetic and explicit unresolved items.
The model cannot supply approval metadata. `PageExtraction` wraps it with
server-owned source identity, model/Region, usage, duration and bounded attempts.
`assemble` builds ReviewMaterial for inspection; missing pages/tables, unresolved
shapes or duplicate facts cannot become completed coverage.

ApprovalReceipt signs the exact ReviewMaterial digest and OS reviewer identity,
case/version and time. It is stored outside submitted data in an owner-only local
store. Confirming model-proposed facts creates a new material version/digest;
approving uses that exact inspected digest. No automatic production bypass exists.
ReferenceCatalog sections are versioned citations, and their arithmetic proposals
must enter the same material approval boundary. See ADR 0006 and the runbook.

For extracted pairs, each side's validated SourceCitations determine its canonical
FactorObservation.evidence. The adapter resolves the actual document/page/region
and derives source_file, document_id, page, block_ids, bounding_box and
coordinate_system locally. No local URI is sent to the model. Optional legacy
location fields may be absent; explicitly conflicting fields are rejected. Missing
canonical citations cannot be replaced by model-supplied legacy evidence.
Canonical evidence confidence and observation confidence are zero after extraction;
method=model_proposed remains until explicit confirmation. Source binding is not
proof of semantic accuracy. The receipt covers all normalized evidence and facts,
so later changes cannot reuse approval. Legacy EvidenceRef and PDFWriter are
unchanged; Reliability has the explicit material extension described in ADR 0008.

Source identity, complete observed/slot value-anchor sets, DAG derivation and
loaded-versus-evaluated audit events are specified in [ADR 0007](adr/0007-source-cell-and-derivation-trust.md).
The PDF writer protocol is unchanged; writer-time snapshot enforcement remains
outside the implemented source URI check.

Reliability explicitly distinguishes measured evidence from localization and
unknown provenance. Native evaluation conservatively uses all critical scores;
controlled confirmation preserves raw scores and binds each side before separate
exact-material approval. Existing method-only material is untrusted and old
receipts require explicit reviewer migration, never automatic re-signing.
See [ADR 0008](adr/0008-measured-confidence-and-human-confirmation.md).
Legacy EvidenceRef and HTTP success/error schemas remain unchanged.

The canonical extraction producer is canonical-pdf-localization-v1. It always
emits localization_only/parser_registry with no confirmation, regardless of model
provenance claims. confirm-facts adds the local-review-v1 side digest and actual
OS UID/login. LocalApprovalStore requires that same reviewer and current side
digest for confirmed material; confirmation alone does not grant authorization.

ADR 0009 specifies one typed fill candidate validated against all incoming and
independent expectations before it becomes a reusable value. It also defines
source-purpose restrictions by selected document identity, not role strings alone.
The original observation and all scores remain unchanged. Public PDF contracts
are unchanged; source-role exceptions and ambiguous fill selection are unsupported.

Local receipt acceptance uses a fail-closed eligibility check on every factor side
in both approve and permits. Native measured provenance and complete current reviewer
confirmation are the only accepted paths; no second confidence threshold is added.
Invalid historical receipts remain on disk but cannot be used. Serialization and
public PDF schemas are unchanged in this round. The POSIX reviewer and local URI
platform contract is documented in ADR 0009 and the runbook.

## Full-case golden contract (#23)

`domain/golden_contract.py` types one reviewed acceptance manifest per case, and
`domain/golden_validator.py` both re-derives a manifest from its fixture and compares an
actual run against it. Expected values are authored from citations, rule bands, arithmetic
and recorded adjudications; they are never captured from a review result. Candidate rule
extraction, exact-material authority and formal business approval are separate fields, and a
disputed question stays unresolved instead of grounding a value.

Reviewed manifests live under `tests/goldens/` and are read-only for every evaluation,
including live AWS runs. `adapters/local/golden_cases.py` builds the synthetic fixtures and
the authored expectations; `scripts/generate_goldens.py` writes or checks the manifests. The
procedure, case matrix and reviewer checklist are in [golden acceptance](golden-acceptance.md),
and the decision is recorded in [ADR 0014](adr/0014-full-case-golden-contract.md).
