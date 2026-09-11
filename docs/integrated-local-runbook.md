# Configured local integration rehearsal

Baseline: `main` after PR #45, merge commit
`d148422adb18190bada93b8588a4e34d73e3c2e4`. Its application tree matches reviewed
head `fd22e68321bad6b58f06068dfef1db67fdb1c269`. A complete synthetic privacy
restoration was reported at that head using two local visual OCR review stages;
this does not establish repeatability or automatic OCR reliability. See the
[validation record](local-validation-record.md) for the exact evidence and
[implementation backlog](implementation-backlog.md) for remaining work.

This is an isolated synthetic workflow, using the same Controller, task/job ports,
C2 admission and publication code as the service composition. Model network
responses are injected. It is not production login, business approval or AWS
acceptance. Originals, encrypted mappings, OCR assets and restored PDFs stay in
the private local workspace. See [current progress](project-progress.md) and
[repair evidence](integration-repair-delivery.md) for acceptance scope.

## Prerequisites

- Python 3.12, a Node.js version satisfying every locked dependency, npm and
  Chromium supported by the locked Playwright version. In this lockfile,
  `pdfjs-dist` requires Node 22.13.0 or newer and Vitest excludes Node 23;
  use Node 22.x at least 22.13.0 or Node 24+. Record the actual runtime version.
- Linux or macOS with the required local PDF/process isolation capabilities.
- For restoration, a working Tesseract executable plus English and Traditional
  Chinese language files. Supply an operator-owned TesseractConfig JSON with
  absolute executable/tessdata paths and SHA-256 hashes for the executable and
  every language asset. The application verifies those bytes before use.
  Never add credentials, original documents or private mapping material to Git.
- Free numeric loopback ports 8766, 8788 and 4174. The configured Origin must
  exactly match the frontend server. Do not substitute a public bind address.
- A new ignored rehearsal directory. Mutation scenarios consume tasks; use a
  fresh directory to repeat all browser scenarios, rather than reset stored history.

The repository declares the Python extras and locks the frontend dependencies.
Install a noneditable package into a fresh local environment from the checkout.
After source changes, rebuild/reinstall it before rerunning; do not mix an old
installed wheel with new launcher code:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install '.[dev,aws,pdf,privacy,documents]'
cd web
npm ci
unset VITE_API_BASE_URL
npm run verify
PLAYWRIGHT_BROWSERS_PATH=../artifacts/playwright-browsers npx playwright install chromium
cd ..
```

These commands are validation gates, not assertions of a pass. If verification
fails, retain the exact failure and runtime versions before continuing a rehearsal.
Current frontend runtime/JSDOM and hosted CI follow-up is tracked in
[#49](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/49).

## Pin existing local OCR assets

For the privacy launcher, first select an existing trusted executable and language
asset directory. Resolve symlinks to real files; startup rejects mutable aliases.
The following creates the required JSON without installing tools or downloading
assets. Replace the two example paths with your local selections and independently
verify the reported asset hashes against the trusted source of those binaries.
This configuration is local runtime input, not a material approval.

```sh
export REVIEW_OCR_EXECUTABLE="/absolute/path/to/tesseract"
export REVIEW_OCR_TESSDATA="/absolute/path/to/tessdata"
.venv/bin/python - <<'PYTHON'
import hashlib, json, os
from pathlib import Path
exe = Path(os.environ["REVIEW_OCR_EXECUTABLE"]).resolve(strict=True)
data = Path(os.environ["REVIEW_OCR_TESSDATA"]).resolve(strict=True)
digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
config = {"executable": str(exe), "executable_sha256": digest(exe),
          "tessdata": str(data), "assets": [
              {"language": language, "sha256": digest(data / (language + ".traineddata"))}
              for language in ("chi_tra", "eng")]}
Path("artifacts").mkdir(exist_ok=True)
with os.fdopen(os.open("artifacts/local-ocr.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as out:
    json.dump(config, out, indent=2)
print("Created private OCR configuration; verify its hashes before starting.")
PYTHON
```

This writes a new file exclusively; it does not replace an existing configuration.
The launcher performs the actual preflight and keeps restoration blocked if the
engine is missing, changed, times out, or returns unacceptable observations.

## Start the core without local OCR

This command needs no Tesseract assets. It prepares seven independent synthetic
cases and runs the authenticated local API, durable queue, worker and publisher.

```sh
.venv/bin/python scripts/run_local_workbench.py \
  --directory artifacts/core-demo --port 8766
```

The private fixture manifest is `artifacts/core-demo/fixture.json`. The launcher
prints public listener addresses, not its session token. A browser operator may
use the separately issued local session; API bodies cannot choose a principal,
role, filesystem path or source URL.

Run the core browser scenarios from a second terminal at the repository root:

```sh
REVIEW_ROOT="$PWD"
cd web
REVIEW_BROWSER_FIXTURE="$REVIEW_ROOT/artifacts/core-demo/fixture.json" \
PLAYWRIGHT_BROWSERS_PATH="$REVIEW_ROOT/artifacts/playwright-browsers" \
npm run e2e:real -- reviewer-api.spec.ts
```

`playwright.real.config.ts` starts the built frontend on `http://127.0.0.1:4174`
with one Chromium worker and no retries. It requires the `dist/` built above.
Its loopback server forwards actual `/v1/` requests to the configured API; browser
routes are not mocked. The server also exposes synthetic fault-injection controls
for recovery tests and must never be deployed or exposed publicly.

`gateway-recovery.spec.ts` also uses the `lost_response` scenario. Run it against
a separately prepared fresh core directory, not the one already consumed by
`reviewer-api.spec.ts`. Select that fresh directory's `fixture.json` with the same
environment variable and replace the spec argument with `gateway-recovery.spec.ts`.

## Start the paired privacy and review services

Use the combined launcher instead of the standalone core when testing privacy.
An existing hash-pinned OCR configuration is a required explicit input.

```sh
.venv/bin/python scripts/run_integration_rehearsal.py \
  --directory artifacts/integration-demo --port 8766 --privacy-port 8788 \
  --origin http://127.0.0.1:4174 --ocr-config artifacts/local-ocr.json
```

The privacy workbench uses `/privacy` and a separate bridge session. The public
frontend configuration exposes only the loopback bridge address. Exact rendered
pages, regions and sanitized preview must be reviewed before each one-use transfer.
The initial sanitized admissions create a candidate revision, never confirmations.
Four actual human responses are required for the low-confidence fixture; their
stored confidence remains zero. Independent exact synthetic authority is only for
the fixed authored assets, not arbitrary requests or real material.

## Open the synthetic workbench manually

With the combined launcher running, start its existing rehearsal server in a
second terminal at the repository root:

```sh
REVIEW_ROOT="$PWD"
cd web
REVIEW_BROWSER_FIXTURE="$REVIEW_ROOT/artifacts/integration-demo/core/fixture.json" \
PRIVACY_BROWSER_FIXTURE="$REVIEW_ROOT/artifacts/integration-demo/privacy/browser-private.json" \
node scripts/browser-proxy.mjs
```

Open `http://127.0.0.1:4174`. Use the private core fixture's `session_token` in the
sign-in form and its `completed_job_id` to inspect an existing published result,
or open `/tasks/<task identifier>` using an entry from `tasks`. Read these values
locally; do not copy private fixture files or tokens into reports or shared logs.

Open `/privacy` and use the private privacy fixture's `token` for the separate
local bridge session. The page lists configured source handles. After reviewing
and transferring both sources, use the fixture's current `review_job_id` to find
material tasks. Once publication succeeds, the updated private fixture supplies
`restore_result_id` for the restoration form. Follow the individual review steps
below when OCR requests visual review. The job page has explicit **Refresh** and
**Load current result** controls; it does not poll automatically.

For core-only exploration, set `REVIEW_BROWSER_FIXTURE` to the core-only manifest
and omit `PRIVACY_BROWSER_FIXTURE`; `/privacy` is then unavailable. Stop this manual
server before running Playwright, which starts its own server on the same port.
Manual writes consume the fixture, so use another fresh directory for automated
scenarios. This manual entry remains a synthetic rehearsal using the test proxy;
a deployable frontend/API package is tracked in
[#48](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/48).

## Execute actual browser scenarios

From a second terminal, with the combined launcher still running:

```sh
REVIEW_ROOT="$PWD"
cd web
export REVIEW_BROWSER_FIXTURE="$REVIEW_ROOT/artifacts/integration-demo/core/fixture.json"
export PRIVACY_BROWSER_FIXTURE="$REVIEW_ROOT/artifacts/integration-demo/privacy/browser-private.json"
export PRIVACY_OCR_READING_DIRECTORY="$REVIEW_ROOT/artifacts/integration-demo/ocr-readings"
export PLAYWRIGHT_BROWSERS_PATH="$REVIEW_ROOT/artifacts/playwright-browsers"
npm run e2e:real -- reviewer-api.spec.ts
npm run e2e:real -- privacy-flow.spec.ts
```

These tests use the production frontend build and actual loopback HTTP. The
Playwright server publishes only nonsecret local configuration. It does not mock
API routes. It checks empty jobs, located source PDF, exact confirmation commands,
authoritative units, rejection, competing writes, lost response after server
commit, result publication and authorized hash-checked download. The privacy
scenario additionally checks exact C2 export, resumed review and local restoration.
Its OCR helper requires operator-prepared reading plans as described below; the
command is not an unattended end-to-end acceptance run.

## Complete the two local OCR review stages

Automatic low-confidence OCR still refuses restoration. When the response is the
canonical `409 local_privacy_review_required`, the UI opens local visual review
for the exact published PDF. It does not replace raw OCR scores or silently approve
material. After that stage passes and the operator explicitly resumes restoration,
the restored candidate may require a separate stage and receipt.

For manual review, inspect the verified full-page image and selected region crop,
enter each required visible reading, check its individual inspection box and confirm
that item. Editing a reading clears the check. There is no bulk confirmation.
Only a complete current-stage receipt enables **Restore result through local bridge**.
After both stages pass, **Save restored PDF locally** becomes available and the
download must match the final manifest digest. See
[local OCR review](local-privacy-ocr-review.md) for expiry and explicit restart behavior.

For `privacy-flow.spec.ts`, the shared helper saves the private stage view as
`published-before.json` or `restored-before.json`, along with actual full-page and
region images, under `PRIVACY_OCR_READING_DIRECTORY`. It then waits for
`published-readings.json` and, later, `restored-readings.json`. Inspect those exact
images before preparing each plan. The plan format is:

```json
{
  "purpose": "automated_synthetic_ui_workflow",
  "stage": "published",
  "review_digest": "<current stage review_digest>",
  "input_sha256": "<current stage input_sha256>",
  "readings": [
    {
      "item_id": "<required item_id>",
      "page_image_sha256": "<that item's current page image hash>",
      "reading": "<explicit transcription of the visible region>"
    }
  ]
}
```

This is a format example, not a valid prefilled plan. Include exactly one entry
for every required item, with real current identifiers and hashes; the second
plan uses `stage: restored` and that stage's evidence. Do not copy OCR text into
readings without inspecting the images, reuse an old plan, or increase confidence.
The helper waits up to eight minutes for each plan, while the bridge review lease
is at most fifteen minutes and may be shorter. Expiry requires the explicit
restart workflow and new confirmations.

The helper enters the readings and clicks each confirmation through the actual UI.
These are automated synthetic workflow confirmations, not independent human or
business approval. Stage views, readings, receipts, images and downloaded PDFs
remain private. General traces and videos stay disabled; these explicit local
OCR evidence images are intentionally retained.

`ocr-review-flow.spec.ts` is a focused resume wrapper for an already published
original case with a pending OCR review. With that case's same private fixtures
and reading directory, stop any separate manual proxy and run:

```sh
npm run e2e:real -- ocr-review-flow.spec.ts
```

It does not create, transfer or approve a fresh case and is not an additional step
to run after a completed positive flow. `ocr-review-automatic.spec.ts` instead
checks the same positive flow while paused before any reading plans exist. It
expects `ocr-readings` directly under the integration directory so it can bind the
initial diagnostic. It needs a separate API-only Playwright configuration without
another proxy; that configuration is not checked in. Do not run it blindly in the
standard suite or claim its refusal assertions prove successful restoration. The
remaining harness and OCR reliability work is tracked in
[#22](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/22).

A failed browser action or uncertain transfer must not be replayed with a new
command/key to fabricate success. Inspect its existing state and use a new
isolated scenario for a clean repeat. Low-confidence OCR remains a blocking
restoration result; neither a higher score nor an empty OCR result is substituted.
An explicit visual-reading receipt can satisfy its exact stage while preserving
the original failure and measurements. Do not describe the restoration scenario
as accepted until its actual run passes. The older uncaptured 409 and browser
timeout remain historical failures; a later complete run does not explain them.

## Evidence and limitations

Artifacts and raw observations remain under ignored `artifacts/`. Keep the
browser run log together with its source/configuration snapshot. The read-only
[local observation collector](local-integration-evidence.md) can bind current
SQLite/PDF state and captured bodies, but does not authenticate a capture producer
or establish absence of sensitive content by itself.

The SQLite adapters implement local transactions, fenced publication and process
recovery. They do not claim distributed AWS transactions or cross-region recovery.
The isolated wheel/container checks exercise this configured local root; the
unconfigured Runtime entrypoints remain 503. Real AWS identity, deployment,
image scan acceptance, designated model evaluation and independent business
review remain separate gates.
