# Pull Request 15 Review

## Review identity

- Pull request: [#15](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/15)
- Base commit: `b3760b9ae15d97dd16ffdd0da130e564324c71f6`
- Head commit: `70c8bac657b594d5f1b9707d8161b584633cd1cf`
- Synthetic merge commit: `94f7aae9a190e70cf40fe9ed8b7ea8ee09d98e44`
- Commits: 5
- Changed files: 33
- Diff size: 2,535 additions and 269 deletions
- Review date: 2026-09-06

This report reviews the commit history and code diff available through the Git
pull-request refs. The repository's authenticated web review conversation and
inline comments were not available through the unauthenticated GitHub API.

This review pass used manual diff inspection and small, focused in-memory
counterexamples. It did not invoke an automated CI workflow or the full test
suite.

## Executive conclusion

The architectural direction is sound. The pull request replaces a completion
gate that trusted a claimed `verified` result with a whole-case review that
recalculates factor results from approved rules and facts. It also introduces a
source registry, explicit inventory, comparison contexts, observed slots,
arithmetic constraints, exact-material authorization, and explicit PDF artifact
status.

The current head should not be merged without revision. Four trust-boundary
defects can allow incorrectly bound or insufficiently evidenced material to
reach `verified` or `completed`. One additional regression weakens the audit
trail by removing rule identities and the factor-evaluation event.

### Finding summary

| Priority | Finding | Consequence |
|---|---|---|
| P1 | Reviewed source identity is not bound to the requested and written PDF URI | One document can be reviewed while a different document is sent to the writer |
| P1 | Per-evidence confidence is not enforced | Evidence with confidence below the runtime threshold can authorize a verified case |
| P1 | Observed citations are not bound to their review-slot citations | A value from another valid cell can be assigned to a slot and pass |
| P1 | Arithmetic targets may reference themselves or form cycles | Tautological aggregate checks can satisfy required coverage |
| P2 | Rule IDs and the factor-evaluation audit event are discarded | The audit trail no longer records which rules were evaluated |

## Intended change

The pull request is attempting to establish the following flow:

```text
parse current source documents
-> build a versioned source registry
-> inventory every required page, table, context, factor and output slot
-> bind source citations and reliability
-> select exactly one applicable rule set per context
-> independently calculate grades, rates and totals
-> compare observed values and cross-form arithmetic
-> authorize the exact policy and facts material
-> call a PDF writer only after complete verification
```

The main capabilities introduced by the change are:

- schema 2.0 case-review contracts;
- source documents, pages, regions and citations;
- case identity and comparison-context binding;
- reviewed inventories and parser-discovered table accounting;
- exact-material authorization through an injected port;
- deterministic independent factor recalculation;
- observed grade, rate, subtotal and total comparisons;
- cross-context arithmetic checks using `Decimal`, `ROUND_HALF_UP`, quantum and
  tolerance;
- categorical presence separated from numeric distance;
- multiple comparison contexts with explicit unsupported PDF output;
- actual, simulated, unavailable and not-requested artifact states.

## Commit-message review

All five subjects use clear English imperative verbs, broadly match their diffs,
and contain no prohibited attribution. None of the commits has a body.

### 1. `e6224ba` — Implement independent case review and complete inventory gating

This is the main architectural commit. It adds the source and review contracts,
`CaseReviewer`, authorization port, independent verification, controller
integration, synthetic schema 2.0 fixtures, documentation, and initial tests.

The subject is accurate but too broad for a commit that changes 32 files and
adds approximately 1,866 lines. The data contracts, review engine, controller
migration, artifact-state change, and documentation could have been separate
commits. A body should explain the completion-gate vulnerability, schema
migration, legacy behavior, and trust assumptions.

### 2. `468e636` — Bind legacy evidence to source regions and preserve blank cell citations

This commit binds legacy `EvidenceRef` locations to typed source citations and
allows an empty cell to retain location evidence without inventing text. The
subject accurately describes its focused three-file change.

A short body would still help document the exact matching fields and why empty
text is valid only for appropriate source-region kinds.

### 3. `a52ed54` — Distinguish categorical presence from measured distance in factor rules

This commit introduces the `presence_distance` rule form. Presence categories
such as an explicit inside/absent state remain categorical, while zero is a real
numeric distance and negative physical distances remain unresolved. The matrix
covers the union of categorical and interval grades.

The subject is accurate and the commit is reasonably focused.

### 4. `acf3a45` — Require inventory accounting for every parsed source table

This commit adds `inspected_tables` and compares it with every table identifier
discovered by the parser for criteria and forms documents. Its purpose is to
prevent successful factor extraction from being mistaken for complete document
coverage.

The subject and scope are aligned. The commit correctly treats table accounting
as a reviewed assertion rather than proof that every cell was interpreted
correctly.

### 5. `70c8bac` — Fix review slot binding, runtime confidence and arithmetic tolerances

This follow-up commit addresses three independent areas:

- slot context and factor binding;
- propagation of the runtime minimum-confidence threshold;
- retention and enforcement of every arithmetic target's quantum and tolerance.

The subject is accurate, but the commit combines three separately reviewable
fixes and adds approximately 525 lines. Splitting these changes would improve
regression isolation and future cherry-picking. A body should state the
previously incorrect behavior and the invariant established by each fix.

## Detailed findings

### P1 — Bind the reviewed source to the requested and written document

Affected code:

- `src/appraisal_review/ports/workflow.py`, `ParsedDocument`, lines 18-24;
- `src/appraisal_review/application/controller.py`, source collection, lines
  106-129;
- `src/appraisal_review/application/controller.py`, `PDFWriteRequest`
  construction, lines 218-224;
- `src/appraisal_review/application/controller.py`, page-count comparison,
  lines 241-244.

`ParsedDocument` carries both `document_uri` and an optional `SourceDocument`,
but it does not require `document_uri` to identify the same document as
`source.uri`. It also does not require `page_count` to equal the number of pages
in `source`.

The controller builds the current review registry from `case_document.source`,
then constructs `PDFWriteRequest.source_uri` from the original request's
`case_document_uri`. Those two identities can disagree.

A focused counterexample produced:

```text
request.case_document_uri = file:///unreviewed.pdf
case_document.source.uri  = file:///synthetic/verified.pdf

workflow_status           = completed
writer_source_uri         = file:///unreviewed.pdf
reviewed forms source     = file:///synthetic/verified.pdf
```

This allows document A to supply the authorized registry and facts while
document B is sent to the writer.

Recommended correction:

1. Add a `ParsedDocument` invariant binding `document_uri` to `source.uri` using
   the repository's authoritative document-identity rules.
2. Require `page_count == len(source.pages)` when a typed source is present.
3. Check the criteria and forms roles at the controller boundary.
4. Build the write request from the exact reviewed source identity, not an
   independently supplied URI.
5. Add a negative controller test in which the parser returns a valid but
   differently identified source.

Acceptance condition: no review or writer call may succeed when the requested,
parsed, authorized and written source identities differ.

### P1 — Enforce confidence on every critical evidence reference

Affected code:

- `src/appraisal_review/domain/case_review.py`, reliability calculation, lines
  319-348;
- `src/appraisal_review/domain/factor_engine.py`, observation confidence check,
  lines 126-131;
- `src/appraisal_review/domain/models.py`, `EvidenceRef.confidence`, lines
  15-24.

The case reviewer verifies the location fields of legacy evidence but never
compares `EvidenceRef.confidence` with `minimum_confidence`. The factor engine
checks only the aggregate `FactorObservation.confidence`.

A focused counterexample used an observation confidence of `0.99` and a bound
evidence confidence of `0.00`. The final case status was still `verified`.

Recommended correction:

1. Define the relationship between observation confidence and its evidence
   confidence values in the contract.
2. At minimum, require every evidence item used by a critical observation to
   meet the configured threshold.
3. Prefer deriving the effective observation confidence conservatively from
   its bound evidence rather than accepting contradictory independent values.
4. Add threshold-minus, exact-threshold and threshold-plus tests for both target
   and comparable evidence.

Acceptance condition: any critical evidence below the runtime threshold must
produce `needs_review` and zero PDF-writer calls, even when the outer observation
claims high confidence.

### P1 — Bind each observed value to the source region defined by its slot

Affected code:

- `src/appraisal_review/domain/case_review.py`, expected-slot construction,
  lines 405-425;
- `src/appraisal_review/domain/case_review.py`, observed comparison, lines
  514-590.

The final observed comparison independently checks that `slot.evidence` resolves
and that `ObservedValue.evidence` resolves. It does not require the two citation
sets to identify the same cell or an explicitly approved mapping.

A focused counterexample left a review slot bound to the original road-rate cell
but changed its observed value to cite a second valid cell. Both citations were
valid and the final case status remained `verified`.

Recommended correction:

1. Define an explicit slot-to-observation source-region relationship.
2. For ordinary cells, require the same document ID, version, hash, page and
   region ID.
3. If containment or derived mappings are needed, represent them as approved
   typed mappings rather than implicit acceptance.
4. Apply the same rule to blank and derivable-blank observations.
5. Add swapped-cell, swapped-column and swapped-comparable negative tests.

Acceptance condition: a value copied from another valid region cannot satisfy a
slot unless the policy explicitly authorizes that mapping.

### P1 — Reject self-referential and cyclic arithmetic constraints

Affected code:

- `src/appraisal_review/domain/review_contracts.py`, `ArithmeticCheck`, lines
  61-76;
- `src/appraisal_review/domain/case_review.py`, arithmetic execution, lines
  449-508;
- `src/appraisal_review/domain/case_review.py`, aggregate comparison, lines
  514-590.

The contract checks the number of inputs for `equals` and validates quantum, but
does not prohibit the target from appearing in its own inputs. It also does not
detect indirect cycles between arithmetic targets.

The following approved check is tautological:

```text
kind   = equals
inputs = [subtotal]
target = subtotal
```

With an arbitrary observed subtotal of `123`, both
`arithmetic/self-copy` and `observed/subtotal` were marked `verified`, and the
whole case became `verified`.

Recommended correction:

1. Reject `target in inputs` at the model boundary.
2. Build a directed dependency graph for aggregate slots.
3. Reject direct and indirect cycles before arithmetic execution.
4. Require every verified aggregate chain to terminate in independent factor
   values or other explicitly trusted leaf observations.
5. Add direct self-reference, two-node cycle and longer-cycle negative tests.

Acceptance condition: no arithmetic requirement can establish its own expected
value directly or through a cycle.

### P2 — Restore rule identities and factor evaluation to the audit trail

Affected code:

- `src/appraisal_review/application/controller.py`, `rules_loaded`, lines 78-85;
- `src/appraisal_review/application/controller.py`, evaluation and verification,
  lines 132-162.

The controller now writes `rule_ids=[]` for every `rules_loaded` event, including
legacy rule sets for which IDs were previously available. The previous
`factors_evaluated` event was also removed.

The final review result retains findings and calculation traces, but the audit
event stream can no longer independently answer which rules were loaded and
when factor evaluation occurred.

Recommended correction:

1. Populate `rules_loaded.rule_ids` from all relevant scoped rule sets.
2. Restore an evaluation event after deterministic calculation.
3. Include applicable rule-set identity/version and evaluated context IDs.
4. Keep unresolved and rejected rule selection distinguishable from a generic
   `candidate` status.
5. Add audit-sequence and rule-identity assertions for schema 2.0 and legacy
   paths.

Acceptance condition: every deterministic evaluation is represented in the
audit stream with its rule IDs, version, context and outcome.

## Positive observations

The following changes are valuable and should be retained while addressing the
findings:

- claimed status, grades, rates and totals are treated as untrusted;
- factor results are independently recalculated from rules and facts;
- exact policy and facts material is authorized through an injected port;
- current parser source hashes and versions are compared with approved policy;
- page and parser-discovered table coverage do not depend on successful
  extraction output;
- missing, blank, not-present, not-applicable and present zero are distinct;
- matrix orientation remains target rows and comparable columns;
- presence categories are no longer represented by a numeric zero-distance
  shortcut;
- percent points and ratios are explicitly distinguished;
- arithmetic retains each check's quantum and tolerance and requires every
  constraint on a target to pass;
- multi-context PDF output is explicitly unsupported instead of silently using
  the first comparison;
- simulated PDF output cannot set the workflow to `completed`.

## Recommended revision order

1. Bind requested, parsed, authorized and written source identities.
2. Bind observed values to their declared slot regions.
3. Enforce evidence-level confidence at the runtime threshold.
4. Reject arithmetic self-reference and dependency cycles.
5. Restore the complete evaluation audit trail.
6. Add focused negative tests for each corrected trust boundary.
7. Re-run static analysis, unit tests, API/runtime smoke tests, submission checks,
   and the final merge snapshot only after the code review fixes are present.

## Merge recommendation

**Changes requested.**

The pull request establishes a useful whole-case review architecture, but the
current source, evidence, slot and arithmetic bindings are not yet strong enough
to authorize a completed artifact. Resolve the four P1 findings and cover them
with negative tests before merge. The audit regression should also be repaired
or explicitly deferred with an owner and acceptance criterion.
