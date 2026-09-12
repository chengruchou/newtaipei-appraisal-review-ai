# KPI1 local workbench delivery

Date: 2026-09-12, Asia/Taipei. Feature freeze is 06:30, new operations stop at
07:50, and all work stops by 08:00. The implementation continues from Draft #54
at `fff14e4e255deb9e72df0c26c1cb3b5afa014a7a` on
`feat/local-review-workbench`. No cloud or external model service is used.

**Partial delivery; KPI1 has not passed.** The supplied originals support a
working local review and fact-confirmation flow, but required condition
operations, applicable-rule authority, full scoped coverage and designated
template publication remain incomplete. This status uses the revised eight
acceptance gates; successful publication of a Draft PR does not change it.

## Acceptance record

| Gate | Actual result | Evidence and remaining limit |
| --- | --- | --- |
| 1. Controlled real-case import and opening | Implemented and exercised | Preparation 004 reparses three original PDFs, verifies registry/hash/anchors, creates one isolated real case, and the local API exposes its configured job. The case identifier comes from private configuration, not a hard-coded demo. |
| 2. Traceable and confirmed case conditions | Partial; not passed | Evidence-backed district, date, use, section and comparison candidates are visible. Mixed physical use, regulatory zoning, comparison scope and unknown rule-effective periods remain distinct. No complete condition correction/confirmation transaction is available in this composition. |
| 3. Actual multiple-source rule use | Partial; not passed end to end | The actual manual supplies general procedure candidates and the local criteria supply factor rules. Both remain independent through registry, catalog selection, assembly, source-purpose checks and review. A pinned full catalog is re-resolved; applicability and authority block final calculation. The two observed arithmetic diagnostics are not verified case calculations. |
| 4. Human response, calculation and specified-template output | Partial; not passed | Actual browser confirmation returns an exact receipt and resumes a new material/run with confidence zero unchanged. Missing conditions, complete inventory and necessary authority prevent a formal result. No admitted template/map/font or connected original-runtime writer is present, and no new PDF is generated or downloaded. |
| 5. Five usable views and shared navigation | Implemented; final visual record linked below | Configured cases, progress, results, evidence and human tasks use actual APIs. Route transitions are 180 ms and 6 px, with reduced motion. Actual response recovery survives navigation/back without another POST. Desktop and narrow-view checks use real data. |
| 6. Honest error and unknown-result handling | Exercised in the stated layers | Actual HTTP unauthorized requests return 403; wrong versions and superseded tasks return 409 without receipts; missing output capability returns 503. A real committed response is recovered after transport interruption. Natural session expiry and worker recovery use real application state. API checks are not relabelled as browser checks. |
| 7. Affected verification and source preservation | Verification record below | Local unit regressions, contracts, types/build and real-source checks are separate. Original PDFs, raw OCR and confidence remain unchanged; incomplete/unapproved work never becomes a ready report. Hosted CI is reported only for the actual published head. |
| 8. Reproducible checkpoint and evidence | Partial delivery recorded | The runbook, source inventory, private executed scripts, browser screenshots, source/output limitations and interface gaps are recorded. A formal report hash and approved template version are absent because there is no formal output. |

## Implemented scope

- A controlled local importer reparses actual documents and validates pinned
  originals, native value/unit anchors and separately identified manual proposals.
  It preserves complete page inventories and creates proposed material only.
- A local source adapter and separately paired loopback preview require current
  case/purpose authorization, exact source version/hash, Host and Origin checks.
  They do not issue C2 admission receipts or send originals to remote services.
- A pinned catalog selects independent general and district sources by district,
  category, date and consuming scope. Missing, mismatched, duplicate or unknown
  applicability is explicit. Procedure sources cannot authorize factor rules.
- Read-only API projections expose exact current case/rule/source/condition
  metadata and the paused assessment. The full internal catalog stays private.
  SQLite reads do not rewrite state; live authority is never cached.
- Canonical fact responses preserve frozen payloads, idempotency keys, versions,
  exact receipts and new revisions. Retry attempts retain separate Controller
  output and task identities, with abandoned tasks superseded under a fence.
- The frontend uses warm ivory and forest green, readable Chinese, fixed
  navigation, actual statuses, source comparison, protected local session and
  privacy entry, zoomable original pages and explicit unavailable output.

The original receipt applies only to unchanged original material. A new
revision cannot reuse a confirmation or approval that fails exact binding.
Confirmations, corrections, rule approval, exact-material approval and output
authorization remain separate. There is no arbitrary-password login.

## Real documents and scope

One supplied case is replayed in isolated work directories; these are not
multiple independent real cases. The source inventory includes 6 forms pages,
9 district-criteria pages and the 169-page manual: **184 page records**. All
were parsed natively; manual pages 2 and 4 have no native text. A separate actual
Tesseract run covered all **15 forms/criteria pages** at 200 DPI with pinned
Traditional Chinese and English assets. Whole-manual OCR was not executed.

Preparation 004 contains 47 native rule candidates, 37 supported candidate
shapes, three selected rules, four proposed individual fact sides and two
manual-procedure diagnostics. These counts describe preparation, not accuracy.
The catalog and case conditions are still candidates. Original observations
remain confidence zero where recorded; localization is not semantic confidence.

Limited manual inspection covers forms pages 1-3, criteria pages 2 and 7, and
manual pages 1 and 52-54. The actual road-type and width entries match the
limited inspected source values. That does not establish full-case correctness.
The source's unusual distance-unit text remains unresolved and unused by this
case; it is not silently corrected. No rehearsal value is inserted into an
original. See [the detailed real-data scope](kpi1-real-data-scope.md) for the
current-use/category ambiguity, exact coverage and available template layouts.

The canonical prepared material digest is
`a8e66c73558bb81de0c1f9377d17cd6d45589b0d0ed263743295613de2b24e19`.
Its initial bundle digest is
`24ffd0a0d28a22d32c6d14184f9e2cb69aa9a491cf2f0163a73ad0b842afff87`.
The private preparation manifest pins every input and generated candidate file.
A later confirmed material has a distinct receipt and revision; these digests
are not approval. Template version and formal report hash: **not available**.

## Real API and browser evidence

Private evidence remains outside Git under `artifacts/kpi1/` and the isolated
preparation worktree. It contains real source text and must not be copied into
public PR attachments. The scripts and machine-readable reports identify their
actual source snapshots, original hashes and exact runtime references.

- `screenshots/real-human-recovery-003/report.json`: one main-owned, source-based
  target-side confirmation. A local proxy forwards actual API requests and
  responses unchanged, then cuts the first response only after the server has
  committed and returned 200. The browser retains the command through navigation,
  looks up the exact receipt, sends no second POST, and observes the new revision
  waiting for remaining human work. Original observations are byte-equivalent.
- The same run retains five actual view captures, the original page before
  confirmation, frozen command, unknown state, recovered receipt, resumed view
  and a 390 px viewport with 400% PDF zoom. Strict document-width checks,
  reduced-motion `animation: none`, history navigation and zero script errors
  passed. Initial entry screenshots captured loading; later loaded-view evidence
  is distinguished in [the design comparison](kpi1-design-comparison.md).
- `real-errors-001/report.json`: five actual unauthorized HTTP routes return
  typed 403; wrong-version and actual superseded-task submissions return typed
  409 and have no receipt; an unconfigured output resolver returns typed 503.
  Job, context and task projections remain unchanged after the negative commands.
- Preparation worktree `artifacts/case-preparation/recovery-003/`: faults after
  real review persistence and after task registration recover through the actual
  worker/reconciler/Controller/parser/SQLite path. Two attempts retain exact
  producer identity, fence 1 becomes 2, and old task registration is refused.
  Natural session expiry waits for the issued timestamp and then returns typed
  403. These use actual ASGI routes and local state, not a network/browser server.

- `screenshots/real-human-recovery-004/report.json`: a second actual browser
  operation confirms the comparable side from its inspected original page.
  The updated SQLite implementation preserves one POST, exact receipt lookup
  after a dropped committed response, a new revision and remaining human work.
  Both real confirmations preserve original observations and confidence zero;
  neither approves rules or the complete material.
- Preparation worktree `artifacts/case-preparation/recovery-004/` reruns both
  real worker fault positions on the final cache implementation in 76.867 and
  90.180 seconds. All 218 source and 37 input hashes remain unchanged. Natural
  expiry is the separately executed recovery-003 result, not a new rerun.
- The frontend worktree's `final-readonly-five-views-004/report.json` records
  13 actual screenshots, 45 GET requests, zero POST/external requests, strict
  desktop/narrow widths, protected logout and reduced-motion checks. Initial
  same-path login focus and narrow heading polish remain limited UX follow-ups.
- `read-latency-002.json` records 16 actual post-fix HTTP reads: session 0.09-0.14
  seconds, job 0.014-0.018 seconds, context 0.51-0.557 seconds, assessment
  0.45-0.485 seconds, and four concurrent reads within 1.093 seconds.

Independent read-only review found no blocker in the bounded immutable-payload
cache as inspected around 05:20. It did not independently verify later latency
or recovery evidence. One correction-path provenance observation remains:
changing a previously reviewer-confirmed native side can still label it
`model_proposed`. That path is outside the exercised confirm/reject flow and
requires follow-up before condition correction is delivered.

Earlier failed captures and bounded test runs are retained. Correcting the
verification harness or layout does not convert their failures into passes.
Controlled transport/worker faults are disclosed above; no business response,
source content, OCR observation or model answer was injected.

## Local validation and publication boundaries

The validation environment is macOS, Python 3.13.5 and Node 26.7.0. Frontend
verification 009 passed 354 tests in 36 files, type checking, lint, format, build
and the artifact check. Build 010 incorporates the final targeted desktop
heading wrap; it changes presentation only. Existing bundle-size and JSDOM
scroll warnings are retained rather than hidden.

Backend Ruff, format (572 files), mypy (218 source files), OpenAPI exports and
14 golden contracts pass. The completed full unit run 005 records 2,874 passed,
10 skipped and six warnings in 627.48 seconds. Its 678-file source snapshot
is checked against the publication candidate. Earlier bounded-out runs remain
failed/incomplete records. The full integration/cloud suites were not rerun
in this KPI1 phase; their historical results are not reused. Existing unit fixtures include synthetic
data and injected services. Their results do not count as real-case acceptance.

Historical #54 CI failed before tests due to the recorded GitHub account
restriction. It is not evidence for this candidate. The actual new head, remote
readback, Draft URL and corresponding CI are recorded separately at publication.
No CI rerun, billing change, merge, Issue mutation or review approval is included.

## Reproduction and remaining work

Use [the controlled local runbook](local-original-workbench.md) for dependency
setup, private input preparation, API/preview/frontend commands and checks.
Use the explicit original launcher; the unchanged synthetic launcher retains its
name and does not qualify as real acceptance. Private executed browser and fault
scripts require the recorded real input/runtime directories and must use a new
output directory when repeated. Never replay a confirmation with a fresh key
until the original outcome has been reconciled.

The remaining local mainline requires:

1. A versioned, source-backed condition correction/confirmation transaction and
   human resolution of current-use/category, section and effective-period issues.
   The existing fact confirmation does not edit these conditions.
2. Reviewed catalog metadata, complete context inventory and approved executable
   rules/parameters/units/rounding, with explicit handling of conflicting sources.
3. An admitted real template, complete field map and approved font configuration,
   composed into the actual original runtime under existing exact-material and
   publication gates. Generate, download, reopen and visually verify a new file.
4. Independent review and exact-head hosted verification; repeated real OCR and
   additional independent cases remain separate quality work.

## Venue connection checklist

These cloud items do not determine local KPI1 acceptance and were not executed:

- Configure the deployed API and trusted identity provider; preserve case-scoped
  permissions, version conflicts, frozen commands and receipt reconciliation.
- Add private S3 source/template/report adapters with immutable object versions,
  exact hashes, least privilege and the competition's explicit data-admission
  policy. A downloaded rule or local original is not automatically admissible.
- Compose durable cloud transactions, outbox/worker leases and publication
  storage without weakening current atomicity or current-authority checks.
- Select the authorized model/provider, region, routing, account/role and budget
  only after approval; measure real extraction and retain raw observations.
- Perform real deployed browser, identity/revocation, source, model, retry,
  report/download and stop/recovery acceptance. Administrative district remains
  independent of the cloud region.
