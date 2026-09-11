# Reproduce the local privacy browser scenarios

These scenarios exercise the built workbench and actual loopback services with
the original paired synthetic case. They require explicit individual visual
readings. They do not grant human, business, material or cloud approval. Keep
the original input files, pinned OCR configuration and every failed run; a later
success does not explain an earlier timeout or 409.

Use the dependency lockfile and the existing supported Node/npm runtime. Record
`node --version`, `npm --version` and `npx --no-install playwright --version`.
Run `npm ci` and `npm run verify` in `web` before browser execution. Use the
Chromium installation for that locked Playwright version. Do not substitute a
system browser, change OCR assets or install global tools during a comparison.

The [configured integration runbook](../../docs/integrated-local-runbook.md)
describes the declared Python environment, hash-pinned OCR configuration and
combined launcher. Start it with a new, unused directory inside this checkout's
ignored `artifacts` directory. Originals, mapping keys, sessions and final PDFs
remain in that trusted local namespace. The launcher must stay running through
the paused/resume sequence; receipts cannot be imported after a process restart.

```sh
REVIEW_ROOT="$PWD"
REHEARSAL_RUN="$REVIEW_ROOT/artifacts/privacy-case-001"
.venv/bin/python scripts/run_integration_rehearsal.py \
  --directory "$REHEARSAL_RUN" --port 8766 --privacy-port 8788 \
  --origin http://127.0.0.1:4174 --ocr-config artifacts/local-ocr.json
```

In another terminal, set these paths to that exact run, then work from `web`:

```sh
REVIEW_ROOT="/absolute/path/to/this/checkout"
REHEARSAL_RUN="$REVIEW_ROOT/artifacts/privacy-case-001"
export REVIEW_BROWSER_FIXTURE="$REHEARSAL_RUN/core/fixture.json"
export PRIVACY_BROWSER_FIXTURE="$REHEARSAL_RUN/privacy/browser-private.json"
export PRIVACY_OCR_READING_DIRECTORY="$REHEARSAL_RUN/ocr-readings"
export PLAYWRIGHT_BROWSERS_PATH="$REVIEW_ROOT/artifacts/playwright-browsers"
cd "$REVIEW_ROOT/web"
```

Fixture manifests are private runtime input. Never paste their contents or
session values into commands, reports or public logs. The configurations require
matching core/privacy/readings namespaces and numeric loopback origins. Browser
outputs default to a separate `browser-<scenario>` directory inside the same run.
Existing nonempty output directories are refused to preserve previous evidence.
For a deliberate additional observation of the same pending case, set
`PRIVACY_BROWSER_OUTPUT_DIRECTORY` to another unused direct child of that run.
This changes the evidence destination, not the case, command or authority.

## One complete positive run

```sh
npx --no-install playwright test --config e2e-real/config/positive.config.ts
```

This selects only the original complete positive test. Its shared test helper
retains both original source reviews and exact one-use transfers, four current
side confirmations with raw confidences `[0, 0, 0, 0]`, two comparison contexts,
publication, both OCR stages, both receipts, the actual final download hash and
unauthorized refusal. It cannot finish successfully at an intermediate stage.

When all published pages and every required crop have been captured, the helper
writes `published-ready.json` and emits
`exact_synthetic_stage_ready_for_individual_readings`. It then waits for the
explicit reading plan. Before creating any plan, run the independent refusal
check in a third terminal with the same environment:

```sh
npx --no-install playwright test --config e2e-real/config/automatic.config.ts
```

This checked-in configuration runs the API-only regression without launching
another browser or proxy. It requires the capture checkpoint, verifies all page
hashes and crop files, and compares the actual current view against the original
99 observations with zero confirmations and zero receipts. It binds the initial
409 to its original correlation and private OCR diagnostic. A repeated pending
409 must have a new correlation and no failure headers, because waiting for
individual readings is not a newly thrown OCR failure. Wait for it to finish
before writing the published plan; do not overlap it with page capture or item
confirmation. A successful refusal check is not a completed restoration.

## Intentional pause, then resume

Use a separate fresh launcher namespace for this sequence:

```sh
npx --no-install playwright test --config e2e-real/config/paused.config.ts
```

This distinct test performs the original source-to-publication steps, captures
all published pages and seven required crops, performs the actual automatic
refusal assertions, and exits with no individual confirmations or receipt. The
`original_case_paused_without_readings` checkpoint states its partial scope. It
does not run or replace the original positive test's final-success assertions.

Keep that backend alive. Start the focused resume against the same manifests
and reading directory, then supply the exact plans described below:

```sh
npx --no-install playwright test --config e2e-real/config/resume.config.ts
```

Resume inspects the existing published result and completes its remaining local
stages. It creates no new case or transfer. It preserves the original refusal,
before views, observations and prior workflow evidence, and saves its actual
download separately. Report “paused case plus focused resume” separately from
“one complete fresh positive run.” A resume after a download failure remains
recovery of the same case even when no new reading is required.

## Exact stage plans

Each stage saves `<stage>-before.json`, `<stage>-page-<number>.png`, every
`<stage>-item-<item_id>.png` and `<stage>-ready.json` locally. Inspect both the
full page and each required crop. Original OCR text and confidence remain
unchanged; do not use OCR text as an automatic transcription. Missing or
uncertain observations require their own explicit visible reading.

Only after inspecting the current images, write `published-readings.json` and,
later, a separate `restored-readings.json`. Use this exact shape:

```json
{
  "purpose": "automated_synthetic_ui_workflow",
  "stage": "published",
  "review_digest": "<current stage review digest>",
  "input_sha256": "<current stage input digest>",
  "readings": [
    {
      "item_id": "<one required item identifier>",
      "page_image_sha256": "<that item's page digest>",
      "reading": "<explicit transcription of that visible region>"
    }
  ]
}
```

This format example is deliberately not a valid plan. Include exactly one entry
per required item and no additional fields. Stale stages, hashes, missing or
duplicate items, empty/control-character readings, changed confirmed readings
and incorrect placeholder literals are refused. Never reuse a prior run's plan.
The browser enters and confirms each reading separately; there is no approve-all
action. The published stage retains 99 observations and seven required items;
the original candidate retains 108 observations and four required items. The
two stage receipts grant only their exact local readings.

The existing bounded waits remain in effect: 120 seconds for restoration in the
client, 30 seconds for other client operations, 35 seconds for dedicated local
test requests and confirmation response waits, and eight minutes for each plan.
The review lease is at most fifteen minutes and may be shorter. Browser download
events keep their existing deadline. Diagnose actual response status, stage,
correlation and measured time before proposing a deadline change. One historical
14.318-second successful GET does not prove the cause of an earlier 409.

Both capture and confirmation loops wait for the selected full-page image before
selecting its item. The existing 30-second image poll uses a non-waiting DOM check:
an absent or undecoded image returns false, so a nested 15-second locator wait
cannot prematurely end that poll. This adds no HTTP request, confirmation retry
or authority extension, and every page hash and individual reading check remains.
The polling correction is separate from the observed candidate selector timeout;
that run also lost its bootstrap mapping-key authority. No selector-race cause or
successful full restoration is inferred from the correction.

## Evidence and stopping

`local-privacy-http.jsonl` records bounded observations of the actual browser's
restore, review and final-download requests. Header arrival and body completion
are separate events. Each record contains only a finite endpoint name, method,
elapsed time, actual HTTP status, validated server correlation and recognized
failure stage/code. A pending request when observation ends is not a server
failure. Unknown headers and exception text are never copied into these records.
Backend stage timings are inclusive; do not add nested timings together.

The local review UI displays these same recognized failure details and a valid
server reference when available. A client deadline or unavailable response is
identified separately; it does not invent a server status or cause. The canonical
review-required 409 still opens the individual review workflow. Generic errors
remain generic when the server does not supply the bounded diagnostic contract.

Keep stdout/stderr and browser output private with restrictive permissions, and
scan intended report material using the repository's existing content-redacted
credential scanner. General Playwright traces, screenshots and videos remain
disabled. The explicit OCR evidence images and downloaded PDFs stay local.
Record the exact source SHA or frozen file hashes, runtime versions, OCR asset
hashes, case namespace, scenario, success/failure and individual stage counts.

After a completed positive or focused resume, independently reopen the actual
browser download. Verify its manifest digest, original source hashes, expected
fields and glyphs, exact restored regions and all unaffected pixels. A final
restore response or a PDF found on the server filesystem alone is insufficient.
Preserve failed runs separately. Stop the owned local services and revoke their
sessions after evidence collection; do not delete originals, mappings or required
failure evidence to make a repeated scenario appear fresh.

The loopback browser proxy includes synthetic fault controls for other recovery
tests. These configurations do not make it a production server. Production local
provisioning, platform/key operations, deployed acceptance and actual human
approval remain separate work.
