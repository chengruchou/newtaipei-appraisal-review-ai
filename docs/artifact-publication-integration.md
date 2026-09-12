# Publication integration contract

This branch restores the missing PR #35 package. It does not modify the job store
or the shared service schema. Runtime integration must use these concrete calls:

```python
attempt = PublicationAttempt(
    job_id=claimed.job_id,
    owner=claimed.owner,
    expected_result_version=claimed.expected_result_version,
)
staged = publisher.stage(path, run=run, artifact=artifact, writer=writer)
candidate = ManifestCandidate(
    run=run,
    result_version=claimed.expected_result_version + 1,
    review_status=WorkflowStatus.COMPLETED,
    artifacts=(staged,),
)
# The independently authorized verifier approves candidate.digest() only after
# checking its exact output, source identities, template/map/font and contexts.
committed = publisher.publish(
    candidate,
    fencing_token=claimed.fencing_token,
    principal=trusted_principal,
    attempt=attempt,
)
```

`DynamoDBManifestStore(client, table_name=..., job_table_name=..., clock=...)`
uses the low-level injected boto3 client. Both tables use `pk`/`sk`; they may be
the same physical table. `work_directory` optionally constrains resolver temporary output; the HTTP
`verified_bytes` path needs no temporary files. Configure file downloads beneath
the repository-owned output directory. S3 bucket versioning is required by the
AWS object adapter. Staging returns the exact immutable
`object_version`; callers must use the returned artifact when approving a
candidate. `font_hash` is the approved font byte digest. Missing source versions,
font digest, object version, current authority or independent approval blocks
publication. Legacy DTOs without those added optional fields remain parseable,
but cannot be newly published; existing serialized manifest digests require
explicit migration/reapproval because the signed content now includes these fields.
No deployed old adapter or automatic record migration is claimed.

The transaction checks existing runtime `JOB#{job_id}/META`, `RUN#{run_id}/META`
and `RUN#{run_id}/ATTEMPT#{attempt_id}` rows. It binds current run, revision,
complete source list, running status, cancellation, attempt, owner, unexpired
lease, fence and expected result version. No job schema or job row is written.
Only the manifest is written under `ARTIFACT#{case_id}#{run_id}/MANIFEST`.

The trusted authorization service must provision these separate rows in the
publication table; workers must have no write permission to them:

- `CASE#{case_id}/ACCESS#{actor_id}`: `active` boolean, `permissions` string list,
  `expires_at` integer UTC epoch seconds. Publication requires `publish_artifact`;
  result queries and every new download require `review`.
- `PUBLICATION#{case_id}#{run_id}/GRANT#{actor_id}`: `active` boolean,
  `manifest_digest` SHA-256 of the exact validated candidate, `expires_at` UTC
  epoch seconds. The grant must be created by the verifier/authorization service,
  never from a request body, model proposal or worker self-assertion.

An exact digest grant includes all sources, template/map/font identities,
writer version, contexts, fields, page count, placeholder-only claim and exact
output bytes/version. Source revocation and approval expiry must invalidate this
grant through the trusted source/authorization composition. This adapter checks
the job's pinned source list and the live grant transactionally; it does not
invent a second C2 document catalog or infer original-value absence from a PDF.
The authorizer must check the actual C2 current authorization and privacy output
before granting, and propagate source revocation to the grant. Direct unversioned
S3 URLs and a local revealing/backfill writer are never accepted.

Publication commits an artifact manifest, not the surrounding job success
transition. Runtime must still use `JobStore.finish` with its checked result
reference; the user-facing service must use authoritative job state for success.
The integration owner must preserve the atomic job result/attempt completion
boundary and reconcile a committed artifact manifest if job completion is
interrupted. Existing job records remain untouched by this adapter.

No AWS account/model call, live deployment, production identity acceptance or
formal font/template approval is claimed by offline SDK/emulator tests.

## Local SQLite composition

Import the AWS-free shared core from
`appraisal_review.adapters.local.artifact_publication`. Both classes accept
`objects=...` and `manifests=...` using the protocols below. No AWS bucket or
presigner is needed for authenticated local HTTP downloads.

```python
publisher = AttemptArtifactPublisher(objects=immutable_objects, manifests=sqlite_authority)
resolver = CommittedResultResolver(objects=immutable_objects, manifests=sqlite_authority)
artifact, pdf_bytes = resolver.verified_bytes(principal, case_id, run_id, artifact_id)
```

`ArtifactObjectStore.create(key: str, data: bytes) -> str` creates an immutable
object and returns a concrete opaque version. Same key/same bytes retries return
the same version; changed bytes must never replace an existing object.
`ArtifactObjectStore.read(artifact: PublishedArtifact) -> bytes` reads that exact
version. The core independently verifies returned bytes and PDF structure.

`ManifestRepository.authorize(principal, case_id, permission) -> int` checks
current durable authorization and returns UTC expiry. `read(case_id, run_id)`
returns a validated committed manifest or `None`. `commit(candidate, *,
fencing_token, principal, attempt)` must perform its current job, lease, fence,
result-version, publication permission and exact-artifact approval checks in the
same SQLite transaction as the manifest. Its `clock` callable supplies UTC epoch
seconds for download-expiry bounding. No default permissive store is provided.

`PublicationAttempt(job_id, owner, expected_result_version)` maps directly from
the runtime's current claim. `verified_bytes` checks authorization before and
after reading and returns `(PublishedArtifact, bytes)`. The HTTP route derives
the principal from its authenticated session and invokes this method on every
request, including retries. Trusted synthetic configuration may approve exact
synthetic assets; a request or model cannot promote itself to PUBLISH or create
human approval. Production DynamoDB transaction conditions remain unchanged.
