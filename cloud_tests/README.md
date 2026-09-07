# Synthetic AgentCore Runtime smoke preparation

This is an isolated HTTP container for testing A's deployed invocation boundary.
Preparation merged in PR #14 and is available on `main`. The review and extraction
code from #15/#16 and real writer #19 is also merged; no historical stacked branch is needed. This
packaging remains a synthetic smoke, separate from those real-document adapters.
It does **not** parse documents, call Bedrock, produce PDF files, or implement the
production durable review-jobs pipeline. The separate production design is #9.

A run is accepted as execution_status=running. Poll the same Runtime session
until succeeded/failed; the nested result.status is the business status and may
be needs_review. Duplicate starts of the same run/scenario reuse state; changing
the scenario for that run is 409. A session permits at most 16 runs. State is
explicitly durable=false: it is lost when the Runtime session exits. This must
never substitute for #9's DynamoDB leases/outbox/reconciler/result manifest.

## Local verification

```bash
export PYTHONPATH="$PWD/src"
.venv/bin/python -m pytest cloud_tests
.venv/bin/python -m pip install -r cloud_tests/requirements-dev.txt
.venv/bin/cfn-lint cloud_tests/image-stack.json cloud_tests/runtime-stack.json
.venv/bin/python -m uvicorn cloud_tests.runtime:app --host 127.0.0.1 --port 8080
```

In another terminal, submit a generated UUID and reuse it to poll:

```bash
curl --fail http://127.0.0.1:8080/ping
curl --fail -H 'Content-Type: application/json' --data '{"action":"start","run_id":"00000000-0000-4000-8000-000000000001","scenario":"completed"}' http://127.0.0.1:8080/invocations
curl --fail -H 'Content-Type: application/json' --data '{"action":"status","run_id":"00000000-0000-4000-8000-000000000001"}' http://127.0.0.1:8080/invocations
```

The [official Runtime contract](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-http-protocol-contract.html)
requires ARM64 and port 8080. This server uses custom async task tracking: /ping
returns HealthyBusy for active work and Healthy after success/failure. It omits
time_of_last_update, so polling does not extend idle lifetime. Health remains
responsive while work yields; real blocking tools must be moved to threads or
async I/O. This implements the custom-health alternative in the
[long-running task documentation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-long-run.html).
Run one worker per session; the smoke state is deliberately process-local.

## Reviewable resource plan

Two independent CloudFormation templates avoid depending on an image that does
not exist yet. Use a unique SmokeId (8-20 lowercase letters/digits), never reuse
an existing unrelated resource. Save stack IDs, image digest and exact created
physical resource IDs under ignored artifacts/ before running the live smoke.

1. image-stack.json: one private, encrypted, immutable-tag ECR repository named
   appraisal-review-smoke-${SmokeId}. EmptyOnDelete=false prevents deletion of
   unexpected images. No public repository policy.
2. runtime-stack.json: one execution role and one HTTP Runtime using that exact
   ECR image digest. Runtime network PUBLIC means network egress, not anonymous
   invocation; the default invocation authorization remains IAM/SigV4.
   Role permissions are limited to the dedicated image and Runtime log prefix,
   plus ECR authorization and log-group discovery. No Bedrock/S3/data access.
3. Runtime-created CloudWatch groups/streams: capture their exact names, set
   short retention, and remove only those confirmed for this Runtime after
   evidence export. They are not swept by a broad prefix delete.

Runtime lifecycle is bounded to 60-second idle / 600-second maximum sessions.
These settings follow the
[CloudFormation Runtime schema](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-bedrockagentcore-runtime.html).
The execution-role trust and permissions are based on the
[Runtime IAM guidance](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-permissions.html),
scoped to synthetic work. Deployment-role permissions additionally need exact
CloudFormation/ECR/IAM/Runtime creation and iam:PassRole for the new execution
role. No administrator/root credentials are required by this plan.

## Deploy only after project access is designated

Required user input: profile/SSO login, Region, expected account and role.
Use the same explicitly selected profile/Region for all commands. Verify STS
caller identity against those inputs first. Do not choose default implicitly.
AWS CLI v2 and an active Docker buildx environment are deployment prerequisites;
no global tools or drivers are installed by this preparation.

With the designated environment variables PROJECT_PROFILE, PROJECT_REGION and
SMOKE_ID configured, the deployment sequence is:

```bash
aws --profile "$PROJECT_PROFILE" --region "$PROJECT_REGION" sts get-caller-identity
aws --profile "$PROJECT_PROFILE" --region "$PROJECT_REGION" cloudformation deploy --template-file cloud_tests/image-stack.json --stack-name "appraisal-review-smoke-image-$SMOKE_ID" --parameter-overrides "SmokeId=$SMOKE_ID" --tags Project=appraisal-review "SmokeId=$SMOKE_ID" --no-execute-changeset
```

Inspect and execute the specific change set after verifying project identity,
resource scope and absence of unrelated replacements. Read RepositoryUri from
this stack's outputs into IMAGE_REPOSITORY_URI. Authenticate Docker only to
that ECR registry using the same profile/Region; do not print/store its token.

```bash
docker buildx build --platform linux/arm64 --provenance=false --sbom=false -f cloud_tests/Dockerfile -t "$IMAGE_REPOSITORY_URI:smoke" --load .
```

The Dockerfile-specific context allowlist includes source and the smoke entry
only; credentials, competition documents and local artifacts are excluded.
Image provenance/SBOM generation is disabled for this controlled smoke build.
Inspect the local image config and full history for attribution before pushing;
this is a required publication gate, not optional. The build contains no added
author/maintainer labels. Preserve normal license notices.

```bash
docker image inspect "$IMAGE_REPOSITORY_URI:smoke"
docker image history --no-trunc "$IMAGE_REPOSITORY_URI:smoke"
```

Only after the full metadata/content inspection passes, publish that same image:

```bash
docker push "$IMAGE_REPOSITORY_URI:smoke"
```

Resolve the immutable digest from this repository into IMAGE_DIGEST, then prepare
and inspect the Runtime change set:

```bash
aws --profile "$PROJECT_PROFILE" --region "$PROJECT_REGION" cloudformation deploy --template-file cloud_tests/runtime-stack.json --stack-name "appraisal-review-smoke-runtime-$SMOKE_ID" --parameter-overrides "SmokeId=$SMOKE_ID" "ImageDigest=$IMAGE_DIGEST" --tags Project=appraisal-review "SmokeId=$SMOKE_ID" --capabilities CAPABILITY_IAM --no-execute-changeset
```

Execute that exact change set. Save its StackId and physical resource IDs to the
smoke manifest; wait for successful creation before invocation.

## Live test (not executed here)

```bash
.venv/bin/python -m cloud_tests.smoke --profile "$PROJECT_PROFILE" --region "$PROJECT_REGION" --expected-account "$EXPECTED_TEST_ACCOUNT" --expected-role "$EXPECTED_TEST_ROLE" --smoke-id "$SMOKE_ID" --output "artifacts/runtime-smoke-$SMOKE_ID.json"
```

This client verifies STS account/assumed-role, the exact stack's Project/SmokeId
tags and Runtime ARN account/Region/name before invoking. It never provisions or
deletes resources. It uses bounded polling/retries, different UUID sessions per
scenario and the same session for duplicate starts/polling. It verifies nested
business status and the explicit no-file warning. An accepted response alone
never passes the test. Evidence stays in ignored artifacts; no private URLs or
account identifiers are committed. Keep a failure log and manifest if a call fails.

## Cleanup

After sanitized evidence capture, use the manifest's StackId to delete the
Runtime stack and wait for deletion. Confirm its tags before deleting. Remove
only the saved image digest from the dedicated repository, then delete its image
stack by StackId. An unexpected nonempty ECR repository is a stop condition,
not permission for force deletion. Export/remove only the exact Runtime-created
log groups in the manifest. Verify all newly created resources are gone and
report any cleanup failure. Never delete existing foundation buckets or original
source documents. No resources were created during local preparation, so there
is no live cleanup to perform yet.

## Evidence limits

- Local protocol/task tests and mocked AWS client tests: 8 passed; combined
  with A's revised suite: 141 passed. Ruff, formatting and mypy passed.
- CloudFormation schema lint: passed.
- ARM64 Docker build: not executed; local Docker daemon unavailable.
- AWS identity preflight, deployment, live invocation and cleanup: not executed;
  project profile/Region/account/role has not been designated.
- Durable recovery, real extraction and PDF output: remain #9/#7/#8/#5 acceptance.

The completed scenario exercises a fake writer: its review status is verified,
artifact_status is simulated, and no PDF is created or published.

## M0 service foundation handoff

The working-branch [local service runbook](../docs/local-service-runbook.md) tests
real generated PDF parsing/writing through configured HTTP/invocation. It is a
separate acceptance path from this synthetic session-local Runtime server; this
server still returns durable=false and uses its fake writer.

D should consume [service-v1](../docs/service-contracts.md) document/run/revision/
manifest contracts and reserved JobRepository guarantees when building #9. B owns
trusted human response and revision transitions. Persist tasks and end attempts
while waiting, then start a new authorized revision/run. Local guards do not deliver
DynamoDB transactions, outbox recovery, leases, fencing or a deployed Runtime.
E owns formal CJK/maps and multiple-context output; A owns actual model comparison.
No M0 AWS call or CI rerun is required or claimed.
