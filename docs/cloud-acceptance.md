# Cloud acceptance evidence for Issue #31

Status: integrated offline implementation, **Draft**. No browser/AWS rehearsal, deployment,
independent human acceptance or current-head remote CI is claimed here. The
required human, privacy, workbench, PDF and artifact integrations from PR #34-39
remain unresolved for this delivery. Publish after #21, then #30; retain Draft
until the complete integrated revision has actual acceptance evidence.

## Implemented scope and dependencies

The validator checks bounded, strict manifests, hashes and sizes of signed
receipt files, independent collector authority, exact deployment/material/run
correlation, measured outcomes and the complete mandatory scenario matrix. The
runner separates planning, offline verification, localhost HTTP probes and live
execution. No production provider or browser simulator is installed by this work.
The independent monitoring template creates dashboards, queries and alarms; it
does not publish metrics or demonstrate that alarms have fired.

| Requirement | Integrated branch implementation | Owner / remaining gate |
| --- | --- | --- |
| Authorized sources | #27 C2 plus #42 DocumentSnapshotResolver | Live source/role/Region acceptance |
| Jobs/recovery | #43 DynamoDB/S3/SQS/worker and C2 admission | Live process replacement, IAM and fencing |
| PDF | Merged local writer and reopen checks | PR #34 formal scope; #35 publication module |
| Human/workbench/privacy | Shared contracts and local review/export | PR #37-39 reviewed browser/confirmation workflow |
| Evidence and orchestration | domain/rehearsal.py, two scripts, actual Runtime TCP probes | Real independent browser/AWS collectors |
| Deployment | #43 ARM64 images and role-separated IaC | Complete reviewed execution bundle and deployment |
| Monitoring | infra/rehearsal/stack.json | Authenticated metric publisher and actual alarm evidence |

Direct base: PR #43, 32c5028e17ac4ada86eb9ac911a3d41bb724e203, on top of
PR #42, fa36fb714c6628bef4f0fd66aae0278372f57fae. Neither dependent PR nor
#34-39 is treated as merged or approved. The exact source/dependency inspection
is recorded in [Issue 21](issue-21-delivery.md) and
[Issue 30](issue-30-delivery.md). Review and integration proceed in that order.

## Execution classes

```bash
PYTHONPATH=src python scripts/run_rehearsal.py --execution plan
PYTHONPATH=src python scripts/run_rehearsal.py --execution localhost --port 8080
PYTHONPATH=src python scripts/run_rehearsal.py --execution live
```

`plan` lists every required collector class and reports all checks `not_run`.
`localhost` makes real TCP requests to **127.0.0.1 only**, with bounded reads and
timeouts, no proxies or redirects. Start #30's actual Runtime app first in its
documented fail-closed local configuration without live providers. The probes
expect the default **503 capability_unavailable** `/ping`, rejection of
empty/unknown/malformed `/invocations`, absence of a synthetic transport canary in
responses, and the same unready response after rejection. To test a separately
provisioned ready worker, explicitly select `--expect-runtime healthy`; this
changes the expected ping to 200/Healthy only. The runner checks exact shared
`ServiceProblem` error bodies, not just status codes. Duplicate JSON keys are
rejected before comparison; both raw and decoded JSON are checked for the synthetic
transport canary, including Unicode escapes.
They submit no valid job. They establish only HTTP rejection behavior; they do
not establish ARM64 execution, durable recovery, model calls or browser behavior.
The existing synthetic Runtime is exercised as a negative TCP control: its
validation responses echo input, so the private-error probe rejects it.
The actual #30 module is `appraisal_review.adapters.aws.runtime_app:app`; its
default is deliberately unready because reviewed owner providers are absent.
A passing unready probe reports `production_ready: false`, Draft and no live
acceptance.

`live` currently exits **2**, `live_collectors_not_integrated`. It makes no network
request. Supplying a local app, HTTP CLI, mocked SDK response or synthetic test
reviewer cannot create browser/AWS acceptance receipts. Implement the real
collector integration described below before enabling this execution class.

## Evidence format and trust boundary

`schemas/rehearsal-v1.json` describes the manifest and the standalone
`RehearsalReceipt` and `RehearsalTrust` definitions. Domain validators additionally
enforce cross-record semantics; JSON Schema alone is insufficient. Existing
service DTOs, approval receipts, job state and PDF contracts remain authoritative.
These new types are public evidence projections, never replacements for them.

The gate requires three independently sourced inputs:

1. A manifest with a unique random `campaign_id`, a maximum 24-hour observation
   window, `synthetic_cases_only` classification, exact target and scenario bindings.
2. An operator-pinned target, derived from the inspected release/deployment and CI
   records. Never create this by copying the candidate manifest under evaluation.
3. An operator-provisioned trust file with distinct Ed25519 public keys for
   browser, network, AWS, local, CI and independent-human collectors. Each key is
   limited to one source, one execution mode, a validity window and revocation
   status. Do not trust a key supplied by an upload, job or candidate manifest.

Each receipt is a regular file named `<sha256>.json` under the supplied evidence
directory. The manifest pins its actual byte size and SHA-256. Inputs are limited
to 1 MiB each. Symlink files, duplicate JSON keys, extra fields, coercible numeric
strings/booleans, unknown checks and reused receipt references are rejected.
There are no arbitrary URLs, paths, exception messages or raw log fields in the
contract. Public aliases have the exact form `r-` plus 32 random lowercase hex
characters. Never encode case numbers, filenames, people or account IDs in them.

Collector signatures cover:

```text
ASCII("rehearsal-receipt-v1\n" + key_id + "\n") + rehearsal_canonical(observation)
```

Canonicalization is sorted-key ASCII JSON with compact separators, all fields,
defaults and nulls retained, no NaN or floating point measurements. Use the
exported `rehearsal_canonical`, `rehearsal_digest` and `rehearsal_context` helpers.
The context digest covers the scenario, bindings, previous revision/attempt,
task and rollback image, with only `evidence` replaced by an empty tuple to avoid
circular signatures. The target digest binds all release fields. Finalize the
scenario's observed state before signing. Do not set a state merely to obtain a
signature. Invalid signatures, revoked keys and source/mode substitution fail.

A receipt includes a positive sample count, violation count, actual observed
value, observation time and the digest/size/version of its retained capture.
`happy.asynchronous_202` must observe **202**. Other probes must observe **1**,
meaning the specifically named invariant was measured over the entire scoped
capture, with zero violations. Zero samples or an uninspected surface cannot
mean clean. Incomplete capture, unexercised fault injection or unknown cost/usage
coverage must be `unknown`, `not_run` or failed, never a guessed zero.

This verifier authenticates attestations and their bindings. It does **not**
replay raw traces, prove a collector's honesty or contact AWS/GitHub. Raw captures
remain in an access-controlled local evidence store; collectors verify their bytes
and versions before signing, and the independent reviewer inspects them. The
trust root is only as strong as collector provisioning and this review. No live
trust keys or receipt producer are delivered here. Local tests use clearly
synthetic signing keys restricted to synthetic mode.

## Exact release, revision and artifact binding

`target` pins the full deployed `code_commit`, immutable image digest (64 hex
characters without the `sha256:` prefix), model/inference configuration digest,
prompt, deployment/configuration, parser, normalizer and CI workflow digests.
Model digests must cover the actual resolved model/profile/version and Region
configuration, not a mutable alias; record unavailable provider version detail
as unresolved. A mutable image tag or branch name is never sufficient.

The CI namespace is **`issue31-exact-revision-v1`**. It pins `pull_request_number`,
full `pr_head_commit`, full `pr_base_commit`, `ci_tested_commit`, `ci_run_digest`
and `ci_run_attempt`. `ci_tested_commit` must equal the deployed `code_commit`.
The private CI record covered by `ci_run_digest` contains the repository identity,
workflow/job identities, actual run ID, attempt, event, head/base/merge SHA,
conclusions and annotations. CI collectors independently retrieve and validate
these records. For a PR merge-ref run, pin and deploy that exact merge commit and
verify its parents against the PR refs, or rerun CI against the intended deployment
head. A successful PR-head run cannot attest an untested merge or image.
Billing-blocked, cancelled, skipped, stale and absent CI remain blocked. The
required profile adds schema/HTTP/invocation/container/scans/frontend checks to the
baseline workflow; this work does not pretend those jobs already exist remotely.

Per-scenario bindings connect random case/job/run/attempt/trace/revision aliases
to the exact immutable source bytes and source version, run snapshot, privacy
attestation, input, decisions, material, confirmations, approvals, rules, template,
field map and independent golden digests. These digests cover the actual shared
contracts, including action/reason/evidence/tool-result decisions and exact sides.
Domain labels and private storage versions are represented only by digests.

Human recovery requires a prior binding for the same case/job, a task alias, new
run/attempt/revision, changed material and fresh confirmation/approval digests.
The waiting revision cannot already have a completed artifact. Worker takeover
requires the same case/job/run/revision/material and a different attempt.
Signed browser/network/AWS observations prove the actual transitions; a changed
alias by itself does not prove an authorized response or fenced publication.

Output records bind artifact ID, actual PDF hash and size, immutable object-version
digest, manifest digest and version, run/attempt, revision, template and field map.
Download/local-reopen/AWS-manifest observations must repeat the same output record.
The publisher/readback collector must inspect the committed fenced manifest and
exact object version; the browser/local collector hashes the actual downloaded
bytes and reopens that file. A previous PDF, successful upload, simulated writer
or reused result does not satisfy this check. Failure and terminal DLQ scenarios
must have no output. Rollback requires a distinct pinned previous image digest.

## Required real collector integration

| Scenario | Measurements required before a collector may sign |
| --- | --- |
| Happy | Real authenticated browser session; exact local export confirmation; network upload contains only authorized sanitized bytes; admission returns 202; actual Runtime/model call and deterministic verification; current-attempt real PDF; conditional manifest commit; authorized download |
| Human | Inject low-confidence/conflicting material, persist waiting/findings and release attempt; workbench displays exact source and sides without modifying confidence; authorized response creates a new revision/run; reject stale confirmation; recompute and publish |
| Idempotency | Same confirmed payload/key yields the same job/result; changed payload is rejected; intercept UI timeout, observe the identical confirmed command on resend; count actual durable admissions/completions |
| Worker recovery | Stop heartbeat in one process, observe lease expiry, start a replacement process, recover durable state; attempt stale publication and observe fencing denial; browser receives only current result |
| Outbox recovery | Stop dispatch after committed outbox and before queue send; restart/reconcile; correlate actual queue and store records; exactly one completion and browser result |
| Failure / DLQ | Exercise invalid PDF, timeout, throttle, Runtime error, PDF gate, maximum attempts, retained findings/no output; force queue DLQ, observe alarm and bounded authorized redrive or terminal outcome |
| Authorization | Negative requests across principal/case/job/document/task/artifact, old revision, revoked grant and replaced source version; actual server decisions and Runtime cross-resource denial, not UI hiding controls |
| Privacy | Seed distinct local synthetic canaries in original values/file/filename/mapping/key/rehydrated PDF; verify seed presence; capture every browser egress surface and inspect each AWS storage/queue/log/model boundary; verify local-only rehydration |
| Output | Reopen actual download, compare every required formal-template field/context to independent goldens, verify source unchanged, byte hash/size/version and manifest/run binding |
| Rollback / cleanup | Verify exact owned sandbox inventory; restore distinct previous immutable image; run health/invoke and browser flow; delete only this run's approved resources; verify no unexplained survivors and retained audit inventory |
| Observability | Retrieve exact dashboard/query/metric samples; queue age, latency, success/failure, retry, waiting, leases, DLQ, model latency/usage/cost proxy; prove alarm routing/owner/threshold/runbook and actual state transition |
| Integrations / CI / human | Resolve all PR #34-39 dependencies at pinned revisions, test production composition, complete every exact-revision CI check and obtain independent human acceptance of synthetic rehearsal evidence |

Before implementing live browser collection, integration must supply a runnable
integrated workbench build and stable accessible selectors, test-only identity
realm with two isolated principals and case permissions, actual task/revision/
download endpoints, the local export/confirmation/rehydration bridge, and the
production Runtime/document/PDF/publication composition. Authentication derives
from the actual server session; do not inject a caller-selected role to simulate it.
Synthetic reviewers authorize synthetic fixtures only. Independent human
acceptance remains separate from automated fixture approval and business policy.

The browser harness must launch an actual supported browser, operate the rendered
UI and capture request/response bodies, upload bytes, URL/query/header data,
redirects, fetch/XHR/beacons, WebSocket frames, service-worker egress and download
bytes across the whole run. It must verify capture coverage and fail on unsupported
surfaces, inspect local export bytes before upload, and match browser requests to
server/store/queue/Runtime/model events using the exact correlation mapping.
No test double, DOM-only assertion, CLI request or missing HAR entry counts as
browser evidence. Keep sensitive traces local, sanitize before signing summaries,
and never publish the raw canary, original, filename, mapping/key, signed URL or
rehydrated PDF. Scan AWS versions, unfinished uploads, DynamoDB records, SQS/DLQ,
logs and model inputs with complete pagination and explicit time/resource bounds.

Provision separate protected collector signing processes only after their probes
have passed independent review. CI collectors must authenticate actual CI results;
AWS collectors must verify the approved account/role/Region and exact owned
resources. Browser, network and AWS collectors must not sign command-provided
success booleans. Observe failure injection taking effect before evaluating
recovery. Freeze capture digests/size/version and correlate every event before
signing. Unknown completeness blocks the receipt. Bound all live calls, retries,
parallelism, budget and resource lifetime in the parent deployment runbook.

## Offline commands and result semantics

```bash
PYTHONPATH=src python scripts/check_rehearsal_evidence.py \
  --manifest artifacts/rehearsal/manifest.json \
  --trust artifacts/rehearsal/operator-trust.json \
  --target artifacts/rehearsal/expected-target.json \
  --campaign "$REHEARSAL_CAMPAIGN" \
  --evidence-directory artifacts/rehearsal/receipts
```

The default purpose is `live-acceptance`. Add `--purpose synthetic-validation`
for local fixtures, or use `run_rehearsal.py --execution offline` with the same
file arguments. A valid synthetic bundle returns 0 but always reports **Draft**,
`live_acceptance: false`. Exit 1 means missing/failed acceptance gates. Exit 2
means malformed/unreadable inputs or unavailable tooling. Only complete, fresh,
independently trusted live-sandbox receipts can recommend Ready, and that is not
permission to merge or approve business material. The campaign must end within
24 hours of validation, with no more than 5 minutes future clock skew.

The JSON report contains fixed gate/reason codes and counts only, including on
argument, parse or signature failure. It never copies CLI paths, model values,
manifest identifiers, raw errors, capture bodies or URLs. The scripts write no
files. Store any redirected output under ignored worktree-local `artifacts/`.
Do not commit live evidence or trust configuration. Publish only reviewed necessary
summaries and digests under the repository submission policy.

## Monitoring namespace and alarm response

Deploy `infra/rehearsal/stack.json` only through the parent #30 deployment flow.
It accepts existing exact work/DLQ names, value-free Runtime log group and an
approved same-account/Region SNS notification topic. It grants no permissions,
creates no queues or stores and performs no recovery/deletion actions. Keep its
private dashboard unshared. The deployment owns log retention and approved SNS
delivery; missing either blocks acceptance.

Custom metric namespace: **`AppraisalReview/Rehearsal`**. Every custom sample has
exactly `CampaignId`, `CodeCommit`, `ImageDigest` dimensions matching the acceptance
target; do not add per-job identifiers as metric dimensions. Native SQS metrics
use only the exact `QueueName` from the owned inventory. The CI evidence namespace
is separate from this CloudWatch metric namespace.

| Metric | Unit / observation |
| --- | --- |
| `SucceededJobs`, `FailedJobs`, `Retries` | Count; actual durable transitions, deduplicated by event identity |
| `WaitingTasks`, `ExpiredLeases` | Count; complete current scoped inventory |
| `JobLatency` | Seconds; admission to final durable outcome, including waiting time |
| `ModelLatency` | Milliseconds; measured service call duration, including failed attempts |
| `InputTokens`, `OutputTokens` | Count; returned usage for all attempts, missing usage remains unresolved |
| `EstimatedCostMicrounits` | Count; micro currency units using a separately pinned pricing/currency basis; never invented cost |
| `CollectorHeartbeat` | Count; 1 each minute only after complete collection for that interval |

An authenticated #30 publisher must emit these values and the documented
dimensions; this template is not the publisher. Aggregate queries filter the exact
campaign/code/image and project only event counts by finite `event_kind/outcome`.
Producer schemas must prohibit free text in those fields. No `@message` projection.

### Alarm response

All nine alarms notify the approved topic on ALARM, OK and INSUFFICIENT_DATA;
each carries owner, campaign, pull-request and expiry tags and this runbook
reference. Two of three one-minute datapoints trigger a breach. Missing data is
`missing`, except collector heartbeat which is `breaching`; neither establishes
acceptance. Latency uses p99. Initial configurable thresholds are queue age 120s,
job latency 300s, model latency 30000ms and five waiting tasks. Retry, failure,
expired lease and DLQ alarms trigger at one; intentional failure rehearsals are
expected to trigger them and require evidence of routing and subsequent recovery.

The owner checks the exact inventory/campaign first. For queue age/lease/retry
alarms, inspect dispatch/lease state and apply the bounded #30 reconciler runbook;
never publish stale attempts. For failures/DLQ, retain findings and inspect the
finite cause, then explicitly authorize bounded redrive or terminal disposition.
For waiting tasks, direct an authorized reviewer to the exact task/revision.
For latency/heartbeat, distinguish a slow service from missing telemetry and stop
acceptance until coverage is restored. No alarm grants authority to approve cases,
redrive all queues or delete resources. After planned failure injection, observe
real ALARM/routing/OK transitions and retain signed summaries before cleanup.

## Local validation and handoff

```bash
mkdir -p artifacts/rehearsal-tmp
export PYTHONPATH="$PWD/src"
export PYTHONDONTWRITEBYTECODE=1
export TMPDIR="$PWD/artifacts/rehearsal-tmp"
ruff check .
ruff format --check .
mypy src scripts/check_rehearsal_evidence.py scripts/run_rehearsal.py
pytest --basetemp="$PWD/artifacts/rehearsal-tmp/tests"
python -m pytest cloud_tests --basetemp="$PWD/artifacts/rehearsal-tmp/cloud"
cfn-lint infra/rehearsal/stack.json
```

The unit tests build synthetic signed fixtures in the local pytest temporary
directory. They test missing/skipped scenarios, stale target pins, canary and
unknown-field rejection, wrong trust sources, signature/hash/size errors,
revision/recovery/output mismatches and deterministic schema export. The HTTP
integration tests use real loopback sockets; the synthetic Runtime remains a
negative control. The stacked branch requires the actual Runtime module from PR #43. Its
integration test launches that module on an owned loopback socket; absence is a
test failure, with no optional sibling checkout or replacement provider. Successful
command/provider/recovery tests remain separate from these rejection probes.
No local command is evidence of actual browser/AWS or remote CI success.

Before publication, inspect the complete outgoing content/metadata with
the unchanged submission checker. Preserve exact base/head and dependency order,
list local versus live evidence separately, and leave Issue #31 open/Draft until
all required integrations and real observations are available.

### Earlier independent-slice verification record

Verified locally on macOS / Python 3.13.5 at main c132e4e before integration, with the new
files uncommitted. No AWS, live browser, remote CI or publication operation ran.

| Check | Observed result |
| --- | --- |
| Complete pytest suite with coverage | 1291 passed; 495 warnings; 88% overall coverage |
| New rehearsal tests within that suite | 122 passed; includes actual parent Runtime loopback rejection probes |
| Offline cloud suite | 12 passed |
| Golden manifests | 14 matched the independent fixtures |
| Ruff / format / mypy | Passed; mypy checked 100 source files including both scripts |
| Rehearsal CloudFormation | cfn-lint passed; nine alarms, one dashboard and one aggregate query |
| HTTP smoke | Passed |
| Existing local-service smoke | Failed at `scripts/local_service_smoke.py:164`: expects GET `/v1/review-jobs` 404; mounted route returns 405 |
| Submission gate | Passed complete snapshots/index/working content; zero outgoing commits; configured identity |

Warnings include existing SQLite connection ResourceWarnings and dependency
deprecations from Starlette/AnyIO and PDF bindings. The local-service assertion
and existing application files were preserved for parent integration review.
The parent Runtime default remained 503/unready throughout the five real TCP
probes; this is acceptance of rejection behavior only. The sibling Runtime's
uncommitted implementation and every live collector still require final exact-SHA
integration/CI and browser/AWS acceptance before Ready.

### Integrated verification scope

The stacked delivery inherits the corrected local-service smoke from #42: the
mounted jobs collection returns GET 405 and a valid unconfigured POST returns
503. The earlier 404 assertion failure above is retained as historical evidence,
not the final result. Final commands, warnings, exact head and CI are recorded
in the PR and [Issue 31 delivery](issue-31-delivery.md). Existing contracts,
goldens, submission policy and assertion strength remain unchanged.
