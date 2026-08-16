# Roadmap

## Phase 0 - Domain confirmation

- Inventory every official form and cross-form dependency.
- Confirm factor-table variants and authoritative source fields.
- Define an anonymized acceptance set with the agency representative.

## Phase 1 - Canonical contract

- Map Textract blocks to canonical fields with page evidence.
- Add confidence thresholds and explicit missing-value behavior.
- Freeze the first versioned rule schema.

## Phase 2 - Deterministic review

- Implement grade-to-rate lookup rules.
- Implement interval, sum, and cross-form consistency rules.
- Produce machine-readable and reviewer-facing reports.

## Phase 3 - AWS vertical slice

- S3 upload and asynchronous Textract analysis.
- Case state persistence and idempotent retries.
- Bedrock explanation constrained to existing findings.
- Deploy with CDK in the organizer-provided account.

## Phase 4 - Demo and evaluation

- Measure field extraction accuracy and rule-level precision/recall.
- Measure review time saved and false-negative rate.
- Demonstrate source highlighting and human override.
- Export all source, configuration, and non-sensitive results before account
  access is removed.
