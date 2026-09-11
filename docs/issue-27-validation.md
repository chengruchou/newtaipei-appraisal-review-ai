# Issue #27 local validation

Date: 2026-09-10. Environment: macOS, Python 3.13.5, isolated repository-local
virtual environment. Branch: `feat/versioned-document-transfer`. Base and unchanged
HEAD: `463880af3a6dc6aad2bfa6fdfc3bc267afc4d4a5`. These are working-tree results,
not a published commit, PR head CI, Linux acceptance or a live AWS run.

## Results

| Check | Actual result |
| --- | --- |
| `ruff check .` | Passed |
| `ruff format --check .` | 184 Python files already formatted |
| `mypy src` | Passed, 88 source files |
| `pytest --cov=appraisal_review --cov-config=pyproject.toml --cov-report=term-missing` | 860 passed; aggregate coverage 85%; 495 warnings retained |
| `pytest tests/integration/test_document_transfer.py` | 104 passed across local and S3-double configurations |
| `python -m pytest cloud_tests` | 12 passed, all offline; includes four new SDK/policy/cleanup tests |
| `python scripts/generate_goldens.py` | 14 committed JSON manifests match fixtures (13 case/revision documents and an index); nothing rewritten |
| `python scripts/local_service_smoke.py` | Actual loopback HTTP and invocation passed; 200/422/503, legacy validation shape, diagnostics, source hashes and real output reopen checks preserved |
| `cfn-lint infra/documents/stack.json cloud_tests/image-stack.json cloud_tests/runtime-stack.json` | Passed |
| `git diff --check` | Passed |

No existing assertions were weakened, tests disabled or confidence values raised.
Warnings remain visible, including dependencies and deliberate invalid-fixture
serialization exercised by existing tests. The new malformed document boundary
specifically fails serialization without emitting the private value as a warning.
Full-suite terminal evidence is retained locally under
`artifacts/issue-27/repo-tests-final.log`, outside submitted source files.

## Regression evidence

- Explicit principal/case/operation/purpose grants are required. Invalid references,
  wrong versions/hashes, unsupported paths/URLs/buckets, non-PDF markers, oversized
  bodies and absent or invalid export authority cannot become usable documents.
- Same-export retries and concurrent ingestion allocate one service document
  version. Concurrent different run bindings produce one winner and a conflict.
- New source versions do not change old run bytes. Both byte substitution and
  replacement of the stored VersionId commitment are rejected.
- Confirmation is bound to the exact final payload and consumed once. A substituted
  original canary is stopped locally before the ingestion transport is called.
- Captured SDK arguments, objects, keys, metadata, tags, public JSON, audit and
  log surfaces contain none of the synthetic original/map canaries.
- Current authorization is checked on old snapshots. Failed audit blocks reads.
  Export expiry does not expire previously admitted evidence.
- Public JSON validates against the reproducibly exported document-v1 schema.
- Real SDK Stubber validates VersionId, ExpectedBucketOwner, SSE-S3, checksums,
  conditional creation and fixed tag request shapes. Synthetic cleanup is limited
  to exact successful versions recorded by that invocation and matching tags.

The three shared A3 files match published head
`6131957d8859ddf47c71cab8eb4130614efeeb77` byte-for-byte. No wholesale peer branch
merge was used. The following existing files match the base exactly:

- `src/appraisal_review/domain/verification.py`
- `src/appraisal_review/adapters/local/audit.py`
- `tests/unit/test_verification.py`
- `src/appraisal_review/application/controller.py`
- `src/appraisal_review/domain/case_review.py`
- `src/appraisal_review/domain/service_contracts.py`
- `scripts/check_submission.py`

## Publication recheck, 2026-09-11

The same 27-file delivery was rechecked against the original branch baseline.
Remote main advanced to `b0917732cb0bd4597cb7a098421a2dd19a4cea28` (#32);
local branch results are not a test of that newer merge. The seven protected
files are unchanged, and A3's three shared contracts still match its latest branch.

Initial test collection failed because macOS hidden flags on virtualenv `.pth`
files prevented loading the editable package. Reinstalled the local editable
package; subprocesses still missed the package when relying on `.pth` alone. The final suite and supplemental smoke commands
use `PYTHONPATH="$PWD/src"` explicitly in this local environment. No source/test
assertion or CI setting was weakened for this environment issue.
Ruff, format (185 Python files), mypy (88 source files), 12 offline cloud tests,
14 goldens, actual loopback HTTP/invocation smoke and CloudFormation lint passed.
The full rerun passed: 860 tests, 85% aggregate coverage, 495 warnings retained.
Evidence: `artifacts/issue-27/publication-tests.log` (local and ignored).
The actual PR head and its CI result are reported in the PR delivery, separately
from the historical results above. Cloud acceptance remains outstanding.

## Submission gate and publication state

The unchanged checker is run with the complete current working file set, complete
index/head snapshots, configured author/committer identity, current/intended branch
and the local English publication draft:

```bash
python scripts/check_submission.py --base origin/main \
  --branch feat/versioned-document-transfer \
  --publication-file artifacts/issue-27/pr-draft.md
```

No tool attribution was added. Required technical names, licenses and narrowly
identified policy/test examples remain. At the original local checkpoint there were no staged changes or outgoing
commits. The user subsequently requested PR publication. The publication gate is
rerun before commit and on each actual outgoing commit before push, including its
complete tree and author/committer/message. No Issue mutation, approval, merge or
AWS action is part of this publication. The original
working checkout and its pre-existing untracked `cloud_tests/` copy are preserved.

## Remaining acceptance

Independent code review, Linux CI for a future published head, production A3 human
presenter/key provisioning, authenticated gateway and A2/C1/D1 composition, live
IAM/S3 denial and recovery checks, and end-to-end privacy/network acceptance remain.
The opt-in cloud probe was not executed. No AWS resource was deployed, no real
document/model was used and no real rule or material was approved. #27 should not
be declared fully cloud-accepted from these local results.
