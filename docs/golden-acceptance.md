# Full-case goldens and the reviewer acceptance protocol

Delivery: #23 (B1). Follow-up to the complete-case acceptance work in #7 and #8.

This document defines the shared acceptance basis for a whole appraisal case: what a
golden case declares, how a reviewer grounds every expected value, what two reviewers
record when they disagree, and what CI enforces. The manifests are the contract; this
document is the procedure around them.

## Data use

Every fixture is fully synthetic. Documents, districts, measurements, rules and reviewer
names are invented for testing by `adapters/local/golden_cases.py`; none is derived from a
real appraisal case, from the competition brief, or from any sample form. No fixture reads
an external file, and no committed manifest contains a real case, a reversible identifier
or a binary document. Each manifest repeats this statement in its `data_use` block, and the
manifest tests assert it.

## What a golden case declares

One manifest per case under `tests/goldens/`, typed by `domain/golden_contract.py`:

| Section | Meaning |
| --- | --- |
| `fixture` | The exact material digest and document hashes the manifest was authored against. |
| `identity` | Case, revision, district, zone, category and effective date. |
| `material_authority` | Whether this exact material carries an approval grant. |
| `expected_slots` | Per reviewed field: what the form prints, what the reviewer derives, the expected finding status and kind. |
| `expected_findings` | The complete set of findings the review must report, as `(id, kind, status)`. |
| `expected_coverage` | Which required identities stay uncovered, and which rules are unsupported. |
| `expected_verification` | The completion gate status and the sanitized public diagnostics. |
| `expected_tasks` | The human decisions the case must raise, in the frozen `HumanTask` shape. |
| `expected_artifact` | Artifact status, and the exact field coverage when one is produced. |
| `expected_rules` | Candidate status, exact-material authority and formal business approval, kept separate. |
| `adjudications` / `unresolved` | Two-reviewer records, and every question left open. |

## The case matrix

| Case | Dimension | Case status | Artifact | Human task | Unresolved |
| --- | --- | --- | --- | --- | --- |
| `normal-complete` | normal | verified | simulated | none | 0 |
| `blank-derived` | missing data | verified | not requested | none | 0 |
| `blank-not-derivable` | missing data | needs review | not requested | material correction | 0 |
| `missing-page` | missing page | needs review | not requested | material correction | 0 |
| `missing-observation` | missing data | needs review | not requested | material correction | 1 |
| `conflicting-sources` | conflicting sources | failed | not requested | material correction | 0 |
| `zero-confidence` | zero confidence | needs review | not requested | fact confirmation (two sides) | 0 |
| `unsupported-rule` | unsupported rule | needs review | not requested | rule approval | 0 |
| `multi-context` | multiple contexts | verified | unsupported contexts | none | 0 |
| `unapproved-material` | unapproved material | needs review | not requested | material approval | 0 |
| `revision-r1` | revision chain | needs review | not requested | fact confirmation (two sides) | 0 |
| `revision-r2` | revision chain | verified | not requested | none | 0 |
| `revision-r3` | revision chain | needs review | not requested | fact confirmation (two sides) | 0 |

`normal-complete` and `blank-derived` are the completable cases; every other case requires a
human decision. `normal-complete` covers the required value kinds in one case: target grade,
comparable grade, correction rate, subtotal, total and a total copied across pages, with the
factor rows evidenced on page 1 and the summary rows on page 2.

Every level of the form carries a different number, and each factor a different correction
magnitude, so an engine that reported a rate where a total belongs, summed the wrong group,
or reported one comparison context's results under the other does not pass. In
`normal-complete` the rates are `5`, `-3` and `7`, the two group subtotals are `2` and `7`,
and the total is `9`; in `multi-context` the individual context mirrors every one of those
values with the opposite sign. One residual collision remains by construction: a group with a
single factor has a subtotal equal to that factor's rate.

The regional road measurement sits exactly on its band threshold (`10 m`, lower bound
inclusive), so every case built on the base fixture exercises the interval boundary where the
reviewer-side band reading and the engine's are most likely to disagree.

Three acceptance facts worth reading directly out of the matrix:

- `missing-observation` shows that a single field with no citation withdraws the
  current-source authority every other grounded fill depends on. Correct values elsewhere in
  the case are not enough.
- `revision-r3` shows that approving the exact material does not restore a cleared
  confirmation. A revision that only relabels a side as native extraction is demoted back to
  a proposal, and the earlier confirmations in that revision are cleared.
- `blank-not-derivable` shows that knowing the right value is not authority to write it. The
  review derives and publishes the correct total, and the unapproved blank stays empty.

## How an expected value is grounded

An expected value is authored, never captured. `expected_slots[].independent.basis` names the
authority, and `golden_validator.verify_manifest` re-derives it from the fixture.

**What is re-derived, and what is not.** `verify_manifest` re-derives the reviewed fields:
every observation, citation, classification, correction, summary, arithmetic derivation and
adjudication in `expected_slots`, plus the fixture provenance; it shape-checks
`expected_tasks` against the frozen `HumanTask` contract and `expected_rules` against the
material. It does **not** re-derive `expected_findings`, `expected_coverage` or
`expected_verification` from the fixture: those are authored, cross-checked against each
other and against the slots by the contract validators, and then compared to the engine's
actual output by `compare_case_review` and `compare_review_run`. Case status is derived from
the authored findings, not from the material.

| Basis | Re-derivation performed by CI |
| --- | --- |
| `rule_classification` | The measurement is read from the fixture pair, must be the slot's own side, and must fall inside the stated band. |
| `rule_correction` | Both grades are re-classified from the fixture measurements, and the rate must equal the matrix cell for those grades. |
| `rule_summary` | A context total is the sum of the corrections of every inventoried factor, each re-classified from the fixture. |
| `arithmetic_derivation` | Recomputed with `ROUND_HALF_UP` from inputs that each carry their own grounded expectation. |
| `human_adjudication` | An agreed two-reviewer record must carry exactly that decision. |

Nothing in a derivation may be self-asserted. A manifest states a grade or a grade pair, but
the validator re-classifies from the fixture measurement rather than trusting what was
declared; a manifest that inverts a grade pair and every value below it is rejected, not
accepted as internally consistent.

Four further rules close the ways a plausible-looking manifest could still ground itself:

- **Citations are bound to the field.** An observed citation must resolve in the registry,
  must print the value, and must be one of that slot's own reviewed source cells. Citing a
  neighbouring cell that happens to print the same number is rejected.
- **Derivations may not be circular.** A chain that returns to the field it is deriving
  grounds nothing and is rejected.
- **Arithmetic inputs need their own grounding.** An input must carry its own independent
  expectation; a derivation may not fall back to a printed value, least of all in a case that
  declares that text untrusted.
- **Findings are compared with multiplicity.** The same finding reported twice is a
  difference, not a match.

Where a case cannot ground a field at all, the manifest records no independent expectation
instead of borrowing one from the result.

## Reviewer acceptance checklist

Accept a case only when every line holds. Record the reviewer names and the date in the pull
request that adds or changes it.

- [ ] The case's dimension is stated, and the fixture actually exercises it.
- [ ] Every expected value declares a basis, and a second reviewer independently reproduced
      that basis from the fixture text without running the review engine.
- [ ] No expected value was copied from a review result, a log or a failing test.
- [ ] Fields the case cannot ground carry no independent expectation and a rationale saying why.
- [ ] The expected findings list is complete, not a subset; unexpected findings fail the case.
- [ ] Expected human tasks name real blocking findings and use the frozen task contract.
- [ ] Candidate rule extraction, exact-material authority and formal business approval are
      recorded separately, and no manifest claims formal approval.
- [ ] Disagreements are recorded as adjudication records, and disputed questions appear in
      `unresolved`.
- [ ] The fixture is synthetic, contains no real or reversible data, and adds no binary file.
- [ ] `ruff`, `mypy`, `pytest` and `scripts/generate_goldens.py` all pass locally.

## Adjudication records

Two reviewers examine each contested reading independently. An agreement is recorded with its
decision; a disagreement is recorded as a dispute and the affected identity stays in
`unresolved`. A disputed question may never ground an expected value, and the contract test
`test_a_disputed_question_cannot_ground_an_expected_value` enforces that.

Template:

```json
{
  "record_id": "short-stable-key",
  "question": "The exact question the two reviewers answered.",
  "reviewers": ["reviewer-one", "reviewer-two"],
  "outcome": "agreed | disputed",
  "decision": "the agreed value or outcome, null when disputed",
  "rationale": "What each reviewer relied on, and why the outcome is what it is."
}
```

`missing-observation` carries the worked example: the reviewers disagree on whether an
unobserved copy field is an approved blank or was never observed, so the field stays
unresolved rather than being read the way that would let the case pass.

## Changing a golden

1. Change the fixture or the authored expectation in `adapters/local/golden_cases.py`.
2. Run `python scripts/generate_goldens.py --write`.
3. Read the manifest diff. Every changed expected value needs either a source or an
   adjudication record; a diff that only changes an expected value to match new engine output
   is a rejected change.
4. Re-run the checklist above with a second reviewer and record the outcome in the pull request.

Evaluation runs, including live AWS runs, read these manifests and must never write them.
`scripts/generate_goldens.py` without `--write` only reports drift, and it refuses to write
manifests that no longer follow from their fixtures.

## What CI enforces

`pytest` runs the acceptance suite (`tests/integration/test_golden_cases.py`) plus the contract
tests (`tests/unit/test_golden_contract.py`), and the workflow additionally runs
`scripts/generate_goldens.py` in check mode. Together they assert that:

- the committed manifests match the fixture generator;
- every reviewed field in a manifest still follows from its fixture;
- the whole-case review, the independent claim checker, the completion gate and the writer
  boundary all match the reviewed expectations for all thirteen cases;
- the sanitized service envelope publishes one public diagnostic per blocking finding.

## Consumers

- **A2 (#21)** measures live extraction against these cases; accuracy claims cite a case key
  and may not adjust an expected value.
- **C1 (#26)** uses `expected_artifact` for field coverage, and `multi-context` for the
  single-context writer boundary.
- **B2 (#24)** uses `expected_tasks` as the shape of the tasks its API must persist.
- **B3 (#25)** can drive the browser workbench from the same fixtures without AWS.
- **D3 (#31)** runs the cloud rehearsal against these cases read-only.

Reuse the entry points rather than rebuilding fixtures: `golden_fixtures()` returns each case
with its material and request, `golden_adapters(fixture)` composes a local controller over it,
and `load_golden_manifests(directory)` reads the reviewed expectations.

## Recommended updates to #7 and #8

- #8 should record that the deterministic gates are exercised end to end by a full-case
  matrix, listing which behaviours are now verified: rule applicability selection, evidence
  reliability, arithmetic derivation with a single grounded candidate, cross-page copy
  equality, blank derivation under explicit approval, and coverage reporting.
- #7 should record that candidate rule extraction and formal rule approval remain separate:
  every golden rule set is a `candidate` with `business_approval: pending`. The matrix proves
  the approval boundary is testable; it does not approve any band for competition use.
- Both issues should state that a live-model or AWS evaluation compares against these
  manifests read-only, and that changing an expected value requires an adjudication record.

## Known limits

- Human tasks are declared expectations. The repository has no task producer yet; B2 (#24)
  and #17 own that, and these manifests are the shape they must satisfy.
- The artifact expectation for `normal-complete` is `simulated`: the acceptance run uses the
  contract test double, so it asserts field coverage and the writer boundary without creating
  a file. Real rendering acceptance stays with #5 and C1.
- The fixtures use synthetic English text. Chinese document understanding remains #7 and #21;
  these goldens fix the review contract those deliveries are measured against.
