# ADR 0001: Deterministic validation owns decisions

- Status: Accepted
- Date: 2026-08-16

## Context

The challenge requires AI assistance, but appraisal review includes exact
arithmetic, grade tables, and values copied across forms. Foundation-model
responses are probabilistic and are not sufficient evidence for these checks.

## Decision

The canonical rule engine exclusively determines `pass`, `fail`, and
`needs_review`. Bedrock can propose semantic mappings and explain existing
findings, but its output cannot directly change a finding status.

## Consequences

- Rules and policy versions are reviewable and testable.
- Every decision can identify its exact inputs.
- Template mapping still benefits from AI while remaining human-confirmable.
- New appraisal logic requires explicit rule implementation and tests.
