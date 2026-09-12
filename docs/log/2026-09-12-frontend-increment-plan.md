# Frontend workbench increment — implementation plan

Date: 2026-09-12, Asia/Taipei. Branch: `feat/local-review-workbench`.
Scope owner: frontend and operator experience.

## What this document is

A plan for the frontend work that can start immediately, using contract fields
the service already publishes. It is not a record of completed work, not an
acceptance report, and not evidence that any official form has been produced.

Work that depends on contract changes owned by the integration role is listed
separately under "Blocked" and is deliberately not started.

## Verified template facts

Read-only inspection of the three official workbooks on 2026-09-12. Originals
were opened through `zipfile` and never modified. The files themselves stay out
of version control (`.gitignore`: `docs/table/*.xlsx`).

| Form | Official name | Visible sheet | Granularity | Print area |
| --- | --- | --- | --- | --- |
| Form 3 | 地價區段勘查表 | `表3區段勘查表` | Land value **section** | `$A$1:$V$46` |
| Form 4 | 比較法調查估價表 | `表4比較法調查估價表` | Subject comparison | `$A$1:$R$36` |
| Form 5 | 影響地價區域因素分析明細表（住宅用地） | `表5-1區域因素明細表(住)` | Subject comparison | `$A$1:$M$45` |

All three: 24 worksheets, 1 visible and 23 hidden, **zero formulas on the
visible sheet**, and two `externalLink` parts each. Every published value must
therefore be computed before it is written; no visible cell recalculates.

Local copy SHA-256:

- Form 3 `f85bc407857d2a0072f9a64db1a2edffe224a242ff3d6bc87aa649cc2317342b`
- Form 4 `c5f767e8ab6b759b5f31d321f24707bd4bb0d2d503ce8a24bc7b7872c8bf82a8`
- Form 5 `d2ab663222fbd401c639be02e6bace069dc8f76f4331f8347a2b858e36d15b39`

### Unit rule, confirmed against cell formats

The same five percentage points is stored differently in the two forms:

- Form 4 difference-rate columns `J`, `N`, `R` use the Excel percent format
  `0.00%` (71 cells; a few rows use `0%` or `0.0%`). Five points is stored as
  `0.05` and displays as `5.00%`.
- Form 5 correction-percentage columns `G`, `J`, `M` use the plain numeric
  format `0.00_ ;[Red]\-0.00\ `, **not** a percent format. Five points is
  stored as `5`.

A value moved between the two forms without conversion is wrong by a factor of
one hundred. The frontend renders values and states the unit; it never converts
between these conventions and never recomputes a rate.

### Two structural facts that shape the UI

1. Forms 4 and 5 both fix their horizontal axis as **one target plus comparable
   1, 2 and 3**. The subject roster mirrors the official layout; it is not a
   list the frontend invents. Roles come from the service's `target_id` and
   `comparable_id`, never from document page order.
2. Form 3 is per land value **section**, while Form 5 records a section number
   for the target and for each comparable separately. If the four subjects fall
   in different sections there is more than one Form 3. The published file count
   and naming are still an open question for the organiser, so the UI must hold
   a list of Form 3 instances rather than a single fixed download.

Form 4 cell `A37` (the note citing Article 20 of the 土地徵收補償市價查估辦法)
sits outside the print area `$A$1:$R$36`.

Form 5's filename carries `(住宅用地)` and its visible sheet is `表5-1`, so
other land-use variants exist. Labels must carry the land-use qualifier; a bare
"Form 5" would mislabel the sheet after a district or use change.

## Operator-supplied interpretation rules

`docs/table/rules.md` adds two rules:

1. A mass-transit development parcel is assessed on its pre-conversion land
   specification (ordinary residential).
2. In urban planning, a residential zone beside an ordinary lane (8 m wide or
   less) with no special incentive is commonly planned at a 200% base floor
   area ratio.

These are **human interpretations supplied by the operator**, not values parsed
from the official sources. Rule 2 is stated as a common practice ("常規劃"),
which makes it a default assumption rather than a verified rule.

Frontend obligation: render both as proposed interpretations with their origin
visible, using the existing `CaseConditionCandidate.method` distinction
(`manual_proposed` versus `native_proposed`). Neither may be displayed as a
confirmed condition, and the floor-area-ratio figure must read as an assumption
that a reviewer can correct. The frontend does not apply either rule to a
calculation.

Rule 1 bears directly on the recorded P001 zoning contradiction (mass-transit
development area versus residential zone); it is an input to that human
decision, not a resolution of it.

## Implementable now

Every field below is already published by the current OpenAPI document. No
contract change is required to start.

### 1. Subject roster

Source: `CaseContextView.rule_bundle.contexts` for the target and comparable
identifiers, `CaseContextView.observations[].side` for per-subject observations.

Shows the target and each comparable as its own row, with the count of
confirmed and outstanding observations, cited source pages, and a link into the
task that is actually open for that subject. Roles are read from the contract.
A subject with no supplied observations reads as "not supplied", never as zero.

### 2. Blocker and outstanding-item list

Sources, all already present: `PausedReviewView.coverage` (`missing`,
`unsupported`), `ServiceVerification.critical_errors` and `warnings`,
`CaseContextView.selections[].status`, `rules[].declared_status`, and
`rule_bundle.sources[].unresolved`.

Each row states the field, the reason, the source citation, and the next action
available to the reviewer, with a link to the matching task or evidence view.
Rows of the same kind are collapsed to one entry with a count. Raw codes stay in
a details disclosure. This replaces the four-number coverage line, which reports
totals without saying what is missing or what to do next.

An unreadable assessment and a genuinely empty finding set must not render the
same way; the existing `assessmentError` flag already separates them.

### 3. Case condition entry

Source: `CaseContextView.identity` plus `rule_bundle.condition_candidates`
(field, value, interpretation, method, evidence) and the existing task list.

Presents the current district, land use and effective date together with the
proposed candidates and their source excerpts, and links to the canonical task
that carries the correction. Where the service offers no such task, it says so.
The frontend does not mint a subject identifier, does not submit a synthesised
condition, and does not fall back to another district's rules. A district the
backend does not support reads as unsupported.

### 4. Official form view, shell only

The view, the labels now that the official names are known, and the explicit
"not available in this deployment" state. No download path, because the routes
do not exist yet. The shell must never present a draft as ready, and must not
show a success state for a file it cannot fetch.

### 5. Browser acceptance repair

`e2e-local-stack/stack.spec.ts`, `e2e-real/reviewer-api.spec.ts` and
`e2e-real/privacy-flow-helper.ts` drive a `Load current result` button that
exists only in `src/features/ResultPanel.tsx`, reached through
`src/features/JobPage.tsx` — which no route mounts. `App.tsx` routes
`/jobs/:jobId/*` to `WorkbenchJob`. These browser checks therefore exercise a
screen that is not shipped. Selectors move to the mounted workbench.

Whether `JobPage` and `ResultPanel` should be removed is left to the
integration owner; four unit tests render `ResultPanel` directly.

## Blocked, not started

**Official form download.** No table or workbook route exists in
`web/openapi.json`, and the current shape cannot carry three files:
`ServiceResult.artifacts` is `maxItems: 1` and `ArtifactManifest.media_type` is
`const: "application/pdf"`. Needed from the integration owner: routes, plus a
manifest carrying form identity, template hash, output hash, draft or ready
state, the revision it was produced from, blockers, and the download
authorisation reference.

**Request identifier in error details.** The client does not read a request
identifier from any response header today. The header name has to be settled
first; the six canonical problem codes and their meanings stay unchanged.

## File-by-file changes

New:

| File | Purpose |
| --- | --- |
| `web/src/features/SubjectRoster.tsx` | Target and comparable roster |
| `web/src/features/BlockerList.tsx` | Blockers and outstanding items |
| `web/src/features/ConditionEntry.tsx` | District, use and date conditions |
| `web/src/features/OfficialForms.tsx` | Official form view, shell only |
| `web/tests/subject-roster.test.tsx` | Roles, missing observations |
| `web/tests/blocker-list.test.tsx` | Deduplication, next action, error vs empty |
| `web/tests/condition-entry.test.tsx` | Proposed vs confirmed, unsupported district |
| `web/tests/official-forms.test.tsx` | Labels, unavailable state, no false ready |

Modified:

| File | Change |
| --- | --- |
| `web/src/features/WorkbenchJob.tsx` | Mount roster and blockers; replace the coverage line; add the forms view |
| `web/src/App.tsx` | Sidebar entry and breadcrumb wording for the forms view |
| `web/src/features/workbench-state.ts` | Official form labels with land-use qualifier; blocker and role wording |
| `web/src/ui/styles.css` | Layout for the three new components, reusing existing tokens |
| `web/e2e-local-stack/stack.spec.ts` | Selectors for the mounted workbench |
| `web/e2e-real/reviewer-api.spec.ts` | Same |
| `web/e2e-real/privacy-flow-helper.ts` | Same |

Not touched: `web/openapi.json` and `web/src/api/schema.d.ts` (regenerated by
`npm run generate` after the contract changes), the Python service, and
`web/src/privacy/*`.

No new animation, no new design language, no public registration, and no second
DTO layer. The existing 180 ms route duration, reduced-motion handling and
narrow-screen breakpoints in `styles.css` are reused as they are.

## Verification

Focused, per change: `cd web && npx vitest run tests/<file>`.

Whole frontend before handing over: `cd web && npm run verify`
(typecheck, lint, format check, unit tests, build, artifact check).

Browser: `npm run e2e:real` requires `REVIEW_BROWSER_FIXTURE`; the local stack
configuration requires `LOCAL_STACK_SESSION` and `LOCAL_STACK_EVIDENCE` from the
operator. Route mocks and simulated successes are not acceptable evidence.

## Acceptance for this increment

1. Target and three comparables are visible for a real case, with per-subject
   outstanding counts and source pages.
2. Every blocker names a field, a reason, a source and a next action; an
   unreadable assessment is distinguishable from an empty one.
3. Condition candidates display their origin, and operator-supplied
   interpretations never read as confirmed.
4. The official form view labels the three forms correctly and states that
   download is unavailable in this deployment, without any ready or completed
   claim.
5. Browser checks drive the screens the application actually mounts.

Reaching all five does not mean any official form has been produced.

## Open questions

1. How many Form 3 files does the organiser expect, and under what naming, when
   the subjects span more than one land value section?
2. Which land-use variants of Form 5 are in scope beyond `表5-1` residential?
3. Are `JobPage` and `ResultPanel` to be removed or kept?
4. Which header carries the request identifier?
5. Rule 2 is recorded as common practice. Does the case require a source-backed
   floor area ratio before any calculation may depend on it?

## Increment status, 2026-09-12

Recorded after implementing the "Implementable now" section. Nothing here claims an
official form was produced, and no contract field was added.

Landed:

1. `web/src/features/SubjectRoster.tsx` — one target plus each comparable, read from
   `rule_bundle.contexts`. Per-subject counts are supplied observations, outstanding
   tasks and recorded responses; a subject with no observation reads "not supplied".
   A recorded response is stated not to be a confirmed value.
2. `web/src/features/BlockerList.tsx` — coverage `missing` and `unsupported`,
   verification critical errors and warnings, non-unique selections, rules not declared
   approved, and unresolved catalog metadata. Same-kind rows collapse to one entry with
   a count; raw codes stay in a disclosure. It replaces the four-number coverage line in
   the review overview. An unreadable assessment renders as an alert, distinct from both
   an empty finding set and a missing assessment.
3. `web/src/features/ConditionEntry.tsx` — identity plus `condition_candidates` with
   `method` shown as origin. Operator-supplied interpretations carry an explicit note
   that they are human interpretations applied to no calculation. A revision without a
   rule bundle reads as an unsupported district; no fallback district is used.
4. `web/src/features/OfficialForms.tsx` — the three official names, visible sheets, the
   per-form rate convention, Form 3 held as a list of section instances, and an explicit
   "not available in this deployment" state on every card. No download control exists.
5. Browser checks now drive the mounted workbench: `/jobs/:jobId/results` and
   `/jobs/:jobId/tasks` instead of the unmounted `ResultPanel`, and they set the same
   stored language preference the language button writes. The artifact comparison-context
   table moved into the mounted `PublishedOutput` so that assertion still has a subject.

Verification run locally on 2026-09-12: `npm run typecheck`, `npm run lint`,
`npm run format:check`, `npm run build` and `npm run check:artifact` pass. The four new
unit files pass (21 tests).

Two pre-existing failures remain and are **not** caused by this increment:
`tests/privacy-route.test.tsx` and `tests/session-isolation.test.tsx` (16 tests) fail at
`window.localStorage` being undefined under the installed jsdom. The same failures occur
with this increment's changes removed, so `npm run verify` does not currently pass on
this machine for reasons outside this scope.

Browser acceptance was not executed: `npm run e2e:real` needs `REVIEW_BROWSER_FIXTURE`,
and the local stack configuration needs `LOCAL_STACK_SESSION` and `LOCAL_STACK_EVIDENCE`
from the operator. The repaired selectors are therefore reviewed, not yet exercised.

Still blocked and not started: official form download (no route, `artifacts` is
`maxItems: 1`, `media_type` is `const: "application/pdf"`), and the request identifier in
error details. The five open questions above remain open.
