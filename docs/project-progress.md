# Project progress, 2026-09-10

## Current work, 2026-09-11

Main is `c132e4ee4b1797098bd22676245cdfc01a26ffdb`, including merged #32,
#33, #40 and #41. The current sequence is #21 extraction/evaluation, #30 durable
Runtime deployment preparation, then #31 cloud/browser rehearsal preparation.
See [Issue 21 delivery](issue-21-delivery.md) for exact dependencies, implemented
additions and remaining gates. The sections below retain the earlier C2 delivery
history; their branch and CI statements are historical snapshots.

## Re-established baseline

The competition requires valuation case review. PDF completion is an output of
an evidence-bound, deterministic review, not the entire application. The reviewed
baseline is main `463880af3a6dc6aad2bfa6fdfc3bc267afc4d4a5`, the merge of
[PR #33](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/33).
It includes #15/#16/#19 and the M0 foundation and follow-up fixes in #20.

The implemented core parses authorized local documents, preserves source evidence
and measured confidence, prepares proposed rules/facts, admits explicit human
confirmation and exact material approval, calculates grades/corrections/totals,
and verifies original cells and derived fields. A configured local service uses
the actual parser, controller and optional PDF writer. The writer preserves the
source and creates a separately verified output. HTTP/invocation compatibility
and structured preflight diagnostics are present.

The #20 revision fix preserves native status only across unchanged native sides.
Legitimate explicit human confirmation, including confidence zero, remains valid
and separate from precise material approval. An original receipt applies only to
its unchanged original material; a revised material cannot reuse a confirmation
or approval that does not match the required exact binding.

#33 adds independently derived full-case goldens and reviewer protocol: 13
case/revision manifests plus an index. Candidate rules still need business approval;
golden success does not establish accuracy on real cases or complete formal CJK
output. The main commit's workflow lookup returned no runs during this refresh;
neither a PR-body test count nor historical CI is treated as fresh local evidence.

## Open work and integration order

These statuses describe inspected implementation and issue scope, not estimates
of teammates' completion or approval. Open issues can contain merged partial work.

| Issue | Work line and current boundary |
| --- | --- |
| #17 | Controlled model action selection, trace and human handoff; active branch separate from main |
| #21 | Real Chinese extraction/evaluation; consumes authorized sanitized page inputs |
| #22 | Local privacy processing, original/map isolation, exact export confirmation and local rehydration; active branch |
| #23 | Full-case goldens merged through #33; formal business/reviewer acceptance remains |
| #24 | Authenticated persisted human tasks, revisions and precise approvals; a new work branch is published |
| #25 | Browser private upload, review workbench and result flow |
| #26 | Formal CJK and multiple-context PDF output; a new work branch is published |
| **#27** | **This branch: controlled sanitized ingestion, authorized document versions and immutable run sources** |
| #28 | Fenced artifact publication and authorized downloads; a new work branch is published |
| #29 | Durable jobs, outbox, leases and recovery; active branch |
| #30 | Full AgentCore Runtime assembly and deployment |
| #31 | Full AWS and browser/privacy rehearsal |

Older #5/#7/#8/#9 remain broader PDF/extraction/review/cloud tracking issues. They
do not mean the corresponding local core is absent. #27 does not take over these
work lines or approve their real rules or materials.

Read-only coordination used these published branch snapshots:

| Branch | Inspected head | Relevant interface |
| --- | --- | --- |
| feat/local-privacy-pipeline | 6131957d8859ddf47c71cab8eb4130614efeeb77 | PrivacyManifest, PrivacyExportPayload, confirmation/sink and phase-7 gate |
| feat/model-extraction-evaluation | 6043aaae897475659f8fff5e7d8249fd4c7a0295 | SanitizedSourceReference, PageRequest, AuthorizedSanitizedSnapshot |
| feat/durable-review-jobs | 644a34ff5d9c3d728ddaf3dfaf3e55dc09f54438 | Durable run/revision admission and worker assembly |
| feat/controlled_model_selection | 98c7f34efe70955ca37f0955a175db31373cd24a | Bounded policy decisions over shared contracts |

A final fetch preserved main at the same baseline and found additional published
branches: `feat/human-task-api` at `4911c2b6ce70ffe7f4191cee6d6d9b30c76f8ef0`,
`feat/formal-pdf-output` at `ca4149439ce65dbf8176043aadad13b5d76ba0f9`, and
`feat/artifact-publication` at `debf034b60e2c50f62aa9dc58b7006d13285280d`.
Their publication does not establish integration or acceptance here. D1's updated
head was checked for contract changes; its outbox/recovery changes preserve the
DocumentReference boundary. No teammate branch was modified or incorporated wholesale.

The practical sequence is A3 exact local export, C2 document admission, D1 pinned
run admission, A2 authorized extraction, human review and deterministic checking,
C1 output, C3 publication, then D2/D3 cloud assembly and acceptance. Independent
contract work may proceed in parallel. The integration has not been demonstrated
end to end merely because the individual branch interfaces exist.

## Publication refresh, 2026-09-11

Remote main is now `b0917732cb0bd4597cb7a098421a2dd19a4cea28`, following
[PR #32](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/32).
It adds review-job state/recovery and mounted routes with an injected store; its
reference store is in memory, not evidence of deployed durable infrastructure.
This branch remains based on the earlier #33 snapshot above. The PR targets main;
GitHub CI checks the proposed merge independently of the local branch tests.
A3 advanced to `ec8367a`; its three adopted shared files still match the pinned
contract byte-for-byte. Published teammate updates were preserved.

## This branch

`feat/versioned-document-transfer` starts from the above main, in an isolated
repository-local worktree. The older working checkout and its untracked
`cloud_tests/` copy were preserved. [The C2 contract](document-transfer.md),
[ADR 0024](adr/0024-versioned-document-transfer.md) and
[storage runbook](../infra/documents/README.md) record scope and remaining acceptance.

The implementation offers the controlled ingestion service alternative allowed
by #27. It does not yet mount browser upload routes or wire the A3/D1
applications. No AWS account, real source material, model call, business approval
or deployment is required for its local checks.
