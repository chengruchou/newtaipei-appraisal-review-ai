# ADR 0003: Shared synchronous entry and separate durable cloud jobs

- Status: Proposed for human review
- Date: 2026-09-05
- Deliveries: #4 and #9

A uses one composition root and shared request/run models for synchronous HTTP
and a framework-neutral invocation function. It validates input before resolving
tools; configuration and execution errors use sanitized explicit envelopes.
Runtime mode is validated, and synthetic fixtures require an explicit local opt-in.
No import constructs an AWS client or turns unavailable cloud adapters into fakes.

The future public cloud interface uses authorized document IDs and a separate
review-jobs contract. DynamoDB owns execution state and lease/fencing decisions;
the Controller owns business review status. Accepted dispatch never implies
completion. Persist an outbox and final result manifest to bridge DB, queue and
object-storage failure windows. Runtime sessions are per-attempt context, not
a replacement for durable job storage.

This separation preserves /v1/reviews semantics and keeps A/B tests credential
free while permitting an independently reviewed cloud-test PR. Runtime
container/health/task tracking and real document review require separate evidence.
No speculative service shortage is used to defer the target AWS architecture.

The current verifier's narrow presence/status checks are preserved as required.
Full-case completion requires #8; adding transport or a fake writer does not
expand the meaning of verified. PDF warning metadata remains a separate run field.
