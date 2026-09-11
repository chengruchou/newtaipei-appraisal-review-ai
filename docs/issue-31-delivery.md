# Issue 31: Integrated acceptance-evidence delivery

Current integration note, 2026-09-11: this evidence framework and the component
integration are included in merged
[PR #45](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/45),
main `d148422adb18190bada93b8588a4e34d73e3c2e4`. Real local browser/API validation
has been recorded; deployed AWS rehearsal remains incomplete.
[#31](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/31)
is the current integrated cloud-acceptance issue. The missing integrations,
stacked review order and Draft statements below describe the original component
checkpoint. They are not current PR status or a substitute for live evidence.
Use [local validation](local-validation-record.md) and the
[implementation backlog](implementation-backlog.md) for the current split.

## Historical delivery checkpoint

Status: Draft. Related to #31; the actual browser/AWS rehearsal is not complete.

Direct base is PR #43 at 32c5028e17ac4ada86eb9ac911a3d41bb724e203, on top of
PR #42 at fa36fb714c6628bef4f0fd66aae0278372f57fae. This work preserves those
histories through a normal merge. Review order is #42, #43, then this layer.
Exact new head and remote CI belong in the published PR, not a historical run.

## Requirement map

| Issue requirement | Implemented offline | Still required |
| --- | --- | --- |
| Happy and human browser paths | Exact material/source/task/revision/output evidence binding, mandatory probes | Actual integrated authenticated UI, reviewed providers and live execution |
| Idempotency and command replay | Signed scenario invariants and rejection of stale/mismatched receipts | UI timeout interception and durable observed admissions |
| Worker/outbox recovery | Same-run takeover, distinct attempt and dispatch evidence requirements | Actual process replacement and live queue/store observation |
| Failure/DLQ | Mandatory fault, no-output, alarm and terminal/redrive checks | Real fault injection and bounded recovery |
| Authorization/privacy | Independent browser/network/AWS/local collector scopes, finite public data | Complete capture and scoped cloud inventory inspection |
| Output | Actual hash/size/version/manifest/run/template/golden bindings | Reopened authorized download from formal integrated writer |
| Rollback/cleanup | Distinct rollback digest, exact inventory and retained-audit checks | One safe sandbox rehearsal and verified resource cleanup |
| Observability | Nine alarm resources, private dashboard and value-free aggregate query | Metric publisher, owned routing and actual alarm transitions |
| Evidence and CI | Strict schema, independent signatures, 122 checks/16 scenarios, exact tested commit/run pins | Reviewed live collectors, successful current-revision CI and independent human acceptance |

The CLI distinguishes plan, synthetic verification, localhost rejection checks
and unavailable live collection. Missing collectors exit nonzero. Synthetic
receipts always report Draft, never Ready. The stacked integration test requires
the real #43 Runtime module and uses real loopback TCP; it has no absent-module
success branch or substitute provider. Its five probes establish rejection only.

## Dependency and trust boundaries

No fake browser collector or production signing authority is shipped. PR #34-39
remain independently reviewed work; missing publication and human/privacy/UI
integration cannot be bypassed by producing signed success declarations. The
gate authenticates collector attestations and exact bindings, but cannot prove
collector honesty or reconstruct captures. Provision reviewed isolated collectors
and independently inspect retained captures before accepting their signatures.

The monitoring stack grants no permissions and emits no samples. Its resources
alone cannot demonstrate operational coverage. Compose an authenticated publisher
against the reviewed execution bundle; cost samples need actual usage and dated
pricing, not zero for missing telemetry. See [cloud acceptance](cloud-acceptance.md)
and [ADR 0031](adr/0031-rehearsal-evidence.md).

## Verification and operations

Run Ruff, format, mypy, all tests/cloud_tests, immutable goldens, both existing
HTTP/invocation smokes, the rehearsal TCP tests and cfn-lint for all six templates.
The PR records final local counts, warnings and exact head CI separately.
Image execution evidence from #43 binds that earlier exact source/image; it does
not establish an image of this new head. A future release must build and test
its precise integrated revision and satisfy every additional required CI gate.

The existing submission checker is unchanged and inspects complete working/index
snapshots, every outgoing commit tree and Git metadata, branch and actual PR
text. No actual receipt, private source, credential, original PDF, generated PDF
or raw capture is submitted. The original dirty checkout and teammate changes
are preserved. No Issue is closed and no PR approved or merged.

No AWS account operation, model inference, deployment, billing change or real
rule approval was performed. After reviewed integration and successful CI, live
setup requires the designated short-lived profile/SSO, Region, account/role,
model/routing, budget, owned prefix/lifetime and data-region restrictions. Use
the #30 runbook; do not deploy the unavailable default application to manufacture
an acceptance result.

## Pre-publication regression correction

An actual loopback adversarial server reproduced a false pass: an escaped
synthetic canary in the first duplicate message key was discarded by ordinary
JSON parsing, so the CLI returned zero despite the leaked response. The new
regression failed on that exit-code assertion before the fix. The runner now
rejects duplicate JSON keys and checks decoded values; the same server must
return a failed validation report with finite reasons. Legitimate unready
Runtime responses still pass their explicitly limited rejection probes.

Integrated local check before the packaging follow-up (macOS/Python 3.13.5):
1720 repository tests plus 12 cloud tests passed, 801 warnings, 89% displayed
combined statement/branch coverage. Warnings remain visible and include SQLite
connection ResourceWarnings and dependency deprecations. Ruff/format (265 files),
mypy (125 source/script files), 14 golden manifests, both local HTTP/invocation
smokes and six CloudFormation templates passed. The new loopback regression
first failed with CLI exit 0, then all five Runtime HTTP integration tests passed.
This record is local evidence, not head CI or live acceptance.

## Packaging follow-up integration

PR #43's security follow-up is integrated through another normal merge. It
updates both image targets to Python 3.12 and compatible fixed Pillow wheels,
preserves license notices and removes the complete build-only installer.
Remaining OS/vendor scan findings continue to block deployment. The #43 image
receipts bind its own exact source; no image of this #31 head is claimed.
The complete post-merge suite passed 1722 repository tests plus 12 cloud tests
(1734 total, 7 dependency warnings) in 116.31 seconds without coverage collection.
Ruff/format, mypy, all 14 golden manifests, both localhost HTTP/invocation smokes
and all six CloudFormation templates passed again. The earlier coverage run above
is separate evidence; neither local run substitutes for the exact new-head CI
recorded in the final PR.
