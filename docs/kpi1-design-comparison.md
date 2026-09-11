# KPI1 design comparison

Initial review: 2026-09-12 at approximately 04:31 Asia/Taipei. Final read-only
capture: 05:23:23 to 05:23:38, after the main-owned backend restart. This
comparison separates proposal images, earlier failures, local implementation,
and observed browser results. Only this document was edited for this update;
new private verification screenshots remain in ignored local artifacts.

## Evidence and limits

- **Expected design:** `frontend-proposal-images.zip`, screens 01, 04, 05, 06,
  09, and 10; `frontend-proposal (1).pdf`, corresponding screen descriptions
  and accessibility requirements on page 28. These are proposals, not observed
  service results. Their sample values, completed steps, and actions are not
  acceptance criteria for real data.
- **Actual initial views:** `real-initial` screenshots 01 through 05. Their
  file modification times are 04:18:35-04:18:54. Dimensions are respectively
  1440 x 1150, 1440 x 1785, 1440 x 2558, 1440 x 27978, and 1440 x 1369.
  The associated report describes a real local API, read-only flow and records
  zero JavaScript errors. It does not demonstrate successful human responses.
- **Actual source view:** `source-desktop.png` (1440 x 1000) and
  `source-narrow.png` (390 x 844), both modified at 04:22:18. The associated
  report records zero JavaScript errors and equal viewport/document widths of
  390. This establishes no page-wide overflow in that measured state only.
  The desktop screenshot shows an opened original page; the narrow screenshot
  is scrolled to metadata and does not show the PDF. `source-field.png` is a
  42 x 8 field crop, not evidence that the surrounding page is readable.

These initial images precede the evidence handoff and style/PDF follow-up.
Main-owned recovery evidence and the final `final-readonly-five-views-004`
capture are recorded below. Older failure and paused attempts remain preserved;
they are not current successful views.

## Prioritized gaps

| Priority | Expected versus observed | Bounded action and verification |
| --- | --- | --- |
| Selected-filter hover verified | **Readable selected controls.** Proposal 01 uses a dark selected filter. Initial actual 01 showed nearly white text on a pale selected filter. | Final actual hover computed white text on `rgb(36, 83, 68)`, a calculated contrast ratio of 8.77:1. The dedicated rule fixes the earlier background override. This single control/state check is not a complete contrast or keyboard-focus audit. |
| Narrow geometry verified; broader usability pending | **Readable original evidence.** Proposal 06 gives the cited row and matrix enough space to read. The initial source capture shrinks a whole form into roughly 415 pixels of width. | The completed run now shows 400% original-PDF zoom at 390 pixels and passes the strict page-width check. Enlarged text stays inside the preview. The viewport image does not show the whole page or establish complete highlight alignment, keyboard scrolling, or all zoom states. |
| Bounded view and heading width verified | **Relevant, bounded comparison.** Initial actual 04 defaulted to a trust check without context and was 27978 pixels tall. Final actual 04 shows a factor with observations, scoped references, three visible rule citations, and a show-all control. | The integrated heading-wrap rule passes strict document-width checks: desktop 1440 x 2811 and narrow 390 x 4714. The scoped labels explicitly avoid claiming a direct rule match. Every citation remains available; the final capture leaves the disclosure collapsed and does not repeat the complete disclosure interaction audit. |
| Medium | **Action-first hierarchy.** Proposals 04/05/09 expose the current state or decision near the top. Final actual progress, results, and tasks still place primary content around or below vertical pixel 900, after repeated context and full version hashes. | Final entry has exactly one global data-mode notice. Further condense context to source roles, candidate/confirmation state, and concise version identifiers; retain full hashes and raw metadata in an accessible disclosure. Keep unresolved applicability prominent and expose the next available action earlier without implying approval. |
| Medium | **Readable action labels.** Proposal actions remain on one line. Actual 01 wraps the manual-open button into two vertical characters; actual 03 wraps several evidence-link labels. | Prevent action controls from shrinking below their label width; allow the input or row to reflow instead. Check at 390 and 1440 pixels and with enlarged text. Preserve visible focus and sufficient space between controls. |
| Medium, partial improvement observed | **Understandable findings and errors.** Final actual results and tasks have Chinese descriptions for known kinds and questions. Four translated verification warnings still repeat, technical identifiers remain prominent, and an English raw explanation remains in the evidence trace. | Preserve exact identifiers and raw messages while adding a clear localized explanation where its meaning is known. Summarize repeated diagnostics with an expandable record count and distinguish evidence links by finding. Use honest fallbacks for unnamed factors; do not invent business meanings or discard evidence. |

## Preserve existing strengths

The warm light surfaces, forest-green navigation, restrained borders, serif
headings, and desktop comparison columns already follow the proposal's visual
direction. Status labels accompany color. Source roles and candidate state are
visible, missing values remain missing, and execution success is distinguished
from business verification and output readiness. Preserve those distinctions.

An empty human-task view and unavailable output were truthful states in the
initial captures, not reasons to add mock approvals or download actions. The proposal's
additional product screens, progress examples, and sample calculations should
not be reproduced without corresponding real capabilities and data.

## Implementation and unit regressions

At approximately 04:40 Asia/Taipei, `styles.css` and `PdfEvidence.tsx` implement:

- Selected-hover styling with a dark background and white text.
- Explicitly opened PDF previews with 100%-400% zoom in 50-point steps, a
  fit-width/reset action, Chinese and English labels, a live zoom value, and a
  focusable scroll region. Scrolling is confined by the preview's width and a
  maximum height of 65 viewport percent or 42 rem, whichever is smaller.
- Redrawing of the same verified PDF page without another source request when
  zoom changes. SHA-256 validation, the byte limit, one-based page checks, and
  unrotated CropBox-relative highlighting remain in place. Canvas allocation is
  capped at 16 million pixels and 8192 pixels per dimension; extreme pages may
  reach that resolution cap before the maximum display zoom.
- Cancellation of obsolete rendering. Changed citation or loader bindings clear
  the preview and require another explicit open; identical polling data retain
  the view. No write operation or API/client contract was introduced.

Validation uses `NODE_OPTIONS=--no-experimental-webstorage`. After the overflow
fix, 45 focused regressions passed across four files, including 19 PDF/control
and heading-wrap tests. Type checking, focused ESLint, formatting, and an
in-memory production bundle check passed. Main subsequently reported full
frontend verification passing 354 tests in 36 files, plus types, lint, format,
build, and artifact checks; that broader suite was not independently rerun for
this document. Mock-renderer unit results remain separate from real browser
evidence.

## Overflow diagnosis and final CSS fix

The `real-human-recovery-002` failure image is 1651 x 3685 pixels; its failure
record contains zero writes. A subsequent read-only diagnostic waited for the
authoritative observation card to load before measuring the actual page:

| State at a 390-pixel viewport | Document width | Observation |
| --- | --- | --- |
| Before opening an original | 1651 | `section.card > h3` contained a 196-character unbroken subject identifier. Its text width was 1620 pixels, with a right edge at 1651; wrapping was `normal`. |
| Original PDF at 400% | 1651 | CaseContext remained 362 pixels wide with no internal excess. The PDF had a 326-pixel viewport and 1304-pixel internal scroll width, correctly contained by `overflow: auto`. |
| Same page, only heading wrapping temporarily applied | 390 | Heading text wrapped to 327 pixels; all 196 characters remained present. PDF scrolling was unchanged. |

The narrow overflow handoff added this rule to `styles.css`:

```css
.card > h3 {
  overflow-wrap: anywhere;
}
```

`PdfEvidence.tsx` required no further change. No root-level overflow suppression,
text truncation, source-data removal, or assertion weakening was used. The new
unit regression renders the actual authority component with a long unit-only
identifier and the real stylesheet, checks exact text preservation and wrapping,
and retains internal PDF scrolling at 400%. The read-only diagnostic performed
no submissions; it is distinct from the later main-owned recovery run.

The later desktop diagnosis found a separate scoped-rule heading overflow.
At a 1440-pixel viewport the document was 1468 pixels wide. The heading box
was 519 pixels wide, but its 93-character title occupied 604 pixels and reached
the document's right edge. A read-only temporary CSS probe changed only this
selector, reducing document width to 1440 and text width to 514 pixels while
asserting identical full title text:

```css
.evidence-columns > .panel > section[aria-label] > h3 {
  overflow-wrap: anywhere;
}
```

Main integrated the same rule. Final capture 004 reads computed wrapping as
`anywhere` and passes the unchanged strict width assertion on the fully loaded
evidence page at both viewport sizes. Neither fix masks root overflow.

## Earlier main-owned integration evidence

Inspection waited until `real-human-recovery-003/report.json` was written at
05:07:55 Asia/Taipei. The earlier 05:09 document update reused those screenshots.

- `source-400-narrow.png`, saved at 05:06:14, is 390 x 844. It shows the 400%
  control state and enlarged original text inside the preview. The runner waits
  for `section.card > h3` before its unchanged
  `document.documentElement.scrollWidth <= innerWidth` assertion. That check
  passes with the fully loaded observation card, avoiding a transient pass while
  metadata is still loading.
- `10-narrow-progress.png`, saved at 05:07:31, is 390 x 2846. The resumed progress
  page also passes the width assertion. Its remaining long vertical context
  section still supports the action-hierarchy recommendation above.
- The completed report records zero JavaScript errors, one main-owned
  confirmation POST, a receipt equal to its status lookup, and continuation to
  `waiting_for_human`. Rule-bundle conditions remain unconfirmed. This documents
  the bounded recovery flow, not formal approval or whole-case completion.
- The reduced-motion results view reports computed animation name `none`.
  This establishes that checked state only; normal 180 ms timing, all focus
  transitions, and every route were not visually audited here.

## Final loaded five-view capture

`final-readonly-five-views-004` contains the completed report and 13 PNGs.
The capture started from the actual entry page, waited for the authorized case
row and the loading message to clear, clicked that row's open link, and used
the actual workbench navigation for progress, results, evidence, and tasks.
Each screenshot waited for case context and the relevant page content, including
actual task rows. No source response was fabricated or substituted.

| View | Desktop screenshot | Narrow screenshot | Strict document width |
| --- | --- | --- | --- |
| Loaded case entry | `01-case-entry-desktop.png`, 1440 x 1084 | `01-case-entry-390.png`, 390 x 1711 | 1440 / 390, both pass |
| Progress | `02-progress-desktop.png`, 1440 x 1785 | `02-progress-390.png`, 390 x 2846 | 1440 / 390, both pass |
| Results | `03-results-desktop.png`, 1440 x 2425 | `03-results-390.png`, 390 x 3398 | 1440 / 390, both pass |
| Evidence | `04-evidence-desktop.png`, 1440 x 2811 | `04-evidence-390.png`, 390 x 4714 | 1440 / 390, both pass |
| Human tasks | `05-tasks-desktop.png`, 1440 x 1671 | `05-tasks-390.png`, 390 x 2613 | 1440 / 390, both pass |

All ten views passed `document.documentElement.scrollWidth <= innerWidth`.
The four case routes retained the same actual job. Authorized status reads
before and after capture had identical revisions, and all seven observed
context responses matched that revision. The browser made 45 GET requests,
with zero unsafe or external requests, JavaScript errors, or console errors.
Two additional direct authorized GETs checked the revision boundary. This is
read-only UI verification, not a submission, approval, or report-generation run.

The results view explicitly describes its counts as finding records covering
source, rule, and value checks. The passing `source_identity` record establishes
source identity only; it does not establish a verified business factor.
Candidate source roles, full versions, unconfirmed applicability, and the
unavailable formal report remain visible. Final capture did not open a PDF;
the separate earlier 400% original-PDF evidence retains its narrower scope.

### Focus, motion, and signed-out protection

- Navigation to each of the four case views placed focus on its `h1`.
  Normal computed route animation was `route-enter` with duration `0.18s`.
  These are computed styles, not a frame-by-frame timing measurement.
- With reduced motion enabled, actual navigation to results produced `none`
  and `0s`, with heading focus. The explicitly focused refresh control retained
  focus through one real polling update; the route element stayed identical.
  This polling check ran under reduced motion and does not establish every
  normal-animation or focus interaction.
- Immediately after sign-out, reopening the captured protected evidence route,
  and navigating to privacy through the actual sidebar each showed the sign-in
  gate. Checks found no rendered case context, case rows, PDF canvases, or the
  selected private case/revision identifiers; credential inputs were empty.
  The two signed-out screenshots also passed desktop width checks. This is a
  frontend route/rendering check, not a new backend revocation or cross-account
  authorization test.
- An actionable focus gap remains: successful sign-in at the unchanged entry
  pathname left focus on `BODY`. Move focus deliberately to the loaded entry
  heading or its status when authentication replaces the sign-in form.

### Read latency and preserved unsuccessful attempts

The initial final-capture attempt and attempt 002 timed out while the actual
results page was loading. Successful HTTP responses still required several
seconds, and complete page reads exceeded the 10- and 18-second readiness
windows. This was a product usability problem, not successful acceptance with
a slow harness. Attempt 003 was stopped after four views at main's request;
its report remains explicitly paused, and the dedicated browser was closed.

After main announced the backend fix and restart, the inspected
`read-latency-002.json` recorded 16 successful reads with a maximum of 1.093
seconds. Final capture 004 then completed all requested views, a polling wait,
and the signed-out checks in approximately 14.9 seconds. Earlier failures and
the paused report remain intact. This bounded observation does not establish
production load capacity or eliminate future latency risk.

## Remaining accessibility and visual gaps

The proposal's page 28 calls for keyboard operation, field-linked errors,
non-disruptive polling, document zoom, and usable narrow navigation. The final
checks above cover only the stated interactions. Remaining work includes:

- Keyboard access, visible focus, and announced expanded state for citation
  disclosures; selection and focus preserved during polling.
- Keyboard reachability and discoverability of every narrow navigation item and
  the results table's offscreen source/action columns. The page itself fits,
  but the screenshots do not establish that users discover internal scrolling.
- Reflow the narrow case row so its name has more width. The desktop manual-open
  button and several evidence links still wrap their labels unnecessarily;
  let their adjoining inputs or rows reflow first.
- Enlarged text and document zoom, including readable source text and correct
  highlights without losing surrounding context.
- Accessible names for repeated evidence actions and error-to-input association
  when a real response form is available.
- Normal route-animation timing and absence of replay on polling beyond the
  computed-style and reduced-motion checks described above.

The final browser was closed after capture. No submissions, confirmations, or
implementation edits were performed for this document update. Remaining visual
and accessibility recommendations are not claims that further fixes were made;
the read-only pass does not establish whole-case KPI1 acceptance.
