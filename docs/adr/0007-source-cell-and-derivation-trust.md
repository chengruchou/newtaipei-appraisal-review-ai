# ADR 0007: Source identity, value anchors and grounded arithmetic

Status: proposed for human review. Corrects review 5122010147 in #15 and is
inherited by #16. The earlier context, threshold and tolerance corrections remain.

Every typed parse is revalidated at the Controller boundary. Requested URI,
ParsedDocument.document_uri and SourceDocument.uri must have the same existing
PDF document_identity. Criteria/forms entry roles and page counts must agree;
registered reference/brief sources receive the same checks. Current sources must
match the reviewed document ID, hash, version, role, pages and content. File URI
lexical equivalence is allowed; S3 keys remain literal. Registry comparison allows
only that existing URI equivalence, never a different source alias or version.
Source mismatches produce a sanitized source_binding failure and no writer call.
Legacy source-less adapters retain their input shape and cannot complete a case.

The writer receives the successfully checked forms source URI; page count is
checked against its typed pages. PDFWriteRequest/Result and the sole PDFWriter
protocol are unchanged. URI identity and parser hashes do not enforce an immutable
writer-time byte snapshot. Storage alias checks and byte validation still belong
to B; this correction does not implement or claim that enforcement.

Every ReviewSlot.evidence and ObservedValue.evidence entry is a value anchor.
Their complete sets must match by document ID/hash/version/page/region ID/bbox;
excerpts do not establish identity. Each reference must independently resolve.
Context citations belong to the context inventory, not extra observation anchors.
Present, blank and derivable_blank all use this binding before comparison or
arithmetic. Alternate, containing or cross-cell mappings are not represented by
this contract and require needs_review. No implicit mapping or citation rewriting.

ArithmeticCheck rejects direct self-reference. Execution revalidates mutable
material and sorts the entire approved dependency graph as a DAG; a cycle prevents
aggregate derivation. A leaf must be an independently computed, passed factor
value (including an independent total) whose observed value and anchor passed.
There is no untyped trusted-leaf escape hatch. Derived targets must pass every
incoming constraint before their unchanged observed number can feed later checks.
This permits reviewed rounding differences to propagate through a grounded chain,
while preserving ROUND_HALF_UP, each quantum and inclusive tolerance. All checks
are retained regardless of input order; factor grade/rate comparisons stay exact.
Cross-context DAGs remain valid. Failed, missing or incorrectly located inputs
cannot seed expected values; coverage and findings preserve the blocked targets.

rules_loaded records loaded rule-set ID/version/context/status and rule IDs.
factors_evaluated records actual comparison identities, factor outcomes and
computed/not_computed states, with explicit selection reasons for contexts that
were not computed. Loading a candidate does not imply selecting or evaluating it.
results_verified follows evaluation, including before writer failures. Events do
not include full documents or model responses. The local audit logger is unchanged.
