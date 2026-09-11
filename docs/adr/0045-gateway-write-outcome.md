# ADR 0045: Preserve unknown gateway write outcomes

Status: Accepted

## Context

A reviewer response may commit at the upstream service before a gateway replaces
its response with HTTP 502/504, HTML, or an interrupted body. Treating an
unvalidated error as a service rejection discarded the reviewed command and its
idempotency key, unlocked inputs and prevented recovery of the original receipt.
The reproduced defect is lost receipt recovery; it does not establish duplicate
writes or an authorization bypass in the service.

## Decision

For a reviewer write, classify a non-success response as a definitive service
rejection only when its body validates against the canonical OpenAPI
`ServiceProblem` schema and its machine code matches the canonical HTTP status:

| HTTP status | Service code           |
| ----------- | ---------------------- |
| 403         | unauthorized           |
| 404         | not_found              |
| 409         | version_conflict       |
| 422         | invalid_request        |
| 500         | execution_failed       |
| 503         | capability_unavailable |

A noncanonical body, unknown status/code pair, HTML gateway page or interrupted
response leaves the outcome unknown. This includes 502/504 even if a proxy
supplies a code-shaped JSON body. Successful responses must still validate as
receipts; existing deadlines include headers, body reading and validation.

The form keeps the original detached, deeply frozen command and idempotency key.
It prevents editing and new submission while that command's outcome is unknown.
An explicit retry resends the identical command, allowing the service to return
its original committed receipt or accept the command once. It must not rebuild a
correction from current inputs, mint a replacement key, invent a receipt or
interpret a missing response as proof of rejection.

Validated canonical validation and authorization errors retain their existing
refusal behavior. A validated version conflict requires reload. Read-only errors
also require a canonical envelope; unknown errors retain their precise transport
wording. HTTP status alone never invents a service decision. Domain calculations, confidence, permissions,
response DTOs and backend idempotency semantics do not change.

## Consequences and verification

The unknown-outcome state intentionally holds the current form until the caller
can recover a trustworthy outcome. This change does not persist private commands
across page reloads or promise recovery after browser storage is cleared.

`web/tests/gateway-regressions.test.tsx` reproduces noncanonical errors and
checks frozen corrections/keys while retaining canonical rejections. Eleven
assertions fail on the pre-repair source head; six canonical cases already pass.

`web/e2e-real/gateway-recovery.spec.ts` exercises the actual component HTTP app,
HumanTaskService and stores through a loopback fault proxy. The proxy consumes a
successful upstream response before generating a gateway failure. A correction
commits r2 before the first 502; retries survive 504 and body loss, then recover
the exact original receipt. Each request body is identical, the task is answered,
and revision history remains exactly r1/r2. The fixture is synthetic and its
principal resolver is not deployment authentication acceptance.

See [reproduction and dependency validation](../../web/docs/gateway-recovery.md)
for commands and the scope of the evidence. The integration branch must preserve
its canonical contracts and rerun its own service/browser acceptance after merge.
