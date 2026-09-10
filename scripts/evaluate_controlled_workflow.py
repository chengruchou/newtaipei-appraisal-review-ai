"""Offline synthetic evaluation only: injected responses, no client discovery or AWS calls."""

import argparse
import asyncio
import json
import runpy
from itertools import count
from pathlib import Path
from uuid import UUID

from appraisal_review.adapters.aws.action_selector import BedrockActionSelector, ModelSelectorConfig
from appraisal_review.adapters.local.synthetic import synthetic_material
from appraisal_review.domain.review_contracts import content_digest
from appraisal_review.domain.service_contracts import Budget

CASES = (("correction", None), ("low-confidence-correction", 0.4))


class InjectedEvaluationClient:
    """Fixed structured-output simulator. This is not a language model or benchmark."""

    def __init__(self, fault=None):
        self.fault = fault
        self.calls = 0

    def converse(self, **kwargs):
        self.calls += 1
        data = json.loads(kwargs["messages"][0]["content"][0]["text"])
        action = data["allowed_actions"]["actions"][0]
        revision = data["snapshot"]["revision"]
        if action["action"] == "deterministic_review":
            arguments = {
                "kind": action["action"],
                "revision": revision["reference"],
                "rules": revision["rules"],
            }
        else:
            blocker = data["snapshot"]["unresolved_blockers"][0]
            arguments = {
                "kind": action["action"],
                "reason_code": blocker["reason_code"],
                "question": "Review all synthetic findings.",
                "affected_subject_ids": blocker["affected_subject_ids"],
                "evidence": blocker["evidence"],
            }
        payload = {
            "action": action["action"],
            "action_id": action["action_id"],
            "arguments": arguments,
        }
        if self.fault == "out-of-set":
            payload["action_id"] = "not-advertised"
        text = "invalid synthetic JSON" if self.fault == "malformed" else json.dumps(payload)
        return {"stopReason": "end_turn", "output": {"message": {"content": [{"text": text}]}}}


async def evaluate():
    scenario = runpy.run_path(str(Path(__file__).with_name("workbench_fixture.py")))["scenario"]
    rows = []
    probes = []
    budget = Budget(
        steps_remaining=3, model_calls_remaining=3, retries_remaining=0, time_remaining_ms=5000
    )
    for case_id, confidence in CASES:
        for mode in ("deterministic", "injected-model"):
            rows.append(await run_case(scenario, case_id, confidence, mode, budget))
    for fault in ("malformed", "out-of-set"):
        probes.append(await run_case(scenario, "correction", None, "injected-model", budget, fault))
    for case_id, _ in CASES:
        pair = [row for row in rows if row["case_id"] == case_id]
        if pair[0]["input_binding"] != pair[1]["input_binding"]:
            raise AssertionError(
                "Comparison requires identical material, sources, rules and budget"
            )
    rejected = sum(row["unsafe_rejected_without_tools"] is True for row in probes)
    return {
        "report_version": "offline-controlled-evaluation-v1",
        "scope": "synthetic-injected-only",
        "live_model_calls": 0,
        "human_participants": 0,
        "case_count": len(CASES),
        "runs": rows,
        "adversarial_probes": probes,
        "unsafe_rejection": {
            "rejected_without_tools": rejected,
            "attempted": len(probes),
            "rate": rejected / len(probes),
        },
        "phase10_acceptance": "deferred",
        "full_case_accuracy": None,
        "provider_cost": None,
        "live_latency_ms": None,
        "pdf_output": "not_exercised",
        "formal_cjk_font_coverage": "not_evaluated",
        "multiple_context_output": "not_evaluated",
        "limitations": [
            "Two related synthetic single-factor cases, not representative sampling",
            "Injected structured responses are not model planning or live quality evidence",
            "Correction and actor responses are scripted, not independent human acceptance",
            "No browser, raw-document extraction, PDF writing, deployment or recovery acceptance",
        ],
    }


async def run_case(scenario, case_id, confidence, mode, budget, fault=None):
    client = InjectedEvaluationClient(fault)
    config = ModelSelectorConfig(model_id="synthetic-evaluation-model", attempts=1)
    ids = count(2000000)
    selector = (
        BedrockActionSelector(client, config, proposal_id_factory=lambda: UUID(int=next(ids)))
        if mode == "injected-model"
        else None
    )
    records = await scenario(
        selector=selector, budget=budget, confidence=confidence, evaluation=True
    )
    result = records["evaluation-result"]
    before = records.get("workbench-before-review")
    after = records.get("workbench-after-review")
    response = records.get("workbench-response")
    tasks = [
        records[name] for name in ("workbench-task", "workbench-approval-task") if name in records
    ]
    citations = [citation for task in tasks for citation in task.evidence]
    registry = synthetic_material().policy.registry
    resolved = sum(registry.resolves(citation) for citation in citations)
    expected_wait = result.termination.value == "waiting_for_human"
    event_calls = sum(event.budget_consumed.model_calls for event in result.events) + sum(
        event.budget_consumed.model_calls for event in result.selection_failures
    )
    if event_calls != client.calls:
        raise AssertionError("Actual injected calls must reconcile with trace accounting")
    return {
        "case_id": case_id,
        "mode": mode,
        "fault": fault,
        "input_binding": {
            "revision": records["evaluation-revision"].model_dump(mode="json"),
            "budget": budget.model_dump(mode="json"),
        },
        "model_id": config.model_id if selector else None,
        "prompt_version": config.prompt_version if selector else None,
        "termination": result.termination.value,
        "local_scenario_passed": expected_wait
        and response is not None
        and after is not None
        and after.status.value == "needs_review"
        and response.revision.parent == result.run.revision
        and response.revision.changes[0].original.value.value == 9
        and response.revision.changes[0].corrected.value.value == 10
        and response.revision.changes[0].corrected.confidence
        == response.revision.changes[0].original.confidence
        and bool(after.coverage.missing)
        and resolved == len(citations),
        "workflow_completeness": "correction-reentry-needs-review"
        if response
        else "stopped-before-review",
        "task_count": len(tasks),
        "scripted_human_responses": int(response is not None),
        "new_run_created": response is not None and response.next_run is not None,
        "correction_link": {
            "response_event_id": str(response.event_id),
            "previous_run_id": str(result.run.run_id),
            "next_run_id": str(response.next_run.run_id),
            "new_material_digest": response.revision.reference.material_digest,
            "recomputed_result_digest": content_digest(after),
        }
        if response is not None and response.next_run is not None and after is not None
        else None,
        "before_unresolved_ids": [f.id for f in before.findings if f.status != "verified"]
        if before
        else [],
        "after_unresolved_ids": [f.id for f in after.findings if f.status != "verified"]
        if after
        else [],
        "citation_occurrences": len(citations),
        "resolved_citation_occurrences": resolved,
        "citation_resolution_rate": resolved / len(citations) if citations else None,
        "source_semantic_accuracy": None,
        "injected_client_calls": client.calls,
        "live_model_calls": 0,
        "tool_calls": sum(event.executed_action is not None for event in result.events),
        "reentry_review_calls": int(after is not None),
        "input_tokens": None,
        "output_tokens": None,
        "provider_cost": None,
        "latency_ms": None,
        "unsafe_rejected_without_tools": result.termination.value == "permanent_failure"
        and not result.events
        and bool(result.selection_failures)
        if fault
        else None,
        "trace": [
            {
                "event_id": str(e.event_id),
                "parents": [str(p) for p in e.parent_event_ids],
                "action": e.executed_action.value if e.executed_action else None,
                "disposition": e.disposition,
                "result_digest": e.tool_result.result_digest if e.tool_result else None,
            }
            for e in result.events
        ],
        "selection_failures": [
            {"event_id": str(e.event_id), "code": e.error_code, "attempts": e.attempt_count}
            for e in result.selection_failures
        ],
    }


def main():
    argparse.ArgumentParser(
        description="Offline synthetic evaluation only; no live mode."
    ).parse_args()
    report = asyncio.run(evaluate())
    print(json.dumps(report, indent=2, sort_keys=True))
    return (
        0
        if all(row["local_scenario_passed"] for row in report["runs"])
        and report["unsafe_rejection"]["rate"] == 1
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
