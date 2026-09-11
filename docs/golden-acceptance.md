# Full-case goldens and the reviewer acceptance protocol

Delivery: #23 (B1). Follow-up to the complete-case acceptance work in #7 and #8.

This document defines the shared acceptance basis for a whole appraisal case: what a
golden case declares, how a reviewer grounds every expected value, what two reviewers
record when they disagree, and what CI enforces. The manifests are the contract; this
document is the procedure around them.

## Data use

Every fixture is fully synthetic. Documents, districts, measurements, rules and the
reviewer names appearing *inside* a case are invented for testing by
`adapters/local/golden_cases.py`; none is derived from a real appraisal case, from the
competition brief, or from any sample form. No fixture reads an external file, and no
committed manifest contains a real case, a reversible identifier or a binary document.
Each manifest repeats this statement in its `data_use` block, and the manifest tests
assert it.

The one deliberate exception is the `reviewers` list of an adjudication record. Those name
the real people who accepted the case, because the record's whole purpose is to say who
examined a contested reading; inventing names there would be the kind of ungrounded claim
these goldens exist to catch.

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
  "reviewers": ["the two real people who examined it"],
  "adjudicated_on": "YYYY-MM-DD",
  "outcome": "agreed | disputed",
  "decision": "the agreed value or outcome, null when disputed",
  "rationale": "What each reviewer relied on, and why the outcome is what it is."
}
```

`missing-observation` carries the worked example: the reviewers disagree on whether an
unobserved copy field is an approved blank or was never observed, so the field stays
unresolved rather than being read the way that would let the case pass.

Being named on a record means having examined the question, and on a disputed record it
means having held one of the two readings. It is never a sign-off on the case as a whole.
Both records currently in the suite — `copy-conflict-outcome` in `conflicting-sources` and
`copied-total-treatment` in `missing-observation` — were adjudicated by ChinHan-0451 and
ivylingchiang on 2026-09-10; the working record of that examination is the review history
on #33.

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

## What this matrix settles for the umbrella issues

#7 and #8 are umbrella issues. Both were split on 2026-09-09: #7 into #21 (A2), #22 (A3) and
#23 (B1); #8 into #23 (B1) and #26 (C1). They stay open as umbrellas, so nothing here is a
request for someone to act on them — these are the facts B1 establishes, for whoever reads
the umbrella next.

- **Deterministic gates are exercised end to end** by the full-case matrix: rule
  applicability selection, evidence reliability, arithmetic derivation with a single
  grounded candidate, cross-page copy equality, blank derivation under explicit approval,
  and coverage reporting.
- **Candidate extraction and formal approval stay separate.** Every golden rule set is a
  `candidate` with `business_approval: pending`, which satisfies #8's requirement that
  unapproved rules remain proposed or unresolved. The matrix proves the approval boundary is
  testable; it approves no band for competition use.
- **Goldens are independent of the model implementer**, as #8 requires. Expected values are
  authored and re-derived from the fixture by separate implementations, never captured from
  engine output, and the acceptance sign-off must come from someone outside the A2/A3
  extraction work.
- **A live-model or AWS evaluation compares against these manifests read-only.** Changing an
  expected value requires an adjudication record, not an edit.

## Ruling: an unevidenced field withdraws fill authority case-wide

`missing-observation` freezes a wide behaviour, so it is recorded here as a decision rather
than left implicit in a manifest. #8's split assigns source conflicts and human adjudication
to B1 (#23), so this is B1's ruling to make.

### What the engine does

One observation carrying no citation costs the whole case its fill authority. In
`missing-observation`, all thirteen slots move from `verified` to `needs_review`, not just
the offending cell.

The path is short. `SourcePurposes.allows` is `bool(refs) and all(...)`, so an observation
with an empty citation list fails at the first term and is recorded as a source-purpose
violation. `CaseReviewer` then computes one case-wide flag:

```python
source_trust = (
    authorized
    and not violations
    and not any(f.id in {"sources", "trust"} and f.status != "verified" for f in findings)
)
```

and passes it to every slot, where `validate_slot` returns `needs_review` /
`observed_unresolved` for each. Note that the `sources` and `trust` findings are themselves
`verified` in this case: the registry is current and exact-material authority was granted.
It is `violations` alone that withdraws the authority.

### The ruling

**Upheld. The case-wide withdrawal is the intended semantics.**

The reason is what a fill actually is. Writing a derived value into an official appraisal
form asserts that the value came from the authorized source for this case. That assertion is
not per-cell. An observation with no citation could have come from anywhere — a model
proposal, a stale version of the form, or a different case entirely — and nothing in the
material distinguishes those. Because the origin is unknown, the damage cannot be locally
bounded: if that value came from a different document, then the single current `forms`
document `SourcePurposes.selected` chose for this case may be the wrong one, which makes
every other citation's resolution suspect too.

This is the same conservatism as `RevisionSnapshot.revise` clearing every confirmation on a
revision. In both places the system declines to reason about blast radius because it has no
validated dependency graph with which to do so, and says so rather than guessing.

Three things this ruling does **not** claim:

- It is not a claim that the other twelve values are wrong. Their independent derivations
  still agree, and the manifest records exactly that: *"The value and its derivation still
  agree, but one unevidenced field in the case removes the current-source authority every
  fill depends on."* Every value is still computed and still shown.
- It is not a rejection of the case. `needs_review` routes to a human; it does not fail the
  review or publish a wrong number.
- It is not a statement that the registry is stale. `sources` stays `verified`.

### The cost, stated plainly

The blast radius is total. In a real case, one unevidenced cell sends every field to manual
review. That is the price of refusing to bound the damage, and it should be measured against
real documents in A2 (#21) rather than assumed tolerable.

### Required follow-up

The cascade is currently undiagnosable. Twelve findings share one rationale that says "one
unevidenced field in the case" without naming which field, so a reviewer holding the output
cannot tell where to look. The ruling is upheld on condition that the cascade findings name
the originating field. That is a message change, not a semantics change, and it does not
alter any status in these manifests.

### Reopening this

Per [Changing a golden](#changing-a-golden), overturning this needs an adjudication record,
not an edit. The evidence that would justify narrowing it is a measured false-positive rate
from real documents under #21, or a per-citation trust model that can show one field's
provenance is independent of another's. Neither exists today.

## Known limits

- Human tasks are declared expectations. The repository has no task producer yet; B2 (#24)
  and #17 own that, and these manifests are the shape they must satisfy.
- The artifact expectation for `normal-complete` is `simulated`: the acceptance run uses the
  contract test double, so it asserts field coverage and the writer boundary without creating
  a file. Real rendering acceptance stays with #5 and C1.
- The fixtures use synthetic English text. Chinese document understanding remains #7 and #21;
  these goldens fix the review contract those deliveries are measured against.
