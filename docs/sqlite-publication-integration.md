# SQLite publication integration

This adapter publishes PDFs already produced and verified by the actual local
Controller. It is configured for the fixed synthetic integration fixture. It
creates no material approvals, fact confirmations, human receipts, or increased
confidence. It does not invoke a model or cloud service.

## Composition API

`SQLiteArtifactObjectStore(review_store, *, max_bytes=67108864)` stores immutable
versioned PDF BLOBs in the existing private SQLite database. It implements the
AWS-free publication core's object port. A key cannot be overwritten with different
bytes. Reads verify the exact key, object version, digest and byte count.

`SQLiteManifestRepository(review_store, *, source_authorizer,
trusted_synthetic_approval=False, approval_ttl_seconds=900, clock=time.time)` uses
the same database and transaction domain as `SQLiteReviewStore`. The coordinated
store seam is `_connect()` and `_decode(payload)`; the adapter preserves the store's
private file and identity checks. It owns only `publication_*` tables.

The synchronous `source_authorizer(principal, run, source_versions) -> None` must
read each exact source through `DocumentTransferService.read_snapshot` using the
current principal. It runs outside a database transaction and raises on denial.
An exact source/state pin and durable revocation epoch are checked again inside
the manifest transaction. Downloads pass the current authenticated principal to
this callback; stored publishing permissions are never substituted.

`approve_exact(candidate, principal) -> str` grants an exact candidate digest only
when synthetic approval was explicitly enabled at construction, the principal
has PUBLISH permission, and the current job belongs to that principal. It also
requires the current live run, attempt, owner, fence, lease and result version,
with no cancellation or open human tasks. Request and model adapters must not
receive this capability. Reapproving conflicting content at the same committed
fence fails without replacing the original grant.

`revoke(case_id)` durably invalidates access and candidate grants and advances the
case's revocation epoch. Invoke it before changing the external source grant or
removing a session. Revocation is sticky in this database: there is deliberately
no automatic regrant/reset operation that could race the external change. A
future reinstatement operation needs an explicit coordinated transaction design.

```python
objects = SQLiteArtifactObjectStore(review_store)
repo = SQLiteManifestRepository(
    review_store,
    source_authorizer=read_exact_sources,
    trusted_synthetic_approval=True,
)
publisher = AttemptArtifactPublisher(objects=objects, manifests=repo)
resolver = CommittedResultResolver(objects=objects, manifests=repo)
```

The service returns bytes from
`resolver.verified_bytes(principal, case_id, run_id, artifact_id)`, which returns
`(PublishedArtifact, bytes)`. It reauthorizes before and after reading and verifying
the committed object. Grant expiry is enforced; download lifetimes cannot exceed
900 seconds or the remaining grant duration. The public API must not return local
paths, object keys, or caller-selected file destinations.

## Controller evidence and projection

`PublicationEvidenceWriter(delegate, configuration, *, on_evidence=None)` wraps the
actual `LocalPDFWriter`. Set its `run_id` to the job's current run before giving it
to the Controller. Its multi-context capability is true only when the delegate's
capability is the literal `True`; its reveal capability fails closed.

The optional synchronous `on_evidence(LocalArtifactEvidence)` callback executes
after the underlying verified write and before returning to the Controller.
Persist this evidence, the configured request, and writer configuration in a
trusted per-run store. Callback failure prevents a completed Controller result;
it can leave an uncommitted local output file that is never exposed as a result.
A restart may restore `run_id` and `evidence` on a freshly composed wrapper only
from that trusted store. The projection verifies file identity and bytes again.
Do not reconstruct evidence from request data or merely from finding a PDF.

`PublicationInputs` is a frozen constructor-side record with these fields:

- `configuration: LocalWriterConfiguration`
- `request: AgentReviewRequest`
- `writer: ConfinedWriter` (normally the actual `PublicationEvidenceWriter`)
- `template_version: str`, selected by trusted configuration
- `fixed_synthetic_assets: bool = False`
- `expected_placeholder_tokens: tuple[str, ...] = ()`
- `forbidden_originals: tuple[str, ...] = ()`

The fixture's source PDFs must contain `SYNTHETIC SOURCE`; its template and output
must contain `SYNTHETIC OUTPUT`. These markers alone are not authority: current
registry identities and hashes, the exact template/map/font hashes, actual
writer evidence, and independent synthetic service authorization must all agree.
The expected token inventory must exactly match mapped placeholder tokens and
those tokens must remain present in the output. Configured forbidden originals
must be absent. A writer with revealed placeholder values is always rejected.
The empty token inventory in the current entirely synthetic fixture is explicit;
this is not a general redaction certification for arbitrary documents. An
original-bearing production composition must use its own independently verified
privacy boundary and cannot enable this fixture path from a request.

```python
projection = IntegratedResultProjection(
    publisher=publisher,
    manifests=repo,
    catalog=catalog,
    principal_getter=directory.read,  # async (principal_id, case_id) -> Principal
    inputs_getter=read_trusted_publication_inputs,  # sync (record, attempt) -> PublicationInputs
    synthetic_authorizer=authorize_exact_synthetic_candidate,  # sync (candidate, principal) -> None
)
# Pass projection as IntegratedWorkflowExecution(project_result=projection).
```

`await projection(record, attempt, review)` returns canonical `ServiceResult`.
Blocked/non-written Controller results retain their status, findings and public
verification and return no artifacts. Inconsistent completed/written claims fail
closed. A written result must have `verification.can_complete`, a verified
case review with complete coverage, matching per-call evidence and PDF result,
and all comparison contexts bound to the current registry and field map. The
projection reruns read-only PDF preflight and reopen verification for every
comparison, including output field effects and protected template pages. It never
calls the writer or modifies the source/template/output PDFs.

After validation it stages immutable bytes, calls the independent constructor-only
synthetic authorizer, grants the exact candidate and conditionally commits the
manifest. The callback does not create real business approvals. Deterministic
attempt-scoped artifact identity makes a crash after manifest commit recoverable
as an identical replay. Altered bytes cannot replace that attempt's object.

The result contains `FencedArtifactManifest` with schema version
`artifact-manifest-v2`, complete `contexts`, a primary `context`, all field IDs,
page count, output/template/map/font hashes, writer version and committed manifest
digest. The legacy `ArtifactManifest` keeps its single-context, local-only shape.
The runtime subsequently persists the canonical result and finishes the job with
a `ResultReference` containing the published artifact IDs. After job completion,
downloads require that matching succeeded result version, fence and artifact set.
The projection itself does not mark the job complete or claim result persistence.

## Validation evidence

Focused tests use real SQLite transactions and multiple processes, including
process termination immediately before and after manifest commit. They cover
same-token replay/conflict, cancellation, lease takeover, source changes,
revocation races, permission changes, grant expiry and download reauthorization.
Integration tests use the actual C2 document service, parser, Controller, local
CJK writer, multi-context PDF verifier and SQLite stores. They test immutable
bytes, durable evidence round-trip, blocked review, callback failure, replay and
asset/result tampering. These are local checks, not AWS account, model or browser
acceptance. Run logs are kept under ignored `artifacts/sqlite-publication` and
`artifacts/integrated-publication`.
