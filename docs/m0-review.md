# M0 pre-submission review

This is the historical pre-publication review. The later PR #20 review identified
two gaps outside its counterexamples: method-only restoration of native authority
and service projection of preflight diagnostics. See the
[P2 correction record](m0-p2-review.md) for current fixes and verification; the
earlier "No core defect found" disposition is limited to the tests recorded below.

Reviewed locally on 2026-09-07 on `feat/shared-service-contracts` against main
`ea55043d90aa21e6f0a7e3fe05aa34ef8a3553d3`. At the end of the local review,
HEAD was that base and there were no staged changes or outgoing commits. This is
pre-publication evidence: the actual publication commit, PR and head CI must be
checked separately. The merged writer's successful CI is baseline evidence only.

## Findings and disposition

| Area / problem | Location | Reproduction and impact | Fix or retention reason / evidence |
| --- | --- | --- | --- |
| A: exact revisions and authority | application/revisions.py; existing domain/confidence.py and adapters/local/approval.py | Mutate original material, returned nested facts or revision context; reuse a parent receipt on new material; substitute the other side's confirmation | No core defect found. Serialized snapshots return detached copies; side assertions bind context/factor/side/value/evidence, while the signed material covers full registry, rule, inventory, case/version and values. Tests retain legitimate confidence=0 confirmation/approval, deny missing/wrong-reviewer/wrong-side/stale authority and leave the original receipt usable only for the unchanged original material. |
| B: proposal can claim system origin to bypass model budget | application/service_guards.py: admit_action | Change a model proposal's proposer.kind to system with steps remaining but zero model calls: admission formerly succeeded | Fixed: require an independent trusted proposer from executor context and compare it before budget admission. Forged origin is unauthorized; actual model budget exhaustion remains capability_unavailable; trusted system work remains legal. Pre-fix regression failed with DID NOT RAISE. |
| C1: previous result can claim an existing successful PDF for a new run | adapters/local/service.py: run / ConfinedWriter / _manifest | Return an earlier completed AgentReviewRun from a reused controller without calling the writer again: a fresh run formerly received a completed manifest | Fixed: clear previous write evidence, bind new evidence to this run UUID and require the exact published output, review/result/map digests. Stale results and no-write delegate returns have no valid new manifest. The old PDF is preserved. Pre-fix regression incorrectly returned succeeded. |
| C2: same-page output replacement is accepted | adapters/local/service.py: ConfinedWriter / _manifest | After genuine writing, replace output bytes with the valid blank template before projection. Equal page count formerly passed and the replacement was labelled verified | Fixed: capture published file identity/hash on writer return and compare again before manifest projection. Different bytes or identity fail with sanitized execution_failed. This is local observation, not a continuous snapshot or a storage transaction. Pre-fix regression incorrectly returned succeeded. |
| D: callable contracts, compatibility and command diagnostics | local_service.py; api/app.py; scripts/local_service_smoke.py | Invoke actual HTTP and CLI, invalid requests/configuration, source-binding business failure; compare schemas with base | Existing HTTP 200/business failed, EntryProblem errors and legacy schemas retained. Runbook now explicitly separates invoke stdout error envelopes/exit 0 from run startup errors/stderr/exit 2 and caught run failures/JSON/exit 0. Smoke now invokes the real run CLI and records channels/exits. No new jobs/tasks/download routes. |
| E: artifact names and publication ordering need explanation | docs/local-service-runbook.md; docs/service-contracts.md; publication drafts | Four retained output filenames did not explicitly map producer/result/manifest, and Issue publication was grouped with initial delivery | Clarified both PDFs and their separate producers; smoke emits producer/response/run/hash/size mappings. Issue updates/new issues are a separate later approval group after shared contract review. Read-only search found no duplicate B/C/E work items; existing issue bodies are preserved. |

The pre-fix command exercised three counterexamples and recorded three assertion
failures in ignored `artifacts/m0-review/pre-fix.log` before implementation edits:

```bash
PYTHONPATH=src pytest tests/unit/test_service_contracts.py tests/integration/test_local_service.py -k 'cannot_claim_system or old_controller_result or same_page_replacement'
```

The same regressions now pass. Relevant named tests are
`test_action_cannot_claim_system_identity_to_bypass_model_budget`,
`test_old_controller_result_cannot_claim_existing_pdf_for_new_run`, and
`test_same_page_replacement_after_write_cannot_become_manifest`.
Additional focused coverage exercises no-write results, all five nonwriting states
against an existing successful destination, explicit permitted overwrite and actual
CLI failure channels. Existing tests were retained; no assertion, coverage setting,
submission rule or dependency version was relaxed.

## A: preserved confirmation and revision rules

`RevisionSnapshot.capture` serializes both material and revision metadata. A
returned model or nested collection cannot mutate the snapshot. Configuration also
revalidates a JSON copy before reading exact pinned material and checking every
allowlisted document's URI/version/hash/purpose. The Controller reparses registered
sources and checks identity/content and purpose; a declared digest or revision ID
is not authorization or evidence that disk bytes still match.

A new `revise` operation deliberately changes the case version and clears all
human confirmations because M0 has no reliable dependency analysis for carrying
those assertions across a changed revision. It preserves the old snapshot and
receipt, leaves raw scores intact and retains unchanged native provenance. An
original receipt is valid only for the unchanged original material. A new revision
must not reuse confirmations or approvals that fail their exact binding. New
material needs its applicable explicit confirmations and separate approval; no
receipt is automatically created, moved or re-signed.

Reuse the existing approval, reviewer-trust, confidence-provenance, source-purpose,
case-review and extraction integration tests. Confirmation never bypasses source
roles, applicability, arithmetic, coverage or independent verification. The fixture
helper authorizes only its own fixed synthetic documents in a fresh private store;
there is no HTTP route for automatic approval of caller material.

## B/D: providers, consumers and reserved work

The [service authority](service-contracts.md#ownership-and-consumers) contains
the type-by-type provider/consumer table. Actual consumers are the revision helper,
local facade/configuration/factory, entrypoints, fixture/export scripts and tests.
Task/response and action/idempotency checks are pure functions, not executed human
transitions or a model selector. A model's claimed actor is presentation data; the
executor supplies trusted origin independently. Principal comes from a trusted
adapter, not a body reviewer/roles field. Allowed action data comes from trusted
policy. Admission is separate from actual execution and durable accounting.

Reserved protocols are intentionally not instantiated. They promise duties for
future principal/document/revision/task/job implementations, not current database
transactions, dispatch, outbox, retries, leases or recovery. Proposed actions,
allowed-action policy and executed/rejected/failed decision events remain distinct.
No illustrative fixture is presented as a real model decision or an approval.

The configured factory uses the existing ReviewAdapters/build_controller and
execute_review/Controller. No second review engine was added. Importing modules
reads no configured document/font and creates no AWS clients or credential lookup.
Review-only configuration needs no font; explicit contradictory writer settings
fail and missing files/glyphs fail at preflight without fallback.

## C: actual files and ownership

`output/completed.pdf` is the first real PDF written through the legacy HTTP entry;
`http-written.json` retains that response. `output/manifest.pdf` is a second real
PDF written by the actual service run CLI, not a manifest rendered as PDF and not
misnamed JSON. `result-written.json` is that CLI's ServiceResult; `artifacts[0]`
contains its machine-readable manifest and `run` carries case/revision/material
and run identity. Keep the enclosing run when consuming artifacts.

`smoke-report.json` is diagnostic JSON mapping both files to producer, response,
SHA-256 and observed byte size; it additionally records the second run/artifact ID,
page count and field IDs. Byte size is not a declared ArtifactManifest v1 field.
The actual acceptance produced two one-page, 22,084-byte PDFs with field road-rate,
text +5.00%, and SHA-256
`080270d6ed2ce7e6df04c6a33501a029082f256006c25b3365438ec363326a29`.
Identical deterministic bytes do not imply the same run or artifact.

`result-retry-failed.json` records a distinct retry run with no artifact. The smoke
also sends needs_review to an existing destination and checks unchanged successful
outputs and original source hashes. The core writer retains staging, template/full
map binding, source alias protection, font checks and content reopen verification.
An explicit permitted overwrite still publishes a fresh staged inode successfully.
Multi-context review still refuses single-context output for the whole case.

Per-call write evidence relies on the trusted configured writer and observed local
filesystem state. It is neither a signed receipt nor protection against malicious
operator code or later file mutation. Full source snapshots, immutable artifact
storage and fenced remote publication remain D/E work.

## Verification and handoff

Fresh local evidence on macOS/Python 3.13.5:

- Ruff and format pass; mypy passes for 74 source files.
- Full repository suite: 638 passed, 7 dependency warnings, branch coverage enabled;
  combined coverage displays 87%. This is engineering evidence, not model accuracy.
- cloud_tests: 8 passed. Both CloudFormation templates lint locally.
- Existing http_smoke.py and configured local_service_smoke.py pass using actual
  loopback servers; actual invoke/run stdout JSON, exits, two real PDFs, blocked
  output and same-destination retry are checked.
- Export/schema/fixture equality and JSON roundtrips pass in repository tests.
  Separate comparison with git-archived main confirms identical OpenAPI,
  AgentReviewRequest, AgentReviewRun, InputManifest, PDFWriteRequest/PDFWriteResult
  schemas. Existing request/error/success and PDF contracts stay compatible.
- 73 relative documentation links/anchors resolve and both Mermaid architecture
  diagrams parse. Rendered output inspection confirms readable +5.00% inside the
  intact field border; both identical PDFs have no prohibited attribution metadata.
- Full submission gate passes on 157 inspected blobs, zero outgoing commits,
  configured Git identity and the functional branch. All 11 publication drafts
  pass individually. Seven protected/core/gate files remain byte-identical to main,
  including AGENTS.md and the unchanged submission checker. The pending inventory
  had 13 modified tracked files plus 31 new files (44 total), with no staged changes
  at that local review checkpoint.
- Starlette/httpx, AnyIO alias and PyMuPDF SWIG warnings remain visible. The first
  added CLI validation assertion used the wrong legacy code; the adapter was
  inspected and the expectation corrected to its existing invalid_request code.
  Initial format findings were formatted; no dependency upgrade or suppression.

Raw logs, compatibility inventory, local PDF output, pre-fix evidence and final
submission checks stay in ignored `artifacts/m0-review/`. Prior 624-test evidence
under `artifacts/m0/` is preserved as the earlier 2026-09-07 handoff, not this run.
The [traceability](delivery-traceability.md) records final gate outcomes.

English publication drafts are retained locally in `artifacts/m0/publication/`.
Commit/push/PR publication requires its separately approved workflow and actual
head CI verification; no local result here substitutes for that CI. Issue drafts
remain unpublished pending later approval. Issue #5 stays open; #7/#8/#9/#17 retain their current
bodies as history. No new issue number or assignee was invented. Publish Issue
changes only under separate approval after the shared contract review converges.
A–E's first deliveries remain defined in the [plan](mvp-plan.md): A controlled
model policy/comparison; B authenticated human transitions; C workbench; D authorized
storage/transactional jobs/Runtime; E formal PDF and independently reviewed goldens.
Real models, formal CJK/full-case/multi-context output and human/AWS acceptance
remain outstanding. No cloud/model call or real rule approval was performed.
