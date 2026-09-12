# Issue 21 integration delivery

Current integration note, 2026-09-11: this component is included in merged
[PR #45](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/45),
main `d148422adb18190bada93b8588a4e34d73e3c2e4`.
[#21](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/21)
now tracks designated-model quality and evaluation acceptance. The dependency
heads and unresolved component findings below describe this historical delivery,
not the merged integration's current status. Use the
[implementation backlog](implementation-backlog.md) for remaining work and
[project progress](project-progress.md) for current evidence.

## Historical delivery checkpoint

This preserves `feat/model-extraction-evaluation` at
`6043aaae897475659f8fff5e7d8249fd4c7a0295` and merges reviewed main
`c132e4ee4b1797098bd22676245cdfc01a26ffdb` without rewriting history.
Earlier extraction contracts, execution limits and preflight are retained.

The additions connect #27's actual `DocumentTransferService` to the native parser
and bounded page backend, add located candidate handoffs, and supply a runnable
evaluation scorer and explicitly opted-in synthetic Chinese probe. Read the
[evaluation runbook](extraction-evaluation.md) and
[ADR 0025](adr/0025-snapshot-extraction-integration.md).

## Probe and environment gate

`python cloud_tests/extraction_smoke.py` makes no provider calls by default.
The live path requires `--execute --profile PROFILE --config PRIVATE_CONFIG
--approved-budget-usd POSITIVE_AMOUNT`. Configuration uses `LiveConfiguration`
with exact Bedrock access policy, extraction configuration, execution budget and
dated `ProbePricing` evidence.
It is operator-private and must not be committed. It must allow exactly two pages,
at most four calls, at most 8192 output tokens and at most 180 elapsed seconds.
Only the script's generated two-page Chinese PDF is admitted, with ephemeral
synthetic export authority. It never approves a real material or rule.

The probe rejects an approved amount below its conservative token-cost ceiling
before constructing clients. It reserves maximum input tokens for every possible
attempt and all allowed output tokens using positive rates dated within 30 days.
Before each Converse attempt, CountTokens checks the exact system/messages/images
against that input cap. Missing/unsupported counts stop paid inference. This
bounded probe currently requires a single-region foundation model with CountTokens
support; the general extraction backend still supports explicitly allowed profiles.
See [the CountTokens API](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_CountTokens.html).

This is a dated token-price estimate, not an AWS account spending limit; non-token
charges, taxes and changed prices are outside its scope. Unknown charged usage
keeps the total estimate null. A later-page exception preserves previous attempt
usage and a known subtotal, and marks unscheduled pages separately. Configuration
and price evidence are reported by digest; no account-bearing model ARN, profile,
key or local path is emitted. Preserve exact private configuration separately.

Live prerequisites remain a designated non-root profile/SSO role, enabled Region,
exact account/role/model or inference profile and routing permission, dated price
evidence and an approved budget/data-residency scope. No live probe was executed.

## Integration gates inspected on 2026-09-11

| PR | Pinned head | Gate requiring independent review |
| --- | --- | --- |
| #34 | ca4149439ce65dbf8176043aadad13b5d76ba0f9 | Formal output/backfill alias and writer capability findings |
| #35 | debf034b60e2c50f62aa9dc58b7006d13285280d | Publication package absent from committed source |
| #36 | 98c7f34efe70955ca37f0955a175db31373cd24a | Decision event/receipt failure handling |
| #37 | ec8367aff74a0a750a89b87a9772e15fbb517e22 | Privacy raw-text preservation on candidate edits |
| #38 | fb901866d7c05e43e68ce2dcd214f002f13517f7 | Human response consistency and rejection projection |
| #39 | 3c601537d18844bda06342a5e65fab36a7321dc0 | Confirmed payload and idempotent retry consistency |

These open branches are preserved, not incorporated as approved dependencies.
This stays Draft. #30 can build durable job infrastructure and #31 can build
fail-closed evidence checks independently. Complete browser-to-AWS review and
authorized PDF publication have not been demonstrated on this baseline.

## Evidence classes

Local parser, SDK-double execution, scoring and HTTP tests are offline evidence.
Each PR's actual full head SHA needs its own CI result. Historical baseline CI
and local checks do not replace it. Real model accuracy, browser privacy review,
operator acceptance and live restart/rollback remain unperformed.
