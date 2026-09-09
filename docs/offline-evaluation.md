# Phase 10 offline evaluation preparation

Status: Local preparation only. Designated-model and full-case human acceptance
remain deferred; this report is not Phase 10 completion.

## Run and evidence scope

Run from the repository virtual environment:

```bash
python scripts/evaluate_controlled_workflow.py
pytest tests/unit/test_offline_evaluation.py
```

The command prints a sanitized JSON report to stdout and returns nonzero when its
local scenario checks fail. It offers no live/provider/profile option, creates no
SDK client, discovers no credentials, writes no documents and deploys no resources.
Retain any future protected acceptance evidence outside Git. The generated report
is an offline diagnostic format, not a new public service or evaluation API contract.

The two fixed synthetic cases are a proposed 9 m observation and the same case
with a low-confidence observation (0.4). Each executes the deterministic selector
and BedrockActionSelector with an explicitly injected structured-response simulator.
Both arms use the same material digest, source IDs/versions/hashes, rule references
and budget: 3 steps, 3 model calls, 0 retries, 5000 ms allowance. The deterministic
arm does not consume model calls. The simulator is not a model and always chooses
the advertised branch from its structured input; it proves adapter wiring, not
planning quality or model accuracy.

Each normal run executes real review and human-task tools, records a pause, accepts
a scripted 9-to-10 correction, creates a fresh revision/run and explicitly reruns
the existing case reviewer. The expected outcome remains needs_review because
fact confirmation and exact material approval are still absent. No gate is weakened
to obtain fewer interventions or verified status. Fixture preparation uses a review
to configure finding bindings; this setup operation is not an admitted tool call.

## Metrics and denominators

| Report field | Meaning and limit |
| --- | --- |
| local_scenario_passed | Correct local waiting/correction/reentry behavior, preserved confidence and remaining blockers; not case completion |
| input_binding | Exact revision, source/rule versions and shared starting budget; mismatched arms abort comparison |
| before/after_unresolved_ids | Actual retained findings, allowing inspection of unresolved work rather than counting silence as success |
| citation_resolution_rate | Resolved task citation occurrences divided by all task citation occurrences; repeated occurrences count separately; null when none |
| source_semantic_accuracy | Null: location matching does not independently verify interpretation |
| task_count / scripted_human_responses | Stored task count and accepted scripted correction count, not study participants |
| injected_client_calls / live_model_calls | Trace-reconciled simulator calls versus actual provider calls; live count is zero |
| tool_calls / reentry_review_calls | Admitted executor calls and explicit post-response review, separately counted |
| correction_link / trace | Actual response/run/result digest links and executed event IDs/parents/outcomes, without raw evidence or model text |
| unsafe_rejection | Malformed and out-of-set simulator probes rejected before tools, divided by the two attempted probes; not a general attack rejection estimate |
| latency_ms / provider_cost / token fields | Null, not zero: no provider metrics are measured or invented |
| full_case_accuracy | Null: no representative full cases or independent answer key |

The coordinator uses a fixed test clock for reproducibility, not a wall-clock
performance measurement. The adapter's local simulator timings are not reported as
provider latency. Model/prompt IDs identify the injected adapter configuration;
they do not identify a live evaluated model. No raw source text, proposal rationale,
document URI, credentials or signing receipt is emitted by the report.

## Local smoke outcome

The two cases yielded four passing paired runs. Each run executed two tools,
created two tasks, accepted one scripted correction and performed one explicit
reentry review. Deterministic runs used zero simulator calls; injected-model runs
used two each. Both adversarial probes were rejected before tool execution (2/2).
All four runs retained unresolved findings and none claimed completed artifacts.
These counts describe only this small, closely related synthetic corpus.

## Required future acceptance gates

Before any live work, the user must explicitly designate approved account/profile,
Region, expected role, model access, source material and independent reviewers.
Never request secret keys in chat or commit them. Establish bounded call/cost limits,
privacy-approved inputs and the exact authorized deployment/test scope separately.

Use the [acceptance report template](templates/designated-model-acceptance.md) to
record actual environment/model/prompt versions, independent answer keys, measured
cost/latency and human correction outcomes. None of those fields is supplied by this
offline result. Full-case evaluation must include sample/participant limitations,
formal CJK template/font coverage and unsupported multiple-context PDF output.

Durable recovery and Runtime acceptance remain with #24/#28/#29 and cloud owners;
real browser acceptance remains with #24/#25. No S3, Runtime, deployment, browser,
PDF-writing or process-restart claim follows from this harness. Phase 10 and full
Issue 17 acceptance remain incomplete.
