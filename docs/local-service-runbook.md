# Configured local service runbook

Status: runnable on the M0 working branch; not a deployed, multi-user or durable
service. Use Python 3.11+ and Linux/macOS for the existing reviewer store.
Run all commands from the chosen checkout with its own environment.

## Install and select this checkout

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
export PYTHONPATH="$PWD/src"
```

The narrower runtime dependency set is `.[documents,pdf]`; dev includes those
libraries plus test/typing tools. Nothing installs a system font or creates an AWS
client on import. Optional live extraction additionally needs the existing aws
extra and explicitly verified profile/model configuration; it is not used below.

## Fully repeatable synthetic acceptance

```bash
python scripts/local_service_smoke.py
```

The script creates disposable synthetic criteria/forms/template PDFs, parses real
bytes, binds prepared material to the actual parser registry, explicitly confirms
both sides and signs only that generated test material in a fresh private store.
Both observation and evidence confidence stay 0. It starts loopback HTTP children,
checks health, legacy validation, review 422, missing-config 503, normal review,
invocation parity, real writer output, reopen verification and a blocked review.
It also sends an unauthorized synthetic source through HTTP and both actual CLI
commands: legacy verification agrees, and the service envelope retains a sanitized
source_binding diagnostic with succeeded/failed and no artifacts or execution problem.
It verifies unchanged source hashes and absent jobs/task/artifact routes, then
stops all children and removes temporary data. Unit/integration tests additionally
count zero writer calls for needs_review, reject stale/substituted artifacts and
validate sanitized execution 500.

To retain inspectable evidence, choose a directory that does not yet exist:

```bash
python scripts/local_service_smoke.py --directory artifacts/service-acceptance
```

The retained files have distinct roles:

| File | Format / producer | Relationship |
| --- | --- | --- |
| output/completed.pdf | Real PDF from POST /v1/reviews | First write; legacy response is saved in http-written.json (case identity, no service run UUID) |
| output/manifest.pdf | Second real PDF from the actual local_service run CLI | Separate destination for exercising the service envelope; not JSON or a rendered manifest |
| result-written.json | ServiceResult JSON from that CLI's stdout | Machine-readable manifest at artifacts[0]; its enclosing run binds case/revision/material digest/run UUID |
| smoke-report.json | Local smoke summary JSON | Maps both paths to producer/response/hash/observed byte size, plus the second write's run and artifact IDs |
| result-retry-failed.json | ServiceResult JSON from a same-destination retry | New run, failed execution, no manifest; prior successful PDF remains untouched |
| result-source-binding-failed.json | ServiceResult JSON from an unauthorized synthetic source | Successful execution, failed business verification, source_binding diagnostic, no case findings or artifact |

`request-manifest.json` is the second write's reproducible request. The two PDFs
may have identical hashes because rendering is deterministic; this does not make
them the same run/artifact. Do not detach artifacts[0] from its enclosing run when
consuming the service result. The manifest's declared hashes/page count/field IDs
are checked against the current write; byte size is measured only in the smoke
report, not declared in ArtifactManifest v1. Both reopened PDFs contain `+5.00%`
in field `road-rate` in one synthetic comparison context. The script also proves
that a blocked HTTP request and failed CLI retry cannot appropriate an existing
success, and records the actual CLI exit codes and clean JSON channels.
Original source files remain unchanged. This uses ReportLab's explicit Vera test
font, not an approved CJK font/template or a live model. Output metadata contains
no tool attribution. Source PDFs, receipt/key and all generated output stay ignored.

## Start HTTP and call both boundaries

```bash
python scripts/local_service_fixture.py --directory artifacts/local-service
export APPRAISAL_LOCAL_CONFIG="$PWD/artifacts/local-service/config.json"
uvicorn appraisal_review.local_service:app_from_environment --factory --host 127.0.0.1 --port 8000
```

In another terminal, activate the same environment and PYTHONPATH:

```bash
curl -fsS http://127.0.0.1:8000/health
curl -sS http://127.0.0.1:8000/v1/reviews -H 'Content-Type: application/json' --data '{}'
curl -fsS http://127.0.0.1:8000/v1/reviews -H 'Content-Type: application/json' --data-binary @artifacts/local-service/request.json
python -m appraisal_review.local_service invoke --config artifacts/local-service/config.json --request artifacts/local-service/request.json
python -m appraisal_review.local_service run --config artifacts/local-service/config.json --request artifacts/local-service/request-write.json
python -m appraisal_review.local_service run --config artifacts/local-service/config-needs-review.json --request artifacts/local-service/request-write.json
python -m appraisal_review.local_service run --config artifacts/local-service/config-no-writer.json --request artifacts/local-service/request-write.json
```

Expected: validation 422; HTTP/invoke verified/not_requested; run completed/written
with a real `output/completed.pdf`; needs_review with findings and no write; then
verified/unavailable without a writer. The latter two commands do not overwrite
the already created successful output. For an HTTP write demonstration, use a
fresh output destination in request-write.json, then send it to the same route.
Do not retry a successful write to the same path; existing destinations are rejected
by default. A stale or failed request never proves that an existing output belongs
to that run. Stop uvicorn with Ctrl-C when finished.

The invoke command retains legacy AgentReviewRun/EntryProblem JSON. `run` returns
the independent service-v1 ServiceResult. CLI stdout is one JSON document; parser
package advertising is disabled at CLI startup, and parser messages use stderr.
The command exits describe transport/serialization, not business approval:

| Situation | stdout | stderr / exit |
| --- | --- | --- |
| invoke: valid request/result | Legacy AgentReviewRun | Empty in synthetic acceptance; exit 0 |
| invoke: invalid request shape, missing configuration, controller exception | Legacy EntryProblem error envelope | Empty; exit 0 (existing adapter behavior) |
| run: valid request, including needs_review or caught execution/PDF failure | ServiceResult; inspect execution_status, business_status, verification, artifact_status and problem | Empty in synthetic acceptance; exit 0 |
| run: invalid request shape or configuration cannot load | Empty | Sanitized invalid_local_request JSON; exit 2 |
| Either command: unreadable request file or malformed JSON text | Empty | Sanitized invalid_local_request JSON; exit 2 |

Third-party diagnostic messages may use stderr; they are never part of JSON stdout.
A repeat write without overwrite permission is a run failure envelope with exit 0,
not evidence that the existing PDF belongs to this invocation. Business needs_review
is a valid result, not an OS error. These rules preserve the legacy invocation
adapter; no HTTP status or public response schema is changed.

For actual HTTP needs_review, restart with
`APPRAISAL_LOCAL_CONFIG="$PWD/artifacts/local-service/config-needs-review.json"`.
With no APPRAISAL_LOCAL_CONFIG, health and legacy validation still work, invalid
review JSON is 422 before configuration I/O, and valid review requests get 503.
Unknown allowlisted sources preserve legacy 200/business failed with sanitized
source_binding verification; an unexpected controller exception gets EntryProblem
500. This distinction is unchanged. The separate service run facade also retains
verification when case_review is absent: check verification.critical_errors and
verification.warnings even if findings is empty and problem is null. Specific
source_binding/source_registry_required codes guide correction; generic codes
request human review without exposing internal paths. The regenerated service-v1
schema and fixtures must be adopted together by B/C/D consumers. No jobs/HITL/download
routes are mounted.

## Configure your own prepared documents (no automatic approval)

Follow [document preparation](member-a-runbook.md#real-document-preparation-and-review-7)
for parse/native-candidates/extract/assemble and the existing inspect,
confirm-facts and approve operations. The operator must inspect exact source,
material digest and required facts/rules; M0 does not perform or authorize these
operations for real cases. Model approval flags and submitted reviewer names are
not credentials. An original receipt is valid only for unchanged original material;
a new revision cannot reuse confirmations or approvals that fail exact binding.
Revising a side cannot restore native authority by changing its method label.
Only unchanged native lineage retains that eligibility; other revised sides need
explicit confirmation and the new exact material needs separate approval. The
trusted extraction/assembly flow is the entry for actual re-extraction. Raw
observation and evidence scores are retained through confirmation.
Actual local reviewer identity comes from the OS and private
store; Windows native reviewer support and web login are not implemented.

Build an operator-owned local-service-v1 configuration using
`adapters.local.service.LocalServiceConfiguration`. The schema and validated
placeholder example are in `schemas/local-service-v1.json` and
`examples/local-service-v1.json`. Placeholder hashes/paths cannot execute a review;
replace them only with already inspected, allowlisted source/material identities.
The working synthetic generator also produces a complete config you can inspect.

| Configuration | Required meaning |
| --- | --- |
| schema_version | local-service-v1 |
| inputs.identity | Existing exact CaseIdentity, matching both material identities |
| inputs.documents | Existing InputManifest allowlist: absolute path, unique ID, version, role, exact expected_hash and optional document_date |
| material_path | Absolute path to prepared ReviewMaterial, never an HTTP body material |
| expected_material_digest | Existing content_digest of the exact inspected material |
| revision_id | Stable operator-controlled revision ID; change on material revision |
| approval_store | Absolute path to the existing private local store, or null (unapproved material cannot pass) |
| minimum_confidence | Existing finite threshold, default 0.85; never raise source confidence to meet it |
| writer | null for review-only operation; otherwise all trusted writer configuration below |

The input manifest identity and each URI/version/hash/role must exactly match the
material registry. Every registered source is allowlisted. Parser calls recheck
source bytes and Controller validates source binding/purpose. There is no model
extraction triggered by HTTP: prepare material beforehand through the explicit CLI.
The configuration is loaded for each HTTP request; operator changes take effect on
the next request, while the current request keeps its detached material. This is
not an atomic immutable source snapshot; store inputs in a controlled local area.

For output, configure `writer.template_path`, `output_directory`, `field_map`,
`template_policy` and `render.font_path` as absolute operator-owned paths/settings.
Use the complete existing PDFFieldMap and PDFTemplatePolicy, including
`template_sha256`, `field_map_sha256`, editable_pages and reference_only_pages.
Compute the full map hash with existing `field_map_sha256`, not a subset hash.
Use a reviewed redistributable font with the required glyphs and existing
PDFRenderConfig options; do not choose a system fallback. Request fields must use
the same template URI/map and an output path inside the configured directory.
The existing writer additionally rejects sources/aliases and protects reviewed
inputs. Empty or contradictory writer settings fail; missing fonts/glyphs fail at
write preflight. Omitting the writer requires no font at all.

Use `AgentReviewRequest` with case_id, criteria_document_uri, case_document_uri;
add pdf_template_uri, field_map and output_pdf_uri only for a write request.
These explicit local URIs are for the loopback operator workflow. Future public
services must use authorized DocumentReference IDs through D's resolver.

## Regression and publication gates

```bash
ruff check .
ruff format --check .
mypy src
pytest --cov=appraisal_review --cov-config=pyproject.toml --cov-report=term-missing
python -m pytest cloud_tests
python scripts/http_smoke.py
python scripts/local_service_smoke.py
python -m pip install -r cloud_tests/requirements-dev.txt
cfn-lint cloud_tests/image-stack.json cloud_tests/runtime-stack.json
python scripts/check_submission.py --base origin/main
```

See [service contracts](service-contracts.md), [PDF contract](pdf-contract.md),
[ADR 0013](adr/0013-service-foundation.md) and [traceability](delivery-traceability.md) and [M0 review](m0-review.md).
No cloud invocation, resource creation, real rule approval or production snapshot
claim is needed for these local checks. Remote publication and commit permissions
remain separate under AGENTS.md; keep drafts in ignored artifacts until approved.
