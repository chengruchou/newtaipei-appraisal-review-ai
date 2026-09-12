# ADR 0052: Observed model routing evidence and route-dependent capability

## Status

Accepted for implementation and local verification. It approves no account, no
region, no model, no data transmission and no deployment. A routing snapshot is
an observation an operator must review, never a routing approval.

## Context

`CompetitionProfile.models[].destinations` and `destination_snapshot_sha256`
exist so that a reviewed profile states the complete set of regional destinations
a model identifier can reach, and `check_model_destinations` exists to compare a
discovered set against it. Nothing produced that discovered set. The profile
fields could only be filled by hand from a screenshot or memory, and no later run
could detect that a system-defined inference profile had changed its routing.
[Issue #47](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/47)
tracks the missing request-bound composition; the missing observation is a
prerequisite for it.

Two facts made the gap concrete during a read-only inspection of a live
competition-style account.

First, a system-defined US inference profile advertises destinations in three
regions, including one that is not among the two permitted primary regions. That
is a regional-compliance decision, not an implementation detail, and it cannot be
noticed without reading the profile's destination list.

Second, `preflight` required `ON_DEMAND` in `inferenceTypesSupported` for every
destination foundation model. A model reachable only through an inference profile
publishes `INFERENCE_PROFILE` and no `ON_DEMAND`, so the check refused the exact
model the request would have used, while a `Converse` call through the profile
succeeded. The check was testing the wrong route.

## Decision

Add `adapters/aws/routing_discovery.py`. `discover_routing` reads the Bedrock
control plane through the injected client factory and the existing physical
dispatch guard, and returns a `RoutingSnapshot` recording the model identifier,
kind, calling region, account, observed profile ARN and status, the complete
destination ARN set and the observation time.

The snapshot is evidence with deliberate limits.

- It refuses to return a partial set. A non-`ACTIVE` profile, a destination whose
  ARN does not resolve in its own region, an unreadable destination list, or an
  identifier or region the caller cannot state exactly all fail. A subset would
  understate where a request can travel, which is worse than no observation.
- `digest` covers only the routing identity and excludes `observed_at`, so
  repeating the discovery reproduces the same pin while the observation time stays
  visible. The digest binds an observed set; it is not an approval reference and
  does not become one by being recorded in a profile.
- `routes_outside` answers the regional question against regions the caller
  declares as permitted. The adapter never decides which regions are permitted.
- Discovery invokes no model and creates no runtime client.

Make the required inference type follow the invocation route rather than
preference. A `foundation` request needs `ON_DEMAND` on that model. A
`system_profile` or `application_profile` request needs `INFERENCE_PROFILE` on
every destination. Accepting `ON_DEMAND` alone for a profile route would admit a
destination the profile cannot reach, so this is a narrowing as well as a
widening.

Add `scripts/check_aws_readiness.py` as the read-only operator report. It states
Ready, Missing or Blocked per input, reports identity as digests unless the
operator explicitly asks for the values, keeps `live_acceptance` at
`not_performed` and `data_admission` at `not_granted`, and serializes every
Bedrock call through `SharedModelDispatcher`. It is not an approved competition
entrypoint: it installs no data-admission gate because it transmits no document,
prompt or case content. `adapters/aws/competition_runtime.py` remains the only
entrypoint for work that does.

The report separates two routing questions on purpose. Advertised routing answers
regional compliance even when a destination cannot be verified, and is explicitly
not a pinnable set. Verified routing produces the snapshot digest.

## Consequences

An operator can now fill `destinations` and `destination_snapshot_sha256` from a
reproducible observation and re-check them later, and a routing change or a
region outside the permitted set becomes a reported finding instead of a silent
difference. `check_model_destinations` has a producer.

This does not complete #47. Binding the discovered set to the actual runtime
request, and failing that request when the two differ, remains open. Effective
role permissions, organizer routing approval, budgets, data admission and
deployment acceptance are unchanged and still required.

A profile-routed model whose destinations cross regions still fails the existing
profile and preflight checks unless cross-region routing is separately approved
and every destination region is readable. Narrowing the route to a single region
requires a reviewed application inference profile, which is a resource creation
and an operator decision, not an adapter change.
