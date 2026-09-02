# MVP plan

## Supported factor slice

The first end-to-end slice targets:

1. Main road width.
2. Average road width within the section.
3. Drainage condition.
4. Terrain condition.
5. Proximity to a traditional market, supermarket, or major shopping center.

This set exercises numeric ranges, semantic categories, distance ranges, units,
correction matrices, evidence tracking, and human-review behavior.

## Milestone 0: domain confirmation

- Inventory the exact source fields and cross-form destinations.
- Have a domain owner confirm the applicable example rule set and matrix
  orientation.
- Define synthetic and anonymized acceptance cases.

Acceptance criteria:

- Every MVP factor has a stable factor ID and documented source location.
- The example rule set is marked case-specific and has a source identity.
- Ambiguities in the source PDF are recorded, not guessed.

## Milestone 1: executable contracts and rules

- Validate typed facts, units, applicability, intervals, categories, and
  matrices.
- Reject overlapping intervals, missing grades, and unknown factor IDs.
- Test inclusive and exclusive boundaries and matrix row/column direction.

Acceptance criteria:

- The local core evaluates reviewer-confirmed JSON without AWS credentials.
- Every result has a rule ID and deterministic trace.
- Missing evidence and low confidence produce `needs_review`.

## Milestone 2: local document vertical slice

- Parse the criteria and valuation forms with a replaceable local or cloud
  document adapter.
- Convert extraction output into typed facts and candidate rules.
- Add a review step before candidate rules can be published.

Acceptance criteria:

- One sample case runs from source PDFs to verified findings.
- Extracted facts retain page and coordinate evidence.
- Unsupported tables and unresolved labels are surfaced explicitly.

## Milestone 3: verification, PDF, and audit

- Verify evidence, units, classifications, matrices, totals, and copied values.
- Overlay verified values on a copy of the original PDF using a field map.
- Export a machine-readable audit log.

Acceptance criteria:

- Output PDF page count and readability are checked.
- Chinese text placement and overflow are tested.
- Critical failures prevent PDF completion.
- Every write location and written value appears in the audit trail.

## Milestone 4: AWS vertical slice

- Select services only after account permissions, Region, quotas, and supported
  models are confirmed.
- Add asynchronous processing, persistence, retries, idempotency, and
  observability through replaceable adapters.

Acceptance criteria:

- Infrastructure is reproducible and least-privilege.
- Raw documents and derived results remain private and versioned.
- The tested local core remains provider-neutral.

## Milestone 5: evaluation and demo

- Measure extraction accuracy, rule-level precision/recall, false-negative
  rate, and reviewer time saved.
- Demonstrate source highlighting, human override, rule provenance, and safe
  failure behavior.

## Remaining risks

- OCR may lose table structure, selection marks, or coordinate fidelity.
- The source criteria may contain typographical or interval ambiguities.
- Matrix orientation requires explicit domain confirmation.
- Different templates require different field maps.
- Competition cloud permissions and model availability are not yet known.
