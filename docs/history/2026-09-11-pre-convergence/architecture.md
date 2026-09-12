# Historical snapshot: architecture.md

Captured before documentation convergence on 2026-09-11. This preserves
the originating delivery claims, test scopes and unresolved decisions.
Statements about current branches, main, deployment or ownership below
are historical quotations, not the current integration status. No approval
status is upgraded by archiving this record. ADR links use the consolidated
registry; see [current project progress](../../project-progress.md).

# Architecture

## Integration view, 2026-09-11

```mermaid
flowchart LR
  subgraph Local
    P["Privacy UI · pending"]
  end
  subgraph Cloud["Cloud target · live pending"]
    D["C2 snapshots · merged"] --> E["Extraction · PR 42"]
    D --> J["Admission · PR 43"]
    J --> DB["DynamoDB · PR 43"]
    DB --> Q["Outbox / SQS · PR 43"]
    Q --> R["Runtime worker · PR 43"]
    R --> DB
    R -.-> B["Execution bundle · pending"]
    E -.-> B
    B -.-> H["Human tasks · pending"]
    B -.-> V["Review core · merged"]
    V -.-> W["Formal output · pending"]
    W -.-> A["Publication · pending"]
  end
  P -.->|Sanitized reference| D
  R --> S["Versioned result · PR 43"]
  Cloud -.-> C["Live collectors · pending"]
  C -.-> G["Evidence gate · this PR"]
```

Solid edges are implemented adapters, not a deployed topology. Dotted edges
require reviewed integration. The default Runtime has no execution bundle and
returns 503; a durable result is not an authorized published PDF. [Issue 30](../../issue-30-delivery.md)
records dependency heads, trust boundaries and separate local/container/live gates.
The evidence gate validates independently signed observations; it does not create
missing browser/AWS observations. The diagrams below retain earlier baseline detail and the larger target scope.

Snapshot: main `463880af3a6dc6aad2bfa6fdfc3bc267afc4d4a5` inspected on 2026-09-10,
including merged #15/#16/#19/#20/#33. M0 is merged and is not deployed.
[Service contracts](../../service-contracts.md) define the shared service-v1 DTOs.
[Document transfer](../../document-transfer.md) adds this branch's controlled C2 ingestion
and immutable source snapshots without changing those DTOs or mounting new HTTP routes.
[Project progress](../../project-progress.md) records the active work lines.

Local privacy review, exact export mapping and restore adapters are integrated from PR #37; browser and combined service acceptance are tracked in the integration delivery record. See [privacy review repairs](../../pr37-exact-export-coordination.md).

## Current program structure

Solid lines are implemented calls/data flow. Orange nodes highlight the merged M0
integration. These are local components, not deployed
Snapshot: main `c3687e0cfe16cee0d38fcb4c1780a6092565aaeb` inspected on 2026-09-09,
including merged #15/#16/#19/#20. Issue #17 Phases 1–6 add exact contracts, pure policy,
controlled selectors, an explicitly composed single-decision coordinator and a bounded
local runner with a non-durable trace, and a local human-task/correction/re-entry
service. Its repository provides process-local transactions only. These components
are not wired into legacy endpoints and do not provide durable storage.
Current and target views below are deliberately separate.
[Service contracts](../../service-contracts.md) is the authority for shared DTOs and ports.

## Current program structure

Solid lines are implemented calls/data flow. These are local components, not deployed
AWS infrastructure. Prepared material may originate from real/native candidate
adapters or the bounded injected Bedrock adapter; M0 tests make no model calls.

```mermaid
flowchart TD
    DOC["Allowlisted PDFs: criteria, forms, registered references"] --> PREP["Document CLI: parser and candidate preparation"]
    PREP --> HUMAN["Local OS reviewer: inspect, confirm, separately approve"]
    HUMAN --> MAT["Prepared material and exact signed receipt"]
    MAT --> CONFIG["M0 operator-owned configuration and immutable material copy"]
    DOC --> CONFIG
    CONFIG --> FACTORY["M0 configured controller factory"]
    HTTP["HTTP /v1/reviews"] --> ENTRY["execute_review"]
    INVOKE["Invocation adapter"] --> ENTRY
    FACADE["M0 local run facade"] --> ENTRY
    FACTORY --> ENTRY
    ENTRY --> CORE["ReviewAgentController: reparse pinned sources"]
    CORE --> REVIEW["CaseReviewer, deterministic factors and independent verification"]
    REVIEW --> GATE{"Complete and verified?"}
    GATE -->|No| FIND["Findings, blockers, no writer"]
    GATE -->|Yes, no output| VERIFIED["verified / not_requested"]
    GATE -->|Output requested| CONTEXT{"Single supported context?"}
    CONTEXT -->|No| UNSUPPORTED["verified / unsupported_contexts"]
    CONTEXT -->|Yes| WRITER["PDFWriter port"]
    WRITER -->|Not configured| UNAVAILABLE["verified / unavailable"]
    WRITER -->|Explicit test double| SIMULATED["verified / simulated; no PDF"]
    WRITER -->|Configured M0 local policy| REAL["LocalPDFWriter: preflight, write copy, reopen, atomically publish"]
    REAL -->|Validated real result| COMPLETED["completed / written"]
    COMPLETED --> MANIFEST["M0 local facade: current-run write evidence, byte hash and exact field/context manifest"]
    CORE --> AUDIT["Existing domain audit events / injected audit logger"]
```

The configured factory injects real LocalPDFParser, MaterialProvider,
LocalApprovalStore and optional confined LocalPDFWriter through existing
ReviewAdapters/build_controller. No second calculation path exists. The service
facade calls the same entry execution; HTTP/invocation keep AgentReviewRun and
EntryProblem. Local service-v1 is a separate boundary, not a hidden v1 migration.
Legacy /health and /v1/validate remain available without a configured review service.

Configuration is explicit and lazy. Importing modules constructs no cloud clients
and reads no documents/fonts. Review-only configuration needs no writer font.
The real writer requires a hash-bound trusted template, full field-map digest,
explicit font and confined output directory. The parser rechecks its allowlisted
source/version/hash and Controller matches the selected criteria/forms to reviewed
material. There is no production synthetic fallback.

Receipt eligibility is distinct from complete review success. Human confirmation
binds an exact side, retains raw confidence and does not approve material. The
Linux/macOS local store verifies the actual OS reviewer and private signed receipt.
M0 revision copies clear confirmations and change the precise case version, making
old receipts unusable. B's future API must derive identity from a trusted adapter,
not request body roles. Windows native reviewer and multi-user login are absent.

The separate Phase 6 `HumanTaskService` creates tasks from stored findings and uses
`LocalReviewerPrincipalResolver` for explicit local identity, case scope and permissions.
Its repository atomically admits a response, consumes the task, appends a changed revision
and records new local review work under one process-local lock. `MaterialSubjectMap`
resolves the exact configured context/factor/side; corrections preserve original values,
source anchors and measured confidence. Correction and evidence supply require subsequent
confirmation/approval where applicable. Re-entry calls the existing `CaseReviewer`.
The adapter loses state at process exit and provides no leases or outbox. See
[local human review](../../local-human-review.md) and [ADR 0020](../../adr/0020-local-human-task-transactions.md).

The review core handles all comparison contexts. The writer still takes one
FactorReviewResult; no selecting the first comparison. It checks exact field value
refs, pages, bounds, occupancy, font coverage, overflow, correction integrity,
template and full map hashes, protected source URI aliases and final output.
The merged S3 wrapper injects a client and uploads only after local validation;
local/mock tests are not live S3 or Runtime wiring. A local material snapshot and
repeated byte-hash checks are not a continuous immutable source snapshot.

## Target AWS integration (future B/D/A/E work)

Every box in this diagram is a target service or integration duty. Dashed borders
mark pending wiring/deployment, including services with existing adapter or IaC
preparation. No arrow asserts a deployed pipeline.

```mermaid
flowchart TD
    UI["C browser review workbench"] --> API["B/D authenticated API: case permissions and authorized IDs"]
    API --> TRANSFER["D controlled upload/download authorization"]
    TRANSFER --> S3IN["Private versioned S3 documents and templates"]
    UI --> LOCALPRIVACY["Trusted local scan, human review, sanitization and export confirmation"]
    LOCALPRIVACY -->|Verified sanitized payload only| TRANSFER
    API --> DB["D DynamoDB: jobs, revisions, human tasks, outbox, manifests"]
    DB --> PUBLISHER["D outbox publisher"]
    PUBLISHER --> QUEUE["SQS work queue"]
    QUEUE --> DISPATCH["D dispatcher: conditional claim and invocation"]
    QUEUE --> DLQ["DLQ and bounded manual recovery"]
    DISPATCH --> RUNTIME["AgentCore Runtime: one attempt and fenced lease"]
    RUNTIME --> RESOLVER["D authorized document resolver: version and hash"]
    RESOLVER --> S3IN
    RUNTIME --> POLICY["A allowed-action policy and actual decision events"]
    POLICY --> MODEL["Bedrock extraction / bounded action proposal"]
    MODEL -->|Untrusted proposals| POLICY
    POLICY --> CORE["Existing review core and deterministic gate"]
    CORE -->|Needs human: findings retained| TASKS["B persist human task, finish attempt, release resources"]
    TASKS --> DB
    API -->|Authorized task response| REVISE["B new revision and required confirmation / approvals"]
    REVISE -->|New run and outbox; no old lease revival| DB
    CORE -->|Verified complete output scope| PDF["E trusted PDF template/map/font and validated writer"]
    PDF --> S3OUT["D attempt-specific S3 artifact"]
    S3OUT --> COMMIT["D human publication authority + fenced conditional manifest/result publication"]
    COMMIT --> DB
    DB --> QUERY["B/D authorized status, findings and manifest queries"]
    QUERY --> UI
    QUERY -->|Authorized manifest references only| TRANSFER
    TRANSFER --> S3OUT
    RECOVERY["D reconciler: expired lease, lost invocation and outbox recovery"] --> DB
    RECOVERY --> PUBLISHER
    DLQ --> RECOVERY
    RUNTIME -->|Lease heartbeat and terminal state| DB
    RUNTIME --> AUDIT["Version-bound domain audit and decision event storage"]
    RUNTIME --> LOGS["CloudWatch operational health and alarms"]
    classDef planned fill:#f3f3f3,stroke:#666,stroke-dasharray:5 5
    class UI,API,TRANSFER,LOCALPRIVACY,S3IN,DB,PUBLISHER,QUEUE,DISPATCH,DLQ,RUNTIME,RESOLVER,POLICY,MODEL,CORE,TASKS,REVISE,PDF,S3OUT,COMMIT,QUERY,RECOVERY,AUDIT,LOGS planned
```

API Gateway/Lambda/FastAPI is the planned API hosting arrangement. D resolves
storage locations only after authorizing case/document IDs and pinned versions.
A supplies the trusted model/system origin for proposal admission; a body actor
cannot change its budget class. A principal-scoped idempotency key identifies one
canonical payload. The API
persists queued job plus outbox before dispatch; publisher/recovery closes the
DB-to-SQS gap. SQS acceptance is responsibility transfer, not completed review.

Run, attempt and Runtime session have different identities. Runtime acquires a
conditional lease, heartbeats and writes attempt-specific artifacts; only a current
fencing token/result-version CAS can publish a manifest. Recovery reconciles expired
leases, bounded retries and lost acknowledgements. Stale attempts cannot overwrite
new results. HTTP contract/health preparation is in [cloud_tests](../../../cloud_tests/README.md);
its durable=false session store does not provide any of these guarantees.

Human needs_review persists findings/tasks and ends the attempt. C obtains task
versions/evidence; B authenticates response, rejects stale/conflicting commands and
appends new material with necessary approvals. A subsequent run uses the new revision.
It never waits indefinitely in Runtime, reuses obsolete authority or treats business
needs_review as an infrastructure retry. Bedrock never grants approval or writes
final fields. CloudWatch provides operational telemetry, not domain audit. Runtime
is compute, not the durable state store.

## Preparation versus integration gaps

| Existing component | Remaining work / steward |
| --- | --- |
| infra/cdk private input/result buckets and Cases table definition | D jobs/tasks/outbox/lease model, API/IAM/queues/runtime/publisher/recovery and actual deployment |
| cloud_tests HTTP/container/CloudFormation synthetic smoke | D explicit identity preflight, build/live acceptance/cleanup; full document assembly separately |
| Injected Converse adapters, pure policy/selectors, single-decision coordinator and bounded runner | Comparison against E independent goldens; D durable trace/storage |
| Local real parser/material/approval/writer factory | B web identity/tasks/revisions API; D Runtime adapter wiring |
| Single-context local PDF and injected S3 wrapper | E formal template/font/maps and versioned multiple-context contract; D live transfer/manifests |
| M0 DTOs/guards and Issue #17 policy/selectors/coordinator/bounded runner/non-durable trace | D durable trace/repository integration; explicit application assembly |
| Local human-task service, POSIX principal and process-local task/revision/re-entry transaction | B authenticated task API and C UI; D durable transaction/outbox/recovery |

M0 requires no AWS calls. Later preflight uses designated profile/SSO, Region,
expected account/role and model availability; never chat-delivered access keys.
MCP, a vector database and another orchestration framework are not required here.

## Local privacy boundary

This flow describes callable local components and explicitly pending consumer
connections. No remote adapter or UI is installed by the privacy work. Ordinary
service-v1 review and its business completion gates remain unchanged.

```mermaid
flowchart LR
    ORIGINAL["Owned local original"] --> SCAN["Isolated PDF scan and configured local OCR"]
    SCAN --> REVIEW["Versioned review SDK and trusted human source approval"]
    REVIEW --> BUILD["Raster rebuild and independent structure/pixel/OCR verification"]
    BUILD --> CONFIRM["Exact immutable export payload and human confirmation port"]
    CONFIRM --> GATE["Live source approval and verifier recheck"]
    GATE --> SINK["Trusted sink port; tests use a local sink"]
    SINK -.-> CLOUD["Pending cloud consumer and authenticated publisher"]
    BUILD --> MAP["Local AEAD mapping service; Linux storage and key provisioning pending acceptance"]
    CLOUD -.-> REFILL["Local refill with exact approved fields and publisher authority"]
    MAP --> REFILL
    ORIGINAL --> REFILL
    REFILL --> FINAL["New local final PDF; no upload or business completion authority"]
```

The map must bind the manifest of the actual confirmed export. Rebuilding a bundle
issues new occurrence IDs even when visible content is unchanged. A future
application composition must persist the mapping for that exact payload before
its network sink accepts responsibility. Independently building a mapping and
then calling a gate that builds another bundle is not a valid composition.
The current ports and stage tests do not provide a durable end-to-end coordinator.

Originals, previews, source hashes, review commands, maps, key references and
refilled PDFs remain local. The export payload contains only sanitized PDF bytes,
public manifest JSON, a fixed filename and optional locally sanitized, explicitly
reviewed text. Known text replacement is not a general detector of newly typed
private facts. The trusted human adapter must review all outgoing text.

Scan, sanitizer and refill workers use bounded local subprocesses and Python
network denial before their target imports. Linux network namespace, real OCR,
private storage and actual terminal tests remain acceptance requirements; Windows
does not provide equivalent evidence. Synthetic SDK/telemetry probes are not live
AWS isolation tests. Existing S3 upload paths remain outside the privacy gate and
must not process sensitive cases until #27/#31 integration is accepted.

Local contracts and ports are under `domain/privacy_*.py`, `ports/privacy*.py` and
`application/privacy_*.py`; implementation adapters are under
`adapters/local/privacy/`. [ADRs 0032-0038](../../privacy-contracts.md) and the
[stage runbooks](../../issue-22-acceptance.md) describe their individual trust boundaries.

## Business-core module map

| Location | Authority |
| --- | --- |
| application/bootstrap.py, entrypoint.py, controller.py | Shared composition/execution and existing review decisions |
| domain/case_review.py, verification.py, review_contracts.py, confidence.py | Deterministic review, independent gates, source/cell and confidence contracts |
| domain/service_contracts.py, application/revisions.py, service_guards.py | Shared wire projections, immutable copies, exact controlled-action contracts and pure admission checks |
| application/action_policy.py | Trusted state/prerequisite/source/budget derivation of versioned allowed actions; no I/O or tool execution |
| ports/action_selection.py, adapters/local/action_selector.py | Provider-neutral selector boundary and deterministic no-model baseline |
| adapters/aws/action_selector.py | Injected Converse selector, strict structured output, bounded provider retry and sanitized failures |
| docs/prompts/controlled-action-selector-v1.md | Versioned selector prompt and model trust boundary |
| application/controlled_workflow.py, ports/controlled_workflow.py | One-decision re-admission, exact one-tool routing and executor/snapshot/trace ports |
| application/bounded_workflow.py | Executable budget ledger, stable failure classes, deterministic retry/backoff, no-progress fingerprint and non-persisted human handoff |
| adapters/local/decision_trace.py | Explicitly non-durable, detached in-memory causal trace for tests/local composition |
| application/human_tasks.py, application/material_corrections.py | Purpose-specific tasks, explicit side changes and full deterministic re-entry |
| adapters/local/human_tasks.py, adapters/local/task_principal.py | Non-durable atomic task/revision/replay/re-entry and configured POSIX principal |
| docs/local-human-review.md, docs/adr/0020-local-human-task-transactions.md | Local human response operation, authority and transaction limits |
| docs/adr/0018-controlled-action-policy.md | Issue #17 action authority, causal trace and pause/re-entry decision |
| ports/service.py | Trusted principal and transactional task ports; durable document/revision/job implementations reserved |
| adapters/local/service.py, local_service.py | M0 actual configured local assembly and separate envelope |
| document_cli.py, adapters/local/document_manifest.py | Preparation and compatible allowlist manifest imports |
| domain/pdf_models.py, pdf_types.py, ports/pdf.py | Sole PDF request/result/error and writer protocol |
| adapters/local/pdf_writer.py, pdf_overlay.py, pdf_config.py | Local deterministic writing and trusted policy |
| adapters/aws/pdf/s3_pdf_writer.py, storage/s3_object_store.py | Injected-client S3 wrapper; no automatic clients |
| adapters/aws/agentcore/runtime.py | Existing invocation contract |
| schemas/service-v1.json, examples/service-v1/ | Shared consumer schema and illustrative fixtures |

See [milestones](../../mvp-plan.md), [local acceptance](../../local-service-runbook.md),
[traceability](../../delivery-traceability.md) and [ADR 0013](../../adr/0013-service-foundation.md).
