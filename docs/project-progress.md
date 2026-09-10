# Project progress

## Current status

The configured local integration is implemented. Seven actual Chromium scenarios
against the real API passed, including published download, empty jobs, explicit
confirmation, unit-preserving correction, rejection, conflicts and exact-command
recovery after an unknown response. **The complete local privacy/restoration flow,
AWS deployment, real model quality and formal business approval are not accepted.**
No test count is used as a completion percentage.

This is the current status authority. Earlier source/main/CI snapshots remain in
[history](history/2026-09-11-pre-convergence/README.md). Original PRs remain open;
normal Git integration merges do not approve or merge GitHub pull requests.

## Implemented and integrated work

| Work line | Current integrated behavior | Remaining acceptance |
| --- | --- | --- |
| #34 PDF | Protected original/download paths and aliases; immutable approved font bytes; two contexts and eight fields through real writer/reopen/manifest | Complete actual OCR restoration; formal font/template/map approval |
| #35 publication | Restored package; authoritative current attempt/lease/fence/grant and exact source versions; SQLite publication/download | Live DynamoDB/S3 deployment and operational recovery |
| #36 workflow | Invalid receipts quarantined with trace; persistent run reservations; canonical committed human-response adapter | Designated model accuracy and operational evaluation |
| #37 privacy | Raw evidence survives label-only edits; exact encrypted mapping readback before one-use C2 transfer; guarded local bridge | Full actual OCR restoration remains blocked, not bypassed |
| #38 human tasks | Actual corrected values; raw zero confidence; empty owned jobs; atomic task/revision/job/outbox/receipt transitions | Production identity and cloud durable adapter composition |
| Local persistence | Real SQLite cross-process transactions, durable dispatch, crash/restart and publication authority | Distributed/cloud recovery is a separate guarantee |
| #39 workbench | Canonical generated client; located PDF/units; frozen confirmed commands and retries; exact all-page PDF preview | Complete privacy browser acceptance and production login |
| #42 extraction | Real source/parser/SDK paths; known input/output usage counted independently | Real paid model was not called; unknown costs stay unknown |
| #43 Runtime | Deadline guards; configured wheel/container executes completed two-context cases; UID 10001 resolves without login | Final image security and live deployment gates |
| #44 acceptance | Read-only local evidence collector, real browser scenarios and strict existing attestation validators | Local observations are not live AWS acceptance or authenticated collector provenance |

See [final local evidence and open gates](local-validation-record.md),
[reproducible local commands](integrated-local-runbook.md),
[original repair heads and review replies](integration-repair-delivery.md) and
[acceptance boundaries](delivery-traceability.md). Deterministic verification,
raw confidence, exact side confirmation and independent material/publication
authority remain separate. No real material is approved.

## Contract and composition ownership

The integration owner maintains one canonical model set and composition root.
Current source combines #36 `domain/service_contracts.py` with #38
`domain/task_contracts.py`. `controlled-action-v1` is retained. All undeployed
strict task consumers must regenerate together; accepting extra fields or
silently dropping task data is not a migration. The decision is a synchronized
migration of the undeployed union; frozen
commands and baseline success responses remain compatibility obligations, while
new serialized nested task keys require upgraded strict consumers. See
[the migration matrix](service-contracts.md#canonical-36-and-38-migration).

Artifact projection ownership includes the new `FencedArtifactManifest`
(`artifact-manifest-v2`): primary and complete contexts, review-context scope,
fenced publication and exact digest/font/writer bindings. Legacy `ArtifactManifest`
stays unchanged. The service result accepts the legacy/new union; consumers must
regenerate to read the new version. The projection owner must demonstrate actual
multi-context service, manifest and reader coverage, not only writer output.

`integrated_service.py` and associated application assembly are owned by the
integration owner. The local SQLite adapter owns its job/task/result transaction;
the publication adapter owns manifest/grant/object tables, and source authority
owns current C2 document authorization and revision snapshots. The workflow
budget ledger cannot be treated as the same transaction merely because both
stores use SQLite.

## Evidence and open gates

- The final real core browser run passed seven scenarios in one clean 14.4-second
  run. Earlier failures and reruns remain separate historical observations.
- The actual installed wheel and network-isolated Linux container each executed
  four explicit confirmations, produced a two-context/eight-field PDF, reopened
  and hash-checked the authorized download, replayed receipts and survived restart.
  Their exact source/build hashes are retained with local artifacts; later source
  updates require a new package gate rather than reusing that evidence.
- The final actual privacy browser run reached two exact transfers, four current
  confirmations, completed two-context publication, hash-checked download and a
  current restoration plan. Restoration returned 409; Tesseract observations
  matching the published first-page raster included four below 0.85, with no final restored output.
  The response-body diagnostic then timed out; no HTTP error code/body is claimed.
  [Legibility diagnostics](privacy-ocr-legibility.md) preserve the negative result;
  scores, observations, placeholder gates and decimal assertions are unchanged.
- A native PDF iframe failed to complete in actual Chromium. The workbench now
  uses the existing PDF.js renderer and requires every page to finish before
  enabling explicit approval. A failed page still blocks approval.
- Local tests, exact-head remote CI, emulator checks, browser observations and
  cloud/operator acceptance remain distinct. The repair record identifies each
  component CI run; failed or unstarted runs are not green.
- Image scanning reported unresolved findings. No scan was disabled and no
  threshold reduced. Image security remains a deployment gate.

A completed core flow does not establish a completed privacy flow. Actual
restoration must produce another local PDF and prove both the original and the
published placeholder PDF unchanged. The full browser assertion remains enabled;
an OCR rejection is reported as an acceptance failure.

Live AWS acceptance requires a scoped non-root account/role, region, designated
model, budget/data-region authorization and real IAM/storage/recovery/alarm
observations. Formal rules/fonts/templates and human approval are separate gates.
None is cleared by a synthetic signature or a local test.
