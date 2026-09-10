# ADR 0014: Full-case goldens are authored, re-derivable expectations

Status: proposed on the golden acceptance branch, 2026-09-10; not merged.
Delivery: #23 (B1); follow-up to #7 and #8; consumed by #21, #24, #25, #26 and #31.

## Context

Five workstreams need one acceptance basis for a whole case rather than per-unit
assertions. The repository already has deterministic review, an independent claim
checker, a completion gate and a typed writer boundary, but no case-level contract
that all four share. The obvious approach, recording current engine output as the
expected result, cannot detect a wrong result: an incorrect grade, rate or total
would be recorded as correct, and a later regression would be "fixed" by rewriting
the expectation. Live model and AWS evaluations make this worse, because a run that
can write back its own expected values proves nothing.

## Decision

A golden case is a reviewed manifest, not a captured result.

Every expected value declares the authority a reviewer used: a citation into the
fixture, a rule classification, a rule correction cell, an arithmetic derivation, or
an agreed two-reviewer adjudication. `verify_manifest` re-derives each one from the
synthetic material before the manifest may be written or trusted, using its own
interval reading and its own rounding rather than the review engine's. Grades are
re-classified from the fixture measurements rather than taken from the manifest, so
no part of a derivation is self-asserted; derivations may not be circular, and an
arithmetic input must carry its own grounded expectation rather than falling back to
printed text. An observed
value must additionally be printed in the cited excerpt, and every citation must
resolve in the fixture registry, so a manifest cannot expect a number the document
does not show.

Expected findings are complete rather than a subset: an unexpected finding fails the
case. Human task expectations are built through the frozen `HumanTask` contract, so
a golden cannot describe a task the service could not legally issue. Candidate rule
extraction, exact-material authority and formal business approval are three separate
recorded facts. Where a case cannot ground a field, the manifest records no expected
value; where two reviewers disagree, the manifest records the dispute and the
identity stays unresolved.

Manifests live under `tests/goldens/` and are read-only for every evaluation. The
generator writes them only under an explicit `--write` flag, and CI fails when the
committed manifests and the fixture generator disagree.

## Consequences

- A wrong deterministic result is a failing case, because the expectation is derived
  from the criteria and the form text rather than from the engine.
- Changing an expected value requires either a new source or an adjudication record,
  which makes "fix the test" visible in review as a manifest diff.
- Fixtures and manifests are coupled by material digest, so a fixture edit without a
  manifest review fails rather than silently passing.
- The matrix is deliberately explicit: adding a finding kind updates twelve manifests.
  That cost is the point of a complete acceptance contract.
- Human task expectations run ahead of their producer. They are declared and shape
  checked now, and #24 and #17 are held to them when the producer lands.
- The fixtures remain synthetic English text. They fix the review contract, not
  Chinese document understanding, which stays with #7 and #21.
