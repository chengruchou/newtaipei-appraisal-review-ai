# ADR 0016: Controlled execution failure boundaries

Status: Accepted for local implementation; production durability is out of scope.

## Context

The Issue 17 audit reproduced false terminal success after an invalid receipt,
execution after selection exhausted its deadline, overlapping provider calls after
timeouts, retry of permanent authorization errors, loss of failed-selection trace,
invented source regions, and failure to continue from review to human handoff.
Source-only preparation also incorrectly required already populated rules.

## Decision

- Terminal state alone cannot certify a failed decision. Successful review with
  business blockers may select its next legal human action; technical failures
  keep their original error category.
- Selection and execution share the remaining wall-clock allowance. A timeout
  quarantines the run from automatic re-execution. The synchronous provider call
  holds an adapter lock until its actual return, even if its awaiting task ends.
  Python cannot forcibly terminate that thread: configure transport timeouts in
  the injected SDK client. This is not rollback or a cross-process execution lease.
- `SelectionFailureEvent` records sanitized error code, selector identity,
  available model/prompt/token/attempt metadata, budget consumption and causal
  parents without inventing a valid proposal or retaining malformed output.
  Unknown attempt count is null; remaining call budget is conservatively reserved.
  DecisionTrace implementations must add append_failure/read_failures and enforce
  unique event IDs and causal parents across both streams.
- Source actions require a trusted injected registry and exact supplied located
  citations. An allowed document/page does not authorize invented regions.
- `MaterialRevision.canonicalization = source-documents-json-v1` hashes the JSON
  array of document references sorted by document_id, using existing content_digest.
  It contains no rules or corrections and supports only source preparation states.
  Existing review-material-json-v1 digests and nonempty-rule requirements remain.
  Preparation does not confer approval or authorize deterministic review.
- `LocalControlledCase` composes the existing reviewer and human-task service for
  prepared cases with located inputs and approved rules. It stores actual results
  and hashes, uses configured finding-to-task bindings, and creates an admitted
  task batch atomically. Aggregate calculation findings remain in the review and
  are recomputed, not confirmed as side facts; they can be deferred only alongside
  mapped findings in that comparison. Unmapped independent findings fail closed.

## Consequences and limits

Consumers must regenerate service-v1 schemas and support selection_failures on
bounded results, the additional failure category and source canonicalization.
The outer service version is unchanged; older strict consumers need a coordinated
schema update before accepting these additive controlled-workflow records.

Injected-model tests execute real review, task creation, correction and fresh-run
re-entry. Exact material signing remains a separate explicit operator action; the
integration test invokes that existing approval flow directly. This is not a
general automatic finding classifier, a raw-PDF parsing pipeline, a live provider
test, new HTTP routes, durable job recovery or production acceptance.
