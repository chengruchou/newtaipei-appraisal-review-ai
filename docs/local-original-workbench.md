# Controlled local original workbench

This entry uses actual configured PDFs, the native parser, the deterministic
Controller, SQLite jobs and canonical human responses. It makes no model call.
It provides five local views: configured cases, progress, results, evidence and
human tasks. It is a partial delivery until the real-case acceptance gates in
[kpi1 delivery](kpi1-delivery.md) pass. The separate
[synthetic launcher](synthetic-workbench-launcher.md) retains its original name
and behavior and must not be used as real-case acceptance evidence.

## Prepare the environment

Use Linux or macOS and the repository Python extras. The checked environment was
Python 3.13.5 and Node 26.7.0; the lockfile and supported versions in
[the integrated runbook](integrated-local-runbook.md) remain authoritative.
Run from this worktree, not from a separately installed older wheel:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install '.[dev,pdf,documents,privacy]'
cd web
npm ci
cd ..
export PYTHONPATH="$PWD/src"
```

Do not install cloud credentials or enable an external model for this local
flow. Runtime document bytes come from the configured local originals. Browser
source previews require a separately paired numeric loopback service.

## Controlled candidate import

Keep originals read-only in an authorized directory under ignored `artifacts/`.
The operator prepares four private, pinned JSON inputs using the existing
contracts:

- InputManifest: case identity query, canonical original paths, document roles,
  versions and SHA-256 hashes. It is not confirmation of applicability.
- SourceRegistry: the complete actual native parser output for every original.
- RuleCatalog: independently versioned general rules and district basis entries,
  exact scope/date conditions, purpose, original evidence and review state.
- CandidateSelections: traceable condition interpretations, actual native rule
  candidates, fact/value/unit anchors, observed cells and supported procedures.
  These are explicit local proposals, not prewritten model/OCR results.

The preparation verifies all original bytes and reparses the entire registry.
It rejects forged regions, changed values labelled as native, unknown source
roles, ambiguous selections, absent anchors and incompatible rules. Native
localization does not manufacture measured semantic confidence. Output contains
proposed facts and preserves raw zero confidence and unresolved conditions.

```sh
REVIEW_INPUT_DIR="$PWD/artifacts/operator-inputs"
REVIEW_SOURCE_DIR="$PWD/artifacts/originals"
PYTHONPATH="$PWD/src" .venv/bin/python scripts/prepare_local_case.py \
  --manifest "$REVIEW_INPUT_DIR/input-manifest.json" \
  --registry "$REVIEW_INPUT_DIR/registry.json" \
  --catalog "$REVIEW_INPUT_DIR/rule-catalog.json" \
  --selections "$REVIEW_INPUT_DIR/candidate-selections.json" \
  --source-root "$REVIEW_SOURCE_DIR" \
  --output artifacts/prepared-case
```

Replace the two directories with authorized existing local input locations.
The output directory must be new; an existing directory is rejected. Exit zero
means candidate preparation succeeded, not case acceptance. A missing or
ambiguous source selection is retained in `rule-resolution.json` and exits two.
The current importer supports pinned native-parser registries; raw OCR outputs
are retained separately and are not silently converted into trusted selections.

The native parser limit is 200 pages. Preparation defaults to 100 MB per file;
the running original source adapter uses a stricter 20 MiB limit and the browser
checks a 32 MiB limit. The supplied 169-page manual was below all byte limits
and passed the actual local runtime parser. This local mode does not traverse
C2 privacy/snapshot paths whose separate page limits and admission requirements
still apply. Missing text on manual pages 2 and 4 is recorded, not suppressed.

## Start the real local API and paired preview

Use a new private runtime directory for another initial material or OS owner.
Restarting the same directory reopens its durable jobs and immutable receipts.
The session expires after four hours by default, with an eight-hour maximum.
Changing a session does not reissue or approve previous material.

```sh
PYTHONPATH="$PWD/src" .venv/bin/python scripts/run_original_workbench.py \
  --manifest artifacts/prepared-case/input-manifest.json \
  --material artifacts/prepared-case/material.json \
  --data-directory artifacts/original-runtime \
  --port 8768 --preview-port 8769 \
  --web-origin http://127.0.0.1:4176
```

The CLI writes owner-controlled private `session.json`, `preview-pairing.json`,
`configured-job.json` and `browser-config.json`. It does not print their tokens.
The session uses the actual OS reviewer `uid:name`, consistent with existing
local confirmation identity. A copied confirmation from another reviewer is
still offered for current review; it cannot silently acquire current authority.

From another terminal in this worktree:

```sh
cd web
VITE_LOCAL_ORIGINAL_PREVIEW_URL=http://127.0.0.1:8769 npm run build
cd ..
PYTHONPATH="$PWD/src" .venv/bin/python infra/local-validation/server.py \
  --mode host-companion --origin http://127.0.0.1:4176 \
  --assets "$PWD/web/dist" \
  --api-fixture "$PWD/artifacts/original-runtime/browser-config.json"
```

Open `http://127.0.0.1:4176`. Enter the controlled session token and the separately
issued original-preview pairing code through the local operator interface.
There is no public registration or arbitrary-password login. Do not paste
private configuration into an issue, commit, PR, screenshot intended for public
sharing, or an external service.

Only configured/authorized jobs are listed. Browser history is not a case
catalog. The case tabs use actual execution and business status, without invented
percentages or completion times. Source identifiers, dates, rule versions,
selection and condition candidates are inspectable; administrative district is
independent of any future deployment region.

## Human responses, interruptions and output

Open the task's actual PDF page and verify the exact value, unit and comparison
side. The server supplies allowed actions and exact task/material versions.
The final confirmation dialog freezes the payload and idempotency key. A fact
confirmation records that assertion without raising confidence or approving
conditions, rules, material or output. A correction remains a separate action;
the local proposed-fact task generator currently offers confirm/refuse, not a
full condition-correction product.

If the response is unknown, use the submission-status lookup. Do not create a
new key or resend blindly. Only an unchanged task with no existing receipt may
be explicitly retried with its original payload/key. Old tasks remain visible
as answered/superseded history. Receipt lookup remains available under current
case/source grants even when that receipt's run has completed.

Same-run interruption recovery stores each attempt's actual Controller output
separately. New tasks identify the producing attempt; abandoned open tasks are
superseded under the current lease fence. Paused assessments use the committed
current open task set, not an arbitrary historical attempt. This does not grant
an abandoned worker permission to publish.

The current real runtime exposes no formal writer or publication authority.
Actual template availability and bounded existing writer configuration are
recorded in [the real-data scope](kpi1-real-data-scope.md). Existing populated
forms are evidence, not an automatically approved blank template. A verified
map/font/template, complete applicable rules and exact-material approval remain
necessary. A fact receipt is not a downloadable report. The original receipt
applies only to unchanged original material; new revisions cannot reuse a
confirmation or approval that fails its exact binding.

## Checks and evidence

```sh
PYTHONPATH="$PWD/src" .venv/bin/python -m ruff check src tests scripts
PYTHONPATH="$PWD/src" .venv/bin/python -m ruff format --check src tests scripts
PYTHONPATH="$PWD/src" .venv/bin/python -m mypy src
PYTHONPATH="$PWD/src" .venv/bin/python scripts/export_openapi.py
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest tests/unit
cd web
NODE_OPTIONS=--no-experimental-webstorage npm run verify
cd ..
PYTHONPATH="$PWD/src" .venv/bin/python scripts/check_submission.py \
  --base origin/main --head HEAD --branch feat/local-review-workbench
```

The Node option is a compatibility setting for this installed Node/JSDOM runtime,
not a disabled test. Unit suites include labelled synthetic material and do not
constitute real acceptance. Actual browser captures and local transport/recovery
probes use the provided originals and unchanged real API responses; intentional
transport interruptions are explicitly recorded. The preparation includes a
source inventory, hashes, selected page anchors, unsupported candidates,
condition evidence and output/template blockers. Keep these private artifacts
outside Git. Consult the delivery record for actual command outcomes, not an
inferred pass from the commands listed here.
