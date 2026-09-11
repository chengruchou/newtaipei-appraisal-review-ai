# Competition deployment profile and offline checks

Current integration baseline: main
`d148422adb18190bada93b8588a4e34d73e3c2e4`, after merged
[PR #45](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/45),
2026-09-11. The guarded factories and offline checks are implemented. The default
Runtime application still has no configured worker and returns 503; no AWS
deployment acceptance is claimed. Exact model/routing binding remains a code
follow-up in [#47](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/47).
Runtime bootstrap and approved operator/profile inputs are tracked in
[#30](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/30),
followed by live acceptance in
[#31](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/31).
See the [implementation backlog](implementation-backlog.md) for the complete scope.

The checked-in `config/competition-profile.pending.json` deliberately remains
unapproved. It chooses `us-east-1` as the single primary deployment region;
`us-west-2` is the other permitted primary choice. It creates no resources,
enables no models and grants no data admission. Account, role, model destinations,
budgets, resource inventory and actual invocation evidence must be supplied and
reviewed before a trusted operator pins a complete profile.

## Original-source inspection

Both original files were read without modification. No originals, sheet dumps,
rendered pages or local preview paths are submitted.

| Source version | SHA-256 | Complete coverage |
| --- | --- | --- |
| Competition environment rules PDF, 20260722 | `64bbda4d8056d3edd913ced8e96330f282621a00fe9d4152341d162fd385aec0` | Both pages 1-2, text and rendered-page inspection; 158448 bytes |
| Supported AWS Services List 20260722.xlsx | `378eb61dba941647748f03b16ea4fb5f37ceba5610dd0f013dd759b0f5e0ccae` | All three visible sheets and every row, including notes and blanks; 158465 bytes |

`Services List!A1:D315` contains 314 namespace rows and 16947 comma-separated
action entries; columns C:D are blank. A1 is **IAM Namespace**, B1 **Allowed
Actions**. `SageMaker AI!A1:C1909` uses **Resource Key**, **Region**, **Limit**,
with 1899 quota rows and 10 header/note/blank rows: 1, 935-943. Rows 936-942
introduce unsupported resources and explicitly prohibit out-of-band quota
changes. The repeated header is row 943; zero-limit rows start at 944. Every
quota row is retained in the digest-pinned package catalog. `EC2!A1:C11` uses
**Quota Name**, **Quota Value**, **Regions**, with ten quota rows.

The PDF page 2 states that actual competition environment limits and subsequent
announcements take precedence. These catalogs establish the inspected version,
not a claim that later announcements or live account quotas were checked.

## Rule-to-implementation matrix

| Original location | Requirement | Implemented check / evidence |
| --- | --- | --- |
| PDF p1 general 1 | No public S3 bucket | `check_template`: strict four Block Public Access flags, private ACL, encryption, TLS, versioning, retained evidence |
| PDF p1 general 2 | Thirteen prohibited data categories | Separate competition data-admission policy guards outgoing calls through the competition composition; `check_profile` requires its digest to equal a trusted server pin; generic legacy factories are not approved competition entrypoints |
| PDF p1 general 3-4 | No fully open EC2 security group or public RDS/EMR | `check_template`: IPv4/IPv6 and conditional ingress; public/unresolved RDS fails; EMR needs private-subnet evidence, not `VisibleToAllUsers` |
| PDF p1 general 5 | Necessary resources only | Unique resource inventory, existing-ARN reuse and kind-specific stop verification; no additional model-hosting/training path |
| PDF p1 general 6 | Designated primary regions | Profile validation permits exactly one primary `us-east-1` or `us-west-2`; cross-region/global requires separate approval; per-model runtime destination binding remains #47 |
| PDF p1 general 7; Services List A:B | Supported services/action ceiling | Digest-pinned selected project namespace catalog; unknown namespace/action fails; actual role permission evidence remains mandatory |
| Services List row 46 | Bedrock | `InvokeModel`, `InvokeModelWithResponseStream`, `CountTokens`, `GetFoundationModel`, `GetInferenceProfile`; no fabricated Converse IAM action |
| Services List row 47 | AgentCore | Runtime architecture retained; `InvokeAgentRuntime`; network mode is distinct from authentication |
| Services List rows 59,65,106,108,111,128,152,176,181,246,253,258,274,277,288 | Existing project services | CloudFormation, CloudWatch, DynamoDB, EC2, ECR, EventBridge, IAM, Lambda, Logs, S3, SageMaker, Scheduler, SNS, SQS, STS ceilings recorded, not granted |
| PDF p1 general 8-9 | No public credentials; authentic Kiro records if used | Existing submission/build/image/log review remains required; no blanket `.kiro` ignore or invented usage record |
| PDF p1 Bedrock 1 | Below one request per second | Team scope and central store required; all entrypoints plus CountTokens/control plane included until clarified; **1.1 seconds is the conservative implementation setting** |
| PDF p1 Bedrock 2-3 | Only necessary model access; review/revoke unused access | Profile records purpose, complete destination snapshot, review interval and stop method per model; #47 must bind fresh routing observations to the actual runtime request; no automatic enablement/quota changes |
| EC2 rows 4,8 | G/VT and P quota zero | `check_template` rejects those EC2 instance/launch-template families |
| SageMaker AI rows 118,120 vs 1749,1753 | Endpoint and training are distinct | `endpoint/ml.g5.2xlarge` and `endpoint/ml.g5.xlarge` are 2; matching `training-job/...` quotas are 0; exact key+region lookup and endpoint variant count checks |
| PDF pp1-2 EC2/SageMaker; p2 notices | Avoid large training; live limits authoritative | No new training route; unknown quotas remain unverified; deployment still needs environment evidence |

The thirteen categories in PDF p1 general rule 2 are personal data; regulated
data; financial information; race or ethnicity; political views; religious or
philosophical views; trade union membership; genetic data; biometric data or
identifiers; sexual orientation or sex life; health data; payment processing
data; malicious code or malware. Neither desensitization nor a detector miss
grants approval. Synthetic financial material needs an exact trusted organizer
clarification separate from proof that the demonstration was built from scratch.

## Invocation and profile trust

Load `CompetitionProfile` from a private server configuration. Review its
canonical `digest` and supply the independently trusted
`REVIEW_COMPETITION_PROFILE_SHA256` and `REVIEW_COMPETITION_DATA_POLICY_SHA256`.
Do not accept pins, role observations, approval references or a whole profile
from HTTP requests, documents or model output. A digest binds the reviewed
configuration; the digest by itself is not proof of operator approval.

`check_profile` also requires actual role-permission and invocation-auth evidence
digests. A local test fixture is not such evidence for the competition account.
`check_model_destinations` is a helper that compares a supplied complete
destination set against every approved use of the model. At this baseline it
has no production call site. The guarded client separately checks the requested
model identifier and a profile-wide set of allowed regional endpoints; those
checks do not establish the exact model/region/destination relationship or
freshly discovered profile routing for the actual invocation. Do not claim that
this runtime boundary is complete because the helper's unit tests pass.
[#47](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/47)
owns the request-bound composition and negative regression cases. Keep the
existing extraction preflight checks for account, role, all routing destinations
and model capabilities; they do not replace that missing runtime binding.

The shared dispatcher consumes `scope`, `interval_seconds` and the central store
binding. SQLite proves only host-local coordination; a team-wide claim needs the
central store and coverage evidence for extractor, action selector, worker,
smoke, evaluation, CLI and every physical retry. Profile budgets specify the
fixed team window and call/input-token/output-token/cost ceilings. The explicit
competition factory binds production dispatch to the exact guarded central
DynamoDB table. `DynamoDBCompetitionBudget` reserves each physical Bedrock send
with a trusted per-operation/model conservative bound and pricing/routing
evidence. Input reservations cover the trusted full model context ceiling,
including multimodal input, rather than estimating tokens from request bytes.
Rates must conservatively cover all admitted modalities/destinations/billing
modes; the cost floor includes the full token reservation and maximum additional
request fee. Unknown context or price blocks. CountTokens and control requests
need separately reviewed pricing and request-counting scope; zero cost is never
assumed. Counters record conservative reservations, not exact token usage or
billed charges, which remain unknown. Its preexisting ledger pins the profile, window and caps; atomic
updates compare all observed fields and counters. It never auto-creates,
resets, rolls over or refunds counters, including after unknown outcomes.
A new worker uses the same ledger. CountTokens/control requests need their own
explicit bounds. Converse checks the actual serialized output limit;
InvokeModel fails closed until a provider-specific output-bound decoder exists.
This profile check alone does not enforce spending; use the complete composition
specified in [ADR 0047](adr/0047-competition-runtime-composition.md).

Converse's required IAM action is `bedrock:InvokeModel`, as documented in the
[AWS Converse API reference](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html).
AgentCore's PUBLIC mode is a network setting. Preserve IAM SigV4 (or an explicitly
reviewed JWT authorizer) and test permitted and unpermitted invocations against
the real runtime. Do not label a runtime anonymous from its PUBLIC mode alone.

## Offline command

Use the declared repository environment and the original files supplied by the
operator. `ORIGINAL_RULES` and `ORIGINAL_SERVICES` below are local read-only paths.

```bash
.venv/bin/python scripts/check_competition.py \
  --profile config/competition-profile.pending.json \
  --source-pdf "$ORIGINAL_RULES" \
  --source-workbook "$ORIGINAL_SERVICES" \
  --template infra/runtime/runtime-stack.yaml \
  --template infra/runtime/image-stack.yaml \
  --template infra/documents/stack.json
```

This pending example exits 1 with structured missing-approval findings. Source
and template byte digests are reported; raw data and private paths are omitted.
When checking a reviewed private profile, `--identity-file` accepts only
`account_id` and `role_arn` from a trusted offline identity snapshot. Even a zero
exit code reports `live_acceptance: not_performed`. Run cfn-lint/CDK synthesis and
the existing infrastructure tests too. Static inspection does not evaluate all
CloudFormation intrinsics, actual role permissions, live network state or account
quota consumption. Expanded nested stacks/transforms require separate inspection.
Use `--parameters-file` with the exact private CloudFormation
`ParameterKey`/`ParameterValue` array that is intended for deployment. Its bytes
are hashed without echoing values. `UsePreviousValue` and unresolved references
do not prove model scope. Every resolved model ARN must be exact and present in
the reviewed profile; unsupported dynamic expressions require an expanded,
reviewed template. Missing parameters produce a model-scope finding rather than
silently accepting an unconstrained template.

## One deployment and resource reuse

Use `infra/runtime/runtime-stack.yaml` as the competition runtime entrypoint.
The application composition entrypoint is
`adapters/aws/competition_runtime.py`: `CompetitionAWSClients` must validate the
profile/data-policy pins and trusted observed identity before constructing the
base clients, then install the current-authority, data-admission and shared
physical-dispatch guards. The deployment template alone does not configure that
factory. Generic legacy factories are not competition entrypoints. An unconfigured
application remains unavailable; no pending profile is instantiated as approved.
Select the one existing project stack and update it with the reviewed image
digests. Reuse `SanitizedBucketArn`, `DocumentStoreTableArn` and `AlarmTopicArn`
from the owned inventory. The image bootstrap stack is created once only if
its ECR repositories do not exist. The document stack is needed only if the
authorized sanitized bucket/catalog do not already exist.

Do not also deploy the rehearsal stack, smoke stack or CDK demonstration as
another production environment. For an existing runtime stack, reuse its Jobs,
Results, WorkQueue and dashboard resources through that same stack identity.
The current runtime template does not import arbitrary preexisting replacements
for every resource it creates. If those exist under a different owner/stack,
stop and prepare a reviewed import/reuse change; changing the prefix to create
duplicates is not an acceptable reuse plan.

## Stop procedure, preserving evidence

These commands are an operator runbook for a separately authorized stop, not
commands performed by the offline checker. Resolve all identifiers from the
reviewed inventory, record the previous configuration and use the single region.

1. Pause new application submissions. Apply the reviewed model/runtime invocation
   denial for the exact project roles and model/runtime ARNs. Do not revoke
   unrelated team permissions or delete model versions as a shutdown substitute.
2. Disable reconciliation and queue delivery; zero the worker and dispatcher
   concurrency after recording their existing settings:

   ```bash
   aws events disable-rule --name "$RULE_NAME" --region "$REGION"
   aws lambda update-event-source-mapping --uuid "$MAPPING_UUID" --no-enabled --region "$REGION"
   aws lambda put-function-concurrency --function-name "$WORKER_NAME" --reserved-concurrent-executions 0 --region "$REGION"
   aws lambda put-function-concurrency --function-name "$DISPATCHER_NAME" --reserved-concurrent-executions 0 --region "$REGION"
   ```

3. Cancel/fence active application runs and stop every known active runtime
   session from the trusted run/session inventory:

   ```bash
   aws bedrock-agentcore stop-runtime-session --agent-runtime-arn "$RUNTIME_ARN" --runtime-session-id "$SESSION_ID" --qualifier DEFAULT --region "$REGION"
   ```

4. Verify `describe-rule` is DISABLED, `get-event-source-mapping` is Disabled,
   and `get-function-concurrency` is zero for both functions. Verify invocation
   rejection for the revoked roles, no outstanding active run leases/sessions,
   no later physical model dispatch, and no growing invocation/compute metrics.
   Retain the exact requests, bounded results, timestamps and inventory references
   as the stop verification. Owner/ExpiresOn tags do not establish this outcome.
5. Retain bucket versions, manifests, audit/idempotency tables and failed-delivery
   references. Do not delete stacks, purge queues, expire noncurrent versions or
   delete a manifest-bound object to stop computation. Any future garbage
   collection needs a separate reference-aware retention design and approval.

## Validation and remaining acceptance

`tests/unit/test_competition_profile.py` and
`tests/unit/test_competition_preflight.py` exercise positive and negative source,
identity, pin, budget, IAM mapping, destination, throttle, data-policy, reuse,
network, bucket, quota and CLI cases. Positive approval identifiers are clearly
synthetic test fixtures. They approve no real material or account.
`tests/unit/test_competition_runtime.py` adds actual SDK localhost traffic,
immutable operation/method/URL/header checks, retry revocation, asynchronous
snapshot authority, shared DynamoDB budget reservations, independent processes,
worker restart, cap drift and unknown-result retention. Test endpoints and the
Moto ledger are explicit local seams; they establish no real AWS acceptance.

Remaining gates include the exact model/routing repair in #47; organizer
clarification on synthetic financial material
and request-counting scope; approved account/role and effective permissions;
fresh complete routing metadata and model quality; central dispatcher coverage
and enforced budgets; actual invocation authorization; formal assets; hosted CI;
current vulnerability/image evidence; and an authorized deployment/stop rehearsal.
The profile and static checker must not be reported as completion of those gates.
