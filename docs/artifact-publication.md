# Artifact publication boundary (attempt-scoped, fenced)

Design decisions are recorded in
[ADR 0016](adr/0016-fenced-artifact-publication.md); this note is the operator
reference for layout, schema, retention and evidence handling.

## Key layout

```
cases/{case_id}/runs/{run_id}/attempts/{attempt_id}/artifacts/{artifact_id}.pdf
```

`case_id` follows the service OpaqueID charset; run, attempt and artifact IDs
are UUIDs. Segments cannot contain separators or traversal, so a candidate can
never address another case, run or attempt. Manifest records live in the state
table under `artifact-manifest/{case_id}/runs/{run_id}`, one conditional
record per run.

## Manifest schema

`appraisal_review.domain.artifact_publication` defines the wire/state models:

- `PublishedArtifact`: artifact ID and attempt-scoped key, SHA-256 content
  digest, byte size, `application/pdf` content type, writer version, template
  ID/version and template/field-map digests, written field IDs, page count,
  pinned source versions, `placeholder_only=True` and the reopen-verification
  marker.
- `ManifestCandidate`: run reference (attempt required), result version,
  review status and one or more unique artifacts, all belonging to that exact
  attempt.
- `CommittedManifest`: the candidate plus its fencing token and canonical
  candidate digest; loading a record revalidates the digest, so a tampered
  stored manifest fails closed.

## Publication flow

1. The writer produces and reopen-verifies a local output (C1); the publisher
   stages it to the attempt key. Staging requires the producing writer and
   refuses one that reveals placeholders, so a locally backfilled final PDF
   cannot enter this pipeline; cloud artifacts carry opaque tokens only.
2. `publish` re-downloads every staged object and independently verifies
   digest, size and PDF content before the conditional commit. Object
   existence, worker logs and queue acknowledgements are never success.
3. Commit succeeds for a first record, a strictly newer fencing token, or an
   identical candidate under the same token (idempotent replay). A stale token
   is `stale_publication`; a different candidate under the current token is
   `manifest_conflict`. Crash between upload and commit is reconciled by
   re-running `publish`.
4. Consumers use the resolver: committed manifests only, principal
   authorization on every read, short-lived presigned downloads (clamped to
   900 seconds, keys taken from the manifest, URLs never logged) and
   digest-verified fetches with one bounded retry for transient reads.

## Retention policy

The result bucket keeps versioning with lifecycle hygiene: incomplete
multipart uploads abort after 7 days and noncurrent object versions expire
after 30 days. Current objects are retained: published artifacts remain
referenced by committed manifests, and unpublished attempt output is removed
only by an explicit reconciliation decision recorded in the run's evidence,
never by a blanket prefix rule. Reviewed source documents are never cleanup
targets.

## Sanitized artifact evidence format

Live-smoke evidence for this boundary is captured under ignored `artifacts/`
as JSON with exactly: the smoke ID, Region, stack IDs, the attempt-scoped key
*suffixes* (no bucket names), artifact digests and sizes, fencing tokens and
commit outcomes, and pass/fail conclusions. It must not contain account IDs,
bucket names, presigned or other URLs, principal identities, or any document
content. Presigned URLs are never written to logs or evidence; record only
that issuance succeeded and the bounded expiry used.
