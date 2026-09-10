# Architecture

## Integration view, 2026-09-11

```mermaid
flowchart LR
  subgraph Local
    P["Privacy UI · pending"]
  end
  subgraph Cloud["Cloud target · live pending"]
    D["C2 snapshots · merged"] --> E["Extraction · PR 42"]
    D --> J["Admission · this PR"]
    J --> DB["DynamoDB · this PR"]
    DB --> Q["Outbox / SQS · this PR"]
    Q --> R["Runtime worker · this PR"]
    R --> DB
    R -.-> B["Execution bundle · pending"]
    E -.-> B
    B -.-> H["Human tasks · pending"]
    B -.-> V["Review core · merged"]
    V -.-> W["Formal output · pending"]
    W -.-> A["Publication · pending"]
  end
  P -.->|Sanitized reference| D
  R --> S["Versioned result · this PR"]
```

Solid edges are implemented adapters, not a deployed topology. Dotted edges
require reviewed integration. The default Runtime has no execution bundle and
returns 503; a durable result is not an authorized published PDF. [Issue 30](issue-30-delivery.md)
records dependency heads, trust boundaries and separate local/container/live gates.
The diagrams below retain earlier baseline detail and the larger target scope.

Snapshot: main `463880af3a6dc6aad2bfa6fdfc3bc267afc4d4a5` inspected on 2026-09-10,
including merged #15/#16/#19/#20/#33. M0 is merged and is not deployed.
[Service contracts](service-contracts.md) define the shared service-v1 DTOs.
[Document transfer](document-transfer.md) adds this branch's controlled C2 ingestion
and immutable source snapshots without changing those DTOs or mounting new HTTP routes.
[Project progress](project-progress.md) records the active work lines.

## Current program structure

Solid lines are implemented calls/data flow. Orange nodes highlight the merged M0
integration. These are local components, not deployed
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
    classDef m0 fill:#fff0cc,stroke:#8b6508
    class CONFIG,FACTORY,FACADE,MANIFEST m0
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
    UI -->|Authorized transfer only| S3IN
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
    class UI,API,TRANSFER,S3IN,DB,PUBLISHER,QUEUE,DISPATCH,DLQ,RUNTIME,RESOLVER,POLICY,MODEL,CORE,TASKS,REVISE,PDF,S3OUT,COMMIT,QUERY,RECOVERY,AUDIT,LOGS planned
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
new results. HTTP contract/health preparation is in [cloud_tests](../cloud_tests/README.md);
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
| Bounded injected Converse extraction adapter | A permitted action selection, model/prompt trace and comparison against E independent goldens |
| Local real parser/material/approval/writer factory | B web identity/tasks/revisions API; D Runtime adapter wiring |
| Single-context local PDF and injected S3 wrapper | E formal template/font/maps and versioned multiple-context contract; D live transfer/manifests |
| M0 service DTOs, schema/fixtures and pure guards | B/D transactional repositories; C UI; A execution-time policy/event producer |

M0 requires no AWS calls. Later preflight uses designated profile/SSO, Region,
expected account/role and model availability; never chat-delivered access keys.
MCP, a vector database and another orchestration framework are not required here.

## Module map

| Location | Authority |
| --- | --- |
| application/bootstrap.py, entrypoint.py, controller.py | Shared composition/execution and existing review decisions |
| domain/case_review.py, verification.py, review_contracts.py, confidence.py | Deterministic review, independent gates, source/cell and confidence contracts |
| domain/service_contracts.py, application/revisions.py, service_guards.py | M0 shared wire projections, immutable copies and pure checks |
| ports/service.py | Reserved trusted principal/document/revision/task/job adapters |
| adapters/local/service.py, local_service.py | M0 actual configured local assembly and separate envelope |
| document_cli.py, adapters/local/document_manifest.py | Preparation and compatible allowlist manifest imports |
| domain/pdf_models.py, pdf_types.py, ports/pdf.py | Sole PDF request/result/error and writer protocol |
| adapters/local/pdf_writer.py, pdf_overlay.py, pdf_config.py | Local deterministic writing and trusted policy |
| adapters/aws/pdf/s3_pdf_writer.py, storage/s3_object_store.py | Injected-client S3 wrapper; no automatic clients |
| adapters/aws/agentcore/runtime.py | Existing invocation contract |
| schemas/service-v1.json, examples/service-v1/ | Shared consumer schema and illustrative fixtures |

See [milestones](mvp-plan.md), [local acceptance](local-service-runbook.md),
[traceability](delivery-traceability.md) and [ADR 0013](adr/0013-service-foundation.md).
