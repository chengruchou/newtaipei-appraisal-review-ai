# Local Docker validation stack

This launcher builds the browser workbench and runs the configured synthetic API,
durable queue, worker, human-task service and PDF publisher in one local container.
It uses the existing fixed synthetic cases and injected model responses. It does
not provision production identity, approve business material or deploy AWS.
The default AWS Runtime entrypoint remains unconfigured and returns 503.

The supported image is **Linux ARM64 only**. Use an ARM64 macOS host with Docker
Desktop, Python 3.11+ and Node 22.13+ (22.x) or Node 24+. The launcher also accepts
an ARM64 Linux host with a local ARM64 daemon; that host path requires its own
acceptance evidence. Windows and x86 are not claimed. No global package install,
Docker setting change or AWS credential is required. Start the already installed
Docker application and verify `docker context show` and `docker info` before continuing.
The launcher reads only context endpoint metadata, requires an owned local Unix
socket, and pins that socket for every Docker operation without changing the global
context. SSH/TCP endpoints, ambiguous endpoint overrides and explicit builder
overrides are rejected before daemon operations. Buildx uses the verified daemon's
default builder even if another builder was previously selected. Restart/stop
require the same socket recorded by build/start. A changed local context fails
explicitly; select the intended context yourself before retrying.

## Build and start from a clean checkout

From the repository root:

```sh
python3 scripts/local_validation_stack.py build --workspace artifacts/local-validation/demo
python3 scripts/local_validation_stack.py start --workspace artifacts/local-validation/demo \
  --demo --port 4174
```

Use a new directory for a new independent exercise. Preparation creates seven
fixed synthetic cases; only the authored completed/empty scenarios start with
synthetic authority. Other tasks still require their permitted responses. Raw
observations and measured confidence remain unchanged. A container will refuse a
bootstrap containing host-registered cases. This is not a private-file uploader.

Build uses `npm ci` with the existing frontend lockfile, runs the normal TypeScript
and Vite build plus artifact check in an isolated directory, then builds from an
explicit file allowlist. The pinned Python base and both existing runtime lockfiles
are reused without modification. Build commands, exit codes, input SHA-256 values,
actual image ID and architecture are recorded under the private workspace.

Start requires `--demo`. It creates a uniquely labelled bridge network, private
named volume and container. Only `127.0.0.1:4174` is published. Runtime uses
UID/GID 10001, a read-only root filesystem, dropped capabilities, no new privileges,
bounded CPU/memory/PIDs and a temporary scratch filesystem. The volume's root is
owned by that user with mode 0700. The Docker daemon is part of the trusted local
boundary; do not share it with untrusted operators. IP masquerading is disabled
on this bridge. This is not proof of complete outbound network denial: runtime
uses fixed synthetic model responses and receives no AWS credentials. A Docker
`--internal` network alone does not publish ports on the tested Desktop setup.

Startup waits for `/readyz`, which requires completed configuration/preparation,
readable durable state, built frontend assets and no reported worker error.
`/livez` means only that the server responds. Neither endpoint establishes model
access, completed business work or cloud readiness. Missing/invalid configuration,
permissions or startup failure exits nonzero and retains the failed namespace.
Use a new namespace after correcting an image; do not rewrite old state to fit it.

Open `http://127.0.0.1:4174`. The launcher copies the freshly generated session
manifest to `artifacts/local-validation/demo/session.json` with mode 0600. Read it
locally in a trusted editor, enter its `session_token` in the existing sign-in form
and navigate to `/jobs/<completed_job_id>` or `/tasks/<tasks.confirm>` using its
actual identifiers. Never paste the manifest into a report, shell command, issue
or log. It is not served by HTTP and is not embedded in the frontend or image.

The Starlette/Uvicorn server serves only built assets and explicit SPA routes.
It dispatches `/v1/` to the canonical authenticated API. It validates exact Host
and Origin, limits request bodies and does not expose fixture, filesystem or
fault-injection controls. It never uses `web/scripts/browser-proxy.mjs`.

## Verify and preserve evidence

The dedicated browser suite connects to the running stack; it starts no proxy and
uses no route mocks. Select an already installed Chrome executable, or an existing
Chromium installation compatible with the locked Playwright version. The built
workspace contains an isolated `frontend` directory with the declared dependencies:

```sh
# Set this to the frontend directory beside the context recorded in build.json.
STACK_FRONTEND="/absolute/path/to/private/workspace/build-identifier/frontend"
STACK_WORKSPACE="$PWD/artifacts/local-validation/demo"
mkdir -m 700 "$STACK_WORKSPACE/browser"
cd "$STACK_FRONTEND"
LOCAL_STACK_SESSION="$STACK_WORKSPACE/session.json" \
LOCAL_STACK_EVIDENCE="$STACK_WORKSPACE/browser" \
LOCAL_STACK_CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
./node_modules/.bin/playwright test --config playwright.local-stack.config.ts
```

The suite saves the actual browser PDF and checks its manifest hash, exercises
confirmation/correction/rejection through the browser, compares identical replay
receipts and checks unauthorized requests. Output contains local synthetic case
records and remains private. Reopen the actual saved PDF with the declared PDF
tools; report page/field/glyph and source-integrity checks separately. A completed
browser run consumes tasks, so a fresh run needs a new namespace, not a state reset.

```sh
python3 scripts/local_validation_stack.py status --workspace artifacts/local-validation/demo
python3 scripts/local_validation_stack.py restart --workspace artifacts/local-validation/demo
python3 scripts/local_validation_stack.py stop --workspace artifacts/local-validation/demo
```

Restart reuses the exact recorded image and volume, rechecks readiness and refreshes
the private manifest without resetting jobs, tasks, revisions or receipts. Verify
previous receipts and artifact hashes again after restart. Stop retains the
container, volume and command evidence; it does not delete documents or replenish
budgets. There is no automatic destructive cleanup command. Inspect the individually
labelled resources before any separately authorized removal.

## Connect the trusted host privacy companion

Originals, mappings, keys, OCR assets and final revealed PDFs stay in the host
companion's private directory. They are never mounted into this container. For a
paired privacy exercise, export only the built static UI, then stop the container:

```sh
python3 scripts/local_validation_stack.py export-ui --workspace artifacts/local-validation/demo
python3 scripts/local_validation_stack.py stop --workspace artifacts/local-validation/demo
```

Follow the [integrated local runbook](integrated-local-runbook.md) to provision a
fresh host synthetic rehearsal and independently verified Tesseract configuration.
Its executable, tessdata directory and every language file must have exact approved
hashes. Use the existing paired launcher with API port 8766, companion port 8788
and exact Origin `http://127.0.0.1:4174`. Keep its API and companion session tokens
separate; use their private manifests only on the host. Then replace the old test
proxy with this server, using the same installed local Python dependencies:

```sh
.venv/bin/python infra/local-validation/server.py --mode host-companion \
  --origin http://127.0.0.1:4174 \
  --assets "$PWD/artifacts/local-validation/demo/ui" \
  --api-fixture "$PWD/artifacts/integration-demo/core/fixture.json" \
  --privacy-base http://127.0.0.1:8788
```

The proxy forwards only canonical API requests to the configured numeric loopback
API, preserving the caller's authentication; it never injects the fixture session
into user requests. Readiness authenticates a separate status probe and requires a
valid response for the exact configured job. `/local-config.json` exposes only the
numeric companion address. Privacy requests go directly from the browser to that
companion with its separate session and exact Origin checks. Only approved,
sanitized transfers enter its host API. Published and candidate OCR review receipts,
current digest/expiry checks and final local download remain mandatory.

For final stack privacy acceptance, run from this checkout's `web` directory,
using the existing locked browser installation and the exact paired manifests and
reading directory described in
[the privacy scenarios runbook](../web/docs/privacy-browser-scenarios.md). Keep the
real host companion server above running and serve the exact exported built UI.
The stack-specific configuration attaches to it and never starts the rehearsal
fault proxy:

```sh
LOCAL_STACK_PRIVACY_SCENARIO=positive npx --no-install playwright test \
  --config e2e-real/config/local-stack.config.ts
```

Use `LOCAL_STACK_PRIVACY_SCENARIO=automatic` for the original automatic-refusal
check at the published capture checkpoint, before any reading plan. Use `resume`
only for that same published case with its checkpoint. For an intentional pause
followed by resume, start a separate fresh paired namespace and keep that same
backend alive throughout:

```sh
LOCAL_STACK_PRIVACY_SCENARIO=paused npx --no-install playwright test \
  --config e2e-real/config/local-stack.config.ts
LOCAL_STACK_PRIVACY_SCENARIO=resume npx --no-install playwright test \
  --config e2e-real/config/local-stack.config.ts
```

Paused retains the original source-to-publication flow, complete page/crop capture
and automatic refusal assertions, and exits without individual readings or a
receipt. Resume completes that same case using its exact individual reading plans.
Report this sequence separately from a fresh complete positive run. All four
scenarios use the same real server and readiness checks. The wrapper retains
the shared namespace, unused output directory, fresh case, checkpoint and exact
reading guards, all selected test assertions, browser identity and existing waits.
It only removes the test server startup and adds actual HTTP checks for ready
`host-companion` mode, the exact companion address and an absent fault-control
route. A failed check stops before the scenario.

Run the configuration from this checkout, not the isolated frontend build copy:
the shared guards deliberately bind the private namespace to this checkout's
`artifacts` directory. Keep core and companion sessions separate. Individual
published/restored page and crop inspection, exact reading plans, current leases,
receipts and the actual browser download are still required. No response, OCR
confidence, authority or lifetime is changed by this configuration.

This documented pairing is separate from the container core evidence. Rehearse it
with current pinnings before claiming privacy acceptance. The earlier restoration
timeout/409 and repeated-corpus reliability remain in #22; no timeout or privacy
production change is made here. Stop both host processes with their normal SIGINT
shutdown; retain private state and review expiry, not a copied old receipt, on resume.

## Security and remaining acceptance

Runtime logs and Docker log collection are disabled. Request bodies, sessions and
private PDFs are never logged by this packaging. Build contexts contain only
allowlisted code, locked dependency inputs and built UI assets. No runtime
configuration, originals, mapping keys or final PDFs enter an image layer.

The pinned base is the existing reviewed input, **not an OS vulnerability fix**.
The historical scan reported unresolved Debian findings, including Critical and High
findings; #50 owns remediation. That historical count is not a current scan result.
After integrating source changes, rebuild the exact final head and run Trivy with a
fresh database against its saved final filesystem, with no ignores and a nonzero
severity gate. Independently scan every exported image layer for credential
patterns, including deleted files. These are distinct checks. Repeat Python and npm advisory scans even when dependency
versions are unchanged. Retain raw results without severity reductions. Python/npm checks do not clear OS findings. Hosted CI is
#49; production onboarding/privacy provisioners are #25/#22; configured cloud
deployment is #30 and live acceptance is #31. This local demonstration clears none
of those external gates. See [ADR 0050](adr/0050-local-validation-stack.md).
