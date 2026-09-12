# Standard formula batch integration handoff

The user's batch decision is authoritative. Do not ask users to confirm privacy
again, and do not fabricate privacy-passed evidence. Keep existing privacy modules
available for cases outside this exact configured scope.

## B's delivered behavior

The existing B state machine has no privacy wait/consent/restoration transition to
remove. Human data/rule review, corrections, versions, replay, source consistency,
calculation and approval checks remain active.

`application/source_processing.py` supplies an internal SourceProcessingScope and
`adapters/local/sqlite_job_store.py` persists its exact run binding. No public DTO,
API route, source-transfer module or frontend file is modified by B.

A/C can map the resolved, configured batch sources to this internal input:

```python
scope = SourceProcessingScope(
    scope_id=source_catalog_scope_id,
    version=source_catalog_scope_version,
    documents=resolved_batch_document_references,
)
jobs = SQLiteJobStore(database, source_scopes=(scope,))
```

The variables above come from trusted source configuration, not browser fields or
a new confirmation dialog. B has not supplied fictitious production case IDs,
document IDs, versions or hashes. The full source set must match; extra documents
and later cases do not inherit the batch's setting.

The persisted value is `privacy_handling="not_applicable"`, with the configured
scope ID/version and actual document references. It is neither a scan result nor
a content approval. Matching jobs enter the existing queued/running flow without
new privacy tasks. Corrections preserve the same-source setting atomically, while
old content approvals still do not apply to changed material.

For a UI projection, A must first authorize the owning job and run, then obtain
the internal binding through `read_source_processing(run_id=...)`. A owns its
canonical public schema and generated client. A missing binding is no configured
exemption, not evidence that a scan passed or failed.

## Exact cross-role dependencies

| Owner | Files/area to inspect | Required integration |
| --- | --- | --- |
| A/C | `application/document_transfer.py` | Add the configured batch's direct ingest/read/snapshot path without requiring PrivacyAttestation; retain real source integrity and authorization |
| A | `domain/document_transfer.py`, relevant ports and API schema | Represent sanitized versus privacy-not-applicable source processing honestly; current mandatory attestation/classification/manifest fields cannot encode the new batch |
| C | Source catalog/settings, document storage labels and snapshot construction | Supply exact batch references and scope version; preserve original bytes/hash/storage version; use parsed page count, not a fabricated privacy manifest |
| A/D | Upload route, generated client, `web/src/privacy/*` and batch entry routing | Use the shared backend source setting; bypass this batch's privacy confirmation/masking/wait/restoration steps in both request flow and display |
| E/A | Official workbook exporter, conversion and artifact/download routes | Fill official Excel first, convert that exact workbook to PDF, and bind both outputs to the same snapshot/workbook hash; retain authorization/content approval |

B's run-binding accessor is internal only. Do not wire it to an unauthenticated
route, put raw source locations in the client, or accept a frontend exemption flag.
This decision does not alter existing source or content-approval authorities.

## Current blocker in the checkout

The inspected DocumentTransferService.ingest still requires a signed
PrivacyAttestation. DocumentMetadata/ObjectLabels still require sanitized, and
SnapshotEntry still requires a privacy manifest digest. Those owned source/API
contracts must change together before the complete direct-upload workflow is
usable. Hiding the frontend privacy step would leave this backend dependency intact.

No A/C/D/E implementation or complete Excel-to-PDF acceptance is claimed by this
B handoff. See [ADR 0054](adr/0054-scoped-source-processing.md) and the current
[B plan](member-b-implementation-plan.md) for delivered tests and remaining work.
