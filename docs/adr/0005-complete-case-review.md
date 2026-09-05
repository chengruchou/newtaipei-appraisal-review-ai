# ADR 0005: Independent case review and exact-material authorization

Status: proposed for human review. Implements #8; consumed by #7 and #5.

The old verifier accepted an unrelated rule/version and a forged 999 percent
result as complete. The regression was run before implementation and failed.
Result status, grades, rates, totals and rule labels are now untrusted claims.
ReviewVerifier recomputes from supplied facts; CaseReviewer additionally binds
case identity, current parser registry, applicability, inventory and approval.
Legacy factor-only adapters remain loadable but cannot authorize full completion.

Schema 2.0 adds a reviewed inventory, scoped rule sets, observed values, typed
source regions/citations, reliability, findings, coverage and multiple comparison
results. AgentReviewRun.case_review is the whole-case result; review is retained
only when exactly one comparison exists. The source registry is supplied by the
parser again at execution, not accepted from a request as current truth.

Required coverage comes from the approved document inventory plus critical rules,
not successful extraction results. Every criteria/forms page must be inventoried.
An inventory is a reviewable assertion about all required tables, factors and
empty columns. Page visitation alone does not establish semantic completeness.
#7 must produce that assertion for human inspection. Unknown items stay unresolved.

The injected ReviewAuthorization verifies the exact ReviewMaterial digest: case,
version, all source hashes/versions and regions, applicability, inventory, rules,
facts and observed values. Neither a rule's approved string nor model confidence
is an authorization credential. #7 implements the controlled reviewer store/CLI.
The only runtime fixture authority accepts fixed synthetic material digests;
there is no arbitrary-material fixture approval or production bypass flag.

Dates are inclusive at both applicability endpoints. District, zone, land-use,
scope and target/comparable must match exactly and yield one rule set. Numeric
intervals retain their explicit endpoint ownership. Matrix rows are target grades.
Percent values explicitly distinguish percentage points from ratios (multiply by
100). Arithmetic shares the legacy deterministic calculate function; sum/equals
checks run through Controller, with Decimal, declared power-of-ten quantum,
ROUND_HALF_UP and declared tolerance. Grade/rate comparisons use exact equality.
Original values are never overwritten by expected calculations.

Confirmed contradictions and invalid claims have failed priority; missing or
uncertain information produces needs_review; only complete verified coverage can
reach output. Blank, missing, not_present, not_applicable and present zero are
separate states. Approved derivable blanks can be populated only when their
expected value is independently available. Blank comparable columns are explicit
inventory evidence, not case entities. Duplicate composite identities fail.

PDFWriteRequest and ports.pdf.PDFWriter remain authoritative. A bound result's
context must match every field reference. The current writer supports one
comparison: multi-context review returns artifact_status=unsupported_contexts,
no first-column selection and no writer call. No writer returns verified with
artifact_status=unavailable. PDFWriteResult.artifact_created defaults true for
existing actual writer implementations; the fake returns false, producing
verified/simulated with no published output_pdf_uri. completed requires a typed
successful actual writer response. B still owns byte validation and publication.

API error envelopes and legacy validation responses are unchanged. These new
review semantics supersede earlier A-only verifier limitations; the local audit
logger is unchanged. Formal PR approval and real rule approval remain separate.
