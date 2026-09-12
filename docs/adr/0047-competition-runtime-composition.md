# ADR 0047: Explicit competition runtime composition and atomic spending limits

## Status

Accepted for implementation and offline verification. No competition account,
data, model invocation or deployment is approved by this decision.

## Context

Profile validation alone does not protect an SDK send. Document allocation,
catalog records, publications and result JSON can each leave through storage
adapters independently of model prompts. SDK event handlers and retries can
also change a prepared request after application validation. A local rate
limiter or a counter reset at worker startup cannot enforce team limits.

## Decision

Use `CompetitionAWSClients` in `adapters/aws/competition_clients.py`, composed
explicitly by the helpers in `adapters/aws/competition_runtime.py`. Require a
trusted immutable profile, independent profile and data-policy pins, observed
account/role, current authority, data admission, dispatcher and base client
factory. Validate before constructing any SDK client and again at every physical
send. No environment-selected profile, default session or caller approval flag
is introduced. The existing unconfigured application remains unavailable.

Accept fresh low-level botocore clients with the standard URLLib3 transport.
Production endpoints must match the approved service and region. Reject proxy
routes, disabled TLS verification, unknown services/operations, resource APIs,
presigners and mutable/streaming request bodies. Explicit pinned loopback origins
are the local test seam. Copy final method, URL, bytes and headers; reject
case-insensitive duplicate or unsupported headers. Admission reviews the exact
operation/method, URL, body and allowlisted non-authentication metadata sent.
Authentication, dates and session credentials are excluded from data records.
Bind the final method/path/query to the approved SDK operation and original
resource parameters, including exact S3 version and prefix. Additional
subresources and changed DynamoDB targets/tables fail before transport.

Install the shared Bedrock dispatch guard once. For other services wrap every
physical send, including each SDK retry. Resource authorization maps actual IAM
actions, including Converse to InvokeModel, HeadObject to GetObject or
GetObjectVersion, and transaction members to their underlying item actions.
Unknown operation schemas fail closed. Transfer, worker result and publication
helpers construct their S3/DynamoDB adapters from this same guarded factory.
Trusted execution and authority implementations must also use this factory for
all SDK clients; this boundary is not a sandbox for arbitrary application code.

Production dispatch is reconstructed with a guarded DynamoDB client and the
profile's exact central table. Reject a local SQLite dispatcher in production,
a mismatched table or replacement store. Every owner waits the full configured
interval after the preceding send exits. A lost release retains its owner.
SQLite is admitted only with explicit loopback test composition.

`DynamoDBCompetitionBudget` reserves each physical Bedrock request, including
retries, CountTokens and control-plane requests. Each operation/model requires a
trusted conservative request-size, input-token, output-token and cost bound,
pricing evidence and exact routing snapshot. The approved model context ceiling
must be known and the reserved input tokens must cover its entire context,
including multimodal input. Request bytes alone are not a token estimator.
Approved per-million input/output prices must upper-bound every admitted
modality, destination, cache mode and billing tier; the maximum request fee must
cover any additional charge. The cost reservation must cover those rates times
the reserved token ceilings plus that fee. Unknown pricing/context fails closed;
no smaller claimed input count overrides the pinned model ceiling. CountTokens
and control requests need explicit reviewed scope and prices, even when a
verified price is zero; no pricing or quota-counting exemption is inferred.
A bound evidence digest is a reference to independently reviewed operator
proof, not proof created by naming a digest. CountTokens cannot invent an input
bound or a price. Converse checks the final wire output limit. InvokeModel
remains unavailable until a verified provider-specific output-limit decoder
exists. Unknown bounds and routing fail closed.

The fixed window identity, timestamps, caps and profile digest are pinned in a
preexisting central ledger row. Operators may prepare `budget_seed_item(profile)`
for review and conditionally create a new authorized window with
`attribute_not_exists(pk)`; runtime construction never creates, resets, rolls
over or refunds a ledger. GetItem is consistent; UpdateItem compares all observed
caps, window fields and counters atomically. Independent processes and restarted
workers consume the same counters. Unknown send/update results remain consumed;
window expiry after a successful reservation still prevents the model send.
Coordinator traffic uses guarded metadata-only GetItem/UpdateItem and cannot
recurse into Bedrock.

Current job/source checks propagate across asynchronous SDK worker boundaries.
Nested authority access permits only scoped DynamoDB reads, S3 configuration
reads, HeadObject version resolution and explicitly versioned GetObject reads.
These requests retain profile, IAM, resource and exact-byte data checks. They
cannot perform writes or model calls. Recheck deadlines after slow authority
callbacks and immediately before sending; revocation or timeout sends no body.

## Validation and limits

Focused tests exercise actual SDK localhost method/path/query/header mutation,
versioned S3 reads and immutable writes, denied derived metadata, revocation,
slow callbacks and the real asynchronous snapshot/source path. Moto DynamoDB
conditional writes verify parallel independent processes, shared caps, restarts,
cap drift and uncertain reservation outcomes. Actual localhost Bedrock retries
traverse the guarded central coordinator and reserve again before each send.
These tests neither contact AWS nor establish live account acceptance.
Ledger token and cost counters are conservative reservations, not measured
provider usage or billed charges. Actual usage remains unknown here; no response
or failure refunds a reservation.

The pending profile remains unapproved. Central schema provisioning, trusted
approval records, fresh identity/routing/pricing evidence, all entrypoint
coverage and authorized deployment/stop acceptance remain operator gates.
See [ADR 0041](0041-competition-deployment-profile.md) and the
[deployment runbook](../competition-deployment-profile.md).
