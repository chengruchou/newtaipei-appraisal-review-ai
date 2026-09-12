# ADR 0043: Shared physical model dispatch

Status: Accepted for implementation; competition execution remains pending approval.

## Context

The competition environment rules, version 20260722, page 1, Bedrock rule 1,
limit requests below one per second. The source PDF SHA-256 is
`64bbda4d8056d3edd913ced8e96330f282621a00fe9d4152341d162fd385aec0`.
The configured 1.1-second interval is a conservative implementation choice,
not a quotation of the rule. CountTokens and control-plane counting remain
unconfirmed, so they consume the same team-wide dispatch interval. The service
allowlist does not establish resource permission or model/data approval.

## Decision

`DispatchStore` has an atomic exclusive owner per team scope. The central
DynamoDB implementation uses one regional table with string partition key `pk`
and conditional owner updates. Every admitted participant, region and model
must use that same table and scope. A global table or separate regional tables
are not a valid shared coordinator. SQLite supports processes on a single host
only and cannot establish coordination across separate AWS machines.

After acquiring ownership, the sender waits at least 1.1 seconds measured with
its monotonic clock, while retaining the exclusive owner. It then checks the
same deadline, cancellation signal, lease/fence authority and attempt allowance
before entering the physical transport. Ownership remains held until that
transport returns or raises. The next owner also waits a complete interval.
No stored wall-clock timestamp or cross-host clock-synchronization assumption
can shorten the gap. This also intentionally spaces the first request and
reduces throughput for slow responses. CPU, local OCR and PDF work can remain
parallel outside this network boundary.

A guard is scoped to the actual attempt. It polls cancellation and authority
while waiting, includes waiting in the caller deadline, and checks them again
after data admission. Existing extraction calls/tokens are conservatively
reserved before waiting and are not refunded by cancellation or reconstruction.
The controlled coordinator supplies its current ledger owner check to the SDK
thread. The existing RuntimeWorker binds current uncancelled durable job/run/
attempt, result version, worker deadline and the last acknowledged lease expiry.
IntegratedWorkflowExecution additionally rechecks the current principal, exact
snapshot and its configured durable lease/owner/fence guard. SnapshotBoundExecution
binds current directory grants and source authorization around its delegate.
`dispatch_async_authority` invokes these checks on their owning event loop from
the SDK thread, restores the outer context on exit and revokes lingering shielded
workers when execution ends. Every nested authority must remain valid.

`install_bedrock_dispatch` wraps the stock botocore `URLLib3Session.send`
transport before the client is shared. Other transport classes are rejected,
because they might retry internally. The stock transport disables urllib3
retries; botocore retries re-enter the wrapper. Normal factories configure
`total_max_attempts=1`. Each extractor/selector inference attempt permits only
one physical send, so an injected SDK retry cannot silently spend unreserved
model-call or token budget. Separately authorized retry attempts reacquire and
wait again. CountTokens receives its own one-send guard while sharing the
original deadline, cancellation and authority.

The private transport seam is deliberately isolated in one adapter and must be
retested against the installed SDK whenever dependency or image inputs change.
The wrapper holds the reservation across non-streaming response-body reading.
It does not retry an unknown store write or unknown release. Store I/O failures
fail closed; there is no fallback to an in-process lock or separate scope.

## Exact competition data admission

Every actual transport attempt, including a retry, requires an injected
`CompetitionDataAdmission`. It checks these immutable parts after the interval:

| Part | Surface | Exact content |
| --- | --- | --- |
| `bedrock.request.body` | `model_prompt` | Serialized SDK body bytes, including encoded image/document/messages |
| `bedrock.request.url` | `metadata` | UTF-8 serialized URL, including model/resource identifier and query |
| `bedrock.request.headers` | `metadata` | Sorted supported non-authentication header names and exact value bytes encoded as hex |
| `bedrock.request.operation` | `metadata` | Canonical JSON with original SDK service, operation, HTTP method and complete serialized URL |

An absent GET body is represented by empty bytes. Mutable buffers, strings and
file-like bodies are rejected rather than read or replayed speculatively. The
checked request copy uses the same immutable method, body, URL and frozen header map
when sent. Unknown metadata headers fail closed. SDK invocation/attempt metadata
is included, so a retry's changed envelope needs current admission too. URLs must
retain the configured SDK endpoint authority and scheme, have no credentials or
fragment, and use HTTPS. Localhost tests explicitly opt into loopback HTTP. Body data
and URLs never enter diagnostics or logs. The admission port must validate the
complete envelope against the exact digest-bound, currently authorized review;
redaction or an operator flag alone cannot grant admission. SDK authentication
headers are transport credentials, not case content, and are not copied into
review data. The existing data-admission policy governs every case-derived
surface before it reaches this adapter.

The shared `competition_wire` helper captures the original serialized operation
through a first `before-call` handler on the newly constructed client. It checks
the SDK schema's HTTP method and binds the complete serialized URL. Immediately
before dispatch, the final request must retain that method, path, query and
destination. A later event handler cannot change a permitted control read into
an invocation or move a request to a different resource. This assumes the
factory controls client construction and approved event handlers; it is not a
sandbox for arbitrary plugins that alter serialization before capture, replace
the installed guard or modify SDK internals. The factory and Bedrock installer
reuse the same client-local binding and transport, without double wrapping.

Missing admission fails closed for a real SDK transport. Unit-test fakes and
the explicit localhost fixture authority demonstrate mechanics only; they are
not competition admission authorities, hosted services or approved model calls.

## Coverage and composition inventory

| Entry point | Physical-send policy |
| --- | --- |
| `BedrockDocumentExtractor` | Requires installed transport for a real SDK client; `extraction_execution` binds attempt guards |
| `BedrockActionSelector` | Requires installed transport; current run owner, cancellation and deadline reach its SDK thread |
| `BedrockSnapshotBackend` | Preflight thread shares cancellation/deadline; each metadata call and inference is guarded |
| `WorkstationClients` | Real Bedrock construction requires an injected dispatcher; admission is mandatory before any send |
| `PricedClients` / `PricedRuntime` | CountTokens and Converse use the same dispatcher with separate attempt guards |
| `BedrockDispatchClients` | Wraps every Bedrock/Bedrock Runtime client from a trusted workload factory |
| document CLI / extraction smoke | Use the workstation factories; without approved dispatcher/admission composition they fail closed |
| controlled-workflow evaluation / local synthetic workbench | Synthetic injected clients; no real model traffic or team-wide acceptance claim |
| Runtime worker | Existing worker binds live durable job/attempt/cancel/deadline and acknowledged lease; integrated/source wrappers add exact principal/source and lease/fence checks |
| AgentCore invocation smoke / SQS bridge | Invoke the Runtime, not model inference directly; downstream model sends still require the same composition |
| external AWS CLI, notebooks, other SDKs or teammate tools | Not instrumented; cannot be listed as covered entry points or used for approved competition execution |

The competition profile remains the authority for exact account/role, region,
model destinations, IAM actions, entry-point inventory and limits. Construct
this dispatcher from its reviewed primitive scope/interval values and central
store; the dispatcher does not import the profile or grant approval. Pending
profiles, missing central stores and unknown external senders must remain
inadmissible. No live resources are created by these adapters.

## Crash quarantine and operator reconciliation

An owner has no TTL and cannot be stolen on lease expiry. A process crash after
acquisition leaves the scope blocked across process restart. A caller whose
own guard expires exits without sending. This favors safety over availability;
a delayed process must never awaken after another owner has taken its slot.

Recovery is a deliberate operator procedure:

1. Stop every producer and trigger in the profile inventory. Stop the actual
   owner process/container and verify it cannot resume; an expired application
   lease alone is insufficient. Revoke/reconcile unknown provider results.
2. Read the central row for `pk=model-dispatch:<scope>` with a strongly consistent
   read and record its exact owner token with the shutdown/reconciliation evidence.
3. Only after step 1 is proved, invoke the store's conditional `release(scope,
   exact_owner)` operation. A stale token must fail; never delete the row or table,
   expire it, clear all locks, or reset workflow/job budgets as a recovery shortcut.
4. Restart the approved producers against the same store and scope. Their next
   request acquires a fresh owner and waits a full monotonic interval again.

There is no automatic recovery command because remote process termination and
unknown provider outcomes cannot be established from a lock timestamp. The
profile's compute/trigger shutdown procedure must supply that external evidence.
This procedure neither deletes result artifacts nor replenishes reserved budget.

## Validation and limits

`tests/unit/test_model_dispatch.py` records real boto3 HTTP arrival times at a
localhost server. Coverage includes multiple models, threads, subprocesses,
normal restart, CountTokens, metadata, fast responses, throttling and transport
retries, implicit-retry budget rejection, cancellation, deadline and authority
revocation, admission on every retry, method/path/query mutation rejection,
immutable body handling and missing
admission. `tests/unit/test_runtime_model_dispatch.py` preserves the independent
real RuntimeWorker/IntegratedWorkflowExecution counterexample for durable cancel,
replacement fence, lease expiry and principal revocation during a shared wait;
no subsequent physical request is allowed. Moto exercises concurrent DynamoDB conditional acquisition, exact
owner release and restart fencing. These are local SDK/conditional-store tests;
they do not prove live DynamoDB IAM, multi-machine networking, competition
permissions, model quality or coverage of tools outside this repository.

References: [Boto3 retries](https://docs.aws.amazon.com/boto3/latest/guide/retries.html),
[DynamoDB UpdateItem](https://docs.aws.amazon.com/amazondynamodb/latest/APIReference/API_UpdateItem.html),
[botocore transport source](https://github.com/boto/botocore/blob/develop/botocore/httpsession.py).
