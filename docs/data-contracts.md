# Data contracts

All boundaries reject unknown fields and serialize to JSON. Policy is versioned
data, not district-specific branching. Technical status below distinguishes
existing models from unimplemented review semantics.

## Existing factor contracts

`domain.factor_models` owns AgentReviewRequest, AgentReviewRun, observations,
rule sets and factor results. AgentReviewRequest has nonempty case_id,
criteria_document_uri, case_document_uri, optional output_pdf_uri and field_map.
It describes an internal/local invocation; the future cloud API authorizes
external document IDs before resolving these URIs (#9).

FactorObservation preserves raw_text, normalized value/unit, evidence references
and extraction confidence. EvidenceRef carries document/source identity,
one-based page, optional bounding box and named coordinate system. Coordinate
and source preservation is a parser requirement (#7), not something established
by the mere existence of model fields. Missing/low-confidence critical inputs
are unresolved. A model's own confidence estimate is not calibrated evidence.

FactorRuleSet declares rule_set_id/version, candidate/approved/rejected,
applicability, source identity, intervals/categories and correction matrices.
Intervals cover the supported domain in ascending order with exactly one owner
for each boundary. Matrix rows are target grades and columns comparable grades.
The engine only executes approved rules. The provider must obtain approval from
a trusted reviewer record; accepting model-declared approved is prohibited (#7).
Applicability fields currently validate date ordering, not case matching (#8).

FactorReviewResult contains per-factor expected grades/rates and a summary total.
The current shape represents one implicit target/comparable pair. It does not
carry the original form's observed grades/rates/totals or a complete case identity.
#8 adds scope/zone/target/comparable/date/version identities, observed vs expected
values, group totals, legacy sum/equals integration and cross-form provenance.
Unused comparable columns are absent entities, not incomplete comparables.

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
AuditLogger interface. The final completed state proves only the current factor
slice and output interface, not full document verification.

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

The request carries source/destination URI, FactorReviewResult and field map.
Only after verification.can_complete may the controller construct it. Every
written field has an explicit value_ref; mixed comparison contexts, duplicate
IDs, missing values, invalid bounds or conflicting URIs fail explicitly.

Successful PDF metadata lives in AgentReviewRun.pdf_result (URI, page count,
written IDs and noncritical warnings), with output_pdf_uri retained as the URI
alias. PDF warnings are not review warnings or audit-schema additions. Errors
produce a stable pdf_error code, failed workflow, retained review/verification,
and no published output URI. A validates the interface; B validates file contents.

## Future cloud job contract (#9; design only)

A job includes principal, case_id, run_id, input document/version references,
rule version, idempotency key + payload hash, execution status, business status,
active attempt/session_id, lease expiry/fencing token and optional result manifest.
Public responses omit raw internal storage URIs. The authoritative execution
state is durable DynamoDB state, not SQS receipt or an invocation response.

## Future complete review contract (#8; design only)

A finding binds observed and expected values, rule identity/version, deterministic
calculation trace, original field/page evidence and criticality. A coverage report
lists required/present/verified checks and comparison identities. All required
checks, including arithmetic and cross-form copying, enter the completion gate.
No inferred value silently replaces a verified source fact. The initial verifier
only checks selected factor presence/status, a limitation with a reproducer in #8.
