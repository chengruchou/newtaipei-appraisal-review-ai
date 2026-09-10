# ADR 0016: Attempt-scoped artifacts, fenced manifests and authorized downloads

- Status: Proposed for human review
- Date: 2026-09-10
- Delivery: output-boundary follow-up to #5/#9; consumes the JobRepository
  fencing-token contract and ADR 0015 placeholder output

## Context

The target architecture requires that a worker's output become the run result
only through a fenced, conditional manifest commit: SQS acknowledgement, worker
success logs and object existence must never be treated as job success, stale
attempts must not overwrite newer results, and consumers must not trust
worker-reported storage paths. Cloud artifacts additionally carry only opaque
placeholder tokens; a locally re-identified copy must never be publishable.

## Decision

Artifacts live under attempt-scoped keys,
`cases/{case}/runs/{run}/attempts/{attempt}/artifacts/{artifact}.pdf`, with
segment validation that makes cross-case or cross-attempt writes unexpressable.
`ManifestCandidate`/`CommittedManifest` (domain models) record, per artifact,
the byte digest, size, content type, writer version, template identity/version
and digests, field IDs, page count, pinned source versions and reopen
verification status; the committed manifest binds a canonical candidate digest
and the fencing token.

`AttemptArtifactPublisher` uploads only locally validated writer output to the
run's own attempt keys (refusing writers that reveal placeholders), then at
publish time independently re-downloads and re-verifies every object against
the candidate before committing. `DynamoDBManifestStore` commits with one
conditional write per run record: absent, lower token, or identical
token+candidate digest may write; anything else fails as stale publication or
manifest conflict, so re-running publish after a crash between upload and
commit is idempotent and expired attempts stay unpublished.
`CommittedResultResolver` reads committed manifests only, requires an
authorized principal per read, issues short-lived (≤900s) presigned downloads
whose bucket/key come from the manifest — never from callers — without logging
the URL, and verifies fetched bytes against the manifest with one bounded
retry for transient reads.

The result bucket gains lifecycle hygiene (abort incomplete multipart uploads,
expire noncurrent versions); current unpublished attempt output is retained
for explicit reconciliation decisions rather than blanket deletion.

## Consequences

- Two competing attempts cannot both publish; the newer fencing token wins and
  replay of the loser is a stable, typed failure.
- Tampering with digest, size, content type or the stored manifest record is
  detected at publish or read time.
- Only manifest-referenced artifacts are exposed; publication of revealed
  placeholder values is structurally refused at staging, and cloud manifests
  declare placeholder-only content.
- The baseline Cases table hosts one conditional record per run under a
  namespaced record ID; D's full job/task/outbox schema can migrate these
  records without changing the store contract.
- Durable leases, dispatch, outbox recovery and the surrounding job pipeline
  remain #9 work; this delivery is the output boundary only, with injected
  clients and no live AWS calls.

## Rejected alternatives

- Trusting worker-computed digests at publish time, because the publisher must
  verify what is actually stored, not what was claimed.
- Last-writer-wins manifest records, because a delayed stale attempt would
  silently replace a newer result.
- Long-lived or logged download URLs, because presigned URLs are bearer
  authority.
- Blanket lifecycle deletion of attempt prefixes, because published artifacts
  live under attempt keys and must outlive the attempt.
