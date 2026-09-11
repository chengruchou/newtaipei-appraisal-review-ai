# ADR 0044: Originating source-field diagnostics preserve case-wide fill denial

- Status: Proposed for human review
- Date: 2026-09-11
- Scope: Review findings and result consumers; implements the PR #46 condition

## Decision

The ruling in [golden acceptance](../golden-acceptance.md) remains unchanged:
missing current-source evidence withdraws fill authority for the entire case.
Diagnostics identify where source review begins without granting any field
permission, changing a measured confidence or replacing calculated values.

`ReviewFinding.originating_field_ids` is an additive list, empty by default.
The reviewer derives its sorted, unique entries from source-purpose violations
that exactly match `observed/<slot id>` in the current reviewed inventory. Entries
are strict strings preserving each exact existing slot ID, including separators,
Unicode and control characters. The existing slot contract has no character or
length restriction; adding one here would silently omit accepted fields. List
cardinality is bounded by the current inventory; unknown observations, generic
source errors and material-approval failures never invent originating fields.
These identifiers already occur in finding IDs and coverage; Unicode is not a
privacy classification. Observation text is never used as an identifier. A direct source-cell
binding failure can identify its own known field without asserting a wider
source-purpose violation. The reviewer recomputes the mapping for every review;
previous results and old revision mappings are never inputs to that decision.

Blocked field and arithmetic findings carry the original violating fields,
including multiple origins and de-duplication when both the slot and observation
violate source policy. Existing statuses, finding kinds, comparison calculations,
evidence confidence, coverage and fill gates remain unchanged. A short trace
suffix JSON-quotes the same IDs, escaping quotes and control characters while
retaining readable Unicode, without adding observation raw text or source excerpts.

## Compatibility

Canonical `ServiceResult` already projects `ReviewFinding` directly. It exposes
the optional metadata without an additional result envelope or authorization
path. Non-empty metadata participates in result digests. Empty metadata is
omitted during serialization so historical stored results keep their original
canonical digest after typed reloading. This supports the declared Pydantic 2.11
baseline and preserves the complete generated finding schema.

Legacy synchronous HTTP and invocation retain their previous finding field
shape by excluding this additive property. They receive the actionable trace
suffix. Old findings parse with an empty list. Service-v1/OpenAPI schemas,
deterministic fixtures and the generated browser client are updated together;
strict consumers of the canonical service contract must use the updated schema.

The result panel groups and de-duplicates the original fields, links each to
its source-binding finding when available, preserves displayed values and states
that filling remains blocked for the entire case. Reloading a result removes
its previous diagnostic mapping. IDs render as escaped React text; links use
numeric finding anchors rather than identifiers as HTML or URLs. No UI interaction
grants approval.

## Evidence and limits

Focused regressions cover original single and multiple missing citations,
wrong source purpose, stale source version, repaired material, unknown observed
IDs, slash/Unicode/control-character and long or empty accepted IDs, raw-text
isolation, old digest round trips and real controller HTTP and
invocation responses. The committed-result route is exercised through actual
application services and a local job/result store. Existing goldens retain all
reviewed expectations and values. These are local synthetic checks; they do not
approve real materials, formal rules, production fonts or live acceptance.
