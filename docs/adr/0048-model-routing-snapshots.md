# ADR 0048: Bind model discovery to each guarded invocation

## Status

Implemented for local verification. Independent review, hosted CI and approved
competition-environment acceptance remain separate gates. No real model access,
pricing basis, materials or deployment is approved by this decision.

## Context

The approved profile can contain model A in one region and model B in another.
Comparing discovered routes only with the union of allowed regions lets an
expanded route for A pass. The existing exact-set helper had no production call
site, and the foundation metadata authorization also used a union across models.
The reproduction uses the actual guarded SDK clients, a localhost metadata and
runtime server, and a synthetic local DynamoDB budget ledger. It requires no AWS.

## Decision

Create one `CompetitionModelClients` view per extraction preflight. Its immutable
routing value pins the exact model identifier, kind, complete destinations,
reviewed routing digest, competition profile digest and bounded deadline. All
approved uses of an identifier must agree. Discover system/application profile
routes through that view and compare the entire set before foundation lookups.
Static foundation models pass through the same extraction capability checks.
The requested foundation ARN and client region must belong to this model's
verified set. The runtime identifier and endpoint must match the bound model and
primary region. Returning a new wrapper keeps the routing value separate from
the cached native SDK transport. A generic dynamic-profile client has no proof.

Capture the routing value in the existing request context and recheck it before
each physical send, including automatic SDK retries. Existing authority,
immutable-wire admission, dispatch serialization and per-send budget reservation
remain in force. A failed fresh preflight increments a factory-local model
generation, invalidating older returned clients for that model. A later exact
discovery can issue a new client, but cannot revive an earlier client. The bound
budget profile must continue to equal the current approved profile, preventing
changed routing or pricing from reusing an earlier budget basis or ledger.
No counters are reset, refunded or implicitly reseeded.

The SDK STS query serializer emits immutable form text. Encode that text once as
UTF-8 before exact-wire admission and sending; retain the existing operation,
body and metadata checks. Other mutable/streaming bodies remain unsupported.
This permits the real guarded identity check used by extraction preflight.

## Limits and compatibility

This boundary protects trusted composition, not arbitrary code with access to
Python internals. It is not a cross-worker routing cache or remote change feed.
Every extraction starts its own metadata preflight, and its client expires at
the bounded deadline. A remote route change after discovery cannot be observed
without a new discovery. Changes to a reviewed destination set require fresh
operator review, pricing evidence, profile pins and matching budget provisioning;
no application request can grant those approvals.

HTTP, invocation, service-result and evidence contracts are unchanged. Existing
cross-region/global opt-ins, role and capability checks remain mandatory. An
approved static foundation model retains its exact direct-client path. Dynamic
profiles require the production extraction preflight. Default unconfigured
runtime behavior remains unavailable. Protected verification/audit files and
the submission checker remain unchanged.

## Verification

`tests/unit/test_competition_routing.py` exercises real SDK serialization and
loopback HTTP with the real competition factory, dispatcher, admission and
budget code. It covers exact positive foundation/system/application routes,
expanded/missing/duplicate/substituted routes, separate model scopes, wrong
metadata regions, generic-client bypass, fresh failure invalidating an older
client, pricing/profile changes, deadline/revocation and automatic SDK retries.
Synthetic credentials, model identifiers and budget approvals are test fixtures.
These checks establish no AWS, model quality or competition-account acceptance.

Original finding: [PR #45 routing review](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/45#discussion_r3987437471).
Follow-up: [Issue #47](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/47).
