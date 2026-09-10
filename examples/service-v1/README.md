# Service v1 consumer fixtures

These illustrative synthetic records support C's workbench and A/B/D/E integration
planning. They are not responses from mounted task/job endpoints, valid approvals,
actual model decisions or downloadable artifacts. Written-result hashes are
placeholders; real PDF/manifest evidence comes from scripts/local_service_smoke.py.
That smoke's output/manifest.pdf is the second actual PDF, described by artifacts[0]
in its result-written.json ServiceResult. The enclosing run carries case/revision/
material/run identity. Its smoke-report.json maps both PDFs to their producers;
it is diagnostic evidence, not the public manifest schema.

index.json maps filenames to model names in schemas/service-v1.json under $defs.
Validate against that exact definition. Python models are authoritative; regenerate
with `PYTHONPATH=src python scripts/export_service_contracts.py`. Tests enforce
exact export equality, schema validity and roundtrip serialization.

Use task/response for exact task/revision/side binding, revision for source/rule
identity, value for original blank versus proposed value, proposal/decision-rejected
for policy state, and result-needs-review/result-written for distinct execution,
business and artifact state. result-source-binding-failed shows a preflight
rejection with succeeded execution, failed business status, empty findings, null
problem and a structured verification diagnostic. job-acceptance is the 202 body,
whose job_status is pinned to queued because acceptance can never advertise progress;
job-status-queued, job-status-waiting and job-status-failed show the durable status
view in progress, awaiting a reviewer with an open task, and terminally failed with a
sanitized problem. None of the four carries findings, a storage URI or a lease owner:
the control plane keeps identifiers, versions, digests, statuses and counts only.
Consume the updated service-v1
schema and result fixtures together; extra-forbid consumers of the earlier PR #20
draft must adopt the added verification field. No fixture exposes a storage URI
or grants authority.
See [service contracts](../../docs/service-contracts.md) and
[local runbook](../../docs/local-service-runbook.md) before integration.
