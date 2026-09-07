# PR #20 revision authority and verification diagnostics correction

Local correction on 2026-09-07, based on published PR #20 head
`89e4c77eea67337cb94ef7f4f0d9afbf7645cef9` and main
`ea55043d90aa21e6f0a7e3fe05aa34ef8a3553d3`, on
`feat/shared-service-contracts`. Both findings were supplied in the review report
outside GitHub; no remote review/comment link was created or resolved.
This record describes local changes pending separate commit/push/PR-update approval.

## Findings and fixes

1. Revision method relabeling could manufacture native authority. A numeric edit
   correctly downgraded a side, but a later unchanged-side revision could set
   `native_numeric` because the side digest omits method. A fresh synthetic receipt
   was issuable without side confirmation. `RevisionSnapshot.revise` now requires
   the immediate parent's native method, the candidate's native method, no human
   confirmation fields and an unchanged exact side digest. All other cases remain
   model_proposed. This includes a non-native parent relabeled as native and an
   invalid native side whose confirmation is removed. Re-extraction requires the
   trusted extraction/assembly entry; the revision helper grants no authority.
2. Preflight failures could disappear from ServiceResult when case_review was
   absent. The facade now projects the existing verification status, ordered
   critical errors and warnings into ServiceVerification/VerificationDiagnostic.
   Exact known source-binding and missing-registry reasons have finite public
   codes/messages; unknown text maps to generic blockers/warnings without copying
   paths or raw inputs. Execution succeeded/business failed remains valid, with
   null problem, empty findings and no artifacts. A later manifest failure retains
   the available verification report while returning its existing execution error.

The side confirmation digest, core reviewer, Controller, verifier, audit logger,
HTTP/invocation types and submission checker are unchanged. No threshold or source
confidence was increased. Revised synthetic observations with observation/evidence
confidence 0 still pass after explicit side confirmation and a separate new exact
approval. The original receipt remains usable only for the unchanged original
material; neither it nor its files are altered to authorize a child revision.

The proposed service-v1 schema and consumer fixtures are regenerated together,
including result-source-binding-failed.json. Existing extra-forbid consumers must
adopt the updated unmerged M0 bundle together. This is an explicit pre-merge change
to the service envelope, not a legacy HTTP/invocation response change. The contract,
runbook and proposed ADR 0013 document the boundary and generic-diagnostic limits.

## Regression evidence

Before implementation, the first counterexample run produced 7 assertion failures
and 1 passing unchanged-native control. Two failures showed the real approval
store issuing a receipt where confirmation was required; four showed non-native
parent relabeling; one showed the missing service diagnostic. Raw evidence is
retained locally in ignored artifacts/m0-p2/before-fix.log.

```bash
PYTHONPATH=src .venv/bin/pytest tests/unit/test_revision_authority.py tests/integration/test_local_service.py::test_service_preserves_source_binding_diagnostics_and_legacy_contract
```

The completed regression set adds 17 tests to the existing 638:

- Nine revision tests cover both sides, sequential corrections, non-native parent
  methods, invalid confirmation cleanup, unchanged-native continuity, old receipt
  preservation and legitimate explicit confirmation/new approval with raw scores 0.
- Four integration tests cover source-binding without case_review, failed and
  needs_review unknown-message privacy, and the actual run CLI. The source-binding
  test compares legacy HTTP/invocation verification and validates the real new JSON
  against the exported schema. Existing normal/PDF/error assertions are retained.
- Four diagnostic validation tests reject private messages, private/unknown codes,
  mismatched finite code/message pairs and extra storage URI fields.

The localhost smoke additionally exercises unauthorized-source HTTP, actual invoke
and actual run processes, clean JSON channels, source_binding diagnostics, no
path echo and no artifact. It retains result-source-binding-failed.json beside its
existing real-PDF success/retry/blocked-case evidence in an ignored directory.

## Local checks and remote evidence

All checks below passed locally on macOS/Python 3.13.5:

```bash
.venv/bin/ruff check .
.venv/bin/ruff format --check .
PYTHONPATH=src .venv/bin/mypy src
PYTHONPATH=src .venv/bin/pytest --cov=appraisal_review --cov-config=pyproject.toml --cov-report=term-missing --cov-report=json:artifacts/m0-p2/coverage.json
PYTHONPATH=src .venv/bin/pytest cloud_tests
PYTHONPATH=src .venv/bin/python scripts/http_smoke.py
PYTHONPATH=src .venv/bin/python scripts/local_service_smoke.py --directory artifacts/m0-p2/service-acceptance
.venv/bin/cfn-lint cloud_tests/image-stack.json cloud_tests/runtime-stack.json
```

Repository tests: 655 passed, 7 visible dependency warnings; cloud tests: 8 passed.
Ruff and format checks pass; mypy checks 74 source files. Coverage remains
branch-enabled; this local run displays 84% combined coverage, not model accuracy.
Starlette/httpx, AnyIO and PyMuPDF SWIG warnings are retained without suppression.
Initial formatting and optional-type findings were corrected before final checks.

Archived-head comparison confirms identical OpenAPI, AgentReviewRequest,
AgentReviewRun, InputManifest, PDFWriteRequest and PDFWriteResult schemas. Seven
protected files match their recorded hashes, including AGENTS.md and the unchanged
submission checker. Source PDFs, test receipt/key, raw logs and generated output
remain ignored local evidence; no real material or external service is used.

[PR #20](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/20)
still points to the published head above. Its
[successful CI](https://github.com/chengruchou/newtaipei-appraisal-review-ai/actions/runs/34116371143)
is evidence for that pre-correction head (638 repository tests and 8 cloud tests),
not for these uncommitted changes. The merged #19 CI remains older baseline
evidence. No new repair commit/head CI exists until separately authorized
publication; do not use either historical run or local tests as its replacement.

Full attribution/branch checks cover working files, the actual index, a separate
candidate index, existing commit snapshots/metadata and the English publication
drafts. The branch is functional and configured human Git identity is retained.
No gate rule, assertion or coverage setting was relaxed. The actual index remains
unchanged with no staged changes. Any later commit must be checked again in full,
including metadata and every outgoing commit, before an authorized push.

Independent code review, coordinated B/C/D contract adoption and subsequent real
document/human acceptance remain outstanding. No commit, remote mutation, merge,
CI rerun, AWS/model call or real rule approval belongs to this local correction.
Issue drafts remain unchanged pending their separate approval.
