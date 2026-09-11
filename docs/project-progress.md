# Project progress

## Current status

The latest inspected remote `main` is
`f241e7a479d3135e81bb47bcf9a94f2685492fdf`. The follow-up stack is under
review after #52 and #53; its clean executable checkpoint is
`add3c0e23dc978dff082450d6475ace1e599261c`. The
[follow-up validation record](followup-local-validation-record.md) separates
current local evidence from the merged baseline below and from hosted CI.

[PR #45](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/45)
was merged into `main` at `d148422adb18190bada93b8588a4e34d73e3c2e4`.
Its source tree matches the reviewed integration head
`fd22e68321bad6b58f06068dfef1db67fdb1c269`. This is a **configured local validation
baseline**, including the browser, API, durable local jobs, human review, PDF
publication/download and local privacy restoration.

The final delivery reports one complete successful OCR restoration/download.
Earlier HTTP 409 and timeout observations remain historical failures; the later
success does not explain their causes or establish repeated OCR reliability.
**AWS deployment, hosted CI, image security, production identity, designated
model quality and formal business acceptance remain open.** Merge is not
deployment approval, and no test count is a completion percentage.

This is the current status authority. [The implementation backlog](implementation-backlog.md)
defines remaining work and its issue mapping. Superseded component PRs and earlier
milestone issues are delivery history, not an instruction to merge old branches
again. Earlier source/main/CI snapshots remain in
[history](history/2026-09-11-pre-convergence/README.md).

## Follow-up candidate after the baseline

The current candidate includes exact per-model routing proofs (ADR 0048), finite
request-local restoration diagnostics and isolated OCR browser configurations
(ADR 0049), and an explicit synthetic local container composition (ADR 0050).
The container serves the built UI and configured API/worker with durable private
state; host-companion mode retains original documents and mapping authority on
the host. See the [local validation stack](local-validation-stack.md).

These are additions under review, not a claim that the baseline CI or image
validated them. Their PRs record exact new source/image checkpoints, failed and
successful browser runs, and advisory results. Full OCR corpus reliability,
legitimate operator provisioning, supported-platform acceptance and all real
approval/deployment gates remain open. The historical evidence below is retained
without being relabelled as validation of the follow-up candidate.

At the clean executable checkpoint, 3,307 repository tests, 12 local cloud tests,
230 frontend tests, 101 installed-wheel regressions, quality/contracts and six
CloudFormation checks passed. The actual image core passed four browser scenarios
and lifecycle checks. The original synthetic full privacy flow and a separate
paused/automatic-refusal/resume sequence passed using the exact image-exported UI
and native host companion. Both downloaded PDFs passed independent source-crop,
eight-field and non-restoration-pixel checks; all raw confidence values remain.
The final documentation commit does not alter runtime or package/image build
inputs and is not a claim that the full suite ran at a different SHA.

The fresh image still fails its OS gate: 176 findings, including 3 Critical and
51 High. Fresh Python/npm advisory checks do not clear that result. The exact
heads of #52 and #53 had hosted jobs blocked before tests by payment/spending-limit
annotations; the final stack head requires its own CI readback. See the
[current record](followup-local-validation-record.md) for hashes, retained failed
attempts, warnings and pending production/operator work.

## Implemented and integrated baseline work

| Work line | Current integrated behavior | Remaining work or acceptance |
| --- | --- | --- |
| PDF and publication (#34, #35) | Protected source/download paths, immutable font bytes, complete two-context/eight-field writer/reopen/manifest; authoritative attempt/lease/fence/grant and authorized download | Formal font/template/map approval; live DynamoDB/S3 composition and recovery |
| Controlled workflow (#36) | Invalid receipts retain failure/quarantine traces; same-run reservations persist; canonical committed human-response adapter | Designated model evaluation and operational recovery |
| Privacy and OCR (#37, #45) | Exact encrypted mapping readback before one-use transfer; restricted local bridge; page-bound individual readings and two-stage OCR receipts; one successful complete restoration | Repeated reliability, retained failure diagnostics and local application distribution |
| Human tasks (#38) | Applied corrections, raw zero confidence, owned empty jobs and atomic task/revision/job/outbox/receipt transitions | Production identity and equivalent durable cloud composition |
| Local persistence | Real SQLite transactions, durable dispatch, restart and publication authority | Distributed/cloud recovery is a separate guarantee |
| Workbench (#39) | Canonical client, located PDF/units, frozen confirmed commands and retries, all-page preview, local OCR review | Production login; job discovery/upload/create flow; deployment packaging |
| Extraction (#42) | Actual source/parser/SDK paths; known input/output usage counted independently | Real designated model quality, effective authorization and measured costs |
| Runtime (#43) | Deadline guards; configured wheel/container local success; non-login UID 10001 | Default AWS entry composition, complete Docker stack, image security and live deployment |
| Acceptance (#44) | Local evidence collector, real browser scenarios and strict attestation validators | Independent live AWS observations and authenticated collector provenance |
| Competition controls | Pinned rule/service/quota catalogs, data-admission checks, guarded clients, shared physical dispatch and conservative persistent budget reservations | Per-model routing/destination binding, approved private profile, live role/quota/budget/stop evidence |

Use [the integrated local runbook](integrated-local-runbook.md) to reproduce the
configured synthetic flow. [The validation record](local-validation-record.md)
retains exact checkpoints and evidence limits; [traceability](delivery-traceability.md)
maps requirements to their implementations. No real material is approved by a
synthetic confirmation or a local test.

## Evidence for the merged baseline

| Evidence | Result and scope |
| --- | --- |
| Integration owner's final report at `fd22e683` | Python 3,223 passed, 10 platform skips; cloud 12 passed; frontend 150 passed. These are reported delivery gates, not rerun counts from this documentation update. |
| Reported actual Chromium runs | Core 7/7, gateway recovery 1/1, automatic OCR refusal 1/1 and full privacy 1/1. Synthetic confirmations do not approve real material. |
| Independent pre-merge focused review | Core 289, competition/data-admission 273 and privacy 83 passed in separate scopes. Counts may overlap and are not an integration total. Privacy used PyMuPDF 1.26.6, below the declared minimum 1.28.2. |
| Independent frontend supplement | 150 tests passed with Vitest 2.1.9, Vite 5.4.21, Router 6.30.6 and compatibility setup; this does not reproduce the locked Vitest 4.1.11, Vite 6.4.3 and Router 7.18.3 gate. |
| Independent configured service observation | Result read, PDF download/hash equality and restart succeeded; unauthorized download returned 403. |
| Final restored PDF | SHA-256 `2489bae87b1ea91d79435b27aecab19c2e5e632deff397f43b82492cd4f54982`; two pages/eight fields, three crop checks and non-target pixel equality reported. |
| OCR evidence retained | 99 published-stage and 108 candidate-stage raw observations; four material confidences stay 0.0; 11 individual visual readings and two exact local OCR receipts. |
| Restore latency and prior failures | Final download took 14.318 seconds. An earlier 15-second browser failure and later HTTP 409 remain separate unresolved observations. One success is not a latency/reliability guarantee. |
| Packaging | Final wheel/image gates were built at `754e7059`; the later `fd22e683` change only adjusts a budget test's transport seam. Package-source equivalence is documented; the build must not be relabeled as having run at another commit. |
| Dependency and image security | Final delivery reports zero Python/npm audit findings. The image still has 174 OS findings: 3 Critical, 51 High, 57 Medium, 57 Low and 6 Unknown. A clean package audit does not clear image security. |

Both the integration-head [CI run 34579247155](https://github.com/chengruchou/newtaipei-appraisal-review-ai/actions/runs/34579247155)
and merged-main [CI run 34580935446](https://github.com/chengruchou/newtaipei-appraisal-review-ai/actions/runs/34580935446)
failed with no test steps or artifacts. The exact cause of the current-main
failure is unconfirmed. Investigation and a successful rerun belong to the
backlog; local evidence and merge state do not substitute for hosted CI.

## Composition and contract ownership

The integration owner maintains one canonical model set and composition root.
`domain/service_contracts.py` combines the workflow/task union;
`domain/task_contracts.py` defines API projections. `controlled-action-v1`
remains separate. Strict undeployed consumers and the frontend regenerate
together; frozen commands and legacy HTTP/invocation success remain compatibility
obligations. See [the migration matrix](service-contracts.md#canonical-36-and-38-migration).

`FencedArtifactManifest` (`artifact-manifest-v2`) binds primary and complete
contexts, review scope, publication identity, digest, font and writer. Legacy
`ArtifactManifest` is unchanged. The service and current consumer accept the
versioned union; old strict consumers require an explicit upgrade.

The local review store owns job/task/revision/outbox/receipt transactions. The
publication adapter owns manifest/grant/object authority; document admission owns
current C2 authorization and revision snapshots. Workflow reservations do not
share their transaction merely because all stores use SQLite.

Canonical decision IDs remain [ADR 0024](adr/0024-versioned-document-transfer.md)
for transfer, [ADR 0028](adr/0028-controlled-execution-failure-boundaries.md) for
controlled execution, and [ADR 0035](adr/0035-raster-privacy-bundles.md) for raster
privacy. The [ADR registry](adr/README.md) includes the later competition and OCR
decisions without changing their approval status.

## Next implementation boundary

Review the functional follow-up branches in dependency order; do not re-merge
the superseded integration branches. The [backlog](implementation-backlog.md)
separates the candidate's routing/diagnostic/local-container changes from missing
frontend/operator workflows, OCR reliability, CI/security and authorized
cloud/model acceptance. Preserve raw confidence,
exact individual confirmation, independent material/publication authority,
source bytes and failed observations while completing those items.
