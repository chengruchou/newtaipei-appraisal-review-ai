# Architecture

Issue 22 supplement, 2026-09-10: local privacy phases 1-7 are implemented in the
uncommitted `feat/local-privacy-pipeline` working tree based on
`8591bddc76584ad630774f214c7452c397c937d5`. This is not a deployed or fully accepted
pipeline. See [privacy acceptance and handoff](issue-22-acceptance.md). The older
business-core snapshot below remains historical evidence; cloud transfers must
also satisfy the local privacy boundary described here.

Snapshot: main `ea55043d90aa21e6f0a7e3fe05aa34ef8a3553d3` inspected on 2026-09-07,
including merged #15/#16/#19, plus M0 review-branch work on
`feat/shared-service-contracts`. M0 is not deployed or merged. Current and target
views below are deliberately separate. [Service contracts](service-contracts.md)
is the authority for all new shared DTOs and ports.

## Current program structure

Solid lines are implemented calls/data flow. Orange nodes are this working-branch
integration; other nodes are merged core. These are local components, not deployed
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
`adapters/local/privacy/`. [ADRs 0014-0020](privacy-contracts.md) and the
[stage runbooks](issue-22-acceptance.md) describe their individual trust boundaries.

## Business-core module map

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
