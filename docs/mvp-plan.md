# Delivery plan and ownership

Current integration baseline: main
`d148422adb18190bada93b8588a4e34d73e3c2e4`, reviewed on 2026-09-11.
[PR #45](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/45)
is merged; its source tree matches reviewed head
`fd22e68321bad6b58f06068dfef1db67fdb1c269`. The integrated local application is
available for user validation. Production deployment and competition acceptance
remain separate deliverables. Start new work from current main.

The [implementation backlog](implementation-backlog.md) is the current issue
index. Historical phase reports retain their original evidence and dates; their
unmerged-branch and process-local-only descriptions do not describe this baseline.
No role below implies a GitHub assignee.

## Delivered integration

| Area | Available on main | Remaining boundary |
| --- | --- | --- |
| Deterministic review and controlled execution | Versioned rules, evidence checks, bounded decisions, execution traces, completion gates and legacy HTTP/invocation compatibility | Designated-model quality and independent full-case acceptance |
| Human review | Canonical task/response contracts, authenticated API, immutable revisions, receipt replay and SQLite persistence | Atomic cloud human/revision/run transitions and production identity integration |
| Reviewer workbench | Real API task/response flow, source citations, PDF preview, result download and local privacy/OCR review | Complete case creation, document onboarding, identity/session experience and broader product acceptance |
| Privacy | Local sanitization, explicit export authority, encrypted mapping, exact output binding and reviewed OCR refill workflow | Repeatable OCR restoration, diagnostics, key lifecycle and operational recovery |
| PDF and publication | Multiple-context manifests, approved font/template checks, immutable output, authorized publication and download adapters | Formal approved assets, complete-case goldens and live cloud publication evidence |
| Jobs and Runtime | Durable local service, DynamoDB/S3/SQS adapters, leases/fencing, outbox/recovery logic and guarded competition factories | Configured production worker bootstrap, effective IAM and deployed recovery evidence |
| Competition controls | Source-pinned service/quota catalogs, data-admission policy, profile checks, shared dispatch limiter and budget reservations | Exact model/routing binding repair, approved live profile and independently observed enforcement |

The integrated local runbook provides the current validation entrypoint. Real
browser/API checks and local restart/download checks have been recorded. One
complete OCR restoration success is reported for the integrated head; this is
not a stability claim. Earlier failed attempts remain relevant evidence.
See [project progress](project-progress.md),
[local validation](local-validation-record.md) and the
[integrated local runbook](integrated-local-runbook.md) for their exact scopes.

The default Runtime application still has no configured worker and returns 503.
The existing Runtime Docker image does not by itself provide a complete browser,
API and persistence installation. Local Docker packaging and cloud bootstrap are
distinct remaining tasks. The latest pre-merge hosted CI failed without executing
test steps; reported local results do not make that run pass.

## Remaining workstreams

| Workstream | Current issue | Next concrete delivery and exit evidence |
| --- | --- | --- |
| Model strategy and quality | [#21](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/21) | Run the designated model against independently reviewed labels; record field/source accuracy, missing or wrong values, latency, actual usage and unknown costs without granting authority to model output |
| Privacy operations | [#22](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/22) | Reproduce restoration across repeated supported inputs, retain bounded error diagnostics, and verify mapping/key recovery without changing raw confidence or weakening confirmation gates |
| Cloud human transitions | [#24](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/24) | Compose authenticated task, revision, receipt and next-run changes into the cloud transaction boundary; prove duplicate, conflict, stale-version and partial-failure behavior |
| Product workbench | [#25](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/25) | Add case/document onboarding and identity/session handling around the existing review UI; verify real API flows and unauthorized access |
| Formal outputs and independent goldens | [#26](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/26) | Pin approved template/font/map assets and independently reviewed complete-case expectations; reopen outputs and verify required fields, geometry and unchanged sources |
| Cloud Runtime composition | [#30](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/30) | Configure the guarded execution/provider bundle in the actual Runtime entrypoint, with truthful readiness, persistent stores and one reviewed deployment inventory |
| Deployed acceptance | [#31](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/31) | Exercise the integrated browser-to-cloud path, human wait/resume, authorized PDF download, fault recovery, alarms, rollback and evidence-preserving stop |

Local Docker packaging is tracked in
[#48](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/48),
hosted CI in [#49](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/49),
image security in [#50](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/50),
and exact model routing in
[#47](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/47).
Their dependencies are recorded in the [implementation backlog](implementation-backlog.md).
Keep their acceptance evidence distinct from the historical component test counts.
Completed or superseded component issues are reference history; do not recreate
their implemented scope under a new umbrella issue.

## Delivery sequence

| Stage | Work and dependencies | Exit evidence |
| --- | --- | --- |
| 1. Reproducible local validation | Use merged main and the configured local workbench; package the browser, API and durable state for Docker | A clean installation opens the UI, completes an authorized review, downloads the result and retains state after restart; unconfigured services remain unavailable |
| 2. Close deployment code gaps | Repair exact model/routing binding; complete cloud human transactions and Runtime bootstrap; resolve CI and reviewed security findings | Focused negative cases plus current integrated checks, configured image health and documented disposition of remaining scan findings |
| 3. Validate designated data and assets | Run model-quality work and formal-asset acceptance; continue privacy/OCR stability work | Independent labels, explicit authority, complete output coverage, reproducible restoration results and retained failure evidence |
| 4. Rehearse one authorized cloud deployment | Consume the preceding deliverables under an approved competition profile and resource inventory | Actual role allow/deny evidence, complete browser/cloud outcomes, bounded recovery, joined telemetry, rollback and verified stop |

Stages can overlap where their inputs are independent. A local validation result
does not authorize cloud transfer or deployment. Competition work must retain the
thirteen-category data-admission rules, conservative shared physical-dispatch
limits and budget reservations. The pending profile is deliberately unapproved;
see [competition deployment](competition-deployment-profile.md).

An attempt ends while awaiting a human. A response creates a new authorized run;
it does not revive the old session or lease. Neither human review nor OCR
correction may manufacture confidence or bypass deterministic completion checks.

## Historical source observations

On 2026-09-06 the official brief and supplied criteria/forms were read without
modification. Criteria had 9 pages; forms 6 pages with portrait/landscape A4 and
landscape A3 and no AcroForm widgets. Regional main road 18m -> normal; individual
front road 18m versus 6m -> +5% under their respective source rules. Regional totals
are copied into the comparison form; blank comparable columns stay blank. A mixed
unit interval remains unresolved. These earlier observations are not new source
or model validation, universal rules or authorization to approve a real case.

Preserve boundary, reverse-matrix, blank-column, contradictory-unit and mixed-page
regressions. See [traceability](delivery-traceability.md),
[architecture](architecture.md), [service contracts](service-contracts.md) and
[cloud smoke plan](aws-smoke-plan.md). Historical branch migration remains in
[PR migration](pr-migration.md); do not rewrite published history.
