# Local privacy scan runbook

For current cross-stage results, blocked dependencies and review readiness, see
the [Phase 8 acceptance record](issue-22-acceptance.md). The dated Phase 2 results
below are preserved as historical evidence.

Status: Phase 2 local scan implementation. Runtime acceptance targets Linux.
The subsequent human review service is covered by the [Phase 3 handoff](local-privacy-review.md).
Raster rebuilding and independent verification are covered by the
[Phase 4 runbook](local-privacy-sanitization.md).
This is a trusted local callable component and synthetic harness, not a web
endpoint, complete privacy pipeline or agent orchestration implementation.

## Implemented scope

- Confined acquisition into immutable in-memory source snapshots.
- Bounded independent PDF inspection/rendering and source-file provenance.
- Native format/label candidates and explicit per-page manual review coverage.
- A configured local Tesseract TSV adapter with asset hash checks and no download.
- `needs_review` versus `blocked` reports and a complete-only detector interface.

No approval issuer, sanitizer, encrypted mapping store, export boundary or
rehydrator is provided. Existing upload paths must not receive sensitive originals.
[ADR 0033](adr/0033-local-privacy-scan-isolation.md) documents security assumptions,
coordinate conventions, limits and the dependency on later stages.

## Environment and approved assets

Use the repository-local Python environment with existing `documents`/`dev`
dependencies. The input root and work directory must be explicit existing local
directories under operator control, with restrictive Linux ownership/permissions.
During development keep them under the ignored repository `artifacts/` directory.
The store is ephemeral; restarting loses snapshots. It is not secret persistence.

Optional `TesseractConfig` JSON uses these keys:

| Key | Required value |
| --- | --- |
| executable | Absolute path to an already approved local Tesseract executable |
| executable_sha256 | Expected digest from the approved asset inventory |
| tessdata | Absolute path to local traineddata files |
| assets | Objects with `language` (`chi_tra` and `eng`) and expected `sha256` |

Configure both languages. A binary/model file merely existing is insufficient;
preflight compares expected hashes, then checks the local executable version.
Hashes establish identity, not publisher trust: obtain them through approved
provisioning, not from an untrusted case document. Core execution never installs
packages or downloads assets. Model/engine installation and OS changes require
the separate permission described in the issue plan. No engine or models were
installed during Phase 2 development. Existing PyMuPDF licensing and any engine,
model or font redistribution obligations still apply.

## Synthetic smoke

Choose a new directory; the harness refuses to overwrite an existing run.

```bash
python scripts/privacy_smoke.py --work-dir artifacts/privacy-smoke-001
python scripts/privacy_smoke.py --work-dir artifacts/privacy-smoke-002 --ocr-config artifacts/approved-ocr-config.json
```

The first command can exercise native extraction with no OCR assets. Its scanned
and mixed cases must report `blocked`/`ocr_unavailable`, and the process exits 2.
The second command uses real local OCR if the approved assets are available.
It requires recognition of the synthetic English and Traditional Chinese canaries
on image pages, unchanged original bytes and no blocked page for exit 0. Even an
exit 0 is scan-harness evidence only; the report still requires human review and
does not authorize export. It is not a redaction or egress canary test.

Only aggregate counts, fixed issues and unchanged-byte booleans go to stdout.
Generated PDF fixtures stay in the ignored run directory. The harness saves no
OCR dumps, approval records, keys or public manifests. Original competition/case
documents are not used by this command.

## Callable interface

```python
from pathlib import Path
from uuid import uuid4

from appraisal_review.adapters.local.privacy.source import IsolatedPrivacyPDF, LocalSnapshotStore
from appraisal_review.adapters.local.privacy.scanner import LocalPrivacyScanner

root = (Path.cwd() / "artifacts" / "operator-input").resolve()
work = (Path.cwd() / "artifacts" / "operator-work").resolve()
store = LocalSnapshotStore(root, IsolatedPrivacyPDF(work))
snapshot = store.capture("selected.pdf", case_id=uuid4())
scanner = LocalPrivacyScanner(store)  # Inject configured TesseractOCR for image pages.
report = await scanner.scan(snapshot)
```

This snippet assumes the directories already exist and the trusted local
application has authorized selection. It is not an unauthenticated request
handler. The complete report is local-only; its text and hashes must never be
logged or serialized to cloud DTOs. `store.source_file(snapshot)` retains the
local capture path without including it in the scan wire model. `store.read`
returns owned immutable bytes even if that path later changes. A new source
requires a new capture and, in Phase 3, a new confirmation.

`scanner.detect(snapshot)` preserves the earlier candidate-only port: it raises
`ScanFailure` on any blocked page. Prefer `scan` for per-page coverage and fixed
issue diagnostics. `capabilities` reports local assets, not recognition accuracy
or OS network isolation. No regex match is a verified business fact.

## Contract and test commands

```bash
python scripts/export_privacy_scan_contracts.py
python -m pytest tests/unit/test_privacy_scan.py tests/unit/test_privacy_contracts.py --basetemp=artifacts/privacy-unit
ruff check .
ruff format --check .
mypy src
pytest --cov=appraisal_review --cov-config=pyproject.toml --cov-report=term-missing
```

Set `TEMP`/`TMP`, pytest caches and coverage data to ignored repository locations
for local runs. `schemas/local-privacy-scan-v1.json` and the synthetic blocked
fixture are versioned together; public privacy-v1 and service-v1 are unaffected.
Actual PDF tests and mocked OCR tests are named separately in the test file.
The Linux openat/no-follow test is skipped on non-Linux hosts and must run before
claiming Linux acquisition acceptance. Cross-target `mypy --platform linux`
does not replace Linux execution.

## Development validation, 2026-09-10

Branch `feat/local-privacy-pipeline`, unchanged HEAD
`8591bddc76584ad630774f214c7452c397c937d5`; these are uncommitted local changes.
Platform: Windows 11 AMD64 / Python 3.12.3. Linux remains the runtime target.

| Check | Result |
| --- | --- |
| Final full pytest with existing coverage configuration | Exit 1: 703 passed, 40 failed, 43 errors, 1 skipped, 2 warnings; 79% aggregate coverage |
| Phase 1/2 privacy tests within that full run | 131 passed; Linux openat/no-follow test skipped on Windows |
| Failure-node comparison against Phase 1 | Same 83 failed/error nodes; no new failed/error node |
| Ruff lint and format | Passed |
| `mypy src --platform linux` | Passed; 85 source files; type analysis only |
| Native Windows `mypy src` | Same three existing POSIX API errors in the approval adapter |
| Local/mocked cloud tests | 8 passed; no live AWS |
| Submission and branch gate | Passed; zero outgoing commits |
| Real generated-PDF smoke without OCR config | Exit 2: native `needs_review`; scanned/mixed `blocked` with `ocr_unavailable`; originals unchanged |
| Actual Tesseract recognition / Linux security acceptance | Not performed; approved engine/models and usable Linux runtime remain prerequisites |
| CloudFormation lint | Not run; tool remains unavailable from Phase 0 |

The final full run used `--basetemp=artifacts/issue-22-p2/final`,
`-o cache_dir=artifacts/issue-22-p2/final-cache`,
`--cov=appraisal_review --cov-config=pyproject.toml --cov-report=term-missing`.
Process-local temporary paths and coverage data were also placed under
`artifacts/issue-22-p2/`. Raw synthetic test logs and the exact failure comparison
remain there as ignored local artifacts. No original document text/hash or real
case data was added to repository fixtures or uploaded. Network denial across
all children has not been verified; that acceptance remains separate in Phase 7.

Phase 2 implementation and rejection paths are available for local review, but
the real scanned-PDF acceptance is still blocked. Do not infer OCR accuracy,
secure redaction, human authorization or production readiness from the doubles.
No commit, push, PR mutation, dependency installation or system change occurred.
