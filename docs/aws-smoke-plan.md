# Project-scoped AWS smoke plan (#9)

Status: reviewed design and local preparation only. No profile/account/role was
explicitly designated for this project in this session. No AWS API calls,
provisioning or live Runtime tests have been made. A's PR contains no deployment
resources. Runtime preparation is on test/runtime-smoke in PR #14, based on A's
feat/member-a-entrypoint PR #13. It replaces #12 and prepares the Runtime HTTP
container and smoke entry without claiming document review. This review-fix
revision does not deploy AWS or require account access.

## Bounded first smoke

The first live smoke will validate only the deployed entry using synthetic
parser/rules/fake writer. It must show accepted vs terminal execution, responsive
health and retained result metadata. It will not invoke Bedrock or write actual
PDFs and therefore cannot satisfy real extraction/PDF/full-review acceptance.

| Resource | Intended scope | Cleanup |
|---|---|---|
| ECR repository + one ARM64 image | Dedicated appraisal review smoke name/tag | Delete only this image and repository after captured evidence |
| AgentCore execution role | New smoke role, scoped ECR/log access; no source-data access | Delete only role created by smoke stack |
| AgentCore Runtime | One synthetic HTTP runtime, bounded lifetime | Delete only stack-created Runtime |
| CloudWatch logs | This Runtime only, short retention | Export sanitized evidence, delete its confirmed log groups |

No S3 case buckets, DynamoDB production table, queues or real document objects
are needed for this entry-only smoke. Those belong to the full #9 deployment,
which must implement durable job state, retry/lease recovery and publication as
described in architecture.md. In-memory smoke state is not that implementation.

## Access and preflight

Obtain one explicit profile/SSO session, deployment Region and expected test
account/role identifier. Do not select default simply because it exists. Use
[CLI SSO](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sso.html)
for interactive login when needed. Never request root credentials or long-lived
keys in chat. Resolve STS caller identity through the selected profile, compare
account/role/Region against the supplied values, then bind all clients to it.

Prepare a resource plan and unique smoke identifier before provisioning. Use
CloudFormation change sets for review, tag all created resources, and record
exact stack/resource IDs in an ignored artifacts manifest. Never infer ownership
from a name substring or delete a pre-existing resource. Package only source
code/synthetic fixtures; no credentials or documents in image layers.

## Runtime and test behavior

The official [HTTP contract](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-http-protocol-contract.html)
requires an ARM64 container listening at 0.0.0.0:8080 with POST /invocations and
GET /ping. Custom health may report HealthyBusy while tasks are active; return
to Healthy when complete and omit time_of_last_update unless tracking genuine
state changes. Keep background work nonblocking. See the
[long-running task contract](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-long-run.html).

Use a UUID session ID for each attempt and reuse that same session to poll its
synthetic job. Acceptance is not completion. Exercise verified, fake-completed
and needs_review plus malformed/duplicate requests, and verify no warning
metadata disappears. Bound calls and polling; never deploy a continuously
running monitor. Capture image digest, Runtime/version, sanitized responses and
test conclusion in ignored artifacts, not account IDs or private URLs in Git.

After evidence capture, delete only resources in the run manifest and verify
removal. If cleanup fails, report exact outstanding resources to the owner;
do not sweep the account. Existing foundation S3 data is never a cleanup target.

## Later full cloud acceptance

#9 depends on #5/#7/#8 for actual document understanding and output. Its tests
cover crash/retry windows, scoped idempotency, duplicate dispatch, lease expiry,
stale writers, S3 manifest commit, partial batch failure/DLQ and authenticated
document access. Only after those and authorized source-document checks pass
may the project claim full cloud review. No extra Gateway/MCP/vector store is
needed to make this smoke work.
