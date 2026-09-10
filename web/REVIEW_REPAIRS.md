# Reviewer workbench repair and integration notes

Scope: PR #39, stacked on PR #38. These changes repair the browser consumer;
the shared Python models, task service and integration composition remain owned
by the service workstream. Do not merge unrelated branches into this head.

## Submission invariants

The review step detaches and recursively freezes one command. Its action,
original observation, proposed value, unit, source evidence and submission key
stay together. Inputs remain locked during confirmation, submission and an
unknown outcome. Going back before submission requires a new review and key.
A transport retry sends the original command; it cannot rebuild edited input.
A version conflict requires reloading authoritative state.

Headers, body consumption, JSON parsing and canonical schema validation share
one transport deadline. A stalled body, interrupted stream or invalid successful
receipt produces an unknown outcome, preserving the command for recovery.
Known service problems remain service problems. The frontend does not manufacture
a successful receipt from malformed JSON or an empty object.

## Authoritative correction metadata

`TaskView` remains unchanged. The browser independently requests
`GET /v1/review-tasks/{task_id}/subject`, returning `TaskSubjectView`:

- `task_id`, `revision`, `subject_id` bind the projection to the opened task.
- `observation` preserves the exact original value, raw text, evidence and confidence.
- `required_type` is `number`, `text`, `category`, `boolean`, or null.
- `required_unit` and `unit_required` come from stored material, never factor names.

Missing, failed or mismatched metadata blocks corrections while leaving allowed
noncorrection actions available. Numeric corrections require the authoritative
unit. Zero and false remain values; blank input never becomes zero. The client
retains original confidence, including zero, without claiming that confidence
is approval. Server admission remains authoritative and must revalidate the
exact revision and subject on every write.

The OpenAPI input is the service workstream's subject projection export. The
TypeScript types and runtime validators both consume this same document.
`npm run generate` applies the established formatter after generation. A shared
contract update requires regenerating OpenAPI and this client together. The
original PR #39 Python tree predates the subject endpoint; its unchanged Python
export cannot be used as proof that the new projection is already integrated.

## Source page locator

`PdfEvidence` consumes authorized bytes through a `SourceLoader`, checks their
SHA-256 against the citation, and renders the exact one-based page with the
bundled PDF.js worker. Rendering uses unrotated CropBox coordinates, matching
the parser's bottom-left, CropBox-local citation convention. Valid boxes receive
a field overlay. Missing, degenerate or out-of-page coordinates produce a page
view without a fabricated highlight. Source errors never substitute another PDF.

No filesystem path, arbitrary URL, original private document or mapping is sent
to a cloud API. The task and result pages load
`GET /v1/documents/{document_id}/content?version={version}&content_hash={sha256}`
with the current bearer session. Artifact buttons request
`GET /v1/review-jobs/{job_id}/artifacts/{artifact_id}/content` and verify the
returned bytes against the current result manifest before saving. These routes
require the integration service's configured authorization and source/publication
providers; browser implementation alone does not establish endpoint acceptance.
Private upload and restoration require the separate authorized local bridge.

## Reproducible checks

From `web/`:

```bash
npm ci --cache ../artifacts/npm-cache
npm run generate
npm run verify
git diff --exit-code -- src/api/schema.d.ts
PLAYWRIGHT_BROWSERS_PATH="$PWD/../artifacts/browser-binaries" npm exec playwright install chromium firefox
mkdir -p ../artifacts/browser-tmp
TMPDIR="$PWD/../artifacts/browser-tmp" PLAYWRIGHT_BROWSERS_PATH="$PWD/../artifacts/browser-binaries" npm run e2e
```

The generation diff gate applies after the reviewed canonical schema and types
are committed together. During edits, compare repeated generation hashes as
well as the intended canonical schema diff. Browser binaries and npm cache stay
within this repository. Existing route-stubbed browser smoke proves UI behavior
only; it does not establish service acceptance.

## Real API browser rehearsal

Use a fresh isolated synthetic API instance per run. Its ignored manifest must
contain `api_base_url` (loopback HTTP), a server-issued development
`session_token`, `empty_job_id`, `completed_job_id`, and independent task UUIDs under
`tasks.confirm`, `tasks.correct`, `tasks.reject`, `tasks.conflict`, and
`tasks.lost_response`. The correction case must have a numeric `m` observation. The confirmation task
must cite a retrievable PDF with a valid field box; the completed job must expose
one published PDF through its result and authenticated download endpoint.
Tasks must belong to separate jobs so a correction does not supersede another
test's material. No test body may assert a principal or role.

```bash
VITE_API_BASE_URL='' npm run build
mkdir -p ../artifacts/browser-tmp
REVIEW_BROWSER_FIXTURE=/absolute/path/to/ignored/fixture.json \
TMPDIR="$PWD/../artifacts/browser-tmp" PLAYWRIGHT_BROWSERS_PATH="$PWD/../artifacts/browser-binaries" npm run e2e:real
```

This suite uses the real API with no browser route mocks. The isolated test
server serves the production bundle and forwards HTTP requests. For lost-response
recovery only, it consumes the actual upstream committed response and then
interrupts its body. The browser must resend identical bytes and key and recover
the receipt. A competing real submission causes the conflict scenario. The
correction scenario checks committed revision history for the retained unit.

`scripts/browser-proxy.mjs` is a loopback-only synthetic test tool with explicit
fault injection, never a production authentication or privacy bridge. Neither
the proxy nor these tests prove durability across processes. Results belong in
ignored `artifacts/`; no raw private content should enter browser traces.

Real API, source/download and complete-case acceptance require the configured
integration service. A passing unit/build run is not a substitute. AWS, real
model quality and formal human approval remain separate acceptance boundaries.

## Local evidence for this repair

Regression evidence is retained under this worktree's ignored
`artifacts/review-evidence/`. The unchanged head was
`3c601537d18844bda06342a5e65fab36a7321dc0`:

- `before-tests.log`: eight failures covering command binding, retry payload,
  body deadline/interruption and invalid successful receipts.
- `before-correction-tests.log`: six failures covering metres, percent zero,
  missing metadata/type/unit and category input.
- `before-generation.log`: unchanged-schema generation changed committed formatting.
- `verify.log`: 50 tests plus types, lint, formatting, production build and
  artifact checks passed after repairs.
- `generation-after.log`: repeated generation produced the same file hash.
- `chromium-final.log`: three Chromium smoke paths passed. In `browser-smoke-fixed.log`, three Firefox
  cases could not launch because the executable reported
  `Could not find profile folder`, including with a repository-local temporary
  directory. This is an environment failure, not a passing browser result.
- `submission-preflight.log`: existing submission checker passed over the
  original PR delta, current working files/index and configured identity.

No repair commit or push has been performed. The parent must inspect and gate
these changes before publication. Real configured API, source rendering and
artifact-download acceptance remain pending; route-stubbed smoke is kept
explicitly separate from those paths.
