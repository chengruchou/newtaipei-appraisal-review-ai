# Architecture decision registry

This is the unique integration index. Existing main decision IDs and filenames
are retained; colliding branch decisions receive unused numbers. Each ADR keeps
its originating approval status and evidence scope. A historical “not merged”
or phase statement records its original delivery, not today's integration state.
Number allocation does not approve rules, material, templates, fonts, publication
or deployment. Use [project progress](../project-progress.md) for current status
and [service contracts](../service-contracts.md) for the selected task/manifest
migration and ownership.

## Decisions

| ID | Decision |
| --- | --- |
| 0001 | [Deterministic validation owns decisions](0001-deterministic-validation.md) |
| 0002 | [One typed PDF boundary and an explicit migration](0002-shared-pdf-contract.md) |
| 0003 | [Shared synchronous entry and separate durable cloud jobs](0003-entry-and-cloud-job-boundaries.md) |
| 0004 | [Preserve legacy validation errors and publish the review envelope](0004-transport-error-compatibility.md) |
| 0005 | [Independent case review and exact-material authorization](0005-complete-case-review.md) |
| 0006 | [Source-grounded document candidates and local reviewer authorization](0006-document-understanding-and-reviewer-trust.md) |
| 0007 | [Source identity, value anchors and grounded arithmetic](0007-source-cell-and-derivation-trust.md) |
| 0008 | [Explicit measurement and confirmation provenance](0008-measured-confidence-and-human-confirmation.md) |
| 0009 | [Validated fills and source-purpose boundaries](0009-validated-fill-and-source-purpose.md) |
| 0010 | [Separate case-data and PDF-template document identities](0010-separate-pdf-template-source.md) |
| 0011 | [Conservative PDF mutation, fonts and publication](0011-conservative-pdf-mutation-and-publication.md) |
| 0012 | [Bind PDF inputs and template policy before publication](0012-bind-pdf-inputs-and-template-policy.md) |
| 0013 | [Shared service contracts and explicit local composition](0013-service-foundation.md) |
| 0014 | [Full-case goldens are authored, re-derivable expectations](0014-full-case-golden-contract.md) |
| 0015 | [Durable review jobs, outbox dispatch and lease fencing](0015-durable-review-jobs.md) |
| 0016 | [Authenticated human tasks and transactional revision submission](0016-authenticated-human-tasks.md) |
| 0017 | [Local pause and continuation integration seam](0017-local-pause-continuation-seam.md) |
| 0018 | [Controlled action selection and execution authority](0018-controlled-action-policy.md) |
| 0019 | [Multiple-context output, template registry and opaque placeholders](0019-formal-multi-context-output-and-placeholders.md) |
| 0020 | [Local human-task transactions and revision re-entry](0020-local-human-task-transactions.md) |
| 0021 | [Versioned extraction outcomes and reproducible evaluation inputs](0021-extraction-evaluation-contracts.md) |
| 0022 | [Close raw model entrypoints and require authorized snapshot assembly](0022-authorized-extraction-preflight.md) |
| 0023 | [Bounded attempts, complete raster validation and truthful page outcomes](0023-bounded-extraction-execution.md) |
| 0024 | [Controlled sanitized documents and immutable run sources](0024-versioned-document-transfer.md) |
| 0025 | [Resolve admitted run bytes before candidate extraction](0025-snapshot-extraction-integration.md) |
| 0026 | [Transactional DynamoDB job persistence](0026-dynamodb-job-store.md) |
| 0027 | [Reference-only Runtime with durable execution authority](0027-runtime-composition.md) |
| 0028 | [Controlled execution failure boundaries](0028-controlled-execution-failure-boundaries.md) |
| 0029 | [Authoritative artifact publication and immutable downloads](0029-fenced-artifact-publication.md) |
| 0030 | [Atomic local review state and result storage with SQLite](0030-sqlite-local-review-transactions.md) |
| 0031 | [Independent acceptance receipts and bounded public reports](0031-rehearsal-evidence.md) |
| 0032 | [Local privacy contracts and a separate sanitized boundary](0032-local-privacy-boundary.md) |
| 0033 | [Bounded local acquisition and explicit scan coverage](0033-local-privacy-scan-isolation.md) |
| 0034 | [Local privacy review and ephemeral human authority](0034-local-privacy-human-review.md) |
| 0035 | [Raster rebuild with independent privacy verification](0035-raster-privacy-bundles.md) |
| 0036 | [Immutable encrypted local mapping and session keys](0036-encrypted-local-mapping.md) |
| 0037 | [Local refill and publisher authority](0037-local-privacy-refill-boundary.md) |
| 0038 | [Owned export snapshots and offline regression evidence](0038-local-export-and-offline-regression.md) |
| 0039 | [Separate local privacy HTTP authority and exact C2 export](0039-local-loopback-privacy-bridge.md) |
| 0040 | [SQLite publication transactions and current download authority](0040-sqlite-publication-transactions.md) |

## Originating filename migration

These old names identify the original branch records only; links use the unique
destination. The original main records at 0014, 0015 and 0024 remain unchanged.

| Originating filename | Current decision |
| --- | --- |
| `0014-controlled-action-policy.md` | [ADR 0018](0018-controlled-action-policy.md) |
| `0015-formal-multi-context-output-and-placeholders.md` | [ADR 0019](0019-formal-multi-context-output-and-placeholders.md) |
| `0015-local-human-task-transactions.md` | [ADR 0020](0020-local-human-task-transactions.md) |
| `0016-controlled-execution-failure-boundaries.md` | [ADR 0028](0028-controlled-execution-failure-boundaries.md) |
| `0016-fenced-artifact-publication.md` | [ADR 0029](0029-fenced-artifact-publication.md) |
| `sqlite-local-review-transactions.md` | [ADR 0030](0030-sqlite-local-review-transactions.md) |
| `0021-local-privacy-boundary.md` | [ADR 0032](0032-local-privacy-boundary.md) |
| `0022-local-privacy-scan-isolation.md` | [ADR 0033](0033-local-privacy-scan-isolation.md) |
| `0023-local-privacy-human-review.md` | [ADR 0034](0034-local-privacy-human-review.md) |
| `0024-raster-privacy-bundles.md` | [ADR 0035](0035-raster-privacy-bundles.md) |
| `0025-encrypted-local-mapping.md` | [ADR 0036](0036-encrypted-local-mapping.md) |
| `0026-local-privacy-refill-boundary.md` | [ADR 0037](0037-local-privacy-refill-boundary.md) |
| `0027-local-export-and-offline-regression.md` | [ADR 0038](0038-local-export-and-offline-regression.md) |
| `local-loopback-privacy-bridge.md` | [ADR 0039](0039-local-loopback-privacy-bridge.md) |
