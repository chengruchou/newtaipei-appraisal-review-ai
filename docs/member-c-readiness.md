# Member C: source catalog, rules and AWS readiness

Recorded 2026-09-12. Working tree baseline: `main`
`f241e7a` (integration baseline `d148422`, merged
[PR #45](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/45)).
No commit, push or pull-request action was taken.

Taking this over? Start with the
[continuation guide](member-c-continuation.md), then use this document for the
recorded state and evidence behind it.

What this document claims: the five organizer originals were supplied locally,
inspected read-only, pinned by digest and parsed into a source registry through a
direct non-privacy path; a live AWS account was inspected read-only and its result
recorded; and the code involved has local checks passing. What it does not claim:
no AWS resource was created, no case material was transmitted to any model, no
model produced a candidate that entered a case, no deployment happened, no rule set
was approved, and no PDF was rendered from a workbook. `live_acceptance` remains
`not_performed` everywhere.

## Scope change for this batch

A reviewer confirmed with a professional that this batch of standard official forms
needs no privacy processing. The flow for it is upload, parse, check data and rules,
calculate, fill the official workbook, render that same workbook to PDF, preview and
download. No masking, privacy scan wait or restoration step is involved.

That scope is recorded per source in `configs/sources/newtaipei-shulin-1110901.json`
as `handling: direct_non_sensitive` with a named `handling_reference`, so the front
end and back end read the same recorded decision and no runtime confirmation step is
introduced. Nothing reports a privacy step as passed. The privacy modules are
unchanged and a new case still starts at `privacy_required`. See
[ADR 0050](adr/0054-direct-non-sensitive-source-path.md).

This does not admit the material to a cloud model. Local direct processing and cloud
data admission are separate decisions and only the first has been made; the case
still carries valuation amounts.

## AWS: Ready, Missing, Blocked

Observed read-only in `us-west-2` on 2026-09-12 between 01:56 and 02:20 UTC, as an
assumed Workshop Studio participant session. Account and role are recorded
privately; the report digests them unless the operator asks otherwise.

| State | Input | Evidence |
| --- | --- | --- |
| Ready | Primary region | `us-west-2`, one of the two permitted primaries |
| Ready | Caller identity | STS resolved an assumed participant session in a single account |
| Ready | Model entitlement | `anthropic.claude-sonnet-4-6`: `authorizationStatus` AUTHORIZED, `entitlementAvailability` AVAILABLE, `regionAvailability` AVAILABLE, lifecycle ACTIVE, input modalities TEXT and IMAGE |
| Ready | Model reachability | One minimal `Converse` request through `us.anthropic.claude-sonnet-4-6` returned a normal response. No case content was sent |
| Ready | Inference profile | `us.anthropic.claude-sonnet-4-6` is ACTIVE and SYSTEM_DEFINED in this account |
| Blocked | Regional routing | That profile advertises destinations in `us-east-1`, `us-east-2` and `us-west-2`. `us-east-2` is not a permitted competition region |
| Blocked | Destination verification | Bedrock control-plane reads succeed in `us-west-2` and `us-east-1` and are denied in `us-east-2`, so the complete destination set cannot be verified |
| Missing | `bedrock:CountTokens` | Denied for this role. No code path may call it, and the profile still has to declare it in the action ceiling |
| Missing | Effective role permissions | Not provable from a probe. An operator permission-evidence digest is still required |
| Missing | Sanitized document bucket | No S3 bucket exists in the account |
| Missing | Shared dispatch table | No DynamoDB table exists, so `throttle.central_store_arn` cannot be filled |
| Missing | Deployment resources | No CloudFormation stack, ECR repository or AgentCore runtime exists |
| Missing | Model invocation logging | Not configured |
| Blocked | Case data admission | The case carries valuation amounts, which fall under the prohibited financial-information category. No organizer clarification exists |
| Blocked | Organizer originals | None of the five expected files is present on this machine |

Two observations changed during the session and are recorded as observations, not
conclusions. `agreementAvailability` for the designated model read `NOT_AVAILABLE`
at 01:58 and `AVAILABLE` at 02:19 in both readable regions. And the model publishes
`inferenceTypesSupported` of `INFERENCE_PROFILE` only, with no `ON_DEMAND`, which
is why it must be reached through a profile.

### The routing decision an operator has to make

Using `us.anthropic.claude-sonnet-4-6` means a request may be served in
`us-east-2`. Three options, none of which an adapter may choose:

1. Obtain organizer approval for cross-region routing, then set
   `allow_cross_region`, add the region to `allowed_regions` and record
   `routing_approval_reference`. This still leaves destination verification
   blocked, because the role cannot read Bedrock metadata in `us-east-2`.
2. Create one application inference profile scoped to a single permitted region.
   The destination set then contains one region, `allow_cross_region` stays false,
   and every destination is readable. This creates a resource and needs explicit
   approval; it belongs in the profile inventory as a `create_once` `model`
   binding with `deny_exact_model_invocation` as its stop method.
3. Select a model that supports direct on-demand invocation in the primary region.
   This changes designated-model quality and is a competition decision.

Until one is chosen and approved, the readiness report stays blocked and no
extraction run through this model may be treated as compliant.

## Case sources

All five originals were supplied, and every structural expectation matched the real
file. `scripts/check_case_sources.py` reports `sources_pinned`.

| Source | Usage | Registry role | Observed structure |
| --- | --- | --- | --- |
| `brief-case` | case values | `forms` | 6 pages, 0 pages without text |
| `evaluation-basis` | rule candidates | `criteria` | 9 pages, 0 pages without text |
| `form-table-3` | output template | none | 24 worksheets, 23 hidden, visible `表3區段勘查表` |
| `form-table-5` | output template | none | 24 worksheets, 23 hidden, visible `表5-1區域因素明細表(住)` |
| `form-table-4` | output template | none | 24 worksheets, 23 hidden, visible `表4比較法調查估價表` |

Digests are pinned in the manifest. Since neither PDF states an edition, each
document's version is its pinned digest, which names exactly those bytes rather
than inventing an edition number.

**No OCR is needed for this batch.** Both PDFs are unencrypted, contain no embedded
image and have a native text layer on every page. The native parser produced, for
the brief, 6 tables with 1558 table cells, 480 text blocks and 512 selection marks;
for the basis, 9 tables with 2007 cells and 908 text blocks. Citations built from
the parsed registry resolve, and a citation with altered text does not.

### Document structure, read from the originals

The brief is one document containing all three table types, which is what ties it
to the three workbook templates:

| Brief page | Content |
| --- | --- |
| 1–4 | Four table 3 section survey sheets, one per section, in the order `P002`, `P003`, `P004`, `P001` |
| 5 | Table 5-1 regional factor analysis for ordinary residential land, listing the three comparables |
| 6 | Table 4 comparison sheet, landscape, case number `1110901-99-XXX`, already printing difference and adjustment percentages |

The evaluation basis splits by factor class: pages 1–5 are the regional factor
basis for Shulin ordinary residential land, pages 6–9 the individual factor basis
for Shulin residential land. The printed correction bands are present in the
document, so the bands themselves no longer need to be guessed.

One case, four properties. `P001` is the comparison base, printed `P001-00` on
page 4; `P002`, `P003` and `P004` are comparables on pages 1, 2 and 3. Page order
must never decide the role. The effective date `1110901` is present on all six
brief pages and maps to `2022-09-01`.

Two details that will bite a text matcher: the basis spells the base-parcel term
with a variant character on pages 6–9, and brief page 6 already prints worked
percentages that must not be copied in as inputs.

Questions that block work, with their owner. Pinning the bytes resolved none of
them:

| Question | Owner | Blocks |
| --- | --- | --- |
| `P001` is described with both a mass-transit development area and a residential district. Which statutory zoning, actual use and rule-facing category apply? | reviewer | rule selection |
| Parcel area, width and depth, road frontage and facility distances are incomplete. A missing value is not zero and a section road is not a parcel's frontage | organizer | calculation |
| Complete factor weights are not stated. Equal weighting must not be assumed | reviewer | calculation |
| The regional basis prints base section against target section and the individual basis prints comparable property against base parcel. Which axis is the row, and does the sign read from comparable towards base or the reverse? | reviewer | calculation |
| Brief page 6 already prints percentages. Are they the expected answer to reproduce, or an example to derive independently and compare against? | organizer | calculation |
| A floor area ratio difference belongs to a land development analysis and must not be guessed from a ratio of the two figures | reviewer | calculation |
| How many workbook files, under which names, and must hidden worksheets and external links be retained? | organizer | output |
| Is any part of this material admitted for transmission to a cloud model, and in what form? | organizer | recorded, not blocking local work |

No rule set, matrix orientation, interval band or weight is proposed here. The
bands are now known to be printed in the basis, but reading them into an executable
rule set is a reviewer's decision, and getting the matrix orientation backwards
inverts every correction.

## Excel to PDF conversion: converter absent, fonts present

The final delivery step renders the PDF from the same approved workbook, so the
converter must be a faithful renderer and never a second author. See
[ADR 0051](adr/0055-workbook-pdf-conversion.md).

| Component | State |
| --- | --- |
| Headless office converter | **absent**. No `soffice` or `libreoffice` on this host, so `capability()` reports `converter_missing` and `convert()` refuses |
| Traditional Chinese fonts | present. 79 font families cover Han, including Noto Sans and Noto Serif CJK TC |
| Ghostscript, `pdftoppm` | present, but neither converts a spreadsheet |

`extra/libreoffice-still` is available for this host's package manager. Installing
it is a system-level change outside this repository and needs the operator's
approval. Until then the flow stops at the filled workbook, which is the honest
outcome: the workbook is complete and the PDF step is unavailable.

Re-drawing the sheet with a PDF library was rejected rather than used as a
fallback. It would re-invent layout, merged anchors and print range, so it is a
second authoring step and not a rendering of the approved workbook.

Once a converter exists, the adapter verifies the output per run: correct input
digest, a bounded readable PDF, the expected page count, extractable text, and the
presence of required headings. That last check is what catches a font fallback that
renders headings as blank boxes.

## What was implemented

| Change | Purpose |
| --- | --- |
| `adapters/aws/routing_discovery.py` | Read-only, dispatch-guarded discovery of a model's complete destination set, returning a reproducible `destination_snapshot_sha256`. Gives `check_model_destinations` a producer. See [ADR 0052](adr/0052-observed-model-routing-evidence.md) |
| `adapters/aws/extraction_preflight.py` | The required inference type now follows the invocation route: `ON_DEMAND` for a direct foundation request, `INFERENCE_PROFILE` for a profile-routed one. The previous check refused the designated model |
| `scripts/check_aws_readiness.py` | The Ready/Missing/Blocked report above. Read-only, Bedrock calls serialized through `SharedModelDispatcher`, identity digested by default |
| `domain/source_manifest.py`, `configs/sources/newtaipei-shulin-1110901.json`, `scripts/check_case_sources.py` | Declared case sources, subject roles, effective date, per-source handling scope and open questions, plus a checker that measures supplied originals without pinning them. See [ADR 0049](adr/0053-declared-case-source-manifest.md) |
| `adapters/local/case_sources.py` | The direct non-privacy path: pinned originals to a parsed `SourceRegistry` through the existing native parser. See [ADR 0050](adr/0054-direct-non-sensitive-source-path.md) |
| `ports/workbook_conversion.py`, `adapters/local/workbook_pdf.py` | Faithful workbook to PDF rendering with an honest capability report and per-run output verification. See [ADR 0051](adr/0055-workbook-pdf-conversion.md) |

The readiness probe is deliberately not an approved competition entrypoint. It
installs no data-admission gate because it transmits no document, prompt or case
content. `adapters/aws/competition_runtime.py` remains the entrypoint for
anything that does.

## Commands

```bash
python -m venv .venv && .venv/bin/python -m pip install -e '.[dev]'

# Ready/Missing/Blocked against the current credentials. Read-only.
.venv/bin/python scripts/check_aws_readiness.py \
  --region us-west-2 \
  --model us.anthropic.claude-sonnet-4-6:system_profile \
  --permit-region us-east-1

# Add --bucket, --dispatch-table, --profile and --expect-account once they exist.
# Add --emit-identity only when building a private operator record.

# Which organizer originals are present, and do they match the pinned digests?
.venv/bin/python scripts/check_case_sources.py \
  --manifest configs/sources/newtaipei-shulin-1110901.json \
  --file brief-case="$BRIEF_PDF" \
  --file evaluation-basis="$BASIS_PDF" \
  --file form-table-3="$TABLE3_XLSX" \
  --file form-table-5="$TABLE5_XLSX" \
  --file form-table-4="$TABLE4_XLSX"

# Can this host render an approved workbook to PDF?
.venv/bin/python -c \
  'from appraisal_review.adapters.local.workbook_pdf import LocalWorkbookConverter; \
   print(LocalWorkbookConverter().capability())'
```

The two checkers exit 1 while anything is blocked or unpinned. Their output is
private operator evidence and does not belong in Git. Local paths are supplied at
call time and appear in no manifest and no report.

## Verification

Run on 2026-09-12 in a local Python 3.14 environment.

| Check | Result |
| --- | --- |
| `ruff check .` | passed |
| `ruff format --check .` | 549 files already formatted |
| `mypy src` | no issues in 213 source files |
| `python -m pytest` | 3331 passed |
| `python -m pytest cloud_tests` | 12 passed |
| `python scripts/export_openapi.py` | `web/openapi.json` matches the mounted routes |
| `python scripts/generate_goldens.py` | 14 golden manifests match their fixtures |
| `python scripts/scan_secret_exposure.py --repository .` | `needs_review`, findings only in two pre-existing web test files |

Environment limits found while verifying, both of which affect the team and not
only this work:

- `pytest` must be invoked as `python -m pytest`. The bare `pytest` entry point
  produces eight collection errors, because several test modules import `tests.*`
  and `scripts.*` and the repository root is not on `sys.path`. The CI workflow
  uses the bare form, which is consistent with the empty hosted jobs in
  [#49](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/49).
- `scripts/check_submission.py` cannot run here: no Git `user.name` or
  `user.email` is configured on this machine. Git configuration was left
  untouched. Whoever commits must configure their own identity first and rerun
  that check.

The web workspace was not touched and its checks were not run.

## Not done, and who owns it

- Nothing was committed, pushed or published. All changes are local working-tree
  edits.
- No AWS resource was created. The bucket, dispatch table, ECR repository and
  runtime remain absent, and creating any of them is an operator decision.
- `config/competition-profile.pending.json` is unchanged and still rejects
  execution. Account, role, permission evidence, models, budget, resources,
  invocation principals, data policy pin and throttle store remain unset. Filling
  them is [#30](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/30).
- Binding the discovered routing to the actual runtime request, and failing that
  request when the two differ, is still
  [#47](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/47).
- No model candidate entered a case. That path needs an admitted source, a
  resolved snapshot and B's candidate transaction; it cannot be demonstrated from
  a reachability check. It also still requires the sanitized-snapshot contract,
  which the direct local path deliberately does not satisfy.
- No rule set or applicability selection was produced. The registry and the printed
  correction bands exist; turning them into an approved `FactorRuleSet` is a
  reviewer decision.
- No workbook was rendered to PDF, because no converter is installed on this host.
- The organizer originals stay in the ignored `artifacts/` directory. They are not
  committed and must not be.

## Handoff

```text
Role: C
Shared baseline SHA / branch head or patch: main f241e7a; uncommitted working-tree changes only
Contract version / changed files: routing-snapshot-v1 and case-source-manifest-v1 added.
  src/appraisal_review/adapters/aws/routing_discovery.py (new)
  src/appraisal_review/adapters/aws/extraction_preflight.py (route-dependent inference type)
  src/appraisal_review/domain/source_manifest.py (new; handling, declared_version,
    visible_worksheet fields and the direct/registry source selectors)
  src/appraisal_review/adapters/local/case_sources.py (new; direct non-privacy path)
  src/appraisal_review/ports/workbook_conversion.py (new)
  src/appraisal_review/adapters/local/workbook_pdf.py (new)
  scripts/check_aws_readiness.py, scripts/check_case_sources.py (new)
  configs/sources/newtaipei-shulin-1110901.json (new; all five sources pinned)
  tests/unit/test_routing_discovery.py, test_aws_readiness_cli.py,
    test_case_source_manifest.py, test_case_sources.py, test_workbook_pdf.py (new)
  tests/unit/test_extraction_preflight.py (fixture and three added cases)
  docs/adr/0048, 0049, 0050, 0051, docs/adr/README.md,
  docs/competition-deployment-profile.md, this document
Working capability: five organizer originals pinned and structurally verified; both PDFs
  parsed into a source registry through the direct non-privacy path with resolving
  citations; live Ready/Missing/Blocked AWS report; reproducible model routing snapshot
  with a pinnable digest; workbook-to-PDF capability reported honestly as unavailable.
Real data and human review scope: all five organizer files were read locally, read-only,
  and stay in the ignored artifacts directory. Page and worksheet structure, subject
  labels and the effective date were verified against the originals. No case content was
  sent anywhere. Model reachability used a fixed literal prompt with no case content.
Commands, test results, output hashes: ruff clean; mypy clean on 213 files; 3331 tests
  passed; cloud_tests 12 passed; openapi and goldens match; secret scan clean for all new
  files. check_case_sources reports sources_pinned for all five. Source digests are pinned
  in the manifest; the manifest digest is reported by the checker on each run.
Untested / blocked / who continues: Excel to PDF needs a headless office converter
  installed on the host (operator approval required, then C). Regional routing of the
  designated model needs an organizer decision or a reviewed single-region application
  profile (operator, then A). Matrix orientation, weights, the P001 zoning conflict and
  the status of the page 6 worked example need a reviewer or the organizer, and they block
  B's calculation. Cloud extraction of this material still needs the sanitized-snapshot
  contract and data admission, which is a shared-contract question for A. Runtime request
  binding is #47 (C with A). Profile completion is #30 (operator with A). Git identity
  must be configured before any commit (human).
Single goal for the next 45 minutes: with the converter installed, render one filled
  workbook to PDF and record its output digest and page count; otherwise hand E the
  pinned template digests and visible worksheet names and extract the printed correction
  bands from the basis as reviewable candidates.
Services and processes still running: none. artifacts/ holds the five organizer originals
  and aws-readiness-dispatch.sqlite3, an ignored host-local dispatch interval store.
```
