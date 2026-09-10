# Synthetic local workbench launcher

This composition uses actual C2 admission, parsed PDF sources, deterministic
review, the canonical human-task API, SQLite job/task/result transactions,
durable dispatch, controlled decision records, real PDF output and fenced
publication. Its Converse client is an explicitly fixed mock model. It does not
measure model quality or establish AWS, formal document or business acceptance.

## Prepare and serve

Use the repository-local installed environment so isolated workers can import the
same package. Run from the integration checkout:

```sh
PYTHONPATH="$PWD/src" .venv/bin/python scripts/run_local_workbench.py \
  --directory artifacts/workbench --port 8765 --prepare
PYTHONPATH="$PWD/src" .venv/bin/python scripts/run_local_workbench.py \
  --directory artifacts/workbench --port 8765 --serve
```

With neither mode flag, the command prepares and serves. `--fixture` optionally
selects a private manifest beneath the same directory. Startup prints the numeric
loopback URL, never the bearer token. The directory must be private. Keep every
generated input, database, credential and manifest under ignored artifacts.

Seven independently generated cases share one server-issued human session: empty
task collection, completed PDF, confirm, correct, reject, conflict and lost
response. `fixture.json` contains `api_base_url`, `session_token`, `empty_job_id`,
`completed_job_id` and the five response task IDs. It is private authentication
configuration, not a publishable test report. The two completed fixtures have
explicit synthetic approval; the other five start with actual confidence-zero
observations and human tasks.

Restart reuses the admitted sources, original case/job IDs, session and durable
state. It neither recreates consumed tasks nor interprets a consumed fixture as a
fresh test. Use a new directory for another independent browser scenario run.
Every result/download still checks current authorization and exact committed
bytes; the manifest is a navigation aid, not an authorization certificate.

## Exact synthetic authority and persistence

Already approved completed fixtures retain their existing OS-signed local
approval path. Resumed revisions with legitimate UUID human confirmations use
the fixture's explicitly named `authorize(snapshot)` capability. It checks exact
authored synthetic policy, rule/source identities and hashes, output assets and
every required current confirmation. It neither rewrites the reviewer nor raises
raw confidence. A private SQLite grant binds the complete run, revision and
material digest with scope `synthetic-only`; replay rechecks both that record and
the exact fixture capability. This is an independent synthetic authorizer, not a
formal receipt. Central reviewer approval still needs its own future adapter.

Per-run request, writer configuration and evidence are typed JSON in SQLite. The
writer persists observed inode, output digest, field-map/result/review digests
before returning to the Controller. Publication rehydrates these records and
rechecks output bytes, exact attempt, sources and approved assets. Missing or
changed evidence fails. No resident writer object is authoritative after restart.
Dispatch is also durable before outbox acknowledgement. Workflow reservations
remain a separate recovery boundary; an unknown interrupted action is not reset
to a fresh allowance by this launcher.

## Fresh privacy-case callback

Trusted local composition calls
`await context.register_synthetic_case(fixture, name=optional_name)`. The fixture
must supply the fresh sanitized C2 source references, freshly derived snapshot,
explicit writer configuration/request and authored synthetic authorization
capability. Native case facts, evidence and material grants are not copied onto
new sanitized documents. The method returns `case_id` and `review_job_id` after
durable admission, before execution or any human confirmation.

`await context.case_status(case_id)` exposes private callback data: the job ID,
current job status and open task IDs. Only a current completed result adds the
completed job ID, run ID and artifact IDs. The privacy composition maps those
authorized artifacts to its own restore handle; this module invents no restore
authority. A gate running on another thread may schedule registration onto the
running service event loop. It must not wait for human completion in the upload
callback or auto-confirm the four uncertain sides.

The registered descriptor persists the new baseline, writer/request configuration
and C2 database identity for restart. Actual raster export, browser confirmation
and restored PDF acceptance require their separate observed integration tests.

## Combined privacy rehearsal

The repository-only combined launcher uses a new directory for each independent
browser run. It serves the core and privacy bridge on the same event loop:

```sh
PYTHONPATH="$PWD/src" .venv/bin/python scripts/run_integration_rehearsal.py \
  --directory artifacts/integration-rehearsal \
  --port 8766 --privacy-port 8788 --origin http://127.0.0.1:4174 \
  --ocr-config artifacts/integration-ocr/config.json
```

`--raster-dpi` selects the initial sanitized raster resolution; `--ocr-dpi`
selects both actual restoration rendering and OCR resolution. Both default to
144 and accept the existing worker range of 72 through 300. A different
resolution applies to a new rehearsal, never an already admitted source.

The OCR configuration must pin a real local Tesseract executable and the English
and Traditional Chinese language assets by digest. Startup performs preflight;
it neither installs global tools nor substitutes synthetic OCR for restoration.
The privacy scanner and extraction proposals remain explicitly authored synthetic
fixtures. Restoration uses the actual local refill processor and OCR checks.
Successful preflight does not establish restored-output acceptance.

The private `core/fixture.json` provides the seven untouched core cases. The
private `privacy/browser-private.json` provides the bridge session and source
handles. Its callback adds `review_job_id` and open task IDs after both sanitized
sources have been admitted and a new raster-derived case registered. The trusted
callback passes exact `raster_source_versions`; registration checks image-only
source pages and a template digest equal to the admitted forms. Those pins persist
in the private descriptor. Publication requires `TrustedRasterPublication` with
those exact versions, the approved template hash and the fixture authorization
for the current confirmed material. Native fixtures retain their marker checks.
Human
confirmation remains necessary for all four confidence-zero sides. Only an actual
current completed result adds `completed_job_id` and an opaque `restore_result_id`.

Restore handles persist in SQLite and bind a job, run and artifact. Each resolution
reauthorizes the server-issued principal, reads current durable job/result state,
requires the exact committed digest, downloads verified published bytes, and
rechecks current state. The result body's claimed attempt remains authoritative;
a terminal job view deliberately omits its attempt ID. The coordinator separately
checks the exact C2 forms and encrypted session mapping, unchanged placeholder
pixels, and existing occurrence identities before building a real local refill
plan. A stale or revoked result cannot authorize a cached plan.

Core state can reopen independently. The combined launcher intentionally requires
a fresh privacy session: its mapping keys remain session-local, and this command
does not claim recovery of those keys across process restart. Keep both private
fixture files and all generated PDFs out of public evidence reports.


The combined launcher records same-request restoration evidence under the private
`privacy/private-evidence` directory (mode 0700), with files mode 0600. The recorder
retains the actual plan, exact published PDF bytes and digest, and every unchanged
OCR text, bounding box and raw confidence. It forwards the OCR method arguments,
returns the original observations, and re-raises the same exception on failure;
failed records contain the exception class without its potentially private message.
The recorder prints no document values. A failed HTTP restoration attempt does
not produce evidence of a restored PDF. Browser checkpoints and response statuses
must be collected from that actual run, with source hashes checked independently.

Status polling waits until trusted registration has persisted the submitted job
identity. A fixture may already be available to execution while admission is
awaiting submission; that intermediate state does not publish a job ID. Errors
after registration remain visible and are not swallowed by the poller.
