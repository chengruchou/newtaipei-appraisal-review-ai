# Problem definition

## Goal

Assist reviewers in checking whether appraisal forms consistently apply the
case-specific factor table, correction rates, and totals across multiple
documents.

## Primary failure modes

1. A factor is assigned to the wrong grade or interval.
2. A correction rate does not match the selected grade.
3. Component correction rates do not sum to the reported total.
4. A total is copied inconsistently across forms.
5. Evidence is missing, ambiguous, or too low-confidence for automation.

## System outputs

Each finding must include:

- rule identifier and version;
- `pass`, `fail`, or `needs_review` status;
- expected and observed values when available;
- source page/block evidence and extraction confidence;
- a concise explanation suitable for a human reviewer.

## Non-goals

- Issuing an official appraisal decision.
- Inventing missing values or silently correcting source documents.
- Treating a language-model answer as arithmetic evidence.
- Encoding official thresholds before the responsible agency confirms them.
