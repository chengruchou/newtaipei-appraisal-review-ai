# Delivery plan and ownership

The project delivers auditable appraisal review and assisted filling. Reading
PDFs and drawing output are stages; observed-value verification is essential.
The closed parent #3 remains history, not an active catch-all milestone.

Snapshot: 2026-09-06, after PR #15 and PR #16 merged. Use the merged `main`
as the integration base; exact merge SHAs are in [traceability](delivery-traceability.md).
The table distinguishes code delivery from acceptance with real documents and AWS.

| Stage | Deliverable | Dependencies | Implementation / remaining work |
|---|---|---|---|
| Merged | Shared PDF contract #6, PR #10 | Shared domain types | Models, ports, metadata and contract tests; no rendering |
| Merged | Member A #4, PR #13 | #6 | Composition, sync HTTP, invocation, compatible errors and submission checks |
| Merged | Complete review #8, PR #15 | Shared entry and typed material | Independent verification, original cells, validated fills, arithmetic and coverage; full-case human acceptance pending |
| Merged | Chinese extraction #7, PR #16 | #8 source/review contracts | Real parser, native/Bedrock candidates and local reviewer controls; live model and complete goldens pending |
| Preparation merged | Runtime smoke, PR #14 | Shared entry and synthetic adapters | HTTP/container/templates; live build, invocation and cleanup pending |
| Planned | Formal PDF writer #5 | Merged PDF contract and output gate | Drawing, corrections, immutable source handling, fonts and atomic storage |
| Planned | Durable AWS integration #9 | Shared entry; #5 for real artifacts | Authorized jobs APIs, persistent state, dispatch, recovery and cloud acceptance |
| Planned | Controlled actions and human tasks #17 | #7/#8 and shared task/trace contracts | Allowed actions, bounded model choices, decision records and reviewer responses |
| Planned | Web review workbench | Human-task API and authorized source access | Evidence inspection, corrections, approvals and output download; new work item required |

The original foundation SHA was e24265d7c4ef0bb5d2d74d0ed94bffa2052a08ed;
[branch migration](pr-migration.md) records historical stacked delivery. New work
should use merged `main`, rather than an obsolete stacked branch. The public
PDFWriter contract is unchanged by #15/#16. Coordinate shared changes through #6
and preserve the independent verification and audit boundaries. The initial A/B
verifier freeze was superseded only by the explicit #8 verifier work. Human
review remains required before merging subsequent PRs.

## Acceptance stages

1. Foundation: schema/URI/placement/lookup and JSON tests, one writer protocol,
   warnings/errors preserved, compatibility imports. No rendering claims.
2. A: real HTTP and invocation parity, validation before invocation, explicit
   missing config, one call, failed/needs-review gates, typed PDF failure cases,
   no-credential imports and a runnable clearly synthetic demonstration.
3. B: original hash unchanged, multiple page dimensions/rotation/CropBox,
   glyph/overflow checks, blank-fill vs annotation vs genuine correction,
   readable output and atomic publication, injected mocked S3 transfer.
4. Extraction/full review: original-file checks with source hashes/pages,
   reviewer-approved candidate rules, observed/expected differences, precise
   identities, sums and copied values, coverage and conservative completion.
5. Cloud: independent execution/business states, authenticated document IDs,
   durable outbox/idempotency/lease recovery, bounded retries/DLQ, final manifest,
   live evidence and cleanup. An entry-only synthetic cloud smoke can precede
   document integration; it does not satisfy full cloud review acceptance.

The five initial factors (main road, average road width, drainage, terrain,
market proximity) remain a representative slice. Their success cannot approve
all other factors or empty comparison columns.

## Parallel workstreams

This is a proposed five-person split, not an assignment of repository members.
The earlier planning assumption that #5 was complete is not the current code
status: #5 remains open and no formal writer is present in the inspected `main`.
Writer delivery is a separate prerequisite for acceptance of real output PDFs.
API contracts, local policy, frontend mocks and evaluation can proceed meanwhile.

| Owner slot | Workstream | Primary boundary | Reviewable acceptance |
|---|---|---|---|
| A | Workflow policy and decision records (#17) | Allowed-action policy, typed tools and trace events | Invalid transitions rejected; bounded retries; actual action/evidence history; model cannot approve or publish |
| B | Human-task and revision API (#17, coordinate #9) | Task DTOs, reviewer permissions, revision/approval commands | Stale responses rejected; originals preserved; distinct confirmation, approval and publication events |
| C | Web workbench (new work item) | UI built against agreed OpenAPI and fixtures | Source-page inspection, blocker list, editable proposals, explicit human actions and honest artifact status |
| D | AWS jobs and persistence (#9) | Authorized uploads, job/outbox/lease store and deployment | Duplicate delivery, interrupted attempts and delayed human responses recover; live evidence and cleanup |
| E | Real-model evaluation and case acceptance (#7/#8/#17) | Versioned goldens, regression fixtures and evaluation reports | Per-field/source accuracy, unsupported cases, human corrections, latency/cost and complete-case outcomes reported separately |

Before parallel implementation, agree on one shared versioned schema for case/run,
document identity, material revision, human task, decision event and artifact
manifest. Assign one owner per shared contract; consumers use fixtures from that
contract. Keep task schemas in B's workstream and durable storage in D's workstream,
with A consuming the task port and C consuming the HTTP API.

Integrate in this order: local policy/task behavior with synthetic fixtures;
workbench against the actual task API; durable cloud jobs and human handoff;
then real-model, formal-writer and complete-case acceptance. A human wait must
persist a task and release execution resources. A response produces a new version
and the required reauthorization, not a flag that bypasses the existing review gate.

## Source checks performed on 2026-09-05

The official brief was read without modification. The supplied criteria have
9 pages; forms have 6 pages, including portrait/landscape A4 and landscape A3,
with no AcroForm widgets. Source thresholds and matrices confirm regional main
road 18m -> normal; individual front road 18m vs 6m -> +5%. The forms copy the
regional total into the comparison form; blank comparable columns remain blank.
A suspicious mixed-unit interval appears in the criteria and must remain
unresolved until confirmed. These are read-only source observations, not passing
extraction tests or permission to auto-approve rules.

#7/#8 acceptance includes those cases, endpoints, reverse matrix direction,
contradictory source text, empty comparable columns and mixed page dimensions.
Only synthetic fixtures enter Git. No source PDFs or OCR dumps are committed.

## Integration resources

The organizer supplies the intended AWS services; implementation planning is
not deferred on speculative service availability. Specific account access,
Region, model capabilities and actual quotas must still be verified. See
[architecture](architecture.md), [traceability](delivery-traceability.md),
[local runbook](member-a-runbook.md), and [cloud smoke plan](aws-smoke-plan.md).
