# Project progress

## Current status

The local integration worktree is assembling reviewed component repairs into one
configured service. **The combined local/browser release is not yet accepted;
AWS deployment, live model quality and formal business approval are not
accepted.** This page is the current status authority. Older branch/main/CI
snapshots are preserved only in the [history index](history/2026-09-11-pre-convergence/README.md).
No test total is used as a completion percentage.

Component code being present, an isolated test passing, a configured service
being integrated, and an accepted user workflow are distinct claims. The
[traceability matrix](delivery-traceability.md) records those distinctions and
required evidence. The integration branch preserves original PR histories;
local integration merges do not mean those PRs were approved or merged on GitHub.

## Work lines and next acceptance

| Work line | Current component delivery | Required next evidence |
| --- | --- | --- |
| #34 PDF | Downloaded artifact alias protection, immutable approved font bytes, literal-True capability and real multi-context registry/write/backfill regressions | Complete integrated manifest coverage and actual download/backfill; formal CJK/template/map approval |
| #35 publication | Restored publication implementation and current job/attempt/source/grant conditions; local SQLite publication composition being joined | Same-database authority/grant revocation race and actual authorized download through the service |
| #36 workflow | Controlled action/receipt failure repair and persistent run reservation/budget recovery | Canonical #38 handoff, source-bound resumed work and combined job/ledger recovery |
| #37 privacy | No-op/group/category edits preserve raw evidence; mapping is persisted for the exact reviewed export | Actual local bridge/browser export and restoration; failure must cause zero transfer |
| #38 human tasks | Applied-value receipts, owned empty-task reads, rejection projection and idempotent revision registration | Complete revision/task/job/outbox/receipt transaction through real API; exact source and approval rebinding |
| Local SQLite mode | New combined job/task/result store with real file, transaction, process-race and crash/restart tests | Review and service composition; publication/source tables and workflow ledger remain explicit integration boundaries |
| #39 workbench | Existing consumer requires the canonical union and authoritative subject/unit projection | Generate/verify without drift; immutable confirmation/retry command, body-timeout recovery and real browser API flow |
| #42 extraction | Actual parser/snapshot/extraction/evaluation adapters and conservative budget controls | Report known partial usage without inventing unknown totals; run source authorization and live model evaluation separately |
| #43 Runtime | Durable job/result adapters, worker/recovery and packaging; configured local composition being assembled | Deadline before late heartbeat/publication, restart and outbox recovery, actual packaged success; live deployment later |
| #44 acceptance | Strict evidence validator and scoped collectors | Independently observed complete local/browser scenario matrix; live collectors stay unaccepted when not executed |

The deterministic review core, original-cell checks, confidence/source gates and
full-case golden protocol remain required foundations. No owner is permitted to
replace those checks with a success-shaped fixture, increased confidence or an
implicit approval.

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

## Local evidence recorded in this repair round

| Scope | Observed result | What it does not establish |
| --- | --- | --- |
| PDF repair on original PR worktree | Initial 10 failing regressions; corrected full suite 686 passed, with actual PDF reopen, multi-context fields and unchanged sources | Full merged-service/browser acceptance or production CJK typography |
| Combined SQLite store | 45 dedicated checks including 31 existing JobStore contract checks; broader job/human/API selection 410 passed | Entire evolving integration checkout, AWS transactions, source grants or browser acceptance |
| #36 integration merge | Integration owner reports 78 focused checks passed before the workflow merge | Regenerated #39 consumer, whole-service acceptance or live model behavior |

Evidence counts are scoped observations from their own tested trees. Do not add
them together or present them as an exact-head full integration gate. Logs and
synthetic artifacts remain ignored under their worktrees' `artifacts/` directories.
The integration owner must attach final exact commit/configuration/command
records after its complete rehearsal. Remote CI is not refreshed by this
Markdown consolidation; earlier CI links remain historical.

## Required integrated rehearsal

Use a small synthetic case, isolated stores and clearly designated test assets.
Observe actual parser, HTTP, deterministic review, writer, reopen, publication,
authorized download and local backfill. Model responses may be injected; that
must remain visible in the evidence classification.

1. Inspect the exact sanitized payload. Mapping persistence/key/confirmation
   failure causes zero transfer, and original/map canaries do not reach cloud
   request bodies, keys, metadata or logs.
2. Read a legitimate job with no tasks, then complete a normal case. The PDF and
   complete manifest must belong to this exact run and attempt.
3. Run needs_review, explicit correction/confirmation and two revision rounds.
   Scores do not increase and obsolete confirmation/approval cannot be reused.
4. Retry identical commands and restart processes. Preserve idempotency,
   reservations, trace failures, job fences and immutable result selection.
5. Refuse other principals/cases, revoked sources, wrong versions/hashes, stale
   approvals and timed-out/cancelled attempts. Blocked review calls no writer.
6. Download and restore a multi-context artifact to a new local PDF while the
   original template and downloaded placeholder bytes remain unchanged.

Live AWS acceptance additionally needs explicit account/role/region/model and
budget scope, actual IAM/storage/worker recovery, package/scan gates and operator
observations. Formal rules/fonts/templates and human approval are separate gates.
None is cleared by documenting it or by accepting a synthetic signature.
