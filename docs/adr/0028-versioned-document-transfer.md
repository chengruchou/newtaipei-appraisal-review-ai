# ADR 0028: Controlled sanitized documents and immutable run sources

Status: proposed implementation, pending independent review and live acceptance.

## Context

Issue #27 needs authorized document IDs for A2/C1/D1. The existing S3ObjectStore
accepts internal URIs and downloads the latest object; it is a legacy writer
transfer adapter, not an authenticated source registry. The A3 branch defines
privacy-v1 and an owned local export gate. D1 needs sources that cannot change
between job admission, retry and execution.

## Decision

Implement a separate controlled ingestion service, an alternative explicitly
allowed by #27. Keep service-v1 DocumentReference and legacy entry points unchanged.
Adopt A3's exact shared manifest/carrier/port definitions from its inspected head.
Require signed, exact, short-lived local human export attestation as well as
authenticated case/operation/purpose grants. A manifest alone is never authority.
Use operator-pinned Ed25519 public keys; signing keys remain local to the trusted
privacy gate. This does not establish production key provisioning or a real human
presenter, both of which still require integration acceptance.

Store every source and catalog record under newly allocated opaque immutable keys.
Preserve S3's concrete VersionId separately from the service document version.
Create run manifests once and bind the complete stored-record digest, including
the exact source VersionId, plus revision/reference/manifest/page information.
Use bounded reads and recheck actual bytes. Current principal grants are checked
on every access. No raw source or re-identification map crosses this boundary.

Use SQLite for durable local execution and a private versioned S3 adapter for the
cloud boundary. S3 creation uses If-None-Match; source GetObject calls specify
VersionId, owner and checksum mode. Conditional creation is atomic for a key and
does not make multi-object admission transactional. Preserve orphans for explicit
operator recovery rather than inventing a retention period or deleting evidence.

The dedicated CloudFormation template blocks public access, enforces TLS/SSE-S3,
bucket-owner ownership and conditional creation, scopes the runtime to one random
namespace, and denies runtime evidence deletion. Fixed tags are required and no
additional tag keys are accepted. PutObjectTagging is required for initial tagged
PutObject requests; any allowed subsequent tag write can only reassert the fixed
classification and namespace. Version-tag mutation and tag deletion are denied.
Retain/UpdateReplaceRetain protect evidence from stack lifecycle deletion. A
separate approved operator can perform exact synthetic test cleanup; no application
role receives an evidence deletion or retention automation feature.

## Consequences

- D1 must admit/persist run snapshots before making a job executable; leases and
  transaction/outbox behavior remain D1 responsibilities.
- A2 and C1 receive authorized exact sanitized bytes without durable storage URLs.
- A3 must compose the confirmation/sink pair inside its owned export gate. The
  signed assertion is only as trustworthy as that provisioned local process.
- Existing confidence/verification/receipt semantics are untouched. Changed
  material never inherits authority from a mismatched original receipt.
- There is no public upload endpoint until its authentication, input limits and
  privacy transport are composed. Tests and schema provide the service boundary.
- Cloud tests and lint establish offline behavior only. Production IAM, key
  governance, browser redaction, human presentation and live S3 require acceptance.

## Primary references

S3 conditional creation considers the current version, including delete markers;
runtime deletion is therefore prohibited as well as overwrite. See
[conditional writes](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html)
and [policy enforcement](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes-enforce.html).
Explicit source version reads require GetObjectVersion; see
[GetObject](https://docs.aws.amazon.com/AmazonS3/latest/API/API_GetObject.html).
Tagged uploads require the tagging permission; see
[PutObject](https://docs.aws.amazon.com/AmazonS3/latest/API/API_PutObject.html) and
[tag policy conditions](https://docs.aws.amazon.com/AmazonS3/latest/userguide/tagging-and-policies.html).
The signature adapter uses the library's standard
[Ed25519 API](https://cryptography.io/en/latest/hazmat/primitives/asymmetric/ed25519/).
