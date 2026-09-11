# ADR 0016: Authoritative artifact publication and immutable downloads

- Status: Proposed for human review
- Date: 2026-09-11
- Scope: PR #35 publication adapter and provider-neutral integration ports

## Context

The original PR head omitted its adapter because an unanchored `artifacts/`
ignore rule matched the source package. Its proposed manifest-only fencing rule
also cannot establish a worker's current authority: an expired worker may have
no previous manifest to compete with. Local service integration needs the same
validation with SQLite authority and object storage, without an AWS dependency.

## Decision

Restore the source package and anchor the ignore rule at the repository root.
Keep shared byte/PDF/manifest validation in a local adapter with injected
`ArtifactObjectStore` and `ManifestRepository` ports. AWS is one composition of
those ports. The immutable object version and approved font digest join the
candidate content; legacy DTOs remain parseable but cannot publish without those
fields, and any old digest requires explicit migration/reapproval.

Require a current job attempt, owner, lease, fencing token, expected result
version and publication permission. The DynamoDB adapter checks the actual
job-store-v1 job/run/attempt rows, current access and independently approved exact
manifest digest in the same transaction as the manifest put. It never replaces
these conditions with an in-process boolean or a previous-manifest comparison.
Its source list and revision are bound in the final transaction. Worker IAM must
not allow writing authorization/approval rows. Approval follows actual source,
asset and privacy verification; DTO validity creates no authority.

Object staging uses immutable versions. The publisher reads one local byte
snapshot, creates the object conditionally and verifies the exact returned
version; commit verifies it again. The core rejects revealing writers and
requires exact source/font evidence plus independently issued approval. PDF
metadata alone does not prove de-identification or authorized font selection.

The resolver reauthorizes each request and after object I/O, reopens and verifies
the pinned PDF, and issues no more than 900 seconds of download authority, bounded
further by current access expiry. The local HTTP route can return verified bytes
directly without a public signed URL. Existing local files are never overwritten.

## Consequences and integration ownership

- A stale or superseded worker cannot commit, including before any manifest exists.
- Same-token changed content conflicts; exact retries recheck current authority.
- An uncertain SDK response is not converted into success. New adapter instances
  reconcile from stored state; expired attempts cannot bypass fencing for recovery.
- This adapter writes only manifests. The shared runtime still owns atomic job
  completion/result/attempt transitions and reconciliation across that boundary.
  A manifest alone is never job-success evidence.
- Local SQLite implementations must enforce these conditions inside their durable
  transaction. The core does not claim that a process lock provides persistence.
- C2 source revocation must reach current publication and download authority through
  the shared composition. The publication adapter does not create a parallel source
  catalog or infer current source access from old hashes.
- Version-pinned artifacts require retention of referenced versions; generic
  noncurrent-version expiration cannot establish safe artifact retention.
  The result bucket therefore disables automatic expiration of completed object
  versions. Only incomplete multipart uploads are aborted after seven days.
  Unreferenced-version cleanup requires a trusted reconciliation decision and is
  not implemented by this lifecycle rule; storage can grow until that exists.
- No live account/model calls, deployment or formal business approval are covered
  by offline Moto/Stubber and real local PDF tests.
