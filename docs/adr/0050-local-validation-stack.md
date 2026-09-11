# ADR 0050: Explicit local Docker validation composition

## Status

Accepted for the local synthetic validation boundary. Production deployment,
business approval and privacy reliability require their separate acceptance.

## Context

The configured host launchers already exercise durable jobs, review transactions
and publication. The AWS Runtime image's default unconfigured worker returns 503;
it is not a frontend/API startup package. The browser rehearsal proxy also contains
fault-injection controls and must not become a normal operator-facing server.
Issue #48 needs a reproducible local package without conflating those boundaries.

## Decision

Provide one supported Python launcher and a separate Linux ARM64 Docker recipe.
Build the existing workbench with its lockfile, then copy only reviewed source,
locked Python requirements and compiled frontend assets into an allowlisted build
context. Never copy runtime configuration or a caller's private workspace into an
image. Reuse current dependency locks; OS remediation remains independently owned.

Start requires an explicit synthetic-demo mode. A fresh private volume receives
the existing fixed synthetic bootstrap and random local session at runtime. The
container refuses host-registered cases and has no arbitrary source selector or
public fixture endpoint. Synthetic authority stays scoped to authored cases and
must not be presented as production approval. Existing application contracts,
trust checks, confidence values, required responses and AWS defaults are unchanged.

Serve compiled static assets and the canonical API with Starlette/Uvicorn. Admit
only the configured numeric Host and Origin, bound request size, preserve caller
authentication and avoid test recovery controls. Readiness reports configured
local service/storage state, separately from liveness and business success.

Run as UID/GID 10001 with a private durable volume, read-only root filesystem,
restricted resources/capabilities, scoped Docker bridge and numeric loopback
port publication. The Docker daemon remains trusted. Retain exact image/resource
identity and verified local Unix socket in the private launcher record. Read only
context metadata before rejecting remote endpoints; pin the socket on every Docker
command and explicitly use its verified default builder. Do not change the global
context or inherit a separately selected remote builder. Restart reuses it; stop preserves state
and evidence. Never implement recovery by resetting receipts, deleting cases or
implicitly granting authority.

Keep privacy processing on the trusted host. A separate mode of the same static
server proxies canonical API routes to the pinned numeric host API; it advertises
only the companion's loopback address. User requests retain their own session.
The companion continues to verify exact Origin, independent session, source
handles, OCR identity, published/candidate receipts and output digest/expiry.
Originals, encrypted mappings, keys, OCR assets and revealed outputs never mount
into the Docker image or container. Host pairing requires its own evidence.

## Consequences

The core package can be built and exercised without cloud access or privileged
container execution. Recorded architecture, actual HTTP/browser output, PDF
identity, replay, unauthorized refusal and restart checks establish a bounded
local result. Missing configuration fails explicitly; no default demo or hidden
fixture login replaces it. Windows/x86, real onboarding, OCR reliability, image
security, hosted CI and live AWS acceptance remain separately tracked.

See the [launch and stop runbook](../local-validation-stack.md). Docker's
[volume lifecycle](https://docs.docker.com/engine/storage/volumes/) explains why
stopping a container does not remove its named state, and its
[bridge network documentation](https://docs.docker.com/engine/network/drivers/bridge/)
describes scoped networks and explicit host port binding. The
[default builder contract](https://docs.docker.com/build/builders/) binds that
builder to its Docker daemon; explicit selection avoids stored remote builders. Actual runtime checks
must confirm these settings; configuration alone is not acceptance evidence.
