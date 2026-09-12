# ADR 0038: Owned export snapshots and offline regression evidence

Status: Accepted for Phase 7 local core behavior. Linux isolation, real OCR,
production export confirmation and cloud integration acceptance remain pending.

## Context

An admission guard alone cannot stop a consumer from uploading a different file
after confirmation. Reviewer text, filenames, metadata, exceptions and telemetry
can also reintroduce original values. PDF substring scans miss compressed content
and scanned images. Python mocks alone cannot prove native executables are offline.

## Decision

`LocalPrivacyExportGate` is the only privacy export operation. It builds through
the trusted builder and verifier, prepares optional reviewer text locally, and
passes one immutable payload to trusted human confirmation. Immediately before
the sink call it rechecks the payload, source approval and verifier's live owned
entry. The sink receives the same payload instance, without reopening a path.
No serialized carrier, supplied digest or source-only approval grants export.

The payload contains only PDF bytes, exact public manifest JSON, a fixed filename
and optional reviewed text. Free-text preparation replaces known redacted values;
unknown information still requires human review. Local drafts always carry
`needs_review`. Filenames and metadata have no caller-configurable export slots.
Exceptions expose the existing fixed privacy problem contract. No automatic retry,
business completion or final-local-PDF export is introduced.

The human confirmation and sink adapters are trusted composition, not request
arguments. Phase 7 tests use local doubles only. A production confirmation adapter
must present the exact PDF and all text; a future network sink requires separate
authorization and #31 integration acceptance. Revocation is checked at the final
local handoff; this does not promise distributed atomic revocation or rollback of
a partially completed future remote operation.

Python workers install socket/DNS audit denial before target module imports.
Anonymous AF_UNIX socketpairs support Linux asyncio while explicit connections
remain denied. Windows asyncio's TCP loopback implementation stays blocked.
The offline runner can additionally isolate the entire test process tree using
a Linux network namespace, checking a native libc connection failure and child
namespace inheritance. Python hooks cannot contain native libraries, external
executables, inherited descriptors or interpreter startup code. The namespace
test is separate evidence, and does not establish every production egress path.

Regression reports contain only fixed canary IDs, hit counts, surface enums,
scope and outcomes. Omitted/uninspected surfaces prevent a passing report.
Positive controls cover compressed PDF streams, extracted text, rendered scanned
image templates and message channels. Image templates are synthetic visual
controls, not real OCR accuracy evidence.

## Consequences

Public privacy-v1 and service-v1 stay unchanged. A new report schema describes
test evidence, not export authority. Local SDK consumers #24/#25 can share text
preparation and the gate; existing upload paths are not silently redirected or
approved for sensitive cases. See the [runbook](../local-privacy-export.md) for
current evidence, platform limits and integration handoffs.

## Exact mapping follow-up

The gate now requires the local mapping service and key reference at composition.
It encrypts/persists and authenticates a readback of the same build's complete
command and manifest before confirmation, then reads back again before final
admission and transfer. Missing mapping dependencies cannot enable export.
The local handle is retained for reconciliation when confirmation or the sink
fails; no automatic transfer retry or map deletion is introduced. Public schemas
and the manifest return type are unchanged. See the scoped
[coordination contract](../pr37-exact-export-coordination.md) for the constructor
migration, distinct original/sanitized identities and platform evidence.
