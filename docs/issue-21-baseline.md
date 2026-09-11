# Issue 21 Phase 0 baseline

Current integration note, 2026-09-11: extraction implementation is now included
in merged [PR #45](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/45),
main `d148422adb18190bada93b8588a4e34d73e3c2e4`.
This document preserves the inventory before those changes; its absent-module,
unmerged-branch and provisional ADR statements are historical observations.
[#21](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/21)
retains designated-model quality acceptance. See the
[implementation backlog](implementation-backlog.md) for current dependencies.

## Historical baseline checkpoint

Status: inventory completed on 2026-09-10; extraction implementation and live
acceptance remain pending. This note records the baseline before code changes.
The local implementation plan remains unchanged and outside the deliverable.

## Branch and environment

- Fetched `origin/main` and created `feat/model-extraction-evaluation` with
  `--no-track`; no upstream is configured.
- Base, HEAD and main: `8591bddc76584ad630774f214c7452c397c937d5`.
- Host: Windows 11, build 26100; repository-local Python 3.12.3.
- Deployment baseline: Linux. Existing CI uses Ubuntu and Python 3.11.
  Linux-target type checking on Windows is not Linux runtime verification.
- Installed metadata: boto3 and botocore absent; boto3-stubs 1.43.89;
  Pydantic 2.13.5; pydantic-settings 2.15.0; PyMuPDF 1.28.2;
  pypdf 6.17.0; pytest 8.4.2; Ruff 0.16.6; mypy 1.20.2.
  No dependencies were installed. SDK versions were inspected through package
  metadata without importing or constructing AWS clients.

Local evidence is under ignored `artifacts/issue-21-p0/`: `environment.json`,
`focused-command.json`, `focused.log`, `process-recheck-command.json`,
`process-recheck.log`, `checks.json` and individual static-check logs.
Test PDFs and local material are synthetic, unpublished artifacts.

## Dependency and contract inventory

Read-only GitHub inspection on 2026-09-10 found Issue 21 and all dependencies in
the following table open. The PR listing contained no open PRs. PRs 16
(extraction), 19 (writer) and 20 (service foundation) are merged. Historical M0
documentation still describes its pre-merge snapshot; that wording is not
evidence that the current service foundation is unmerged.

| Issue / owner boundary | Available on this base | Next integration obligation |
| --- | --- | --- |
| 22 / local privacy producer | No privacy implementation on main; four commits remain on local `feat/local-privacy-pipeline` | Consume the accepted sanitized manifest/lineage contract; do not copy a competing privacy model |
| 27 / document and storage owner | URI-free `DocumentReference`, `DocumentResolver` protocol and internal `ResolvedDocument(reference, storage_uri)` | Supply authorized immutable source bytes/version, not just a URI or caller-supplied digest |
| 23 / golden and reviewer acceptance owner | `GoldenSet` and native source string checker | Frozen independent case labels, split policy, scoring tolerances and acceptance thresholds |
| 17 / action executor; 24 / human task owner | Strict action/task DTOs, budgets and pure admission guards | Actual executor accounting, authenticated task persistence and version-bound handoff |
| 25 / browser workflow owner | Shared service contracts | Consume sanitized candidate/handoff outcomes without creating authority |
| 26 / PDF owner; 28 / publication owner | Local single-context writer and artifact contracts | Formal CJK/multiple-context output and authorized durable publication remain separate |
| 29 / jobs; 30 / Runtime; 31 / live acceptance owners | Reserved repositories and separate synthetic Runtime smoke | Durable execution, deployment and recovery acceptance are not proved by extraction tests |

The existing exported bundles are `schemas/service-v1.json` and
`schemas/local-service-v1.json`. Python/Pydantic remains the authority; service
models reject unknown fields. Reuse the current `PageProposal`, `PageExtraction`,
`SourceRegistry` and `assemble`. Introduce separately versioned outcome/telemetry
envelopes where additions would break strict consumers. No schema was changed.

The next available ADR number on this base is **0014**. This is provisional:
the unmerged privacy branch has its own ADR sequence. Recheck merged history
before Phase 1 creates an ADR; no number is reserved by this note.

## Current call graph and entrypoints

Paths below are relative to `src/appraisal_review/`. Inventory used `rg` across
source, tests, scripts and cloud tests for Converse, Textract, explanation,
SDK construction, inference profiles and context forwarding.

| Entrypoint / path | Implemented call flow | Privacy or credential boundary gap |
| --- | --- | --- |
| `document_cli.py:preparation` (`extract`) | `InputManifest` -> allowlisted `LocalPDFParser` -> `bedrock_client` -> render -> `BedrockDocumentExtractor.extract_page` -> page JSON/metrics | No validated privacy snapshot; case identity and prior model contexts enter the request; some input validation occurs after AWS preflight |
| `document_cli.py:bedrock_client` | Explicit profile/region session -> STS account/role -> model metadata -> runtime client | Examines only the first inference-profile model; cross-region boolean is not a complete destination allowlist; no offline request plan |
| `adapters/aws/document_extraction.py` | Injected client -> bounded threaded `converse` -> strict proposal/citation validation | Direct use can bypass CLI preflight; no mandatory sanitized-source authorization at the adapter boundary |
| `adapters/aws/textract.py` | Injected client or `from_default_session` -> start S3 job -> retrieve result page | Default credential chain remains callable; bucket/key lacks object version; no bounded job/pagination state machine or language gate |
| `application/review.py` -> optional `ExplanationGenerator` -> `adapters/aws/bedrock.py` | Injected explanation receives serialized findings and calls synchronous `converse` inside async method | Findings may contain sensitive evidence; default-session factory is callable; no privacy projection or bounded execution |
| `document_cli.py` local preparation commands | Parse, native candidates, assemble and native golden checking | Local artifacts contain source material and need controlled handling; these commands do not call AWS |
| CLI confirmation/approval/review -> `application/document_review.py` -> controller | Canonical candidates -> separate side confirmation and exact-material approval -> deterministic review | Preserve this authority boundary; model output cannot approve or promote confidence |
| `cloud_tests/smoke.py` | Explicit profile session -> synthetic Runtime invocation | Separate operator smoke, not document extraction or a privacy-qualified production route |

`ReviewAdapters`/`build_controller` do not register an explanation generator or
automatically construct AWS extraction clients. The legacy explanation path is
available through explicit injection into `ReviewService`, not established as a
default HTTP extraction route. No production Textract caller was found beyond
the exported adapter. These callable alternatives still need consideration when
Phase 2 defines the approved production assembly.

## Gaps and phase ownership

| Priority / phase | Finding | Required result |
| --- | --- | --- |
| Critical / 1-2, privacy and storage owners | Local allowlist/hash checks establish source identity, not permission to transmit a sanitized immutable snapshot | Accepted provider-neutral snapshot port, privacy/purpose/version checks and safe allowlisted context before model calls |
| Critical / 2, extraction owner | Legacy factories can use default credentials; profile metadata checks cover only one destination | Explicit workstation profile or trusted Runtime injection; full routing/identity checks; denied inputs cause zero model calls |
| High / 3, extraction owner | PNG validation checks header/dimensions/size, not actual decode or selected model limits | Decode validation and pinned request/rendering limits before invocation |
| High / 3, extraction owner | Timeout does not terminate the SDK thread; current code correctly avoids a replacement attempt after timeout | Preserve no-overlap behavior; coordinate SDK/application deadlines and record unknown completion |
| High / 3, extraction owner | Usage recorded only after valid output; missing token counts become zero; retries lack total elapsed/concurrency/cost accounting | Per-attempt nullable usage, classified errors, bounded backoff/budgets and versioned prompt/configuration |
| High / 2-3, extraction owner | Errors chain original exceptions; CLI can expose traceback details; explanation forwards findings without projection | Static safe operational errors and tested sensitive-data canaries across requests/logs/results |
| High / 4, extraction owner with storage owner | Textract lacks version binding, partial/failure handling, pagination validation and capability routing | Explicit supported route or documented non-use; validate current provider language limits before making a Chinese OCR claim |
| High / 5, extraction and human task owners | Failed pages lack a typed located outcome/handoff contract | Preserve unresolved page/field identities and incomplete inventory; no automatic completion or approval |
| High / 6, evaluation and golden owners | `check_fields` scores source strings, not model extraction quality | Frozen held-out manifests, one-to-one matching, false positives/negatives, failed-case denominators and honest localization/cost metrics |

Existing safeguards to retain: one-based pages, canonical unrotated CropBox
bottom-left coordinates, exact source hash/version/region/excerpt validation,
rejection of contradictory legacy evidence, zero promoted observation confidence,
model-proposed reliability, no model confirmation, selected source-purpose
checks, candidate-only assembly and separate exact-material approval. A resolved
citation establishes location, not semantic correctness or transmission authority.

## Verification before implementation

Focused command, using the repository interpreter:

```text
python -m pytest tests/unit/test_document_extraction.py tests/unit/test_document_cli.py tests/unit/test_extraction_review_integration.py tests/unit/test_reviewer_trust.py tests/unit/test_source_purpose.py --basetemp=artifacts/issue-21-p0/focused -o cache_dir=artifacts/issue-21-p0/pytest-cache
```

Result: **44 passed, 18 failed**, exit 1. Initial failures:

- Four PDF/CLI integration tests hit sandbox `WinError 5` when creating Windows
  multiprocessing pipes. A separate elevated rerun of those four produced
  **1 passed, 3 failed**: native parse/golden passed; the three review integration
  tests reached confirmation and correctly stopped with
  `unsupported_reviewer_platform` (CLI exit 2).
- Thirteen reviewer tests hit the existing Linux/macOS POSIX identity gate.
- One unsupported-identity test deletes `os.getuid` unconditionally; on Windows
  the test subprocess fails with `AttributeError` before exercising the gate.

The rerun is a separate diagnostic, not a replacement green suite. There is no
Linux runtime result in this Phase 0 evidence. Do not weaken approval checks or
skip tests to manufacture one. Re-run the focused suite on Linux during subsequent
verification. The observed platform failures do not block offline Phase 1 work.

Both pytest invocations removed inherited `AWS_*` variables, disabled EC2
metadata lookup and pointed SDK config/credential paths to nonexistent local
files. Provider interactions used test doubles. No AWS SDK session was created
against actual credentials and no AWS request was made.

| Static baseline | Result |
| --- | --- |
| `python -m ruff check .` | Passed |
| `python -m ruff format --check .` | Passed; 158 files |
| `python -m mypy --platform linux src` | Passed; 74 source files |
| `python -m mypy src` on Windows | Three existing POSIX API errors in `adapters/local/approval.py` (`getuid`/`getpwuid`) |

Only focused tests and the listed static checks were run; this is not full CI,
live model quality, Chinese OCR accuracy or deployment acceptance.

## Handoff

Phase 1 can define versioned snapshot/outcome/handoff/telemetry and evaluation
contracts against explicit dependency ports. Pending dependency implementations
must remain labelled as such; local fixtures cannot certify privacy, authority
or live acceptance. A concrete approved input/model/routing/budget plan and actual
privacy/source integration are still required before any live phase.

Phase 0 changed documentation only. No source, test, schema, approval store,
original document or implementation plan was modified. Remote activity was
read-only; no commit, push, PR/issue mutation or AWS call was performed.
