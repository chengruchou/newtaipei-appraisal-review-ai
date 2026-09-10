# Agentic AI Real Estate Valuation Reviewer

An evidence-grounded system for reviewing real estate valuation cases for the
2026 New Taipei City AI Smart City Hackathon. Document adapters propose facts and
rules; deterministic code checks applicability, grades, correction matrices,
original values and cross-form arithmetic. Reviewers inspect located findings and
authorize exact material. A verified, completed or corrected PDF is one output,
not the whole product.

## Current delivery

Rechecked main on 2026-09-07:
`ea55043d90aa21e6f0a7e3fe05aa34ef8a3553d3`. Review #15, extraction #16 and
[local PDF writer #19](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/19)
are merged. M0 service foundation below is on `feat/shared-service-contracts`,
pending independent code review and merge. Local pre-publication evidence is dated
separately from baseline CI and the eventual PR head CI.

| Capability | Implemented behavior | Remaining acceptance |
| --- | --- | --- |
| Source-bound review (#15) | Independent calculation, original cells, validated fills, applicability and completion gates | E complete-case golden and human acceptance |
| Document preparation (#16) | Allowlisted real parser, native/Bedrock candidate adapters, Linux/macOS reviewer and signed receipts | A actual model comparison; E full-field/source accuracy |
| PDF core (#19) | Real local rendering/correction, template/map binding, preflight, reopen verification, atomic output; injected S3 wrapper | E formal CJK/template/multiple-context output; D live S3/Runtime |
| M0 local integration (working branch) | Configured HTTP/invocation using real parser/material/authorizer; optional real writer; separate service envelope | B web assembly and D full Runtime wiring |
| M0 shared contracts (working branch) | Versioned DTOs, immutable material helper, pure admission/idempotency guards, schema/fixtures | Transactional adapters, model selector and real event persistence |
| Runtime smoke (#14) | Synthetic HTTP/container/template preparation, durable=false | D durable jobs, identity, outbox/recovery and deployment |
| Browser human review | Consumer fixtures only | B task APIs; C workbench |

[M0 pre-submission review](docs/m0-review.md) records reproduced defects, fixes
and preserved trust boundaries. [Traceability](docs/delivery-traceability.md) separates local tests, historical CI
and live acceptance. Passing synthetic tests does not establish real-case accuracy.

## Design and reliability

AI understands documents; deterministic code owns intervals, matrices, arithmetic,
verification and PDF writing. The existing Controller is a gated workflow; the
model-selected action policy is future A work. Model prose is neither a calculation
trace nor approval. Rules are versioned case data, not universal policy inferred
from one sample district, land-use category or date.

Preserve original text, confidence, observed values, source version/hash, one-based
page and coordinates. Missing, contradictory or low-confidence critical evidence
requires review. Explicit human confirmation preserves scores and remains separate
from exact-material approval. A new revision cannot silently reuse old approval.
Case facts/cells come from selected forms; rules/applicability come from selected
criteria. Registered references support procedures, not substitute case facts.

The writer produces a new file from a trusted original template and configured
field coordinates. Multiple comparison contexts are supported by case review, but
the existing writer accepts only one context. A multiple-context output request
remains `verified/unsupported_contexts`; it never writes the first comparison as a
partial report. End-to-end immutable source snapshots remain future work.

## Quick start: real local integration with synthetic inputs

Use Python 3.11+ and Linux/macOS for the existing local reviewer operations.
Run from this checkout; each fixture command requires a fresh output directory.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
export PYTHONPATH="$PWD/src"
python scripts/local_service_smoke.py
python scripts/local_service_fixture.py --directory artifacts/local-demo
export APPRAISAL_LOCAL_CONFIG="$PWD/artifacts/local-demo/config.json"
uvicorn appraisal_review.local_service:app_from_environment --factory --host 127.0.0.1 --port 8000
```

In another terminal with the same environment:

```bash
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/v1/reviews -H 'Content-Type: application/json' --data-binary @artifacts/local-demo/request.json
python -m appraisal_review.local_service invoke --config artifacts/local-demo/config.json --request artifacts/local-demo/request.json
python -m appraisal_review.local_service run --config artifacts/local-demo/config.json --request artifacts/local-demo/request-write.json
python -m appraisal_review.local_service run --config artifacts/local-demo/config-needs-review.json --request artifacts/local-demo/request-write.json
```

The write command creates `artifacts/local-demo/output/completed.pdf`; the blocked
command creates no new file. A repeat successful write to the same destination is
rejected by default. Use a fresh directory/output for each run. The smoke command
uses disposable files and also checks actual localhost HTTP, invocation parity,
validation/configuration failures, source preservation and reopened PDF manifests.

These fixtures generate their own synthetic PDFs and isolated test receipt, use
an explicit redistributable Latin test font and keep raw confidence at zero through
explicit test confirmation. They do not approve user documents or call a model.
For your own allowlisted documents, prepared material and reviewed template/font,
follow the [local service runbook](docs/local-service-runbook.md). No writer/font
is required for review-only use. No missing configuration falls back to fixtures.

## Public and reserved boundaries

GET /health, POST /v1/validate, POST /v1/reviews and the invocation adapter keep their
existing JSON and error shapes. The service-v1 result is a separate local `run`
facade. Actual statuses remain verified/not_requested, verified/unavailable,
verified/simulated and completed/written. A successful execution may need human
review and retain findings without a PDF.

The durable review-jobs group is now mounted: POST /v1/review-jobs accepts a
submission with 202 once the job and its outbox entry are persisted, and
GET /v1/review-jobs/{job_id}, GET /v1/review-jobs/{job_id}/result and
POST /v1/review-jobs/{job_id}/cancel serve the same principal. It runs over an
injected store, and only an in-memory reference adapter exists: state does not
survive the process, and there is no DynamoDB, queue, dead-letter queue or alarm
yet. Without a configured store and authenticator the routes answer
capability_unavailable rather than a fabricated acceptance. See
[ADR 0014](docs/adr/0014-durable-review-jobs.md).

The [shared service contract](docs/service-contracts.md),
[schema](schemas/service-v1.json) and [fixtures](examples/service-v1/README.md)
are the common handoff for A–E. Document/task/download API groups remain reserved
contracts; no fake-success endpoints are mounted. The local OS reviewer is not
an Internet authentication service.

## Verification

```bash
ruff check .
ruff format --check .
mypy src
pytest --cov=appraisal_review --cov-config=pyproject.toml --cov-report=term-missing
python -m pytest cloud_tests
python scripts/http_smoke.py
python scripts/local_service_smoke.py
python -m pip install -r cloud_tests/requirements-dev.txt
cfn-lint cloud_tests/image-stack.json cloud_tests/runtime-stack.json
python scripts/check_submission.py --base origin/main
```

Existing fake-writer demonstrations remain available through
`python -m appraisal_review.demo completed`: this compatibility scenario is
`verified/simulated`, creates no PDF and is separate from the real local smoke.

## Architecture and next work

See [current and target architecture](docs/architecture.md),
[A–E ownership and M0–M3](docs/mvp-plan.md), [PDF contract](docs/pdf-contract.md),
[document preparation](docs/member-a-runbook.md), [cloud smoke](cloud_tests/README.md)
and [competition requirements](docs/competition-requirements.md).

Target AWS integration uses authorized document IDs, controlled S3 transfer,
durable jobs/tasks/outbox, SQS, dispatcher, Runtime, Bedrock and fenced manifest
publication. Human waiting persists a task and ends the attempt; a response starts
a newly authorized revision/run. Runtime is not a state store, CloudWatch is not
domain audit, and Bedrock cannot bypass deterministic gates. Current CDK and smoke
files are preparation, not evidence of deployed resources.

Real model trials require designated profile/SSO, Region, expected account/role and
model access. M0 needs no AWS call. Formal CJK fonts/templates, full goldens and
complete-case review require E's controlled inputs and human acceptance. No real
source PDFs, OCR dumps, receipts, keys, generated PDFs or private URIs enter Git.
Dependency licensing and sensitive inputs remain governed by
[data handling](docs/data-handling.md).
