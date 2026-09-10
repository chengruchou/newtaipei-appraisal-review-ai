# Local integration validation record

This record separates actual local execution, regression doubles, packaging and
hosted/cloud acceptance. [Project progress](project-progress.md) remains the
current status authority. Original repair heads and discussion links are in
[the repair record](integration-repair-delivery.md).

## Source boundaries

The complete integration code checkpoint is
`71419453e36a6111dae267699a087051c934867d`. Later launcher and browser
diagnostic changes have focused evidence below; they are not silently included
in the complete-suite or actual-browser checkpoint. The original three
protected verification/audit/test files and `scripts/check_submission.py` match
`origin/main` byte for byte in this integration; the original user workspace is
untouched.

The final independent observer report has SHA-256
`5d540e5fef4c3aabc921a89329d858594772e62bff1839cc7fca2096d9ca2cf9`.
It confirms five exact revisions with confirmation counts 0 through 4 and all
four stored side confidences remaining 0.0. Original source hashes before and
after the final browser execution are unchanged. Raw reports stay private.

The earlier clean `94abaaf3cb91def5c44b6a7537ba40943b1b418f` checkpoint passed
2778 Python/cloud tests with 10 platform skips and 88% coverage. It is historical
baseline evidence, not a substitute for the final code checkpoint.

The previous `30ae99ba8b80bc10c80bd55d12aea99ea9a83971` clean checkpoint
passed 2789 tests with 10 platform skips, but emitted 13925 SQLite connection
ResourceWarnings plus seven dependency deprecations. These were not merely
dependency deprecations. The resulting fix explicitly closes each local document
connection after commit or rollback. Eight real-connection regressions fail on
the archived code and pass on the fix, including rollback after an actual INSERT
and release of its write lock. No warning filter or assertion was weakened.

The new clean detached checkout of exact `71419453e36a6111dae267699a087051c934867d`
passed **2797 Python/cloud tests**, with **10 existing platform skips**, in 646.60
seconds. Coverage is **87.9193% combined (displayed 88%)**, with branch measurement
enabled; pure branch coverage is 77.076%. Ruff passed, format checked 479 files,
and mypy checked 190 source files. All complete-suite and accompanying gate exit
codes are zero, and the validation checkout has no tracked changes.

The final log still records **191 warnings: 184 unclosed SQLite connection
ResourceWarnings and seven dependency deprecations**. They are retained without
suppression. The lifecycle regression proves the repaired document-store methods
close their own connections; it does not establish that every test/helper
connection is closed. Remaining allocation sites are not inferred solely from
the warning emission stack, which may be triggered during later garbage collection.

The clean frontend install at `30ae99ba8b80bc10c80bd55d12aea99ea9a83971`,
regeneration with no diff, type/lint/format checks, 81 tests, production build and
artifact check passed. Production frontend sources and lock have not changed
since that gate. The real resource-final browser run used the separate test
snapshot SHA-256 `794f3648c11686d5321f3534c5035195560d8edf71abb4941b8e2121f2d948fd`:
current job/run/revision readiness assertions and private failure diagnostics.
Later bounded diagnostic changes have their own focused evidence below.

The clean npm install reported seven dependency audit findings: five moderate,
one high in Vite and one critical in Vitest. They were not suppressed or treated
as a passed security audit. Dependency security remains a separate gate.

Golden generation (14 fixtures), mounted OpenAPI equality, HTTP/invocation smoke,
configured local reference smoke, the six CloudFormation templates and the
non-network rehearsal plan passed. Complete clean logs and dependency inventories
are under the repository's ignored
`artifacts/verification/final-delta/final-gates-71419453e36a/` and
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

The final real core Chromium run passed seven scenarios in 14.4 seconds, with
zero route mocks or retries. It covers legitimate empty jobs, located source PDF,
confirmation, correction with a unit, rejection, conflicting writes and a body
lost after real commit followed by the identical command/key. The separate full
privacy scenario failed at real restoration, as detailed below. Hosted CI remains
separate: none of these local checks substitutes for a pushed-head workflow run.

## Installed package and Linux image

The new noneditable wheel and ARM64 image were built from the exact
`71419453e36a6111dae267699a087051c934867d` checkpoint. All 214 declared build
inputs, 196 image-context files and 190 package files in the wheel ZIP, installed
wheel and running image were compared with that archive. The document-storage
connection fix is included. Later launcher/test/docs changes are outside these
inputs; no unrelated repository equivalence is inferred.

| Artifact | SHA-256 |
| --- | --- |
| Noneditable wheel | `b483e1f3014f0c3a643a069efe20d8f2f2ab3216f79833b29d729f3ff9421837` |
| Linux image ID | `7fa5e3f17007d0690ef7750d224e8761311a5041dba33542ba66c828f5ffbfa4` |
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

This new image built successfully on its first attempt with unchanged base and
locks. Full build/smoke/provenance logs remain under ignored
`artifacts/resource-final-validation-1xhnttvt/`. The older `319e945` build's
network-disabled failure and subsequent success remain historical in
`artifacts/final-package-validation-3pu3_hs6/`; they are not this build's results.

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
`dd399f25dbeceffd0682659e7e6fa5ef704db09a789f52b10c68b05159998621`.

The same run then called the real local restoration endpoint, which returned
HTTP 409. The test's diagnostic response-body read did not complete before its
original 240-second timeout. No error body, hash or error code was retained;
that timeout is distinct from the observed HTTP status. There is no final
restored PDF. This is a failed complete privacy scenario, not an accepted backfill.
The positive success assertion remains enabled.

Independent rendering of the exact published PDF at 144 DPI matches the recorded
first-page OCR input bytes. Actual Tesseract output contains 50 observations,
four below 0.85, minimum 0.41766273. Page two has no OCR record. This proves page
content equality, not a unique request identity or the exact cause of HTTP 409.
No further page accuracy or restored-value correctness is inferred.

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
the final code gates above. Private logs/captures and page-content-linked OCR/plan evidence
remain in ignored `artifacts/` namespaces, including the unsuccessful attempts.

The synthetic fixture's initial placeholder OCR is an explicitly authored
adapter, not measured OCR accuracy. Candidate facts and human corrections retain
raw confidence zero. Real isolated PDF rasterization/redaction and actual browser
preview run, but passing an authored OCR seam does not prove arbitrary sensitive
text was removed. Actual restoration uses the configured Tesseract engine with
its unchanged raw observations and the existing 0.85 gate.

String scans of admitted local C2 objects, metadata/audit records, native PDF text
and browser metadata logs are scoped to the privately derived synthetic canaries.
The resource-final scan covers 12 C2 objects and 3367 audit events. These checks
do not prove raster visual absence, capture unobserved request bodies, inspect
all map/key traffic, or establish real cloud network behavior. Originals, maps,
keys, generated PDFs, raw OCR and private diagnostic files are not committed.

The observation collector reports `observations_only` and `live_acceptance=false`.
A report binds observed files/state but does not authenticate capture producers,
prove full raw-trace coverage or grant business authority. Real AWS, designated
model evaluation, formal assets and independent human approval remain open.

## Subsequent focused changes

Commit `94fcfb9f30dda82eeed78bd8bb42cb968e4dabc6` prevents the private launcher
poller from terminating both services when an existing publication access grant
expires or is revoked. Only `publication_unauthorized` is handled: the private
fixture omits the restore handle, exposes a bounded unavailable code and retains
job status. No authorization is extended or new approval/output/publication
created; unexpected errors still propagate. Two real revoked/expired-publication
counterexamples failed before the patch. Six launcher checks, Ruff/format and
launcher mypy passed afterward. This path is separately tested, not part of the
already completed resource-final browser run.

Commit `f8bde95406a7ad4f87806d49fe33635771e28d91` bounds the browser test's private
error-body diagnostic to two seconds. An actual isolated HTTP server and Chromium
regression covers complete and stalled 409 bodies: the former was captured in
56 ms and the latter became explicitly unavailable in 2004 ms. Both then reach
the unchanged positive HTTP assertion and fail as required. Captures have mode
0600 and no fabricated body/hash; raw bodies do not enter console logs. Type,
lint and formatting checks pass. This verifies the diagnostic only; it does not
reclassify or rerun the earlier full privacy test.

`test_controller_final_verification_blocks_real_writer_for_unconfirmed_material`
in `tests/integration/test_integrated_workflow.py` executes the configured real
Controller/writer assembly with unconfirmed material. It persists human tasks,
checks verification cannot complete, observes no write action or output file,
and checks all input bytes unchanged. It is part of the complete Python suite.
The separate controller boundary regressions directly assert zero writer calls
for candidate rules, missing/low-confidence/unknown factors and missing evidence;
their extraction/writer spies remain explicitly test doubles.
