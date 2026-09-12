# Member B contract and integration handoff

Date: 2026-09-12. Status: concrete proposal for A; shared contracts are not changed.
Implementation baseline: `774ff6cba719e0235d95a103972f80293d46fdf3`.
Local work branch: `fix/case-response-transactions`; no commit or remote publication.

## Delivered within the existing contract

- Human response replay checks current task permission and human actor before lookup.
- Corrections retain measured confidence exactly, including zero, independently of
  submitted confidence. Original evidence and prior revisions remain preserved.
- Existing receipt format, canonical subject naming, rejection and version admission
  are unchanged. The reference stores remain in-memory.
- See [ADR 0052](adr/0052-response-replay-authority-and-raw-confidence.md) and
  [the implementation plan](member-b-implementation-plan.md).

Second increment: optional SQLite job, human-task and result adapters now implement
the existing ports, with atomic task/revision/receipt/run/outbox persistence. They
do not change the shared DTOs or select A's runtime. See
[local storage composition](local-review-storage.md) and
[ADR 0053](adr/0053-local-review-transactions.md). The original in-memory adapters
remain available as reference implementations.

## Decisions needed from A before shared implementation

A owns `domain/*contracts.py`, shared factor models, ports, API routes and generated
clients under the current team plan. B owns application transaction/calculation
implementation and storage adapters. The field names below describe a proposed
mapping, not a second DTO family or permission to edit A's files.

### 1. Preserve original observations and represent manual adoption

Relevant types: `Reliability`, `FactorObservation`, `EvidencedPair`, `PublicValue`,
`ValueRevision`, `TaskSubjectView`, and their confirmation/material digests.

Proposed semantics:

| Component | Required content and authority |
| --- | --- |
| Original observation | Immutable raw text, original typed extraction, raw confidence, source file identity, citations/page/coordinates and extraction metadata |
| Adopted value | Typed value/unit, server-authored origin (including manual), originating response/operation reference, validated citations |
| Confirmation | Separate human assertion bound to the exact adopted input digest; does not rewrite extraction origin or score |
| Change ledger | Original, submitted proposal and actually adopted value remain distinct; actor comes from server context |

An explicit human correction assigns manual origin. A pure material revision must
not infer manual origin for arbitrary changed values. Native origin survives only
unchanged native lineage; system/model candidates cannot assert manual authority.
Confirmation changes confirmation state, not extraction origin. Current
`reviewer_confirmed` conflates those concepts and needs compatibility treatment.

Legacy records lack enough information to reconstruct every original observation.
Preserve existing snapshots and digests; do not relabel old model_proposed records
as manual without an authoritative human change ledger. A must choose a versioned
migration or compatibility projection with explicit unresolved legacy provenance.
B will update `RevisionSnapshot.revise` and the correction service after that decision.

### 2. Add canonical condition subjects

Keep the existing factor-side subject as one supported task subject. Add an explicit
condition subject alternative, with a server-generated subject ID and an allowlisted
field identifier. Proposed conditions: district, legal zoning, actual use,
rule-use category and effective date. Subject type and task purpose determine the
allowed correction type/unit; the client cannot select arbitrary material paths.

Bind command to task/version, case revision/material digest and condition input
digest. The server derives the actor. Preserve the existing idempotency key and
response receipt semantics. B applies the typed change, selects an applicable
bundle and invalidates old calculations/approval applicability in one transaction.
Unsupported combinations produce blockers. No fake factor ID or frontend boolean
may stand in for this task. Confirm whether no-rule selection commits a blocked
revision or rejects the change; recommend committing the truthful blocked revision.

### 3. Define a source-bound candidate operation

Proposed input mapping:

- Case and job identifiers, expected revision/material, and worker run/attempt/fence
  when the producer is a worker.
- Stable operation ID and server-calculated canonical payload digest.
- Bundle identifier/version and complete source/rule references with content hashes.
- Explicit subject roles and typed candidates/citations with unchanged extraction
  confidence; candidate producer identity comes from the trusted caller.

The system receipt must be a distinct type from `ResponseReceipt`. It identifies
the operation/digest, case, source bundle, resulting revision and created task IDs.
No human actor, confirmation or approval is invented. Exact replays return the same
receipt; changed payload conflicts. One batch cannot duplicate revision/task creation.

Distinguish committed success, known terminal rejection, and an unknown caller
outcome. A timeout is an unknown observation, not a successful or failed stored
transaction. Reconcile by exact operation lookup; never mint a replacement ID blindly.
Specify whether known rejections are durable operation records and their replay
semantics. The database must enforce operation uniqueness and immutable digests.

### 4. Recover an unknown human response

The current store exposes `read_receipt(principal_id, key)` while HTTP exposes only
response POST for recovery. Agree a canonical authorized lookup route/projection
with A/D rather than B adding a route. Require original task, key and exact payload
identity; validate current task permissions and reject a payload mismatch.

A successful lookup returns the original receipt, not a current-state substitute.
An absent receipt does not prove an in-flight request cannot still commit. D must
retain the original key/payload and use safe exact replay or reconciliation policy.
No lookup may reveal another actor's task, operation or material.

### 5. Migrate rates to exact decimals and distinguish zero

Affected shared fields include matrix cell values, factor adjustments and summary
totals. Use Decimal internally and decimal strings on the agreed new exchange
contract. An adapter that converts an already rounded binary float to a string is
not a precision-preserving migration. Do not reinterpret historical material hashes.

Keep explicit units: percent_points and ratio differ. Currency/area, price-date
adjustments and weights need their own defined units and source-grounded rounding.
Missing and not_applicable carry no numerical value. A present zero is not necessarily
confirmed; confirmed_zero requires the ordinary confirmation/evidence authority.
Prefer a typed presence/value plus confirmation model over a zero-specific approval bypass.

### 6. Publish one calculation snapshot contract

Reuse existing references, findings and coverage types where possible. Minimum
snapshot fields/semantics for A's contract:

| Area | Required information |
| --- | --- |
| Identity | Immutable snapshot identifier/digest, case, run, revision/material digest |
| Sources | Exact source bundle and per-document versions/hashes, explicit subject roles |
| Rules | Rule set IDs/versions/hashes, applicability and each row's rule reference |
| Inputs | Adopted typed values, units, original evidence and confirmation references |
| Calculations | Row identity, operands/references, deterministic operation, rounding, result/unit, dependencies |
| Completeness | Coverage, blockers and unsupported inputs/rules; missing never becomes zero |
| Authority | Exact approval scope and separate draft/ready eligibility |

Current `RevisionSnapshot` stores material/revision only; `CaseReviewResult` is not
the complete snapshot. B constructs and persists the joined immutable snapshot.
E consumes it without redoing arithmetic; A handles access control and publication.
Do not expand the PDF-only `ArtifactManifest` by silently treating XLSX as PDF.

## Persistent transaction design for A/B agreement

An optional implementation now exists for a private, single-host SQLite database
shared by injected API/worker services. A still owns the runtime/store selection.
If A already has a different tested durable case adapter, compare/integrate against
the existing port behavior rather than enabling two independent case databases.

The implementation preserves the current JobStore/HumanTaskStore behavior and
adds a shared transaction over the following existing records. Candidate operations
and complete calculation/export snapshots remain pending their shared contracts:

| Record group | Constraints / use |
| --- | --- |
| Jobs and runs | Exact head/run identity; compare-and-swap version and attempt fences |
| Revisions/material | Append-only case/revision identity and material digest |
| Human tasks/responses | Task version/state, server actor, full accepted command and change ledger |
| Receipts | Unique actor/key namespace and immutable canonical payload digest |
| Outbox/attempts | Same transaction as next run; dispatch and takeover guards |
| Candidate operations (pending) | Independent operation identity, producer and source bundle; exact replay |
| Material/result references | Append-only content identity; publish only under current run/fence; full export snapshot remains pending |

Use one SQLite transaction/connection for a response's complete effect, with bounded
lock waiting, private paths and explicit schema initialization/migration. Do not
nest independent task/job transactions or serialize a process-local lock as durability.
Persisted permission grants, if any, need an explicit revocation/version policy; a
per-request Principal alone cannot guarantee revocation atomicity across processes.

Required fault tests: exception/cancellation before commit, crash after commit before
receipt delivery, competing writers, changed payload, stale worker completion,
dispatch crash and restart. A fresh process must recover the same receipt, head and
queued continuation without creating a second revision/run. Retain the existing
in-memory adapter as a reference and apply the store conformance suite to the new one.

## Integration prerequisites and next increment

Await A's TEAM_BASE_SHA, CONTRACT_VERSION, ownership confirmation, runtime/store
choice and round stop time. The local branch starts from the inspected HEAD, not
an asserted team baseline. No shared contract migration or durable-runtime claim
is made before those decisions.

C must supply the verified Shulin source/rule bundle, roles, applicability, formula
references and unresolved inputs. The local Jinshan samples are not substitutes.
Human correction/confirmation and final source validation remain human actions.

Once A supplies the manual-adoption/condition contract, the next B increment is
P2: apply canonical corrections, preserve original provenance, and produce the
new blocked-or-calculable revision. The SQLite adapter can be wired into A's chosen
composition using the documented atomic worker handoff. Work that requires real-case acceptance stays
explicitly blocked until C's materials and human review are available.

## Current batch processing decision

The user's confirmed standard formula batch requires direct ingestion without
privacy confirmation, masking, scan waiting or restoration. This supersedes earlier
privacy prerequisites for the configured batch only; source/version validation,
case authority and content approval remain required. Do not add a user confirmation
or manufacture a passed scan.

B supplies internal versioned SourceProcessingScope configuration and atomic
SQLite run bindings. These neither change shared DTOs nor authorize calculations.
A/C must supply resolved exact source references and integrate the still-blocking
document-transfer contracts. A/D must use the same scope in the upload flow, and
E/A must convert the exact filled official Excel into the downloadable PDF.
See the [file-level handoff](formula-batch-integration.md) and
[ADR 0054](adr/0054-scoped-source-processing.md) before runtime composition.
