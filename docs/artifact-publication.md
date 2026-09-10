# Artifact publication boundary

The shared core in `adapters/local/artifact_publication.py` accepts the
`ArtifactObjectStore` and `ManifestRepository` ports. It imports no AWS SDK.
The AWS module composes that same core with immutable S3 objects and the actual
DynamoDB transaction adapter. See [integration signatures and ownership](artifact-publication-integration.md)
and [ADR 0029](adr/0029-fenced-artifact-publication.md).

## Verified objects and manifests

Objects use `cases/{case}/runs/{run}/attempts/{attempt}/artifacts/{artifact}.pdf`.
Every identifier and candidate is revalidated at the publication boundary.
Staging reads the local bytes once, checks their digest/size, reopens the PDF,
checks page count and writer metadata, creates an immutable object version,
then reads and verifies that exact version. A writer must explicitly declare
`reveals_placeholders is False`; missing values, strings and integer flags fail.
The grant described below, not a worker-supplied flag or PDF metadata, supplies
independent approval of the exact placeholder-only artifact and assets.

A candidate binds run, revision, attempt, result version, completed review status,
all source versions/hashes, output digest/size/version, template ID/version/hash,
field-map hash, approved font hash, contexts, written fields and page count.
Legacy DTOs without font/object-version fields decode but fail new publication.
Existing candidate digests need explicit migration/reapproval; they are never
silently accepted as approvals of the extended content.

The publisher re-reads the exact immutable versions before commit. Sources must
match the authoritative run's complete pinned document set. The authorization
service must have approved the exact candidate digest after verifying actual
source authorization, asset identity and privacy output. Workers cannot issue
that approval to themselves. The local integration's synthetic asset approval
is explicit trusted configuration, not production human approval.

## Authoritative transaction

`DynamoDBManifestStore.commit` performs a real `TransactWriteItems`: condition
checks on current job, run, attempt, access permission and exact digest approval,
plus the manifest put. The job/run conditions bind current run/revision/source
list, cancellation, status, owner, attempt, unexpired lease, fence and expected
result version. Clock sampling happens after authority reads. No previous
manifest is needed to reject a stale worker or one superseded before a newer
worker publishes. The transaction does not edit the surrounding job rows.

An identical candidate under the same current token is an idempotent retry.
A changed candidate under the same token is `manifest_conflict`; lost authority
is `stale_publication`. Lost acknowledgments remain uncertain errors; a retry
rechecks current authority and returns the same committed manifest. An expired
worker cannot use a retry to bypass its lease, even if its earlier commit landed.
Only authoritative job completion establishes job success; a staged object or
artifact manifest alone never substitutes for the runtime's job result transition.

## Authorized downloads

`verified_bytes(principal, case_id, run_id, artifact_id)` is the authenticated
HTTP integration entry point. It resolves only manifest-owned artifact IDs,
checks current access before and after reads, verifies the exact immutable
version's bytes and PDF, and returns `(artifact, bytes)`. Principals come from the
server's authenticated context. Local adapters must provide current access and
source-revocation checks through their authority implementation.

The S3 composition signs only a manifest-owned key and VersionId, after verifying
bytes and reauthorizing. Expiry is positive and bounded by both 900 seconds and
the remaining current access grant. Already issued bearer URLs remain valid
until expiry unless storage-side access is revoked; no immediate revocation of
an issued URL is claimed. Downloads are not logged. Verified local file output
uses no-overwrite publication and preserves existing files and their aliases.

## Evidence and limits

Offline regressions execute Moto's DynamoDB/S3 request paths and a botocore
Stubber service failure. They cover stale-first publication, superseded attempts,
transaction interleavings, permission/approval expiry, exact replay and conflict,
crashes before/after commit acknowledgment, immutable-version retries, malformed
PDF/manifest rejection, download revocation, and a real CJK placeholder PDF
write/upload/download/reopen. A separate ignored probe uses the sibling's actual
`DynamoDBJobStore.create_job` and `claim`, proving compatibility with its real rows.

These are local/emulator observations, not live AWS, production IAM, formal
font/template approval or end-to-end service acceptance. SQLite authority and C2
source-revocation integration belong to the shared service composition. Keep
output and evidence under ignored repository `artifacts/`. Preserve noncurrent
S3 versions for the full lifetime of manifests that pin them; blanket expiration
of all noncurrent versions is incompatible with version-pinned downloads.
