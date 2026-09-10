# Local integration validation record

This record separates actual local execution, regression doubles, packaging and
hosted/cloud acceptance. [Project progress](project-progress.md) remains the
current status authority. Original repair heads and discussion links are in
[the repair record](integration-repair-delivery.md).

## Source boundaries

The complete integration code checkpoint is
`30ae99ba8b80bc10c80bd55d12aea99ea9a83971`. Later evidence-only documentation or
browser readiness assertions must be identified separately. The original three
protected verification/audit/test files and `scripts/check_submission.py` match
`origin/main` byte for byte in this integration; the original user workspace is
untouched.

The final independent observer report has SHA-256
`5bc89c0ea27bcdc0f37c44b4a1382ba64bdca60bc57bf1e79f74c39312d75dc6`.
It confirms five exact revisions with confirmation counts 0 through 4 and all
four stored side confidences remaining 0.0. Original source hashes before and
after the final browser execution are unchanged. Raw reports stay private.

The earlier clean `94abaaf3cb91def5c44b6a7537ba40943b1b418f` checkpoint passed
2778 Python/cloud tests with 10 platform skips and 88% coverage. It is historical
baseline evidence, not a substitute for the final code checkpoint.

On a fresh isolated checkout of that exact code checkpoint, Ruff passed, format
checked 477 files, mypy checked 190 source files, and the combined Python/cloud
suite passed **2789 tests**, with **10 existing platform skips** and **88% combined
coverage with branch measurement enabled** (654.25 seconds). Dependency warnings remain in the complete log:
13932 warnings, mainly the repeated Starlette/httpx/AnyIO deprecations and
PyMuPDF binding warnings; no warnings or assertions were suppressed.

The clean frontend install, regeneration with no diff, type/lint/format checks,
81 tests, production build and artifact check passed. The subsequent test-only
commit `f41f7447ab065a49cdcc16fb1e09fcd0811a0246` adds browser readiness checks
and private failure capture; its type/lint/format checks passed and the final real
browser executions used it. Production frontend and Python code are unchanged. The clean npm install also
reported seven dependency audit findings (five moderate, one high and one critical).
Those install-time findings were not suppressed or treated as a passed security
audit; dependency security remains separate from successful frontend checks.

Golden generation (14 fixtures), mounted OpenAPI equality, HTTP/invocation smoke,
configured local reference smoke, the six CloudFormation templates and the
non-network rehearsal plan passed. Complete clean logs and dependency inventories
are under the repository's ignored
`artifacts/verification/final-delta/final-gates-30ae99ba8b80/` and
`frontend-gates-30ae99ba8b80/` directories. Exact commands include:

```sh
ruff check .
ruff format --check .
mypy src
python -m pytest tests cloud_tests --cov=appraisal_review --cov-config=pyproject.toml --cov-report=term-missing
python scripts/generate_goldens.py
python scripts/export_openapi.py
python scripts/http_smoke.py
python scripts/local_service_smoke.py
cfn-lint cloud_tests/image-stack.json cloud_tests/runtime-stack.json infra/documents/stack.json infra/runtime/image-stack.yaml infra/runtime/runtime-stack.yaml infra/rehearsal/stack.json
cd web
npm ci
npm run generate
npm run verify
```

The final real core Chromium run passed seven scenarios in 11.2 seconds, with
zero route mocks or retries. It covers legitimate empty jobs, located source PDF,
confirmation, correction with a unit, rejection, conflicting writes and a body
lost after real commit followed by the identical command/key. The separate full
privacy scenario failed at real restoration, as detailed below. Hosted CI remains
separate: none of these local checks substitutes for a pushed-head workflow run.

## Installed package and Linux image

The new noneditable wheel and ARM64 image were built from
`319e945d4319fa775a5efcff797ffd5ba9efc589`. All 214 declared build-input files
were compared with the final code checkpoint and are identical; the later poll
repair changes the repository launcher, its test and documentation only. This
comparison does not assert that unrelated repository files are identical.

| Artifact | SHA-256 |
| --- | --- |
| Noneditable wheel | `091914195a1a4ec221abc2f3253dbf28c73e4e8549a0a21dd4cc70a78102292e` |
| Linux image ID | `7022a01ee4c66b94973d62846e4a1a9392aa4695f32138439a4dc0b083e93125` |
| Actual published PDF, in both environments | `0527c33ec242a78e042c20db527d79fe2b4f81e75eb4163b34f6f32e66546862` |
| Bundled CJK font | `ee38813ea00c3e32add4268fff7fff9e39417b4913cb13be2415164a47807cc2` |

Both environments executed the configured local factory through actual HTTP:
`execution_status=succeeded`, `business_status=completed`, two contexts, eight
fields and `artifact-manifest-v2`. They performed four explicit confirmations,
receipt replay, restart to the same artifact, unauthorized download rejection,
actual PDF reopen/download hashing, unchanged source hashes and raw confidence
zero. The installed wheel imports from its clean `site-packages`; the image runs
as the non-login `appraisal` identity, UID 10001, with runtime networking disabled.
Unconfigured Runtime ping/invocation still returns 503. These observations do not
establish a deployed AWS composition or production login.

The first network-disabled image build failed before producing an image. The
same source and dependency locks built with the established build network mode;
no lock or assertion was relaxed. Both build logs remain under ignored
`artifacts/final-package-validation-3pu3_hs6/`, together with exact commands,
provenance, package inventory and acceptance observations.

## Image security remains a deployment gate

Actual Trivy 0.74.0 scanning of that new image exited 1: 174 findings comprising
3 Critical, 51 High, 57 Medium, 57 Low and 6 Unknown. The cached database is dated
2026-09-10 13:49:36 UTC. The scan used no network, ignore file or severity filter;
its finding keys are unchanged from the earlier image. Empty FixedVersion fields
in that database do not mean that upstream fixes do not exist.

The official Debian tracker still marks the installed trixie `perl-base
5.40.1-6` vulnerable for [CVE-2026-13221](https://security-tracker.debian.org/tracker/CVE-2026-13221),
[CVE-2026-42496](https://security-tracker.debian.org/tracker/CVE-2026-42496) and
[CVE-2026-8376](https://security-tracker.debian.org/tracker/CVE-2026-8376).
Fixed versions exist in other suites. Those are not automatically compatible
replacement pins for this stable image. No distribution-suite migration or
severity waiver was applied. Package findings do not by themselves establish
exploitability; the scanner limitations and all findings remain recorded.

## Browser and privacy evidence

The final complete privacy browser attempt used the actual production frontend
and real loopback API/bridge. Both source payloads were previewed, explicitly
confirmed and transferred once. Four distinct current sides were confirmed
through the normal response form, preserving confidence zero. The service
completed two contexts and published a two-page PDF. The actual browser download
was retained and hash-checked:
`79962dae2dfe6c523235749eab1d4f4d9f249392ab5ec5935a79a915f3c3b5be`.

The same run then called the real local restoration endpoint, which returned
HTTP 409. Its recorded first-page Tesseract output contains 50 observations,
including four below 0.85; the minimum is 0.23203516000000002. The gate stopped
before a final restored PDF existed. This is a failed complete privacy scenario,
not an accepted backfill. No further page accuracy or restored-value correctness
is inferred. The positive browser assertion remains enabled and failed.

Earlier real attempts remain separate: native iframe loading blocked approval
(fixed with all-page PDF.js), registration polling crashed before exposing the
job (fixed in `30ae99ba8b80bc10c80bd55d12aea99ea9a83971`), and a later fourth
confirmation received 409 after three successful responses. That last response's
body and instantaneous job state were not retained, so its exact cause is not
claimed. An independent controlled real Runtime/SQLite/ASGI-route probe proves
that tasks may be listed while the worker is still RUNNING: a response is then
correctly rejected without a receipt, and the identical command/key succeeds
after the actual WAITING_FOR_HUMAN transition. The final browser test adds current
job readiness, run/revision and open-task membership assertions before submission;
it does not weaken the response, immutable-command or confidence assertions.

Browser confirmations are explicit automated operations on synthetic material,
not an assertion that a real operator approved real cases. Earlier clean core
browser evidence passed seven scenarios; the final core rerun is recorded with
the final code gates above. Private logs/captures and same-request OCR/plan evidence
remain in ignored `artifacts/` namespaces, including the unsuccessful attempts.

The synthetic fixture's initial placeholder OCR is an explicitly authored
adapter, not measured OCR accuracy. Candidate facts and human corrections retain
raw confidence zero. Real isolated PDF rasterization/redaction and actual browser
preview run, but passing an authored OCR seam does not prove arbitrary sensitive
text was removed. Actual restoration uses the configured Tesseract engine with
its unchanged raw observations and the existing 0.85 gate.

String scans of admitted local C2 objects, metadata/audit records, native PDF text
and browser metadata logs are scoped to the privately derived synthetic canaries.
They do not prove raster visual absence, capture unobserved request bodies, inspect
all map/key traffic, or establish real cloud network behavior. Originals, maps,
keys, generated PDFs, raw OCR and private diagnostic files are not committed.

The observation collector reports `observations_only` and `live_acceptance=false`.
A report binds observed files/state but does not authenticate capture producers,
prove full raw-trace coverage or grant business authority. Real AWS, designated
model evaluation, formal assets and independent human approval remain open.
