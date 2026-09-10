# C2 private document storage

This directory prepares the Issue #27 storage boundary. It does not deploy a
gateway, Runtime, jobs, keys or authentication. No live cloud result is implied.
The [service contract](../../docs/document-transfer.md) describes integration.

## Offline checks

```bash
python -m pip install -e '.[dev,aws,privacy]'
python -m pip install -r cloud_tests/requirements-dev.txt
ruff check .
ruff format --check .
mypy src
pytest --cov=appraisal_review --cov-config=pyproject.toml
python -m pytest cloud_tests
python scripts/generate_goldens.py
cfn-lint infra/documents/stack.json cloud_tests/image-stack.json cloud_tests/runtime-stack.json
python scripts/check_submission.py --base origin/main --branch feat/versioned-document-transfer
```

The shared storage probe is exercised by both local and S3-double tests. Stubber
checks the actual boto3 operation shapes without credentials, metadata-service
discovery or network. No test automatically selects a developer AWS profile.

## Operator configuration

The CloudFormation template requires an existing RuntimeRoleArn and newly issued
random Namespace UUID v4. It creates a dedicated versioned bucket with SSE-S3,
all BlockPublicAccess settings, BucketOwnerEnforced ownership and Retain policies.
The runtime may inspect bucket safety configuration and read/create only within
`sanitized/<namespace>/`. Bucket policy requires conditional creation, explicit
AES256 encryption and exactly the fixed classification/namespace tags. Runtime
deletion of source objects/versions and removal of tags is denied. No lifecycle
expiration or source-evidence purge is installed.

Pass bucket name, expected account ID and namespace to S3DocumentConfiguration
from trusted deployment configuration. Never accept these from the upload body.
Use the approved session/profile/region for the injected S3 client and pinned
export public keys for Ed25519ExportVerifier. The signing private key is supplied
only to the local A3 gate. Do not send keys or re-identification maps to Runtime.
Call `check_configuration()` at deployment preflight; each storage instance also
performs the safety check before its first I/O. Runtime has no configuration
mutation permission. Operator changes require repeating acceptance/preflight.

The service needs a durable audit adapter. ImmutableDocumentAudit uses the same
append-only storage; a private SQLite store also supports local audit. Require
authenticated random identity IDs and explicit case/purpose/operation grants.
Do not construct Principal from a consumer's asserted actor fields.

## Separately approved live probe

Only use a designated disposable synthetic namespace after account/role/region
and cleanup scope are approved. Provision the stack through the deployment team's
reviewed workflow; this probe does not create or delete stacks. The application
role has no delete permission. A distinct approved cleanup role needs version-tag
read and version-delete permission restricted to this test's namespace.

The probe below requires explicit flags and profiles. Replace placeholders with
reviewed configuration; do not store actual configuration or journals in Git.

```bash
mkdir -p artifacts/document-cloud-check
python -m cloud_tests.document_smoke \
  --execute --allow-synthetic-cleanup \
  --profile REVIEWED_RUNTIME_PROFILE --cleanup-profile REVIEWED_CLEANUP_PROFILE \
  --region REVIEWED_REGION --expected-account REVIEWED_ACCOUNT \
  --bucket REVIEWED_SYNTHETIC_BUCKET --namespace NEW_RANDOM_UUID_V4 \
  --journal artifacts/document-cloud-check/new-run.jsonl
```

The same storage contract creates one bounded synthetic object, validates exact
version reads, rejects duplicate creation and oversized reads, and verifies the
original bytes remain available. Before each write it records intent in a new
private local journal. Successful responses add exact bucket/key/version/owner
records. Cleanup considers only those successful records and checks both namespace
and exact tags before deleting that particular version. It never lists and sweeps
a bucket/prefix or removes another test's resources. An uncertain write retains an
unresolved intent for explicit operator recovery; absence of a returned VersionId
is not proof that nothing was stored. Preserve the journal until resolved.

This bounded probe is not complete cloud acceptance. Before production, separately
exercise runtime IAM denial of untagged/unconditional/unencrypted writes and
deletes, unauthorized principals/cases, anonymous access, latest-version changes,
full A3 canary transport, D1 retries and audit durability. Confirm real retention
requirements with the data owner. A synthetic cleanup exception is not authority
to delete production source evidence.
