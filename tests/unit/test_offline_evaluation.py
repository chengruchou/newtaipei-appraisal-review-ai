"""Offline evaluation claims are bounded by actual synthetic execution evidence."""

import asyncio
import json
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE = runpy.run_path(str(ROOT / "scripts/evaluate_controlled_workflow.py"))


@pytest.fixture(scope="module")
def report():
    return asyncio.run(MODULE["evaluate"]())


def test_identical_inputs_versions_and_budgets_for_each_comparison(report):
    assert report["case_count"] == 2
    assert len(report["runs"]) == 4
    for case_id, _ in MODULE["CASES"]:
        baseline, injected = [row for row in report["runs"] if row["case_id"] == case_id]
        assert baseline["input_binding"] == injected["input_binding"]
        assert baseline["local_scenario_passed"] and injected["local_scenario_passed"]
        assert baseline["before_unresolved_ids"] == injected["before_unresolved_ids"]
        assert baseline["after_unresolved_ids"] == injected["after_unresolved_ids"]
        assert baseline["input_binding"]["budget"]["model_calls_remaining"] == 3
    assert (
        report["runs"][0]["input_binding"]["revision"]["reference"]["material_digest"]
        != report["runs"][2]["input_binding"]["revision"]["reference"]["material_digest"]
    )


def test_actual_tools_corrections_and_call_accounting(report):
    for row in report["runs"]:
        assert row["tool_calls"] == len(row["trace"]) == 2
        assert row["reentry_review_calls"] == 1
        assert row["scripted_human_responses"] == 1
        assert row["new_run_created"] and row["task_count"] == 2
        assert row["after_unresolved_ids"]
        assert row["citation_occurrences"] > 0
        assert row["citation_resolution_rate"] == 1
        assert all(event["result_digest"] for event in row["trace"])
        assert row["trace"][1]["parents"] == [row["trace"][0]["event_id"]]
        assert row["injected_client_calls"] == (2 if row["mode"] == "injected-model" else 0)
        assert (row["model_id"] is None) == (row["mode"] == "deterministic")
        assert row["latency_ms"] is None and row["provider_cost"] is None


def test_unsafe_rate_has_explicit_denominator_and_zero_tool_effects(report):
    assert report["unsafe_rejection"] == {"rejected_without_tools": 2, "attempted": 2, "rate": 1}
    assert {row["fault"] for row in report["adversarial_probes"]} == {"malformed", "out-of-set"}
    for row in report["adversarial_probes"]:
        assert row["unsafe_rejected_without_tools"]
        assert row["tool_calls"] == row["reentry_review_calls"] == row["task_count"] == 0
        assert row["citation_resolution_rate"] is None
        assert row["injected_client_calls"] == 1
        assert row["selection_failures"] and not row["trace"]


def test_report_never_claims_live_human_or_full_case_acceptance(report):
    assert report["scope"] == "synthetic-injected-only"
    assert report["live_model_calls"] == report["human_participants"] == 0
    assert report["phase10_acceptance"] == "deferred"
    assert report["full_case_accuracy"] is None
    assert report["pdf_output"] == "not_exercised"
    assert report["formal_cjk_font_coverage"] == "not_evaluated"
    assert report["multiple_context_output"] == "not_evaluated"
    serialized = json.dumps(report)
    for forbidden in (
        "file:///",
        "storage_uri",
        "raw_text",
        "excerpt",
        "rationale",
        "invalid synthetic JSON",
    ):
        assert forbidden not in serialized


def test_report_is_reproducible_and_requires_no_cloud_discovery(report, monkeypatch):
    import boto3

    def forbidden(*args, **kwargs):
        raise AssertionError("Offline evaluation must not discover an SDK client or credentials")

    monkeypatch.setattr(boto3, "client", forbidden)
    monkeypatch.setattr(boto3, "Session", forbidden)
    assert asyncio.run(MODULE["evaluate"]()) == report


def test_cli_emits_only_parseable_sanitized_report():
    result = subprocess.run(
        [sys.executable, "scripts/evaluate_controlled_workflow.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    report = json.loads(result.stdout)
    assert all(row["local_scenario_passed"] for row in report["runs"])


def test_cli_rejects_live_mode_instead_of_discovering_an_environment():
    result = subprocess.run(
        [sys.executable, "scripts/evaluate_controlled_workflow.py", "--live"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 2
    assert result.stdout == ""


@pytest.mark.parametrize("scenario_pass, rejection_rate", [(False, 1), (True, 0)])
def test_cli_fails_when_local_acceptance_or_rejection_fails(
    monkeypatch, capsys, scenario_pass, rejection_rate
):
    async def failed_report():
        return {
            "runs": [{"local_scenario_passed": scenario_pass}],
            "unsafe_rejection": {"rate": rejection_rate},
        }

    monkeypatch.setattr(sys, "argv", ["evaluate_controlled_workflow.py"])
    monkeypatch.setitem(MODULE["main"].__globals__, "evaluate", failed_report)
    assert MODULE["main"]() == 1
    assert json.loads(capsys.readouterr().out)["runs"]
