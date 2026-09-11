# Issue 30: Durable Runtime delivery

Status: Draft. Related to #30; does not close #29 or imply cloud acceptance.

This branch continues main c132e4ee4b1797098bd22676245cdfc01a26ffdb and normally
merges extraction PR #42 at fa36fb714c6628bef4f0fd66aae0278372f57fae. It targets
feat/model-extraction-evaluation because its C2 integration tests use that
branch's actual snapshot/parser path. Review #42 before this layer, then #31.
Publication records each new head and its own CI; main and local results are
not substituted for head CI.

## Scope and acceptance map

| Issue item | Implemented in this delivery | Remaining gate |
| --- | --- | --- |
| 1, actual composition | Explicit real DDB/S3 builder, bounded worker, sanitized fail-closed app | Reviewed execution/directory/revision/provider bundle |
| 2, persistent control plane | Transactional job/run/attempt/outbox, recovery index, SQS bridge/dispatcher and C2 admission | Live restart, IAM, consistency and DLQ evidence |
| 3, human/PDF/publication | Shared contracts preserved; persisted task IDs required | PR #34-#39 findings; publication module; cross-store human transaction |
| 4-5, Runtime protocol | ARM64 HTTP 8080, strict invocations, cheap readiness, actual local image verification | Configured health and live Runtime invoke |
| 6, reproducible deployment | Hash-locked dependencies, two image targets, IaC, build receipt and manual rollout/rollback | Accepted image scan, ECR push, create/update/rollback |
| 7-8, IAM and limits | Separate roles, exact resource/prefix scopes, bounded processing/concurrency, tags/retention | Sandbox role tests and production network review |
| 9, correlation | Opaque job/run/attempt/dispatch identities and safe finite errors | Joined live application telemetry and dashboard evidence |
| 10, execution failures | SDK shape/emulation, lease/cancel/timeout/source/5xx regressions | Actual deployed failure injection |
| 11, operations | Deployment, rollback, retention-aware cleanup and troubleshooting commands | Independent operator rehearsal |

The default app deliberately remains unavailable when providers are missing.
No in-memory store is used by production adapters. Moto tests reconstruct stores
against emulated service state, but do not establish actual AWS persistence or
cross-process recovery. A durable service result is not a fenced PDF manifest.

## Dependencies and ownership

The dependency heads recorded in [Issue 21](issue-21-delivery.md) remain unmerged
and are not copied wholesale. In particular PR #35's tree lacks its publication
module, #38 has confirmation/response issues and #39 depends on #38. Formal
writer, privacy export, model selection and workbench reviews remain with their
owners. Infrastructure and worker tests use explicit isolated synthetic values;
they grant no authority to a real case or policy.

## Validation and operations

Run the repository quality commands, tests and cloud_tests, immutable golden
comparison, both HTTP smoke scripts and the cfn-lint commands in README. Focused
regressions are test_dynamodb_job_store.py, test_runtime_worker_storage.py,
test_runtime_source_binding.py, test_runtime_jobs_protocol.py and
test_runtime_infrastructure.py. The PR records final command counts, warnings,
image identifiers and the exact remote run after publication.

[ADR 0026](adr/0026-dynamodb-job-store.md),
[ADR 0027](adr/0027-runtime-composition.md) and
[the Runtime runbook](runtime-deployment.md) describe the migration boundary.
Use new named resources; do not repurpose the incompatible historical Cases
table. No resource was created, model invoked or real rule approved. Existing
Git history and the original dirty checkout remain intact. Pending live setup
must name the profile/SSO, region, account/role, model routing, budget, resource
prefix/lifetime and data-region restrictions. Credentials never enter Git.

Final local integration check (macOS, Python 3.13.5): 1597 repository tests plus
12 cloud tests passed, 7 dependency warnings (Starlette/httpx, AnyIO, PyMuPDF).
Ruff/format passed (256 files); mypy passed (122 source/build files); 14 golden
manifests matched. Actual loopback HTTP, invocation and reopened synthetic PDF
smokes passed. All five CloudFormation templates linted. SDK service emulation
and loopback execution are separate from live AWS and browser acceptance.

## Packaging scan correction

The initial Python 3.11 / Lambda AL2 packaging scans failed. The follow-up moves
both images to Python 3.12 with an AL2023 Lambda base, pins compatible ARM64
wheels and Pillow 12.3.0, upgrades the build installer with a wheel hash, then
uninstalls that complete build-only tool after preserving its license notices.
It does not remove individual vulnerable files or scanner metadata while keeping
the affected code. Installed runtime dependency licenses remain intact.

The full local suite passed 1611 tests with 7 warnings after the Python/lock
update; final installer-removal infrastructure/protocol regressions also passed.
Both native-ABI images are checked with generated PDF parse/render/reopen probes,
separate from production provider acceptance. OS and vendor findings remain
unsuppressed Draft gates. Exact clean-head images, final scan totals and new CI
are recorded in PR #43; earlier scans retain their own source/image binding.
