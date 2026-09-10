# Local integration observation collector

`scripts/collect_local_integration_evidence.py` inspects existing private SQLite
state and PDF bytes without invoking the service, worker, writer, model, network,
or publication authorization. It supplements the existing rehearsal verifier;
it does not modify its target, trust, signature, scenario, or acceptance gates.
Exit code 0 means an observation report was written, not that acceptance passed.
Every report records `live_acceptance: false` and `assessment: observations_only`.

Run from the integration checkout with its declared local Python environment:

```sh
mkdir -p artifacts/local-integration-evidence
PYTHONPATH="$PWD/src" .venv/bin/python \
  scripts/collect_local_integration_evidence.py \
  --review-db artifacts/integration-rehearsal-live/core/state/review.sqlite \
  --source-db artifacts/integration-rehearsal-live/core/completed/documents.sqlite \
  --config artifacts/integration-rehearsal-live/core/completed/config.json \
  --output artifacts/local-integration-evidence/observed.json
```

Repeat `--source-db` for every relevant case's C2 database and `--config` for
configuration files. Omitted source databases produce explicit unobserved source
bytes/snapshots, never assumed verification. The report covers **current** job
runs, not every historical scenario. Select the actual newly launched rehearsal
root when collecting newer observations. Do not pass private `bootstrap.json`,
`fixture.json`, session credentials, original PDFs, encrypted maps, or keys as
configuration or capture input. Output must be a new file under ignored
`artifacts`; it is created with mode 0600 and is never overwritten.

The collector opens SQLite in read-only mode, backs up committed state (including
WAL) into memory, and validates that snapshot. It does not checkpoint or modify
application records. Each database has its own snapshot; there is no atomic
snapshot across separate databases. Input databases must be private regular
files. File inputs are bounded and confined to the checkout without symlinks.
Keep private input directories under trusted local control during collection.
Malformed input fails closed with a finite diagnostic, without printing payloads.

Observed bindings include job/case/revision, material digest, current run/fence,
attempt history corresponding to the stored result, result version and digest,
causal trace hash/count, C2 source bytes/version/snapshot, manifest/object identity,
PDF hash/size/page count, writer metadata, public multi-context projection, and
stored writer inputs with template/map/font hashes. The collector reopens PDF
bytes; it does not independently establish correct visual placement, absence of
private values, OCR correctness, or the truth of document contents. Stored
configuration and writer evidence are observations, not human approval grants.
It does not reauthorize a download or evaluate a publication lease.

Git HEAD, a digest of tracked and nonignored working files, dirty status, collector
hash, and start/end collection times bind the observation to its collector code.
A change to that Git/working snapshot during collection rejects the report. These
values do not prove which code produced earlier runtime records. Runtime source
HEAD is explicitly unattested. Configuration-file aggregation is SHA-256 over
sorted binary SHA-256 digests joined by a newline; per-run writer configuration
and request hashes use sorted compact JSON. Database hashes describe serialized
in-memory snapshots, not necessarily the on-disk SQLite file byte sequence.

## Optional existing HTTP/browser capture files

Supply `--capture-index` only for files already captured by the real client.
The index has `schema_version: local-integration-captures-v1` and a `records` list.
Every record requires `kind`, `body_file`, `body_sha256`, `job_id`, `case_id`,
`run_id`, `revision_id`, `attempt_id`, and integer Unix-seconds `observed_at`.
Paths are relative to the index's directory and remain confined to the checkout.
Bindings must match the collector's current job projection: UUIDs are canonical;
non-UUID internal labels are represented as `sha256:` plus their UTF-8 SHA-256.

Supported kinds:

| Kind | Additional fields | Independent comparison |
| --- | --- | --- |
| `publication_pdf` | `artifact_id` | Same job's committed artifact and reopened bytes |
| `source_pdf` | `source_sha256` | Same job's observed C2 source bytes |
| `result_json` | None | Exact authoritative ServiceResult digest |
| `restore_json` | `artifact_id`, `restored_pdf_file` | Response's `manifest` parsed as FinalLocalManifest; case/run/revision/input artifact hash and reopened final PDF hash/size |
| `browser_trace` | None | File hash only; contents are not interpreted |

Optional `source_head` and `configuration_sha256` are compared to collection-time
values, but are not authenticated runtime attestations. Optional `http_method`
and `http_status` must appear together; only finite method names and integer
100–599 status values are emitted as **declared** metadata. No headers, URLs,
principals, session tokens, map IDs, local document IDs, paths, or extracted text
are copied into the report. Capture producer and timestamps remain unattested.
Even a matching PDF does not establish that an authenticated HTTP request occurred.
No capture index yields `captures.status: not_observed`.

Restore observations count restored and omitted fields and bind the final bytes
to the current publication. They do not establish that restored original values
are correct. A stale or foreign-job artifact, changed source/result/manifest,
incorrect capture hash, or mismatched restore is rejected rather than counted as
successful evidence. Reports and raw captures remain private until independently
reviewed; this collector neither signs evidence nor declares whole-system readiness.

## Reusable regression checks

`tests/integration/test_local_integration_evidence.py` generates the existing
seven-case workbench once per module, using its bundled synthetic CJK assets.
It needs no saved rehearsal, ignored input fixture, listener, or cloud service.
Each test gets an independent in-memory SQLite snapshot, and verifies that the
shared generated database files remain byte-identical. Its ten checks cover
source/result/publication binding, absent captures, duplicate JSON, read-only
access, object/manifest tampering, substituted source snapshots, unattested PDF
files, foreign-job capture binding, capture digests, and path confinement.

```sh
mkdir -p artifacts
PYTHONPATH="$PWD/src" python -m pytest \
  tests/integration/test_local_integration_evidence.py \
  --basetemp=artifacts/collector-tests
```

The PDF capture input in these tests is explicitly an unattested copy of real
fixture publication bytes. A passing test does not create an HTTP receipt or a
live acceptance report.
