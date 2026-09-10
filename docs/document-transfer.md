# Authorized document transfer (C2 / Issue #27)

## Service contract

`DocumentTransferService` is a synchronous controlled ingestion and resolution
port. It is the controlled-service alternative to an upload-init/complete API.
It has no arbitrary file/URL/bucket/key parameter and no public HTTP route. An
authenticated gateway must supply a trusted Principal; a JSON DTO is never a
credential. Async hosts must run its blocking storage I/O outside the event loop.
Legacy `/v1/reviews`, `/v1/validate`, invocation and service-v1 remain unchanged.

| Operation | Input | Result and authorization |
| --- | --- | --- |
| ingest | Exact immutable PDF bytes and signed PrivacyAttestation | Service-issued DocumentMetadata; case membership, REVIEW permission, explicit ingest/purpose grant and trusted human export key |
| read | Exact DocumentReference | AuthorizedDocumentBytes; current case/read/purpose grant, registry version/hash match, bounded pinned storage read |
| create_snapshot | Server-admitted RunReference and exact MaterialRevision | Create-once RunSourceSnapshot containing every revision document; snapshot/purpose grant and actual source availability/integrity |
| read_snapshot | RunReference and exact DocumentReference | Only a document in the stored run/revision; current read/purpose grant and exact stored-record commitment |

Use `read` before a run. Workers use `read_snapshot`; reading the latest document
by key is not a substitute. D1 must pin sources before publishing a runnable job
or outbox event and persist the returned manifest with admission. A failed or
orphaned admission does not authorize an attempt. C2 does not implement D1's
transaction, lease, job authentication or recovery machinery.

Case, actor, revision and document-version IDs at this new boundary are random UUID v4
identities issued by trusted identity/document adapters. Legacy OpaqueID remains
unchanged for other service-v1 users. DocumentReference.version is a service-issued
UUID, distinct from S3's VersionId. The latter stays in the internal immutable
catalog. The exported [schema](../schemas/document-v1.json) is reproducible with
`python scripts/export_document_contracts.py`.

## Privacy and authority

The public manifest has exact privacy-v1 fields: random case/document/entity IDs,
sanitized digest and size, fixed sanitized filename/media type, processor/policy
versions, pages and placeholder locations. It has no original filename, source
digest, raw values, map, paths or free-form metadata. Unknown fields are rejected.
PDF admission checks size, SHA-256, media type and header/end marker. These checks
establish a bounded PDF carrier; they do not prove complete PDF sanitization or
replace A3's structural/visual verification.

Manifest validity alone cannot grant upload authority. ExportClaims binds the
entire manifest, purpose, principal, export ID, optional exact predecessor,
confirmation time and expiry. Ed25519 signs the canonical sorted-key compact JSON
in UTF-8; arrays retain order. The verifier pins public keys to specific actors
and cases and rejects unknown keys, modified claims, future/expired confirmation,
validity windows over ten minutes and non-human export actors. Private signing
keys remain local and are never received by C2. Key provisioning, rotation,
revocation and the production presenter require controlled operator integration;
there is no default trusted key or automatic approval.

`ConfirmedDocumentExport` implements both A3 confirmation and sink ports. Compose
the same instance into `LocalPrivacyExportGate`, whose admission checks must still
verify the owned sanitized bundle and live local authority before calling the
sink. The presenter must show the exact final bytes and obtain real human consent.
The bridge signs only after that call, consumes the same payload instance once,
and rechecks content binding. A generic boolean from a request or directly calling
the bridge with arbitrary bytes is not a production privacy workflow. This branch
does not add a separate browser upload path that bypasses A3.

C2 accepts PDF-only exports; the bridge rejects optional reviewer text. That field
must be excluded when assembling this sink. Text/model transport belongs to A2's
separately controlled path. No original PDF or re-identification map is an input to
any C2 service method, S3 metadata or public schema.

The following shared definitions are adopted byte-for-byte from A3's published
head `6131957d8859ddf47c71cab8eb4130614efeeb77` to avoid a second privacy contract:

- `domain/privacy_models.py`
- `domain/privacy_export.py`
- `ports/privacy_export.py`

This is selective shared-contract adoption, not a claim that the A3 application is
merged. Local-only models in that module are never accepted as public manifests.
Coordinate later merges against the pinned files and preserve subsequent A3 work.

## Persistence, updates and snapshots

Local storage uses a private SQLite database with atomic insert-only keys. S3 uses
one configured bucket, account and random namespace. Keys contain only fixed kind
names and UUIDs. Every object has sanitized classification, hash/size, content type,
creation time, opaque case/uploader IDs and purpose metadata; document/version IDs
are in the service-derived key and object metadata. The immutable document catalog
also stores the exact attestation and returned source VersionId.

An export reservation allocates service document/version IDs once. Replaying the
same valid signed export returns the same metadata. A different attestation under
the same export ID conflicts. Each new revision gets a new version and may name
an existing exact predecessor; previous source bytes and records are unchanged.
There is deliberately no mutable latest-document pointer or overwrite operation.
Version branches from an explicit predecessor are allowed; selecting material for
a new run remains the revision service's responsibility.

Run manifests contain exact references, privacy-manifest digests, page counts and
commitments to the complete stored document records, including storage VersionId.
They are keyed by case/run and created once. Replays with identical revision,
document set and admitting actor are idempotent; changed bindings conflict. Every
snapshot read checks the saved revision, reference membership, stored-record hash,
exact storage version and actual PDF hash/size. Later updates, even a replacement
latest S3 version with identical bytes, cannot silently change the pinned binding.

Export expiry only limits new admission; it does not expire previously admitted
evidence. Authorization is checked again on every read, so removing a document
grant blocks old runs too. This is distinct from rule/material approval: original
receipts remain valid only for their unchanged exact material; new revisions must
satisfy their own confirmation and approval binding. Confidence values are never
raised by document transfer.

Multiple objects cannot be atomically committed by S3. The service exposes a
usable document only after its source and catalog are present; a snapshot only
after all sources pass admission. Failures may leave allocated IDs or sanitized
orphan objects. Replaying the same still-valid export can complete partial writes.
No automatic cleanup, lifecycle expiry or evidence deletion is implemented.

## Minimal audit and safe errors

Operations with valid boundary types and actor/case IDs record opaque identities, operation, timestamp and
outcome. Reads also identify the document or run. `ImmutableDocumentAudit` supports
append-only object audit; local SQLite also supports audit. Failed audit prevents
returning source bytes. Audit is required domain evidence, not a debug logger.
Public faults use finite DocumentProblem JSON, never raw SDK/parser exceptions,
paths, storage URLs or request content. Reject malformed external JSON in a future
gateway without serializing Pydantic's raw input values.

| Code | Meaning / consumer action |
| --- | --- |
| document_invalid_request | Unsupported identity or carrier; fix the request contract |
| document_unauthorized | Missing case/operation/purpose grant or document absent from this run |
| document_privacy_attestation_invalid | Missing trusted export authority, changed/expired confirmation; repeat the legitimate local export flow |
| document_too_large | PDF or bounded metadata/read exceeds configured limits |
| document_not_pdf | Required PDF markers are absent |
| document_not_found | Stored key/version is absent when the backend can distinguish absence |
| document_version_conflict | Export reservation or run binding was already used differently |
| document_integrity_failure | Byte hash/size/version or immutable record binding differs |
| document_capability_unavailable | Storage, audit or safety preflight failed; do not expose backend details |

S3 without ListBucket can return AccessDenied for absent keys. That ambiguous
backend response is treated as unavailable, not used to disclose object existence.
No new transport status mapping is imposed on existing HTTP/invocation routes.

## A2, C1 and D1 integration

A2 can build its SanitizedSourceReference from the saved DocumentReference,
privacy-manifest digest and page count. Its resolver must call `read_snapshot`,
check the requested page/purpose and return the same bytes to its real parser.
The unmerged A2 contract is inspected, not copied or wired as a production runtime.
C1 reads its admitted template/reference from the same run and writes a separate
sanitized output. C3 owns publication fencing and download authorization. Private
rehydration remains local to A3. D1 owns trusted run creation and persists this
manifest alongside its transaction/outbox admission; C2 is not a job scheduler.

## Evidence boundaries

Shared local/S3-double tests exercise admission, authorization, confirmation,
replay, concurrent conflicts, revision binding, version/hash substitution, audit
failure and synthetic canary capture across carrier, returned JSON, SDK arguments,
stored bytes, metadata, tags and logs. Real SDK Stubber tests validate request
shapes. They make no AWS calls and do not prove complete redaction of unknown
personal information. Full A3 production composition, browser network isolation,
live IAM denial checks and real S3 acceptance remain separate work. See the
[runbook](../infra/documents/README.md) for the opt-in probe and exact cleanup scope.
