# Implementation backlog after the integration merge

Updated 2026-09-12. The implementation baseline is merged [PR #45](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/45),
main `d148422adb18190bada93b8588a4e34d73e3c2e4`, whose runtime tree matches
`fd22e68321bad6b58f06068dfef1db67fdb1c269`. Subsequent documentation commits do not
represent new runtime validation. [Project progress](project-progress.md) records
current capability; [validation evidence](local-validation-record.md) records
exact checkpoints and environment limits.

The follow-up candidate adds ADRs 0048-0050: exact model routing proofs, bounded
local restoration diagnostics and explicit synthetic container composition.
Their PR evidence identifies the new source/image checkpoints. The baseline
counts above must not be reused as validation of these additions.

The current remote main snapshot is `f241e7a479d3135e81bb47bcf9a94f2685492fdf`.
Follow-ups #52 and #53 are integrated into `feat/local-validation-stack` at
executable checkpoint `add3c0e23dc978dff082450d6475ace1e599261c`.
[Current local evidence](followup-local-validation-record.md) includes full tests,
the installed wheel, actual image core, a complete original synthetic privacy
flow and a separate paused/automatic/resume flow. The 176-finding image OS gate
and exact-head hosted CI remain separate blockers.

The next local candidate, `feat/local-review-workbench`, starts at the exact
Draft #54 head `fff14e4e255deb9e72df0c26c1cb3b5afa014a7a`. It adds controlled
original import, five workbench views, separately paired source preview, a
pinned multi-source catalog and canonical fact-response recovery. Its
[KPI1 delivery record](kpi1-delivery.md) separates actual supplied-document
acceptance from synthetic unit regressions and historical CI. Case-condition
editing/confirmation, complete applicable rules and real template publication
remain explicit local gaps; they are not excluded merely because cloud work is
deferred. No Issue closure or formal approval is implied by this candidate.

The local integrated core is usable for verification. One complete synthetic OCR
restoration was reported successful; repeatability remains work. Cloud deployment,
designated-model quality and formal business approval remain unaccepted. The
table below tracks remaining work rather than recreating completed components.

## Active work and dependencies

| Issue | Remaining deliverable | Start condition / dependency |
| --- | --- | --- |
| [#48](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/48) | Explicit synthetic container and separate host companion implemented and exercised at the recorded checkpoint; independent review, supported-platform and production acceptance remain | Follow the [local validation stack](local-validation-stack.md); coordinate #22/#25 production provisioning. Existing AWS Docker default remains unconfigured |
| [#25](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/25) | Browser onboarding, trusted local file import, case/job creation and listing, session lifecycle and complete operator workflow | Build against current local contracts; coordinate deployed identity/transactions with #24 and #30 |
| [#47](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/47) | Fresh routing discovery now binds each requested model's exact approved destination set in the guarded composition | Local SDK/HTTP regressions implemented; independent review, hosted CI and real invocation acceptance pending; see ADR 0048 |
| [#49](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/49) | Obtain actual exact-commit hosted verification; current inspected jobs never started | GitHub reports payment or spending-limit restrictions; account-owner remediation is required before separately authorized CI execution |
| [#50](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/50) | Remediate and verify the intended image's OS vulnerability findings | Scan exact image/base/lock inputs; supported fixes or explicit pending human risk decisions |
| [#24](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/24) | Cloud human-task/revision/receipt transactions and atomic resume/outbox integration | Reuse implemented host-local SQLite semantics; provide production persistence and identity interfaces to #30 |
| [#30](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/30) | Configured AWS API/worker/publication entrypoints and approved competition deployment inputs | #24 and #47; effective role/data/model/budget/resource/stop approval; release evidence from #49/#50 |
| [#22](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/22) | Bounded diagnostics and isolated browser configurations implemented; corpus repeatability and supported local key/map/isolation operations remain | Preserve failed and successful runs separately, exact two-stage review and every original observation; see [OCR review](local-privacy-ocr-review.md) |
| [#21](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/21) | Designated-model extraction and controlled-selection evaluation against reviewed cases | Exact model/routing/data/budget approval; quality metrics are separate from the local injected-model evidence |
| [#26](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/26) | Approved rules, templates, CJK fonts, maps and independent formal acceptance cases | Authorized assets/reviewer input; keep them private and retain existing synthetic goldens |
| [#31](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/31) | Deployed browser-to-AWS acceptance, live collectors and recovery/rollback/stop evidence | The applicable work above, separately authorized environment and actual reviewer participation |

No GitHub assignee is inferred from historical member labels. Each linked issue
defines concrete unchecked acceptance criteria, prerequisites and the evidence
needed to close it. Issue creation or a passing synthetic test grants no AWS,
model, material, financial-data or deployment approval.

## Recommended execution order

1. Review the #47/#22/#48 follow-up stack and its exact-checkpoint evidence.
   Continue #25 for the missing user flows and #50 for image remediation; #49
   needs account-owner action. These workstreams do not require a paid model run.
2. Complete #24, then wire #30 using the actual persistence and authority providers.
   The checked-in pending competition profile must continue to reject execution
   until real operator inputs and approvals exist.
3. Continue #22, #21 and #26 with their own prerequisites and evidence. A single
   successful OCR download is not a reliability measurement; model output and
   synthetic signatures are not formal approval.
4. Run #31 last against the intended release image/configuration with the actual
   browser and deployed services. Record failures and recoveries as observations.

## Operator inputs owned by #30

The competition profile needs an exact account/role and effective permission
evidence; one primary region; approved models and complete routing; agreed request
scope and a shared dispatch store; conservative pricing and a durable budget
window; invocation authentication; a data-policy pin; an owned resource inventory
and a tested stop procedure. Resolve organizer clarification before admitting
synthetic financial data. Sanitization alone does not permit any of the thirteen
prohibited categories. Retain immutable object versions and audit evidence when
stopping computation. These inputs cannot be replaced by test approval fixtures.

## Consolidated historical work

| Historical issue | Integrated scope | Remaining work has an active owner |
| --- | --- | --- |
| #5 | Deterministic writer, protected original/download paths and S3 boundary | #26 formal assets; #30 composition; #31 live output acceptance |
| #7 | Parser, candidate extraction, source binding and separate confirmation/approval | #21 model quality; #22 privacy/OCR operations; #26/#31 formal review |
| #8 | Deterministic semantics, source/coverage checks and refusal of incomplete evidence | #26 approved full-case rules/answers; #31 actual reviewer acceptance |
| #9 | Documents, durable jobs, outbox, attempts and the integration plan | #24 cloud human transactions; #30 deployment/profile inputs; #31 live recovery |
| #17 | Controlled/bounded actions, causal trace, persistent budgets and local human re-entry | #21 model comparison; #24/#30 cloud execution; #25 user workflow; #31 acceptance |
| #28 | Attempt-scoped artifacts, fenced manifests and reauthorized downloads | #30 deployed composition; #31 real storage/recovery/retention observations |

Closing these historical work items consolidates their remaining acceptance into
the linked issues; it does not mark formal assets, actual cloud behavior or model
quality as accepted. Their original descriptions and discussions remain evidence
of earlier checkpoints, not the current backlog.

PRs #34-#38, #42, #45 and #46 are merged. The complete heads of stacked PRs #39,
#43 and #44 are ancestors of the integrated main and are closed as incorporated
through #45. Their branches and discussions are retained; no separate merge of
their old stacked bases is needed. Future implementation starts from current main
on a functional branch and references its active issue.
