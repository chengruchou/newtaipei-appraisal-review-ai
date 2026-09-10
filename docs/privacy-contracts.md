# Privacy v1 contract inventory

Current acceptance, requirement coverage and external dependency status are in
the [Phase 8 closeout](issue-22-acceptance.md); dated stage results below are historical.

Status: Phase 1 contracts and pure guards, Phase 2 local scan adapters and
Phase 3 local review service with ephemeral human approval authority, and
Phase 4 raster rebuilding with independent structure/pixel/OCR verification.
Phase 5 adds [local encrypted mapping](local-privacy-mapping.md); production key
provisioning and Linux filesystem acceptance remain pending.
Phase 6 adds [local refill](local-privacy-refill.md), with explicit output fields
and publisher/approval ports; production publisher integration remains pending.
Phase 7 adds the [owned export gate and offline regression](local-privacy-export.md).
Production export confirmation and Linux isolation acceptance remain pending.
Linux is the runtime acceptance baseline; native Windows is a development host.
The [local sanitizer](local-privacy-sanitization.md) is implemented; actual OCR
acceptance remains pending. No export endpoint or uploader is implemented. See the
[#25 SDK handoff](local-privacy-review.md); actual Linux interactive acceptance
remains pending.
[ADR 0014](adr/0014-local-privacy-boundary.md) defines the boundary and lifecycle.
The separate local scan schema and execution limits are documented in
[ADR 0015](adr/0015-local-privacy-scan-isolation.md) and the
[local runbook](local-privacy-runbook.md).

## Contract ownership

| Surface | Visibility | Responsibility and limitation |
| --- | --- | --- |
| LocalSourceSnapshot / LocalSourcePage | Local | Owned original byte identity and page transform; reader port must bind actual bytes and local file provenance |
| SensitiveCandidate / ReviewSelection / PrivacyReviewCommand | Local | Evidence and human review input; no caller-controlled authority/state |
| LocalPrivacyApproval | Local | Trusted authority output; matching structure is not issuance |
| PrivacyManifest / PrivacyProblem | Public | Minimal sanitized projection and fixed errors; not an export grant |
| SanitizedBundle | Public contents | Immutable PDF bytes and manifest only; a constructed carrier or processor draft is not verification authority |
| EncryptedMappingEnvelope / LocalMappingRecord / LocalMappingHandle | Local | Phase 5 authenticated encryption and immutable storage; structural validity alone is not authority or proof of encryption |
| RehydrationPlan / PublishedRefillDescriptor | Local | Phase 6 validates exact approved fields and authenticated current artifact; production publisher adapter pending |
| FinalLocalManifest | Local | Exact final bytes and restored/omitted fields; no upload grant or business completion authority |
| LocalTextDraft | Local | Known-value replacements; always needs human review, never an export grant |
| PrivacyExportPayload | Gate handoff only | Exact confirmed bytes, public manifest, fixed filename and optional reviewed text; carrier construction is not authority |
| LeakScanReport | Value-free test evidence | Fixed canary IDs/counts and declared scope; missing surfaces block a passing report |
| PrivacyState | Local lifecycle vocabulary | Phase 3 implements blocked/awaiting_confirmation/confirmed; processing states remain reserved |

Strict Python validation accepts native tuples, UUID objects and enum instances;
JSON consumers use `model_validate_json`, which accepts the corresponding JSON
arrays, UUID strings and enum strings. Unknown fields/versions, coercible integer
strings/booleans, nonfinite geometry and unresolved regions fail. Frozen records
are revalidated at trust boundaries, including `model_copy` misuse.

The public serializer only accepts exactly `PrivacyManifest`. Do not serialize
local objects to a public channel, return raw Pydantic errors from local inputs,
or treat a valid manifest's ID/hash syntax as proof of provenance. Fixed processor
and policy version literals describe this proposal, not an installed processor.
Production code must issue random IDs independently of original values.

## Regeneration and tests

From a repository-local environment with existing dev dependencies:

```bash
python scripts/export_privacy_contracts.py
python -m pytest tests/unit/test_privacy_contracts.py --basetemp=artifacts/privacy-tests
ruff check .
ruff format --check .
mypy src
pytest --cov=appraisal_review --cov-config=pyproject.toml --cov-report=term-missing
```

Set temporary and cache directories under ignored repository paths when running
locally. The schema regression test regenerates into pytest's temporary directory
and compares all checked-in files. `schemas/privacy-v1.json` includes public
definitions only; `schemas/local-privacy-v1.json` must not be used as an upload
schema. Select a definition using `#/$defs/ModelName`.

`examples/privacy-v1/index.json` maps fixtures to types. The `local/` examples
contain synthetic evidence, deliberately invented approval records and dummy
ciphertext. Do not pass these to production authority or decryption code.
The `public/` examples contain no original values or source hashes. Fixture
UUIDs are fixed, version-4-shaped values for repeatability, not random issuance.

## Consumer handoffs

- #25: the [local review SDK](local-privacy-review.md) issues ephemeral approval
  through a trusted human port and rejects caller-authored authority fields;
  no browser or local HTTP session protocol exists yet.
- #27: consume only the owned payload from the Phase 7 local export gate.
  Manifest JSON alone never authorizes upload of caller-selected bytes/paths.
- #26: agree output occurrence/field IDs, supported kinds, font and geometry;
  do not reuse original coordinates for a reformatted output.
- #28: authenticate artifact case/run/revision and writer/template identity;
  a supplied digest does not establish publisher provenance. Final refill stays
  local and cannot be sent back through publication.
- #21/#24/#31: preserve tokens, sanitize manual free text locally and verify
  actual egress paths later. Contract tests are not live integration evidence.
  #24/#25 share `sanitize_reviewer_text`; exact-output human confirmation is
  required. Python denial and synthetic SDK probes do not establish AWS behavior.

## Phase 0 carry-forward

Baseline HEAD is `8591bddc76584ad630774f214c7452c397c937d5`. The initial Windows
run had 572 passes, 40 failures and 43 setup errors. Named-pipe restrictions,
POSIX reviewer APIs and POSIX fixture paths explain observed failure groups;
native mypy also reported three POSIX attribute errors. This does not establish
Linux test results. The detailed original inventory is a local ignored artifact
under `artifacts/issue-22-p0/` and is not a required repository dependency.

Missing local OCR/model/font assets and secure key provisioning remain runtime
acceptance work, independent of the Windows compromise. No existing tests or
platform gates are skipped, weakened or converted to mocks for this feature.

## Phase 1 validation, 2026-09-10

Working branch: `feat/local-privacy-pipeline`, created from freshly fetched
`origin/main` at the baseline SHA above. The supplied untracked instruction plan
was preserved. No commit or remote mutation was performed.

| Check | Observed result |
| --- | --- |
| `pytest tests/unit/test_privacy_contracts.py` with repository-local basetemp/cache | 69 passed; real Pydantic/schema/guard execution, mocked authority/verifier ports |
| `ruff check .` | Passed |
| `ruff format --check .` | Passed |
| `mypy src --platform linux` | Passed; 77 source files; cross-target type analysis, not Linux execution |
| `mypy src` on Windows | Same three POSIX API errors in existing approval adapter |
| Full pytest with coverage and repository-local basetemp/cache | 641 passed, 40 failed, 43 errors, 2 warnings; 79% aggregate coverage |
| Full-suite failure-node comparison with Phase 0 | Identical 83 failed/error node IDs; no additions or removals |
| `pytest cloud_tests` with repository-local basetemp/cache | 8 passed; local/mocked tests, no live AWS |
| `scripts/check_submission.py --base origin/main` | Passed; no outgoing commits |
| cfn-lint | Not run; dependency remains unavailable |
| Actual Linux execution | Not performed; no usable local Linux runtime established |

Full logs and temporary synthetic data remain ignored under
`artifacts/issue-22-p1/`. No originals were used as test fixtures or uploaded.
No network-denial harness, real OCR, redaction, encryption, refill, browser or
consumer deployment acceptance is claimed. No dependencies, system tools,
drivers or key stores were installed or changed.
