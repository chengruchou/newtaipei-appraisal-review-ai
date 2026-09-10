# Requirements, implementation and acceptance evidence

## Current integration boundary

The current work joins reviewed components in a configured local service. **Local
end-to-end/browser acceptance is still being completed; AWS deployment, live
model quality and formal human/business approval are not accepted.** This matrix
supports the single status recorded in [project progress](project-progress.md).
It does not turn component tests, source inspection or synthetic signatures into
complete acceptance.

Earlier SHA, PR, CI, test-count and real-document subset observations are retained
in the [historical traceability snapshot](history/2026-09-11-pre-convergence/delivery-traceability.md).
Those observations keep their original scope and must not be reported as fresh
runs on this integration head. A prior CI startup/billing failure establishes that
that job did not run tests; it is not evidence about a different head's CI.

## Requirement matrix

| Required behavior | Implementation / owner | Existing local evidence | Required integrated observation |
| --- | --- | --- | --- |
| Review cases, not just fill PDFs | Controller, CaseReviewer, deterministic engine and independent verifier | Domain/source/trust-boundary and complete-case golden suites | Exact synthetic case flows through actual parser and review, retaining findings |
| Preserve critical uncertainty | Evidence/confidence contracts, confirmation and independent approval | Unknown/missing/low-confidence and forged-result regressions | needs_review with zero writer calls; explicit confirmation preserves raw score |
| Exact private export | Privacy review/export/mapping adapters; local bridge owner | `test_privacy_review_preservation.py`, `test_privacy_export_mapping.py`, export/network suites | User sees exact payload; key/store/confirmation failure causes zero transfer and no original/map canaries escape |
| Authorized immutable sources | C2 admission/resolver, Runtime source wrapper | `test_snapshot_extraction.py`, document admission/revocation tests | Wrong principal/case/version/hash or replaced/revoked source fails before and after use |
| Durable jobs and attempt authority | JobStore state machine, outbox/reconciler, SQLite local and DynamoDB adapters | Shared JobStore contract; actual SQLite independent-process claim and recovery | Restart/retry does not duplicate visible results; old lease/fence/result version cannot publish |
| Atomic human response | #38 service, HumanTaskStore, combined SQLite adapter | Applied-value receipt, empty-task ownership, rejection, replay and r1/r2/r3 tests | Task/revision/job/outbox/receipt all commit or none through the actual API |
| Immutable confirmed browser command | #39 consumer and canonical subject projection | Consumer regressions belong to the workbench owner | Confirmation equals captured/submitted command; unknown transport result retries identical payload/key; unit and body timeout retained |
| Explicit model decisions | Allowed-action policy, selector/executor, decision/failure traces and run ledger | `test_workflow_run_authority.py`, run-ledger and SQLite-ledger process suites | Same-run budgets never refill; invalid receipt/unknown external outcome remains failed or quarantined |
| Truthful extraction cost | Extraction page outcomes and smoke evaluator | Extraction/evaluation regression scope | Known input/output usage contributes independently; unknown totals remain unknown |
| Complete multi-context PDF | Registry, LocalPDFWriter, protected sources/font snapshots | `test_pdf_output_protection.py`, `test_template_registry.py`, actual reopen and backfill | Actual service artifact covers primary plus every additional context, all fields, exact writer/assets and this run |
| Authorized publication/download | Restored publication core, current-attempt/grant repositories and local SQLite composition | Publication condition and source/grant regression suites | Manifest and result are current, bytes match, every download reauthorizes, revocation prevents replay |
| Safe local restore | Local privacy refill and placeholder backfill | Actual synthetic PDF reopen and source-byte equality | Another local revealed PDF is created; original/template/downloaded placeholder bytes remain unchanged |
| Runtime success and deadlines | RuntimeWorker and configured factory/packaging | Storage/worker regressions; fail-closed unconfigured path | Actual configured package executes success; deadline/cancellation blocks late publication even during heartbeat I/O |
| Independent acceptance evidence | rehearsal-v1 validator and collectors | Strict source/trust/signature/hash/dedup/scenario tests | Actual observer traces bind case/revision/run/attempt/head/configuration; synthetic attestation remains synthetic |
| Submission integrity | Existing full-snapshot/metadata/branch/publication checker | Dedicated checker subprocess suite and per-delivery gates | Exact outgoing history, index, working files and actual publication text inspected before submission |

These test names identify relevant regression coverage, not a claim that every
listed suite has been rerun on the final merged checkout. The integration owner
records the exact final commands and results after the complete delivery gate.
No skipped/relaxed assertion, confidence increase or fabricated success is an
acceptable substitute for a missing observation.

## Repair-round observations with bounded scope

| Checkpoint | Observed result | Scope limit |
| --- | --- | --- |
| PR #34 original repair tree | 10 initial regressions failed; corrected PDF/registry coverage and full 686-test local suite passed; source bytes unchanged | Actual local PDFs, not formal font/template/business acceptance or full service acceptance |
| Combined local SQLite adapter | 45 dedicated tests including all 31 JobStore contract checks; 410-test related job/task/API selection passed | Real SQLite transactions/processes; no AWS, browser or source-grant claim |
| Workflow union merge | Integration owner reports 78 focused tests passed | Component/source merge evidence only |
| Canonical EVIDENCE API path | Integration owner reports 14 focused regressions passed | Resolved citations, preserved zero confidence and reset confirmation; not whole-browser acceptance |

The PDF evidence is in its original worktree's ignored `artifacts/pr34/`.
SQLite logs and result digests are in the integration worktree's ignored
`artifacts/sqlite-review/`. Other repair owners retain their exact failing/passing
logs and source identities. No raw real case, PDF, mapping, key or private URI is
included in this Markdown record. The documentation consolidation itself makes
no new code, schema, CI, AWS or browser acceptance claim.

## Canonical consumer migration gate

The selected undeployed service-v1 upgrade combines #36 task reason/affected/
evidence fields with #38 task projections. There is one union in the integration
tree; controlled-action-v1 stays explicitly versioned. All compiled consumers and
the regenerated #39 client must use that union. Legacy frozen commands and baseline
success remain obligations, but old extra-forbid consumers must upgrade for new
serialized nested task fields.

`TaskSubjectView` remains separate from TaskView's task/subject shape. The internal
controlled-workflow HumanResponseResult is mapped explicitly to API receipt
semantics rather than treated as an alias. The legacy `ArtifactManifest` stays
unchanged with `single_context` / `local_only` semantics. The selected separate
`FencedArtifactManifest` uses `artifact-manifest-v2`, primary `context` plus complete
`contexts`, `review_contexts` scope, fenced publication and digest/font/writer
bindings. `ServiceResult.artifacts` becomes a legacy/new union and all undeployed
consumers regenerate to accept the new version. `PublishedArtifact` retains all
contexts. This needs actual producer/service/reader validation,
not just a writer regression. Changed signed content requires explicit migration
and reapproval, not silent re-signing. See [the full migration](service-contracts.md).

## Evidence record required at final local rehearsal

A final record must identify its tested code revision, configuration digest,
source/case/revision/run/attempt identities, actual command, time and observer.
Keep observations and conclusions separate. The final code revision and startup
commands are supplied by the integration owner after assembly; an evolving
working-tree result must not be relabeled as that release.

| Scenario | Required observation |
| --- | --- |
| Exact export and admission | Confirmed bytes/manifest are those uploaded; encrypted exact map is durable before any transmission |
| Empty-task legitimate job | Actual API returns job, empty tasks and available revision history without an error page |
| Normal success | Actual parser -> prepared material -> review/verification -> real writer -> reopen -> manifest/publication/download; artifact belongs to the run |
| needs_review and two response rounds | Original low score preserved; explicit commands yield a valid r1/r2/r3 chain with no duplicate registration or receipt |
| Interrupted confirmation/submission | UI, captured command and request are identical; retry preserves payload/key and units; body reads can time out |
| Process restart and lease loss | Persisted allowance/unknown effects survive; duplicate delivery is fenced; timed-out or cancelled attempt never commits success |
| Unauthorized/revoked/changed source | Other principal/case, version/hash mismatch, source replacement and outdated approval are rejected |
| Multi-context download and refill | Complete contexts/fields, byte-pinned template/map/font/writer, unchanged originals and a separate local revealed PDF |

## Independent later gates

Local HTTP, local privacy isolation, real browser, SDK/emulator, Linux/container,
GitHub CI and real AWS evidence are separate categories. In-process dictionaries
cannot establish process-independent durability; a synthetic model response cannot
establish model quality; a signature over a self-authored success statement does
not establish that an independent collector ran.

AWS acceptance needs explicit account/role/region/model/budget scope and actual
IAM, object versions, queue/outbox/lease recovery, package/image scan and operator
observations. Unfixed applicable image vulnerabilities remain deployment gates.
Do not disable scans or promote unavailable live collectors to empty success.
Formal rules, fonts, field maps and exact material/publication approval are
independent human controls. No ADR renumbering, generated client or local test
changes those approval flags.

See [cloud acceptance](cloud-acceptance.md), [golden acceptance](golden-acceptance.md),
[Runtime operations](runtime-deployment.md), [submission checks](submission-checks.md)
and [the consolidated ADR registry](adr/README.md).
