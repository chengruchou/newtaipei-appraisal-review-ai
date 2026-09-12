# ADR 0041: Versioned competition deployment profile

## Status

Accepted for offline configuration validation. Competition deployment, account
access, paid invocations and data admission remain separately gated.

## Context

The 20260722 competition environment rules restrict data, public resources,
regions, model access, request rate and resource use. The companion workbook
lists IAM namespace/action ceilings and workload-specific quotas. Those lists
do not establish the actual permissions of a competition role. Metadata tags
cannot substitute for a stop procedure.

## Decision

Use the immutable `competition-profile-v1` contract from trusted server/operator
configuration. Pin the canonical profile digest outside the serialized profile.
There is no `approved` field in the contract. Require the data-policy digest to
match a separately trusted pin; profile acceptance grants no data admission.
Caller, document and model payloads cannot supply these trusted pins or identity
observations. Missing evidence is a finding, never an inferred approval.

Bind complete source read coverage, account and role, a single primary region,
explicit IAM actions, exact model destinations, team throttle integration and
scope, call/token/cost budgets, invocation authentication, resource ownership,
reuse ARNs and stop verification. Cross-region and global routing require
separate explicit approval. Preserve the existing Bedrock metadata preflight
and compare its complete destination set with the profile; a matching subset
does not suffice. Budgets in the profile are configuration ceilings; the shared
dispatch/workflow budget implementation owns their actual enforcement.

Source-derived service and quota catalogs are digest-pinned package data so
installed wheels retain the same checks. Unknown actions and quota keys require
review. API names are explicitly mapped to IAM actions, including Converse to
`bedrock:InvokeModel`. Endpoint and training quotas have different keys.

Static template checks inspect conditional branches conservatively and reject
unverified security properties. An AgentCore PUBLIC network setting alone is
not an unauthenticated invocation. Validate configured authentication and require
actual invocation-auth evidence separately. Preserve evidence bucket versions;
stopping computation and triggers must not delete manifest-referenced objects.

## Consequences and limits

The checked-in profile is intentionally pending. Structural validation,
source hashes and offline fixtures cannot approve a real account, model access,
model quality, synthetic financial content or a deployment. The CLI never
contacts AWS and reports `live_acceptance: not_performed` even when offline
checks pass. Trusted configuration must be reviewed and pinned by the operator;
this checker is not a signature verifier or an IAM policy simulator.

See [the deployment profile runbook](../competition-deployment-profile.md) for
the complete original-source coverage, rule-to-check matrix, entrypoint, reuse
plan, stop procedure and remaining evidence.
