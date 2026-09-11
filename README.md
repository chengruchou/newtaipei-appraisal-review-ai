# Agentic Real Estate Valuation Reviewer

An evidence-grounded valuation case review system for the New Taipei City
competition. Document and model adapters propose facts and rules; deterministic
code calculates grades, correction rates and totals, verifies evidence, and gates
PDF output. Reviewers inspect findings and explicitly authorize exact material.
PDF filling is one output of case review.

**Current delivery: the configured local core is integrated and its seven real
Chromium scenarios passed. The complete privacy/restoration workflow is not yet
accepted. AWS deployment, live model quality and formal business acceptance are
not accepted.** [Project progress](docs/project-progress.md) is the current status
authority; [repair evidence](docs/integration-repair-delivery.md) records exact
component heads and [traceability](docs/delivery-traceability.md) separates scopes.

## Integrated boundaries

| Boundary | Implemented and integrated locally | Remaining acceptance |
| --- | --- | --- |
| Local privacy | Restricted Origin/session bridge, exact reviewed export, encrypted mapping readback and sanitized C2 admission | Complete actual OCR restoration; production desktop distribution |
| Documents and extraction | Authorized immutable snapshots, actual PDF parser, source checks for resumed runs, production SDK adapter with injected model responses in rehearsal | Measured model quality and real cloud authorization |
| Jobs and human tasks | SQLite job/task/revision/outbox/receipt transactions, durable dispatch queue, actual authenticated API | Production identity and cloud transaction composition |
| Controlled actions | Canonical response adapter, trusted allowed actions, persisted run reservations, failed/unknown-effect quarantine | Designated model and operator evaluation |
| PDF and publication | Two-context/eight-field actual writer, immutable font bytes, reopen, exact fenced publication and reauthorized download | Complete OCR backfill; formal assets and business grants |
| Workbench and Runtime | Regenerated canonical client, seven actual browser core scenarios, installed wheel and isolated container configured success | Final image/security gate and AWS operation |

There is one canonical #36/#38 service/task union and regenerated #39 consumer.
The undeployed strict consumers migrate together; frozen commands and legacy
HTTP/invocation success remain covered. `controlled-action-v1` remains separate.
Legacy `ArtifactManifest` stays unchanged; `FencedArtifactManifest`
(`artifact-manifest-v2`) carries every context and publication identity.
See [contract ownership and migration](docs/service-contracts.md).

## Reliability and authority

- Preserve original facts, units, raw text, measured confidence, source version
  and hash, one-based page and coordinates. Missing or contradictory critical
  evidence stays `needs_review`; model prose is not calculation or approval.
- Rules are versioned case-specific data. A sample district or land-use category
  does not define universal policy. Unknown rules and factors fail explicitly.
- Human confirmation binds an exact side and preserves confidence, including
  zero. Correction does not silently confirm or approve. New material requires
  matching independent approval and new authorized run sources.
- A model selects only from trusted allowed actions. Failed receipts and unknown
  external effects retain failed/quarantined evidence and consumed reservations;
  re-entering the same run must not refill its budget.
- Original PDFs and downloaded placeholder artifacts remain unchanged. A real
  writer uses the approved template/map and the same immutable font bytes for
  measurement and embedding, then reopens and verifies a separate output.
  Unsupported context coverage never becomes a partial first-context report.
- A result is visible only through its current committed reference. Lease,
  attempt, fence, result version, source authorization and independent publication
  grant must still match. Every download reauthorizes; local revealed values
  cannot enter the publication writer.

## Run the configured job and review workbench

The [integrated local runbook](docs/integrated-local-runbook.md) provides the
repository-local installation, core service, separate privacy bridge and actual
Chromium commands. Use its fresh synthetic workspace and hash-pinned local OCR
configuration; it makes no AWS or paid model calls. The exact acceptance status
and unresolved restoration or deployment gates remain in
[project progress](docs/project-progress.md).

## Run the existing local reference service

This established synthetic local facade is distinct from the new integrated
job/task/privacy/browser rehearsal. It uses actual parser/review/PDF code with
isolated synthetic assets and no AWS or model calls. It does not establish
production identity, formal font approval or full integrated-service acceptance.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
export PYTHONPATH="$PWD/src"
python scripts/local_service_smoke.py
python scripts/local_service_fixture.py --directory artifacts/local-demo
export APPRAISAL_LOCAL_CONFIG="$PWD/artifacts/local-demo/config.json"
uvicorn appraisal_review.local_service:app_from_environment --factory --host 127.0.0.1 --port 8000
```

Use a fresh fixture/output directory for each run. The fixture write and blocked
requests, invocation parity and source-preservation checks are described in the
[local service runbook](docs/local-service-runbook.md). Missing configuration
fails closed; it never selects synthetic material or grants approval implicitly.
The fake-writer demo's `completed` scenario remains `verified/simulated` and
creates no PDF.

The configured `create_integrated_service` composition has a separate reproducible
[runbook](docs/integrated-local-runbook.md). Its evidence distinguishes the accepted
local core scenarios from unresolved full privacy and cloud acceptance. [Architecture](docs/architecture.md) separates the
local composition from the AWS target.

## Verification and delivery

```sh
ruff check .
ruff format --check .
mypy src
pytest
python -m pytest cloud_tests
python scripts/check_submission.py --base origin/main
```

Run additional frontend generation/verification, package/container checks and
CloudFormation lint for the affected delivery scope. Schema regeneration is part
of the coordinated migration, not evidence that an older strict client remains
compatible. CI at an exact pushed head, local tests, SDK/emulator results and
live AWS/browser observations must be reported separately. A job blocked before
CI startup is not a green test run.

Do not commit real cases, original competition PDFs, mappings, credentials,
private URLs, generated PDFs or execution artifacts. Use explicit isolated
synthetic approvals only for tests. See [data handling](docs/data-handling.md),
[submission checks](docs/submission-checks.md), [the ADR registry](docs/adr/README.md)
and [cloud acceptance](docs/cloud-acceptance.md).

Earlier main SHA, branch, CI and milestone claims are preserved in the
[historical delivery snapshots](docs/history/2026-09-11-pre-convergence/README.md).
They do not describe the current integration checkout or its acceptance.
