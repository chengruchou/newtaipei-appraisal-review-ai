# Runtime deployment - offline infrastructure slice (#30)

Status: **Draft, not deploy-ready**. These templates and build tools implement
the infrastructure boundary; they do not supply the reviewed execution bundle.
The default `appraisal_review.adapters.aws.runtime_app:app` has no real worker:
`GET /ping` and a well-formed `POST /invocations` return sanitized HTTP 503. A
configured DynamoDB/S3 backend alone does not make that application ready.
Do not enable triggers or describe the runtime as healthy until the reviewed
revision, human-task and output/publication providers are integrated and accepted.
The required dependencies from #34-#39 remain unresolved for this delivery.

`cloud_tests/` is an earlier synthetic smoke and is not this production entry.
This slice introduces no successful in-memory execution provider. See the
[runtime composition decision](adr/0027-runtime-composition.md) and
[durable job decision](adr/0026-dynamodb-job-store.md).

## Files and trust boundaries

| File | Responsibility |
| --- | --- |
| `infra/runtime/image-stack.yaml` | Retained private ECR repository, immutable tags, encryption and scan-on-push |
| `infra/runtime/runtime-stack.yaml` | AgentCore HTTP Runtime, durable job table, versioned result bucket, queues, independent roles, dispatcher/worker, retention and alarms |
| `infra/runtime/Dockerfile` | Non-root HTTP server on 8080, private operator configuration, ARM64 hash-locked dependencies |
| `infra/runtime/lambda.Dockerfile` | Lambda base image with synchronous dispatcher/bridge commands; no private runtime configuration |
| `scripts/build_runtime_image.py` | Local build and actual image/config verification; never login, push, deploy or invoke AWS |
| `tests/unit/test_runtime_infrastructure.py` | Offline schema structure, security, integration entry names, retention and image-validation tests |

The table uses `pk` and `sk` string keys. Its only GSI is `job-recovery`, with
`recovery_pk` string HASH and `recovery_at` number RANGE keys, `KEYS_ONLY`
projection. Missing recovery attributes keep terminal/nonrecoverable rows out of
the index. GSI reads never replace strongly consistent base-table checks and
conditional writes. There is no TTL that could silently remove idempotency or
audit records. PITR, encryption, deletion protection and retain-on-replacement
are enabled. Application transactions need the corresponding item permissions;
there is no table Scan grant.

The EventBridge schedule invokes
`appraisal_review.adapters.aws.runtime_jobs.dispatch_handler` with exactly
`{"schema_version":"reconcile-v1"}` once per minute when enabled. It reconciles
expired leases/retries and sends durable outbox references to SQS. Its role has
DDB base-table/GSI access and SQS SendMessage only, plus its own log streams.
The `NoResultAccess` adapter explicitly refuses result reads/writes; the
dispatcher has no S3, document-catalog, model or Runtime-invocation permission.

SQS invokes `appraisal_review.adapters.aws.runtime_jobs.worker_handler` with a
batch of one and `ReportBatchItemFailures`. This role receives queue messages
and invokes only this Runtime and its DEFAULT endpoint. It cannot read or write
the job table, documents, models or results. Both handlers are synchronous Python
functions, not coroutine entry points.

The bridge sends only the reference DTO (`schema_version`, `job_id`, `run_id`,
`outbox_seq`, `dispatch_token`, `enqueued_at`). There is no caller-supplied
principal, document text, bucket, model or rule in an invocation. IAM controls
the invoker; the execution path must reauthorize current principal/source
snapshots from durable records. The dispatch token is a 36-character Runtime
session ID, not an authorization credential. The bridge limits response reads
to 4097 bytes and validates the response identity and allowed outcome.

## Explicit workload configuration

| Variable | Runtime | Dispatcher | Worker bridge |
| --- | --- | --- | --- |
| `REVIEW_REGION` | Stack region | Stack region | Stack region |
| `REVIEW_ACCOUNT_ID` | Stack account | Stack account | Stack account |
| `REVIEW_JOB_TABLE` | Jobs table | Jobs table | Absent |
| `REVIEW_RESULT_BUCKET` | Results bucket | Absent | Absent |
| `REVIEW_QUEUE_URL` | Absent | Work queue | Absent |
| `REVIEW_RUNTIME_ARN` | Absent | Absent | Exact Runtime ARN |

`REVIEW_REGION` is required; do not rely on an `AWS_REGION` fallback. Workload
clients use the assigned role. Never set `AWS_PROFILE` or `AWS_DEFAULT_PROFILE`,
mount a workstation AWS directory, bake credentials, or pass a custom SDK
endpoint. These are different from the explicit CLI profile an operator uses
outside the containers.

The HTTP build requires a private JSON file with mode 0600/0400 supplied through
a BuildKit secret mount. It is deliberately copied into the final image at
`/opt/appraisal/runtime.json`; it is **not a secret vault**. ECR readers can read
that file. Use only non-secret operator configuration, never cases, signed URLs,
local mappings or credentials. Do not commit it. `REVIEW_RUNTIME_CONFIG` records
this path for composition integration; the baseline application does not consume
it to invent a worker. A JSON file by itself cannot make `/ping` healthy.

The source context is an allowlist of package `.py` files, Dockerfiles and the
hash lock. Private files, PDFs, cache directories, Git metadata and the broader
working tree are excluded. The dependency lock targets CPython 3.11 and
`manylinux2014_aarch64`; the Dockerfiles force that platform during installation
to avoid selecting a different wheel for the same version. Review and regenerate
the lock if the provider bundle adds dependencies. Font/template packaging for
the final PDF provider remains an integration requirement.

## Limits, network and permissions

| Control | Value and meaning |
| --- | --- |
| Worker timeout | 120 seconds, fixed; Runtime processing is bounded to 90 seconds and bridge read to 100 seconds |
| SQS visibility | Parameter with minimum/default 720 seconds; batch window zero |
| Work retries | Five receives, then a 14-day encrypted work DLQ |
| Dispatcher | 60-second timeout, reserved concurrency 1, bounded pass of 25 candidates |
| Dispatcher failures | EventBridge delivery and Lambda asynchronous failures reach a separate 14-day DLQ; each retry layer is bounded |
| Worker concurrency | Parameter 2-10, shared by reserved concurrency and event-source maximum concurrency |
| Lambda memory | Dispatcher and worker separately parameterized; retention function fixed at 128 MB |
| Runtime sessions | Idle timeout 60 seconds, maximum lifetime 900 seconds; not a job lease or execution deadline |
| Runtime CPU/memory | Service managed; no fabricated CPU/memory CFN fields and no claim Lambda settings size it |
| Network | Explicit PUBLIC sandbox egress; IAM-authorized ingress, no anonymous resource policy |
| Logging | 7/14/30/60/90-day retention; scoped groups; no request access logs or document dumps |

AWS requires [ARM64 and the HTTP port/path contract](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-http-protocol-contract.html).
The [CFN Runtime schema](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-bedrockagentcore-runtime.html)
defines the actual properties. The visibility floor follows the
[Lambda/SQS six-times timeout guidance](https://docs.aws.amazon.com/lambda/latest/dg/services-sqs-configure.html).

Runtime execution can read only versions under `SanitizedBucketArn` plus the
exact `SanitizedObjectPrefix`, and write/read `results/*` in the dedicated results
bucket. It cannot list the source bucket or read mutable latest source objects.
The optional `DocumentStoreTableArn` adds exact read-only table access, not a
catalog implementation. Model resources come from `ModelResourceArns`; list every
exact inference-profile and backing-model ARN needed, including approved
cross-region destinations. Wildcard model patterns are rejected. SSE-KMS inputs
requiring customer-key permissions need a separate reviewed change; this stack
does not grant arbitrary KMS decrypt.

Only `ecr:GetAuthorizationToken` uses global `Resource: "*"`, because the action
has no resource-level scope. `cloudwatch:ListMetrics` is not needed and is not
granted. S3 object-prefix and scoped log-stream/Runtime-ID suffix wildcards are
bounded to named resources in one account and region. A universal principal in
a TLS **deny** does not grant access. The ECR Lambda pull policy and the schedule
invocation/DLQ policies constrain both source account and source ARN. Workload
roles have no deployment or image-publishing privileges. Invocation covers the
[exact Runtime and DEFAULT endpoint resources](https://docs.aws.amazon.com/service-authorization/latest/reference/list_bedrock-agentcore.html).

Runtime groups are [created by the service](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-configure.html).
A small custom-resource Lambda creates the exact group only if absent, applies
retention, and leaves the group on Delete. Failures block stack completion; the
triggers depend on retention success. It never logs the CFN event or response
URL, never lists all groups, and has no delete permission. It does not enable
account-wide tracing or telemetry capture.

## Offline validation and local image

Recorded local checks on 2026-09-11: cfn-lint 1.56.2 accepts both templates;
27 infrastructure tests, scoped Ruff/format and mypy pass. Docker 28.3.2 built
`appraisal-runtime:offline-20260911`; exported manifest/config verification
reports `linux/arm64`, and executing Python in the container reports `aarch64`.
The container ran with a read-only root, no credential mounts and no supplied
AWS environment. `/ping` and valid `/invocations` returned 503; an invocation
with an extra untrusted principal returned sanitized 422. The temporary
container was removed. Private evidence is under `artifacts/runtime-offline/`.
This was an explicitly unconfigured packaging probe, not a reviewed execution
bundle. A subsequent official Lambda Python 3.11 image also built and ran as ARM64.
Its local Runtime Interface Emulator rejected invalid/unconfigured events for
both synchronous handlers with network disabled. These are local error-path
checks, not live invocation or completed review. The final clean-head image
identifiers and scan findings are recorded in the PR after source publication. No AWS resource, model, billing,
ECR push or deployment operation was performed.

Docker reports `InvalidDefaultArgInFrom` because the Dockerfiles intentionally
require an explicit `BASE_IMAGE`; the build script supplied a verified
digest-pinned Python 3.11 image. This warning is retained in the build log.
No successful health or live AWS acceptance is inferred from the build.

From this worktree, the existing sibling virtual environment can run checks:

```bash
VENV=../model-extraction-evaluation/.venv
PYTHONPATH="$PWD/src" "$VENV/bin/python" -m pytest tests/unit/test_runtime_infrastructure.py
"$VENV/bin/ruff" check scripts/build_runtime_image.py tests/unit/test_runtime_infrastructure.py
"$VENV/bin/ruff" format --check scripts/build_runtime_image.py tests/unit/test_runtime_infrastructure.py
PYTHONPATH="$PWD/src" MYPYPATH="$PWD/src" "$VENV/bin/mypy" --follow-imports=silent scripts/build_runtime_image.py tests/unit/test_runtime_infrastructure.py
# If needed, install cfn-lint locally; this does not access an AWS account.
"$VENV/bin/python" -m pip install --target infra/runtime/.tools 'cfn-lint>=1.40,<2'
PYTHONPATH="$PWD/infra/runtime/.tools:$PWD/src" "$VENV/bin/python" infra/runtime/.tools/bin/cfn-lint infra/runtime/image-stack.yaml infra/runtime/runtime-stack.yaml
```

These are structural/security tests, not a cfn-guard run, deployed IAM test, or
CloudFormation change-set validation. No cfn-lint schema suppression is used.

For a local build, explicitly supply a digest-pinned Python 3.11 base, unique tag
and private non-secret configuration. Logs and receipts stay under ignored
`artifacts/`. The Lambda target requires a digest-pinned AWS Lambda Python 3.11
base and no `--runtime-config` argument.

```bash
: "${RUNTIME_BASE_IMAGE:?digest-pinned Python 3.11 base}" "${LOCAL_RUNTIME_TAG:?unique tag}"
: "${PRIVATE_RUNTIME_CONFIG:?private non-secret JSON file}"
mkdir -p artifacts/runtime-build
python scripts/build_runtime_image.py --target runtime \
  --tag "$LOCAL_RUNTIME_TAG" --base-image "$RUNTIME_BASE_IMAGE" \
  --runtime-config "$PRIVATE_RUNTIME_CONFIG" \
  --output artifacts/runtime-build/image.json > artifacts/runtime-build/build.log 2>&1
```

The script checks Docker inspect **and** a Docker save manifest's actual config
digest, OS and architecture before writing a receipt. It disables automatic
build attestations/metadata. Depending on Docker's image store, a local image ID
identifies a config or a manifest; the verifier checks the corresponding content
and binding. Neither identifies a verified ECR push. The receipt does not claim actual container execution or a
registry push. Re-verify the same tag immediately before any manual push.

Run a temporary container without AWS environment variables or credential
mounts, publishing only to a dynamically assigned loopback port. Check `/ping`
and a valid reference invocation; the current expected status is 503 for both.
Check malformed input is rejected without echoing input. Stop/remove only that
container. This proves runnable packaging and fail-closed behavior, not AWS
Runtime health, real job execution or production-case correctness.

## Manual sandbox create/update

Nothing below is executed by the build tool. Execute resource operations only
after explicit account/profile/region/prefix/budget/expiry authorization and the
dependency gates above. Keep every parameter file and AWS receipt private.
Prepare the image template's full CloudFormation parameter JSON file first,
including Environment=sandbox, team Owner and ExpiresOn. After pushing and
verifying both registry digests, prepare the runtime parameters with those
values, exact source/model/alarm-topic ARNs and EnableTriggers=false. Parameters
have no default account, region or prefix.

```bash
set -euo pipefail
: "${DEPLOY_PROFILE:?explicit CLI profile}" "${DEPLOY_REGION:?explicit region}"
: "${EXPECTED_ACCOUNT:?expected account}" "${DEPLOY_PREFIX:?unique sandbox prefix}"
: "${IMAGE_PARAMETERS:?private full image parameter JSON}"
: "${DEPLOY_ROLE_ARN:?separate approved CloudFormation execution role}"
# Check the identity and image parameters before continuing.
aws --profile "$DEPLOY_PROFILE" --region "$DEPLOY_REGION" sts get-caller-identity
test "$(aws --profile "$DEPLOY_PROFILE" --region "$DEPLOY_REGION" sts get-caller-identity --query Account --output text)" = "$EXPECTED_ACCOUNT"

# Bootstrap ECR first, review the change set, then explicitly execute it.
aws --profile "$DEPLOY_PROFILE" --region "$DEPLOY_REGION" cloudformation create-change-set \
  --stack-name "$DEPLOY_PREFIX-images" --change-set-name "$DEPLOY_PREFIX-images-create" \
  --change-set-type CREATE --template-body file://infra/runtime/image-stack.yaml \
  --parameters "file://$IMAGE_PARAMETERS" --role-arn "$DEPLOY_ROLE_ARN"
aws --profile "$DEPLOY_PROFILE" --region "$DEPLOY_REGION" cloudformation describe-change-set \
  --stack-name "$DEPLOY_PREFIX-images" --change-set-name "$DEPLOY_PREFIX-images-create"
aws --profile "$DEPLOY_PROFILE" --region "$DEPLOY_REGION" cloudformation execute-change-set \
  --stack-name "$DEPLOY_PREFIX-images" --change-set-name "$DEPLOY_PREFIX-images-create"
```

Wait for stack completion and verify the repository ARN, account, region and
prefix before a separately authorized push. Authenticate using the operator's
approved registry-login procedure, without copying authentication into the
images or receipts. For each built image:

```bash
: "${EXACT_ECR_TAG:?full verified repository URI plus immutable tag}"
python scripts/build_runtime_image.py --target runtime --verify-only \
  --tag "$EXACT_ECR_TAG" --output artifacts/runtime-build/pre-push-runtime.json
docker push "$EXACT_ECR_TAG"
```

Capture the registry digest reported by that push, then verify and scan it:

```bash
: "${DEPLOY_PROFILE:?}" "${DEPLOY_REGION:?}" "${DEPLOY_PREFIX:?}"
: "${RUNTIME_DIGEST:?digest recorded from the authorized push}"
aws --profile "$DEPLOY_PROFILE" --region "$DEPLOY_REGION" ecr describe-images \
  --repository-name "$DEPLOY_PREFIX-runtime" --image-ids "imageDigest=$RUNTIME_DIGEST"
aws --profile "$DEPLOY_PROFILE" --region "$DEPLOY_REGION" ecr describe-image-scan-findings \
  --repository-name "$DEPLOY_PREFIX-runtime" --image-id "imageDigest=$RUNTIME_DIGEST"
```

Use `--target lambda` and its distinct receipt/tag/digest for the Lambda image.
Build directly with the ECR tag or explicitly retag the already verified local
image before the pre-push check. Capture the push-reported registry digest and
verify it; never substitute the local image ID. Scan both images and review
dependency/OS findings. Scan-on-push does not itself block deployment or prove
absence of vulnerabilities.

After both images exist and the real configured application passes acceptance,
create the runtime change set. Set `CHANGE_KIND=CREATE` initially, `UPDATE` only
for an existing stack; use a unique `CHANGE_ID` each time. Full private parameter
files must match the explicit prefix and digests.

```bash
: "${DEPLOY_PROFILE:?}" "${DEPLOY_REGION:?}" "${DEPLOY_PREFIX:?}"
: "${RUNTIME_DIGEST:?verified registry digest}" "${LAMBDA_DIGEST:?verified registry digest}"
: "${RUNTIME_PARAMETERS:?private full runtime parameter JSON}" "${DEPLOY_ROLE_ARN:?}"
: "${CHANGE_KIND:?CREATE or UPDATE}" "${CHANGE_ID:?unique reviewed change name}"
aws --profile "$DEPLOY_PROFILE" --region "$DEPLOY_REGION" cloudformation create-change-set \
  --stack-name "$DEPLOY_PREFIX-runtime" --change-set-name "$CHANGE_ID" \
  --change-set-type "$CHANGE_KIND" --template-body file://infra/runtime/runtime-stack.yaml \
  --parameters "file://$RUNTIME_PARAMETERS" --role-arn "$DEPLOY_ROLE_ARN" --capabilities CAPABILITY_IAM
aws --profile "$DEPLOY_PROFILE" --region "$DEPLOY_REGION" cloudformation describe-change-set \
  --stack-name "$DEPLOY_PREFIX-runtime" --change-set-name "$CHANGE_ID"
aws --profile "$DEPLOY_PROFILE" --region "$DEPLOY_REGION" cloudformation describe-events \
  --stack-name "$DEPLOY_PREFIX-runtime"
# Execute only the reviewed successful change set.
aws --profile "$DEPLOY_PROFILE" --region "$DEPLOY_REGION" cloudformation execute-change-set \
  --stack-name "$DEPLOY_PREFIX-runtime" --change-set-name "$CHANGE_ID"
```

Wait for completion before invocation. Inspect health, exact runtime version,
image digests, log retention and the actual workload-role denial boundaries.
Only then review an UPDATE setting EnableTriggers=true. The private deployment
receipt must record account/role, region, prefix, expiry, source commit plus
dirty/source snapshot state, template hashes, dependency hash, config hash, both
image manifest digests, Runtime version, full parameter set and checks performed.
Do not commit the receipt or private account resources.

## Manual rollback and cleanup

Stop new admissions and use a reviewed UPDATE with EnableTriggers=false. Wait
for in-flight invocations/leases to settle and inspect pending outbox and DLQ
references. Do not purge queues or reset the job table to make rollback look
successful. An image rollback does not undo completed jobs, human revisions or
published evidence.

```bash
: "${DEPLOY_PROFILE:?}" "${DEPLOY_REGION:?}" "${DEPLOY_PREFIX:?}"
: "${ROLLBACK_RUNTIME_DIGEST:?previous accepted registry digest}"
: "${ROLLBACK_LAMBDA_DIGEST:?previous accepted registry digest}"
: "${ROLLBACK_PARAMETERS:?private parameters with both prior digests and disabled triggers}"
: "${ROLLBACK_CHANGE_ID:?unique name}" "${DEPLOY_ROLE_ARN:?}"
aws --profile "$DEPLOY_PROFILE" --region "$DEPLOY_REGION" cloudformation create-change-set \
  --stack-name "$DEPLOY_PREFIX-runtime" --change-set-name "$ROLLBACK_CHANGE_ID" \
  --change-set-type UPDATE --template-body file://infra/runtime/runtime-stack.yaml \
  --parameters "file://$ROLLBACK_PARAMETERS" --role-arn "$DEPLOY_ROLE_ARN" --capabilities CAPABILITY_IAM
aws --profile "$DEPLOY_PROFILE" --region "$DEPLOY_REGION" cloudformation describe-change-set \
  --stack-name "$DEPLOY_PREFIX-runtime" --change-set-name "$ROLLBACK_CHANGE_ID"
aws --profile "$DEPLOY_PROFILE" --region "$DEPLOY_REGION" cloudformation execute-change-set \
  --stack-name "$DEPLOY_PREFIX-runtime" --change-set-name "$ROLLBACK_CHANGE_ID"
```

Use the previous template too if schema/IAM compatibility requires it. Verify
the previous digest's readiness and durable-state compatibility before enabling
triggers again. Capture the new Runtime version produced by the rollback.

After explicit deletion authorization, record `describe-stack-resources` for
both exact stacks and inspect each resource's tags. Delete the runtime stack
first, wait for its deletion, then delete the image bootstrap stack:

```bash
: "${DEPLOY_PROFILE:?}" "${DEPLOY_REGION:?}" "${DEPLOY_PREFIX:?}"
: "${RUNTIME_DIGEST:?retained deployment digest}" "${LAMBDA_DIGEST:?retained deployment digest}"
aws --profile "$DEPLOY_PROFILE" --region "$DEPLOY_REGION" cloudformation describe-stack-resources --stack-name "$DEPLOY_PREFIX-runtime"
aws --profile "$DEPLOY_PROFILE" --region "$DEPLOY_REGION" cloudformation delete-stack --stack-name "$DEPLOY_PREFIX-runtime"
# Wait for deletion and inspect failures before removing the bootstrap stack.
aws --profile "$DEPLOY_PROFILE" --region "$DEPLOY_REGION" cloudformation delete-stack --stack-name "$DEPLOY_PREFIX-images"
```

The job table, versioned results bucket, work queue, two DLQs, ECR repository and Runtime log
group remain. They can still incur storage charges. Log streams expire under
their configured retention; expiry tags do not delete resources. Record these
retained resources and ownership explicitly. Do not disable deletion protection,
empty versioned buckets, delete retained images, purge queues, or remove shared
source/catalog/alarm resources as part of automatic cleanup. Any later removal
requires its own reviewed, exact-resource plan and evidence-retention decision.

## Acceptance and troubleshooting

Alarms cover work DLQ depth >=1, dispatch DLQ depth >=1, and work-queue age >900
seconds for three one-minute periods. `Owner` appears in each alarm description;
the existing `AlarmTopicArn` must have a verified destination. Before redrive,
inspect durable job/run/attempt identity, current lease/fencing token and
sanitized failure. For queue age, inspect throttling, bridge timeout and Runtime
availability before raising concurrency. Test alarm delivery in the authorized
sandbox; a template alarm resource is not delivery evidence.

Correlate job/run/attempt/dispatch IDs without document bodies or SDK wire logs.
Job latency, success/failure, retries, waiting tasks, expired leases and model
usage/cost metrics and dashboards require the application/#31 integration; this
slice does not claim those observations exist. No custom global metric-write or
account-wide trace permission is granted.

Required external evidence remains: real configured health; D1 outbox-to-Runtime
execution; attempt/result writeback; process replacement and fenced stale-worker
rejection; malformed input and access denial; model timeout/5xx; document version
and authorization revocation; successful human/PDF/publication dependencies;
scan review; actual create/update/rollback/cleanup; alarm delivery; and #31's
browser/AWS flow. Keep the PR Draft until its stated acceptance scope is met.
