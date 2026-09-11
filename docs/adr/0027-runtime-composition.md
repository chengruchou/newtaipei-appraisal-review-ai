# ADR 0027: Reference-only Runtime with durable execution authority

Status: included in merged PR #45 for local/offline integration; deployment remains
unaccepted. Remaining composition is tracked in #24 and #30, live acceptance in #31.

## Decision

Continue the shared JobStore, ReviewJobService and service-v1 contracts. A
DynamoDB transaction owns job/run/attempt/outbox state (ADR 0026); no process
cache or Runtime session supplies execution authority. SQS carries bounded
opaque references. The separate bridge invokes the IAM-protected Runtime,
which claims, heartbeats, verifies fencing and conditionally finishes a run.
Acknowledgement never establishes review success or PDF publication.

The result body is immutable and addressed by run, result version and content
digest. The committed DynamoDB reference selects the visible body. An expired
attempt's earlier S3 write cannot poison a fixed destination. S3 writes use
conditional creation, versioning, checksums and expected bucket ownership.
Unselected orphan bodies require a separately reviewed retention policy.

SnapshotJobService checks the exact material revision and authorized C2
snapshot/read before admitting a job. SnapshotBoundExecution re-resolves the
current principal and stored submission, then reads the exact immutable C2
sources both before and after execution. A transient capability failure differs
from revoked permission or invalid source. No caller chooses a bucket, local
path, principal grant or model through Runtime input.

RuntimeWorker bounds execution, heartbeats and cancellation, persists a complete
service result before committing its reference, and returns actual terminal
state when retry limits or cancellation win. Human waiting requires persisted
task identifiers and releases the attempt. No confirmation or approval is
manufactured; confidence remains unchanged, including zero.

## Explicit missing dependencies

build_runtime_worker accepts a reviewed execution provider, C2 service and current
principal directory. It creates only the real DynamoDB/S3 adapters. The default
HTTP app has no bundle and returns sanitized 503, including /ping. It never
falls back to synthetic adapters, fake writers or an in-memory durable store.
Runtime configuration is not an implementation of the missing bundle.

The #34-#39 lines, incorporated through PR #45, provide formal PDF, publication,
action policy, privacy, human-task/revision and workbench behavior. The earlier
missing publication package was repaired, and the local assembly uses a durable
combined SQLite store. Cloud task/revision/receipt transactions still require the
implementation tracked in #24; #30 must compose those providers with the deployed
API, worker and current authority. Independent cloud writes do not inherit the
local store's atomicity merely by implementing the same JobStore port.

## Deployment boundary and validation

The scheduler, bridge and Runtime use separate roles. Only Runtime reads the
approved sanitized source prefix and allowed model resources, and writes the
dedicated result prefix. No role can read originals, identity mappings or locally
restored output. Endpoint overrides and workstation profiles are not accepted
by workload client configuration. Infrastructure uses immutable image digests,
disabled triggers by default, bounded concurrency/timeouts, retention and DLQs.
See [the infrastructure decision](../../infra/runtime/adr.md) and
[operator runbook](../runtime-deployment.md) for exact permissions and rollback.

SDK Stubber, Moto and real local C2 tests cover request shapes, persistence
reconstruction, transaction races, stale publication and revocation. Local ARM64
packaging and fail-closed HTTP checks establish executable packaging only.
Live service durability, IAM, model execution, end-to-end browser acceptance and
safe sandbox rollback remain separate required evidence. PUBLIC sandbox egress
does not establish a production network design.
