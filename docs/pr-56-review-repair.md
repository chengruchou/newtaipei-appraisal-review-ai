# PR 56 review repair

Status: local integration and verification; no new commit or remote update.

## Review and merge scope

The reviewer approved the original transaction implementation and identified a
semantic merge conflict in HumanTaskService._correct. The implementation branch
had removed the confidence clamp; the target had added SUPPLY_EVIDENCE handling
at the same location. Taking either entire conflict side would lose required
behavior. The local merge retains the target's source-validation/evidence block
and the implementation's exact preservation of raw confidence.

The review also lists five non-blocking performance observations. They do not
constitute production readiness approval for the optional SQLite adapters.

## Authoritative confidence behavior

A correction or evidence supply cannot raise or lower the extractor's raw
confidence. The submitted confidence remains in the proposal ledger. Newly attached
EvidenceRef records use the unchanged observation confidence. Thus an observation
with raw confidence 0.5 and a proposal of 0.0 retains 0.5 in both places; an original
zero stays zero. This does not grant content approval or current confirmation.

SUPPLY_EVIDENCE still requires nonempty citations that resolve against the pinned
source registry, writes the selected side's sources and observation evidence,
records the canonical change and clears previous confirmations. Forged/missing
citations leave the task open without a new revision. Exact replay retains the
committed receipt and current response permission is still checked.

The canonical evidence-response regression now exercises memory and optional
SQLite storage with raw confidence 0/0.5/0.95, proposed confidence None/0/1 and
valid/missing/forged evidence. Both ordinary correction and evidence supply use
the same confidence semantics.

## Performance observations and disposition

| Review item | Local disposition |
| --- | --- |
| 1. Repeated snapshot capture in list_revisions | Fixed: reconstruct once per entry and reuse for identity checking and the returned revision |
| 2. Full task namespace scans | Deferred until adapter/runtime convergence; preserve the current schema and transactional behavior in this merge repair |
| 3. Rollback journal reader/writer contention | Deferred; WAL needs explicit sidecar permissions, checkpoint/backup and concurrency validation before enabling the adapter |
| 4. Blocking SQLite work inside async methods | Deferred; moving whole transactions to workers requires explicit cancellation/receipt recovery semantics and concurrency tests, not isolated to_thread calls within a transaction |
| 5. Lease/outbox/retry scan efficiency | Partially improved: skip run lookup for non-running jobs; ordered bounded database selection and scan indexes remain pending |

The target already contains a separately integrated SQLiteReviewStore. Reconcile
runtime choice before applying schema, threading and journal-mode changes to the
optional adapter. It remains unwired. These deferred items are prerequisites for
claiming appropriate runtime responsiveness/capacity, not bugs declared fixed by
this patch. No new storage architecture is introduced to resolve a text conflict.

## Verification

The focused human-response, revision, job-store and SQLite regression suite passes
283 tests, including the 54-case canonical evidence-response matrix. Ruff check, format check
(375 files) and mypy (222 source files) pass against the combined local tree.
The complete backend unit/integration run reached approximately 50% before its
180-second command timeout (exit 124), without a completed result. This is not a
full-suite pass. No failure marker appeared in the captured progress before the
timeout. A separate complete unit-suite attempt also reached approximately 50%
before its 120-second timeout (exit 124), with no completed pass/fail summary.
Neither broad attempt is acceptance evidence. The focused 283-test result above
is the completed regression evidence for this repair; a longer full-suite run
remains necessary before claiming all-backend validation.
All tests use synthetic fixtures. The actual competition documents and Excel
writer planning file are preserved and are not part of this repair.
