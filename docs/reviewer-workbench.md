# Reviewer workbench

The integrated workbench is on `main` after [PR #45](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/45)
merged as `d148422adb18190bada93b8588a4e34d73e3c2e4`. It supports an authenticated
review, evidence inspection, task responses, published PDF download and a separate
local privacy and OCR restoration workflow. This is the configured local validation
baseline; hosted identity, deployment and the remaining end-user workflow are still
tracked in the [implementation backlog](implementation-backlog.md).

## Pages and implemented behavior

| Route | Current behavior |
| --- | --- |
| `/` | Accept an operator-issued session token, then open a job by its identifier. |
| `/jobs/:jobId` | Show durable status, open and handled tasks, revision history, findings, verification status, artifact status and authorized PDF downloads. Refreshing the job and loading its result are explicit actions. |
| `/tasks/:taskId` | Show the question, source citations, verified source PDF pages and supported region highlights; submit the actions allowed by the service and display their committed receipt. |
| `/privacy` | Connect to the configured loopback bridge with its separate session, inspect authorized original sources, select privacy regions, confirm exact sanitized exports, and restore an authorized result locally. |

The review session token stays in `sessionStorage` for the tab. The frontend does
not decode it or infer permissions; every operation is authorized by the backend.
The local privacy session stays in component memory and is separate from the review
API session. The privacy page can be opened without signing into the review API.

## Review, respond and download

1. Open a job and follow an open task. Read its question, findings and source evidence.
   Source PDF bytes are authenticated and SHA-256 checked before rendering. Missing or
   invalid citation coordinates are reported instead of inventing a highlight.
2. Choose an action offered by that task: confirm, correct, supply cited evidence,
   reject, approve or authorize publication, as applicable. Corrections use the
   server's subject identity, value type and unit. Evidence choices come from the service.
3. Review the explicit confirmation, then submit. The exact payload and idempotency
   key are frozen when entering confirmation. Going back before submission permits
   editing; an uncertain submitted command stays locked for receipt recovery.
4. Read the receipt, which identifies any committed revision, resulting job status
   and superseded tasks. Return to the job and explicitly refresh its status or load
   the current result as needed.
5. Download a published PDF through its authenticated artifact route. Each click
   obtains fresh authorization and verifies the bytes against the result manifest.
   The page supports both legacy single-context manifests and
   `artifact-manifest-v2` with multiple comparison contexts.

Results include observed and expected values, rule versions, calculation traces,
source citations and independent verification status. Source-evidence diagnostics
identify the originating fields and link to their findings. A blocked case does
not become writable because the UI displays its diagnostics.

Proposals remain visibly separate from accepted values. Original extractor
confidence, including zero, is preserved: confidence is not a human confirmation.

## Conflicts and uncertain responses

A definitive service refusal requires a valid canonical problem envelope with a
known code matching its HTTP status. A canonical `409 version_conflict` offers a
reload and a new decision; it does not resend the stale write.

Timeouts, interrupted response bodies, malformed responses, unknown problem codes
and noncanonical gateway failures such as HTML 502/504 leave the write outcome
unknown. The page keeps the exact confirmed command locked and offers **Send again**
with the same payload and idempotency key. Requests are not retried automatically.
The recovery command is held in page state; persistence across navigation, reload
or closing the tab is not implemented.

## Local privacy and OCR restoration

`/privacy` reads the public bridge address from `/local-config.json`. Only an HTTP
numeric loopback address is accepted. The public configuration contains no token,
source path, mapping key or restoration material.

The source selector lists handles already authorized by the local bridge. Reviewers
inspect every original page and its candidate regions, add or edit coordinates with
the region editor, and explicitly mark pages reviewed. This is not a file picker
or a general case-upload workflow.

After confirming the source and regions, the reviewer prepares the exact sanitized
payload, waits for all preview pages to render, inspects its PDF, text and manifest,
then explicitly confirms and transfers that payload once. An uncertain transfer
requires reconciliation. Original documents and encrypted mappings remain local.
Sanitization alone does not authorize competition cloud admission; the separate
competition data policy still applies.

Restoration accepts an authorized result identifier. When automatic OCR cannot
establish an acceptable reading, the configured bridge returns
`409 local_privacy_review_required` and opens the local visual review screen:

1. Review the **published document** stage. Each required item needs its actual
   verified page and crop, an explicit transcription, and its own inspection check
   and confirmation. All original measurements and confidence remain visible.
2. Once every required item is confirmed, explicitly resume restoration. A separate
   **restored candidate** stage can require the same individual review process.
   The published-stage receipt cannot authorize this second stage.
3. Download the final PDF only after the bridge authorizes the completed result and
   the frontend verifies its digest. Native-text, pixel and security checks remain
   mandatory; a visual reading does not grant business approval.

Expired reviews or incorrect immutable readings have an explicit restart action
that archives prior evidence and revokes old confirmations. Restarting does not
automatically restore or confirm anything. In-memory OCR review receipts do not
survive a bridge process restart. See [the local OCR protocol](local-privacy-ocr-review.md)
and [the browser OCR behavior](../web/docs/local-ocr-review.md).

## Running and checking the frontend

Use the [integrated local runbook](integrated-local-runbook.md) for a configured API,
issued local sessions and an actual browser workflow. Starting the unconfigured
default API alone does not supply these capabilities.

For frontend development and checks:

```sh
cd web
npm ci
npm run dev       # frontend development server on port 5173
npm run verify    # typecheck, lint, format, tests, build and artifact checks
```

Stop the development server before running verification in the same terminal,
or use a second terminal. Use the Node version requirements in the local runbook;
the current lockfile does not support Node 18 or 20 across all dependencies.

`VITE_API_BASE_URL` is a build-time API origin. An unset value uses same-origin
`/v1/` routes, requiring an appropriately configured server or proxy. The Vite
development server alone does not compose a review service or privacy bridge.

Run browser smoke separately after building:

```sh
npm run generate
npm run build
npx playwright install chromium firefox
npm run e2e
```

The committed typed client is generated from `web/openapi.json`; the configured CI
workflow checks generation drift against the backend export. The `e2e` suite uses
the production build and controlled browser fixtures. `e2e:real` instead uses actual
configured loopback services, as described in the runbook. A configured CI job is
not evidence that it ran: retain the exact head and observed result for each run.

The application has no analytics, remote error reporting or remote font dependency.
The production build disables source maps and has an artifact check. Browser traces,
videos and routine screenshots are disabled by default; explicit synthetic OCR
page/crop evidence must remain in the ignored private rehearsal directory.

## Remaining user experience and deployment work

- A case list and case creation/upload flow that does not require pasted job or result
  identifiers; the current source selector uses preconfigured local handles.
- Hosted sign-in, token issuance/refresh and a deployment identity integration.
- Job polling, cancellation controls and durable recovery of uncertain commands
  across page navigation or reload.
- A deployable frontend/API/local-companion package. The existing browser rehearsal
  proxy includes test fault controls and is not a deployment server. There is no
  Docker Compose application in the current baseline; the default AWS Runtime entry
  remains unconfigured. Track this in
  [#48](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/48).
- Direct Windows Edge/Chrome checks, keyboard and scaling checks, and mobile usability
  verification. Chromium coverage does not establish a Windows or mobile pass.

These are remaining portions of [#25](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/25)
and the current implementation backlog. The integrated PDF viewer, result download,
privacy review, sanitized transfer and local visual OCR review are already implemented.
