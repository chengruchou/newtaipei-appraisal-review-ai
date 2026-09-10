# Reviewer workbench

Status: first slice of #25 on the `feat/reviewer-workbench` branch; not merged and not
deployed. Stacked on `feat/human-task-api` (#24), whose task API it consumes.

The workbench is the browser half of the reviewer's job: open a review job, read the
question a run raised, look at the evidence behind it, answer once, and see what the answer
committed. It is not yet the whole of #25 — see [What is not built](#what-is-not-built).

## Running it

```
cd web
npm ci
npm run generate      # regenerate the typed client from ../web/openapi.json
npm run dev           # http://localhost:5173
```

The API origin is a build-time setting, `VITE_API_BASE_URL`. With it unset the client uses
a relative origin, which is what you want when the service and the page are served together.

To point it at a locally running service:

```
cd ..
uvicorn appraisal_review.api.app:app --port 8000
cd web
VITE_API_BASE_URL=http://localhost:8000 npm run dev
```

The service answers `capability_unavailable` on every task route unless it was composed
with both a human-task store and a principal resolver. That is deliberate: an undeployed
plane must not answer as though work had been accepted.

## What a reviewer does

1. **Sign in.** The token is held for the tab only, in `sessionStorage`, and is never
   decoded. What you are allowed to do is decided by the service on each request; the page
   does not read a claim and grant itself anything.
2. **Open a job** by its identifier. The job page shows the durable status, the open tasks,
   the tasks already handled, and the revision history.
3. **Open a task.** The question, the evidence and the findings it answers are all on one
   page, evidence above the form.
4. **Answer once.** The form offers only the actions the task itself allows, asks for an
   explicit confirmation, and disables itself while the request is in flight.
5. **Read the receipt.** It names the revision your answer committed, the new job status,
   and any other task your answer superseded.

## What the workbench refuses to do

These are the behaviours worth knowing about, because each is a case where a friendlier
interface would be a dishonest one.

- **It never presents a proposal as a decision.** A proposed value is labelled
  "Proposed — not accepted" beside the observed value, never shown alone or merged into a
  single current value. An extractor confidence is shown with the words "a confidence is
  not an approval", because a high number is not a human decision.
- **It never draws a highlight it cannot justify.** A citation whose region is missing or
  degenerate is reported as "position unavailable", with the page number to open by hand.
  A task citing nothing at all says so.
- **It never resends a write after a conflict.** A `version_conflict` means someone else
  moved first and nothing was saved, so the only offer is to reload and decide again.
- **It does resend after a timeout**, under the same idempotency key, because a timeout
  leaves the outcome genuinely unknown and the key is what makes one decision one write.
  The key is minted when the form opens, not when submit is pressed.
- **It never rebuilds the subject name of a correction.** The server publishes it on
  `TaskView.subject_id`. See [service contracts](service-contracts.md): the canonical form
  escapes non-ASCII and a browser's `JSON.stringify` does not, so a rebuilt name would be
  wrong for every case identified in Chinese.
- **It sends no telemetry.** There is no analytics, no remote error reporting, no remote
  font and no source map in the built artifact. `npm run check:artifact` checks the last of
  those against `dist/` on every CI run.

## Checks

```
cd web
npm run verify     # typecheck, lint, format, unit/component tests, build, artifact check
npm run e2e        # real-browser smoke in Chromium and Firefox
```

`npm run generate` output is committed. CI regenerates it and fails on a diff, so the
client cannot drift from `web/openapi.json`, which `scripts/export_openapi.py` in turn
checks against the mounted routes.

## Windows browser checklist

The automated smoke runs Chromium and Firefox on Linux in CI. Edge shares Chromium's
engine, so it is covered by inference, not by observation. Before a demo, confirm by hand
on Windows:

- [ ] Edge and Chrome both load the built page from the deployed origin.
- [ ] Sign in, open a job, open a task, submit a confirmation, see the receipt.
- [ ] Tab order reaches every control on the task page, and the focus ring is visible on
      each one against the page background.
- [ ] The page is usable at 125% and 150% display scaling, which is the Windows default on
      many laptops.
- [ ] Traditional Chinese in a question, an excerpt and a case identifier all render without
      missing glyphs or mojibake.
- [ ] With the network throttled to offline, submitting shows the "send again" path rather
      than a browser error page.
- [ ] DevTools → Network shows no request to any origin other than the configured API.

## What is not built

Stated plainly, because #25's scope is larger than this slice:

- **The whole privacy and upload half.** Local file selection, the A3 privacy scan,
  candidate review, the sanitized preview and confirmation, sanitized upload, and local
  rehydration after download are all absent. That half needs the A3 local pipeline
  (`feat/local-privacy-pipeline`) and a loopback companion service, neither of which exists
  on this branch.
- **Case creation and a case home page.** A job is opened by pasting its identifier, which
  is not the "no user needs to know an identifier" experience #25 asks for.
- **Artifact status and authorized download.** The result route is not yet surfaced.
- **A real sign-in.** The service has a `PrincipalResolver` protocol and no implementation,
  so the workbench takes a token by hand. This is a seam, not an authentication system.
- **PDF page rendering.** Evidence is shown as document, page, region and excerpt. There is
  no embedded PDF viewer, so the page-to-citation correspondence #25 asks for is partial:
  the reviewer is told exactly where to look, but has to open the document themselves.
