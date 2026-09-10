# ADR 0022: Close raw model entrypoints and require authorized snapshot assembly

Status: implemented locally for Issue 21 Phase 2, 2026-09-10.

## Decision

Use `AuthorizedExtractionService` as the application entry for document model
execution. It requires trusted principal case/review permission and an injected
Issue 22/27 resolver. Missing integration fails before provider work. Revalidate
the resolver snapshot's bytes, identity, purpose and page binding. Render only
those immutable bytes; never reopen a caller-provided file URI or accept an image
independently of the snapshot. Backend/renderer injection is trusted server
configuration, not a request-body extension point.

The closed context includes only task/language/version. Source projection includes
sanitized document ID, digest, version, role, coordinate conventions and selected
page data; it excludes URI, document date, case identity prose and prior model
contexts. Sanitized parser text and pixels still depend on the resolver's actual
privacy provenance. A manifest-shaped object does not authorize transmission.

Close legacy raw CLI extraction and its client factory, public raw `extract_page`,
explanation execution/default factory, and unversioned Textract start/get/default
factory. Retain the low-level extractor implementation as private internal code
for the authorized backend and existing regression tests. This is a Python
composition boundary, not protection from malicious code with process access.

Workstations use a non-default explicit profile through lazy `WorkstationClients`.
Future Runtime composition injects clients bound to its designated execution role;
it does not create a workstation session. Both paths verify the STS account and
assumed role, client regions and exact configured model identity before Converse.
All workstation clients use finite connect/read timeouts and one SDK attempt.

Model kind is explicit: foundation, system profile or application profile.
Validate active profile/type/identity, every foundation-model ARN and every
destination against the reviewed model/region allowlist. Validate exact metadata,
TEXT/IMAGE input, TEXT output, on-demand inference and active lifecycle for each
destination. Current support is scoped to commercial `aws` ARNs and those model
types; unsupported types/partitions fail explicitly. Cross-region and global
routing require separate strict boolean opt-ins and bounded allowed regions.

Converse compatibility comes from an operator-reviewed exact model allowlist with
a capability-record digest. Foundation-model metadata alone does not expose that
API compatibility or prove actual invocation access. Record it as unverified
until an approved live smoke; do not auto-select a model or accept model terms.
Profile response fields are based on
[GetInferenceProfile](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_GetInferenceProfile.html)
and model metadata on
[GetFoundationModel](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_GetFoundationModel.html),
read on 2026-09-10. Profile model enumeration is bounded to the documented five.

## Consequences

The offline `extract-plan` command validates versioned request/configuration/budget
files without AWS or PDF reads. It deliberately reports unavailable privacy
integration and unchecked model access. A successful plan is not authorization.

Existing native parsing, candidate assembly, reviewer authority and HTTP/PDF
contracts remain unchanged. Existing low-level tests now call the private core
to retain all proposal/citation/confidence/retry assertions; separate tests
assert zero calls on every closed public entry. The old client test moves to
explicit preflight tests, including second-destination denial.

The backend temporarily retains the legacy `PageExtraction` success shape.
Phase 3 still owns complete per-attempt nullable telemetry, total deadline and
budget accounting, full image decoding and detailed safe error taxonomy. Phase 4
owns any justified Textract route; explanation remains unavailable until a
reviewed findings projection exists. No production resolver, live invocation,
independent model quality result or deployment is supplied by this phase.
