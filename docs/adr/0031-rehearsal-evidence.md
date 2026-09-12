# ADR 0031: Independent acceptance receipts and bounded public reports

Status: proposed for Issue #31; local implementation, pending integration and live acceptance.

## Context

Local tests, successful CLI invocations and synthetic Runtime results do not
establish browser/AWS acceptance. A caller-controlled checklist can claim success
without observing the required boundaries, and ordinary validation errors can
copy private input into a public report.

## Decision

Add separately named rehearsal contracts. Require a complete fixed probe catalog,
strict public fields, exact deployment/material/revision/artifact bindings,
content-addressed receipts and independently provisioned, source/mode-scoped
collector signature verification. Pin PR refs, actual CI-tested code/run/attempt,
image and configuration. Missing, skipped, stale, unauthorized or contradictory
evidence fails closed. Report only fixed codes and counts, never input values.

Separate synthetic validation, localhost transport probes and live collection.
The delivered live runner fails until real collectors are integrated; there is
no synthetic production fallback. Synthetic receipts can validate locally but
can never recommend Ready. Existing domain authorization and publication
contracts are unchanged. See [cloud acceptance](../cloud-acceptance.md).

## Consequences

The verifier authenticates evidence provenance and consistency; it does not
reconstruct raw events or prove collector honesty. Trusted collectors must inspect
complete retained captures and sign actual observations, and an independent human
must review the synthetic rehearsal. Collector provisioning, live browser/AWS
integration, metric publishing and remote CI remain explicit dependencies. No
live trust material, raw logs or production data belong in this repository.
