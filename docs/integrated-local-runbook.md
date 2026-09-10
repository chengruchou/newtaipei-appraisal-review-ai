# Configured local integration rehearsal

This is an isolated synthetic workflow, using the same Controller, task/job ports,
C2 admission and publication code as the service composition. Model network
responses are injected. It is not production login, business approval or AWS
acceptance. Originals, encrypted mappings, OCR assets and restored PDFs stay in
the private local workspace. See [current progress](project-progress.md) and
[repair evidence](integration-repair-delivery.md) for acceptance scope.

## Prerequisites

- Python 3.12, Node.js/npm and Chromium supported by the locked Playwright version.
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
npm run verify
PLAYWRIGHT_BROWSERS_PATH=../artifacts/playwright-browsers npx playwright install chromium
cd ..
```

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

## Execute actual browser scenarios

From a second terminal, with the combined launcher still running:

```sh
ROOT="$PWD"
cd web
export REVIEW_BROWSER_FIXTURE="$ROOT/artifacts/integration-demo/core/fixture.json"
export PRIVACY_BROWSER_FIXTURE="$ROOT/artifacts/integration-demo/privacy/browser-private.json"
export PLAYWRIGHT_BROWSERS_PATH="$ROOT/artifacts/playwright-browsers"
npm run e2e:real -- reviewer-api.spec.ts
npm run e2e:real -- privacy-flow.spec.ts
```

These tests use the production frontend build and actual loopback HTTP. The
Playwright server publishes only nonsecret local configuration. It does not mock
API routes. It checks empty jobs, located source PDF, exact confirmation commands,
authoritative units, rejection, competing writes, lost response after server
commit, result publication and authorized hash-checked download. The privacy
scenario additionally checks exact C2 export, resumed review and local restoration.

A failed browser action or uncertain transfer must not be replayed with a new
command/key to fabricate success. Inspect its existing state and use a new
isolated scenario for a clean repeat. Low-confidence OCR remains a blocking
restoration result; neither a higher score nor an empty OCR result is substituted.
Do not describe the restoration scenario as accepted until its actual run passes.

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
