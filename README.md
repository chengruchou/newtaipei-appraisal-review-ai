# Agentic Real Estate Valuation Reviewer

An evidence-grounded valuation case review system for the New Taipei City
competition. Document and model adapters propose facts and rules; deterministic
code calculates grades, correction rates and totals, verifies evidence, and gates
PDF output. Reviewers inspect findings and explicitly authorize exact material.
PDF filling is one output of case review.

**Current delivery: local service integration in progress. AWS deployment, live
model quality and formal business acceptance are not accepted.** Component repairs
and local process tests do not establish an accepted end-to-end release. The
[project progress](docs/project-progress.md) page is the current status authority;
[traceability](docs/delivery-traceability.md) separates implemented behavior,
component evidence and remaining acceptance.

## What is being integrated

| Boundary | Implemented components | Integration or acceptance still required |
| --- | --- | --- |
| Local privacy | Exact reviewed export, preserved source evidence, encrypted mapping for that exact payload, separate local restore | Browser/bridge through actual admission; current origin/session/source authorization |
| Documents and extraction | Authorized document versions, immutable run snapshots, actual PDF parser, native and injected model adapters | Current-source checks across resumed revisions; live model accuracy |
| Jobs and human tasks | Job state machine, outbox/leases/fences, authenticated task API, revision/receipt logic; new SQLite combined job/task/result adapter has process tests | Shared configured service and restart rehearsal; production identity and durable cloud transaction composition |
| Controlled actions | Versioned allowed actions, trusted executor admission, failed-decision tracing and persistent run budget/reservation adapter | Bind workflow ledger, job authority and human handoff in the same run; resolve unknown external effects conservatively |
| PDF and publication | Real multiple-context writer, approved byte-bound fonts, source protection, reopen verification and current-authority publication adapters | Complete service manifest coverage, actual authorized download/backfill; formal templates/fonts and independent publication approval |
| Workbench and Runtime | Browser consumer, source/subject projections, configurable Runtime and deployment packaging | Regenerated compatible consumers, real loopback/browser success and recovery; AWS deployment and operational acceptance |

Current source combines the #36 service contracts and #38 task projections.
`controlled-action-v1` remains explicitly versioned. The undeployed task
extensions require all strict consumers to regenerate together. Legacy frozen
commands and baseline success responses remain compatibility
obligations; newly serialized nested task fields require the synchronized
consumer upgrade. The selected artifact migration preserves legacy
`ArtifactManifest` unchanged and adds `FencedArtifactManifest`
(`artifact-manifest-v2`) through the service result union. Consumers must regenerate
to read the new complete-context projection; its actual service acceptance is
still required. See [contract ownership and migration](docs/service-contracts.md).

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

The new `create_integrated_service` composition and its rehearsal are being
assembled separately. Its acceptance record must identify the actual startup
command, configuration, dependencies and observed scenarios before it replaces
this reference quick start. [Architecture](docs/architecture.md) separates the
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
