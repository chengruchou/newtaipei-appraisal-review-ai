# Scoped source processing without privacy prerequisites

Status: accepted for B's internal processing configuration and durable run binding;
cross-role ingestion, shared API and export integration remain pending.
Date: 2026-09-12.

## Authorized scope

The user confirmed with a professional that the standard formula documents supplied
for the current batch do not require privacy processing. The batch workflow is:

login → direct upload/import → parse → verify data and rules → calculate → fill
official Excel → convert that same Excel to PDF → preview and download.

This batch requires no pre-upload privacy confirmation, masking, privacy scan wait,
or restoration. Record that processing scope in trusted source configuration;
do not create another user confirmation. No privacy scan is claimed to have passed.
This decision does not authorize a default exemption for unknown future cases.
Case authorization, source/version integrity, formulas and content approval remain.

## Inspected backend behavior and ownership

B's job state machine, human response transactions and deterministic case reviewer
already have no privacy-confirmation/scan/restoration states. NEEDS_HUMAN represents
real open review tasks, not a privacy placeholder. Removing it would incorrectly
bypass data correction or approval. These gates remain unchanged.

The current source-transfer path is the real integration blocker: ingest requires
PrivacyAttestation; DocumentMetadata and ObjectLabels require sanitized
classification; SnapshotEntry requires a privacy manifest digest and obtains page
count from that manifest. Those source-transfer/shared-contract changes belong to
A/C under the role boundary. B does not forge attestations, zero-mask manifests,
scan results or substitute generic hashes for privacy-manifest hashes.

## Decision

Add SourceProcessingScope as internal, trusted server configuration. It records
scope ID/version, the complete resolved source reference set, privacy_handling
not_applicable, and the confirmed-standard-formula basis. It is not a new upload
DTO, content approval, domain rule or public API contract.

Match the full source set by exact case ID, document ID, version, content hash and
purpose. Reject empty, duplicate, mixed-case and ambiguous configuration. Do not
infer applicability from today's date, a filename, document text, a model label,
or a boolean supplied by the browser. A/C provide the actual resolved references;
no production references or document hashes are guessed in this implementation.

SQLiteJobStore accepts these explicit scopes in trusted composition. It persists
immutable scope definitions and atomically binds a matching scope to a newly
submitted run, alongside job/run/idempotency/outbox records. Reusing a scope
identity/version for different sources conflicts. A missing binding means no scoped
exemption was established; it is not a failed or successful privacy scan.

Exact replay preserves the original run's scope instead of re-evaluating newer
configuration. A correction continuation copies the pinned scope atomically with
its revision/run/outbox because the current correction port retains the exact
source set. Source-set changes require their own admitted new version; they do
not inherit this scope merely through shared filenames or case descriptions.

Persisted historical definitions are not automatically active for new submissions
on restart. Server composition must explicitly supply scopes for new jobs; existing
run bindings remain readable and carry forward on same-source corrections. This
preserves the confirmed batch while preventing ambient future-case exemptions.

The record grants no REVIEW/CONFIRM/CORRECT permission, creates no human task and
sets no verified/completed state. CaseReviewer and the existing content/formula
checks remain the only review path. SourceProcessingScope does not implement the
approval interface and is not passed to the calculator as authority.

## Compatibility and outstanding integration

Existing request/result DTOs, ports, routes, privacy modules and ordinary unscoped
behavior are unchanged. The SQLite adapter adds two typed record namespaces rather
than altering existing stored row shapes. Its internal read_source_processing
method is for trusted application code after owning-job/run authorization; it is
not a public unauthenticated source lookup.

A must publish a compatible canonical processing-applicability projection and
route authenticated direct import to C's matching scoped source handling. C must
retain original bytes/version/hash, parse actual page counts and classify scoped
documents honestly without privacy-attestation prerequisites. D must use that same
backend projection and remove the batch's confirmation/masking/scan-wait/restoration
dependencies from interactions. E/A must bind PDF output to the exact filled Excel,
snapshot and output hashes. No direct-PDF equivalence is invented by B.

The complete new batch workflow cannot be claimed while the old source-transfer
contract still requires an attestation. This B increment supplies truthful durable
processing scope and proves that privacy non-applicability cannot approve review.

## Validation

Focused tests cover exact source matching and future-case isolation; immutable
policy versions; replay across configuration changes; restart without automatic
defaults; scope inheritance on correction; rejection of client-provided flags;
current write permission and content reapproval; rollback at submission and
continuation scope writes; and deterministic review of missing/low-confidence
values, wrong formulas, unsupported rules and source-version mismatch. Valid
independently approved synthetic material can still be verified, while invalid or
unapproved material remains needs_review/failed with no completed artifact.
