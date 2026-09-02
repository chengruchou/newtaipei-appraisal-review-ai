# ADR 0001: Deterministic validation owns decisions

- Status: Accepted
- Date: 2026-08-16

## Context

The challenge requires AI assistance, but appraisal review includes exact
arithmetic, grade tables, and values copied across forms. Foundation-model
responses are probabilistic and are not sufficient evidence for these checks.

## Decision

The deterministic engines exclusively determine grades, correction rates,
totals, and `pass`, `fail`, or `needs_review` outcomes. A model can propose
semantic mappings and candidate rules or explain existing findings, but its
output cannot directly change a finding status or publish a rule set.

## Consequences

- Rules and policy versions are reviewable and testable.
- Every decision can identify its exact inputs.
- Template mapping still benefits from AI while remaining human-confirmable.
- Case-specific criteria are versioned data rather than district-specific code.
- New appraisal logic requires explicit rule implementation and tests.
