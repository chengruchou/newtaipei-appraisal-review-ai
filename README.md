# Agentic Real Estate Valuation Reviewer

An evidence-grounded valuation case review system for the New Taipei City
competition. Document and model adapters propose facts and rules; deterministic
code calculates grades, correction rates and totals, verifies evidence, and gates
PDF output. Reviewers inspect findings and explicitly authorize exact material.
PDF filling is one output of case review.

**Current delivery: [PR #45](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/45)
is merged. `main` at `d148422a` is the local validation baseline, with the same
source tree as reviewed head `fd22e683`.** Core browser/API flows passed, and the
final delivery reports one complete local OCR restoration and download. Repeated
OCR reliability, hosted CI, image security, AWS deployment, live model quality
and formal business acceptance remain open. [Project progress](docs/project-progress.md)
records current status; [validation evidence](docs/local-validation-record.md)
separates checkpoints, and [the implementation backlog](docs/implementation-backlog.md)
tracks remaining work.

The current follow-up candidate adds per-model routing snapshots, bounded local
restoration diagnostics and an explicit [local validation stack](docs/local-validation-stack.md).
These additions require their own exact-commit validation; the historical
baseline above is not acceptance of the follow-up image or OCR reliability.

## Deploy and run with Docker

This branch packages the reviewer workbench as containers. Everything below runs
on one machine with Docker alone: no Python, Node, AWS account or model
credential, and no step calls a paid API or reaches a real case document.

**Requirements.** Docker Engine with the Compose plugin; check with
`docker compose version`. The first build pulls `python:3.12-slim` and
`node:22-bookworm-slim` and takes a few minutes.

### 1. Get the branch and start it

```sh
git clone https://github.com/chengruchou/newtaipei-appraisal-review-ai.git
cd newtaipei-appraisal-review-ai
git checkout feat/docker-deployment
docker compose up -d --build
```

The workbench is the default service, so no profile flag is needed. Wait for it
to report healthy:

```sh
docker compose ps
# workbench   Up (healthy)   127.0.0.1:4174->8080/tcp
```

### 2. Read the sign-in manifest

On first start the launcher builds a synthetic workspace inside the container's
volume and issues its own session token. Print it:

```sh
docker compose exec workbench workbench-entrypoint fixture
```

```json
{
  "session_token": "rk_...",
  "empty_job_id": "...",
  "completed_job_id": "...",
  "tasks": { "confirm": "...", "correct": "...", "reject": "..." }
}
```

That manifest is local authentication configuration for synthetic fixtures. Keep
it out of Git and out of any evidence report.

### 3. Sign in and open a job

Open http://127.0.0.1:4174 and paste `session_token` on the sign-in screen. Then
paste a job identifier:

- `completed_job_id` opens a finished job: status, findings with their cited
  source regions, independent verification and a PDF download that is
  reauthorized and hash-checked on every request.
- `empty_job_id` opens a job with no open tasks.
- The `tasks` identifiers open individual review questions directly at
  `/tasks/<id>`, each with its evidence and response form.

There is no job list and no upload or create flow; the workbench opens
identifiers you supply.

### 4. Confirm it is really serving

```sh
curl -o /dev/null -w '%{http_code}\n' http://127.0.0.1:4174/
curl -o /dev/null -w '%{http_code}\n' \
  http://127.0.0.1:4174/v1/review-jobs/00000000-0000-0000-0000-000000000000
```

The first is `200`. The second is `403`, which is the correct answer: the API
responds through the proxy and refuses an unauthorized read. To exercise it with
the issued token:

```sh
TOKEN=$(docker compose exec -T workbench workbench-entrypoint fixture \
  | sed -n 's/.*"session_token": "\([^"]*\)".*/\1/p')
curl -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:4174/v1/review-jobs/<completed_job_id>
```

### 5. Stop, reset or rebuild

```sh
docker compose stop workbench        # keep the workspace
docker compose down                  # remove the container, keep the volume
docker compose down -v               # discard the workspace and start clean
docker compose up -d --build         # rebuild after changing the tree
```

Response scenarios consume tasks, so the entrypoint reopens durable state rather
than resetting it. Use `down -v` to replay every scenario from the start.

### What this deployment is not

Only the frontend origin is published, on loopback. The API keeps its numeric
loopback bind and authority inside the container and is never reachable from the
host or the Docker network; the frontend is built with no `VITE_API_BASE_URL` and
reaches it through a same-origin proxy.

The images carry synthetic fixtures only. This is not the AWS entry, not
production login, and not business acceptance, and a healthy container is not
evidence of real-case accuracy. The `/privacy` route does not work here: it needs
a separate local bridge plus an operator-owned OCR executable and language assets
pinned by hash, which must be chosen on the host and never baked into an image.

The optional demo and check profiles, the image layout and the reasoning behind
the single-container design are in [the container runbook](deploy/README.md).

## Integrated boundaries

| Boundary | Implemented and integrated locally | Remaining acceptance |
| --- | --- | --- |
| Local privacy | Restricted Origin/session bridge, exact export/mapping readback, two-stage visual OCR review, bounded failure diagnostics and reproducible synthetic scenarios | Repeated OCR reliability; production provisioner, key recovery and desktop distribution |
| Documents and extraction | Authorized immutable snapshots, actual PDF parser, source checks for resumed runs, production SDK adapter with injected model responses in rehearsal | Measured model quality and real cloud authorization |
| Jobs and human tasks | SQLite job/task/revision/outbox/receipt transactions, durable dispatch queue, actual authenticated API | Production identity and cloud transaction composition |
| Controlled actions | Canonical response adapter, trusted allowed actions, persisted run reservations, failed/unknown-effect quarantine | Designated model and operator evaluation |
| PDF and publication | Two-context/eight-field writer, immutable font bytes, reopen, fenced publication, reauthorized download and separate local restored output | Formal assets and business grants; cloud recovery |
| Workbench and Runtime | Canonical client and explicit synthetic local container entry with durable API/worker state and optional host companion | Independent review, platform acceptance, production login, image security and AWS operation |
| Competition controls | Pinned rule/service catalogs, explicit data admission, per-model routing proofs, shared physical-dispatch reservations and conservative budget ledger | Trusted account/profile approval, cross-process deployment and live verification |

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

## Local original KPI1 workbench

The [controlled original workbench](docs/local-original-workbench.md) adds five
views over actual local jobs, source PDFs and canonical human responses, with a
pinned multi-source rule catalog. See [delivery and validation](docs/kpi1-delivery.md)
for real-data scope and remaining gates. It does not issue rule/material approval
or claim a complete report, external-model evaluation or cloud acceptance.

## Run the configured job and review workbench

The [integrated local runbook](docs/integrated-local-runbook.md) provides the
repository-local installation, core service, separate privacy bridge and actual
Chromium commands. Use its fresh synthetic workspace and hash-pinned local OCR
configuration; it makes no AWS or paid model calls. The exact acceptance status
and remaining OCR stability or deployment gates remain in
[project progress](docs/project-progress.md).

The workbench opens existing job IDs and uses a manually supplied session token.
It supports task review, publication/download and local privacy/OCR review; a
production login, job list and general upload/create flow are not implemented.
The default AWS Docker entry has no configured execution worker and returns 503.
Use the separate [local validation stack](docs/local-validation-stack.md) for the
explicit synthetic frontend/API/worker composition. Its host-companion mode
keeps original documents, mappings and restoration authority on the host. This
does not supply production identity, general operator provisioning or an AWS
deployment. See [architecture](docs/architecture.md).

### Containers

The same workbench runs from one container; see
[Deploy and run with Docker](#deploy-and-run-with-docker) above for the steps and
[the container runbook](deploy/README.md) for the image layout and its limits.

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
local core scenarios, the single completed privacy run and open cloud acceptance.
[Architecture](docs/architecture.md) separates the local composition from the AWS
target.

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
