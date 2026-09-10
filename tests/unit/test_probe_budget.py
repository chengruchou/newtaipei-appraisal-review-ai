"""Offline monetary admission and partial-page telemetry regression evidence."""

import asyncio
import importlib.util
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import boto3
import pytest
from botocore.stub import Stubber
from test_extraction_contracts import payload
from test_extraction_execution import ledger, run

from appraisal_review.adapters.aws.probe_budget import PricedRuntime, ProbePricing
from appraisal_review.domain.extraction_contracts import PageOutcome
from appraisal_review.ports.document_extraction import ExtractionBoundaryError


def pricing(**changes):
    return ProbePricing(
        model_id="synthetic-model",
        region="us-east-1",
        rates_date=date.today(),
        rates_source="synthetic dated pricing",
        input_per_million_usd=Decimal("3"),
        output_per_million_usd=Decimal("15"),
        maximum_input_tokens=1000,
        **changes,
    )


def test_budget_rejects_insufficient_dollars_before_any_provider():
    policy = pricing()
    assert policy.ceiling(4, 8192, Decimal("1")) == Decimal("0.134880")
    with pytest.raises(ValueError, match="exceeds"):
        policy.ceiling(4, 8192, Decimal("0.000001"))
    old = policy.model_copy(update={"rates_date": date.today() - timedelta(days=31)})
    with pytest.raises(ValueError, match="Dated"):
        old.ceiling(4, 8192, Decimal("1"))


def test_priced_probe_uses_valid_sdk_count_and_converse_shapes():
    client = boto3.client(
        "bedrock-runtime",
        region_name="us-east-1",
        aws_access_key_id="synthetic",
        aws_secret_access_key="synthetic",
    )
    system = [{"text": "fixed system"}]
    messages = [{"role": "user", "content": [{"text": "synthetic probe"}]}]
    request = {
        "modelId": "synthetic-model",
        "system": system,
        "messages": messages,
        "inferenceConfig": {"maxTokens": 1024},
    }
    with Stubber(client) as stubber:
        stubber.add_response(
            "count_tokens",
            {"inputTokens": 10},
            {
                "modelId": "synthetic-model",
                "input": {"converse": {"system": system, "messages": messages}},
            },
        )
        stubber.add_response(
            "converse",
            {
                "stopReason": "end_turn",
                "output": {"message": {"role": "assistant", "content": [{"text": "{}"}]}},
                "usage": {"inputTokens": 10, "outputTokens": 2, "totalTokens": 12},
                "metrics": {"latencyMs": 1},
            },
            request,
        )
        result = PricedRuntime(client, pricing(), lambda: True).converse(**request)
        assert result["usage"]["inputTokens"] == 10
        stubber.assert_no_pending_responses()


@pytest.mark.parametrize("tokens", [1001, -1, True, None, "500"])
def test_exact_count_tokens_gate_prevents_over_budget_converse(tokens):
    client = Mock()
    client.count_tokens.return_value = {"inputTokens": tokens}
    wrapped = PricedRuntime(client, pricing(), lambda: True)
    with pytest.raises(ExtractionBoundaryError, match="budget_exhausted"):
        wrapped.converse(
            modelId="synthetic-model",
            system=[{"text": "test"}],
            messages=[{"role": "user", "content": [{"text": "synthetic"}]}],
            inferenceConfig={"maxTokens": 1024},
        )
    client.converse.assert_not_called()
    assert wrapped.count_calls == 1 and wrapped.converse_calls == 0


def test_priced_attempt_counts_exact_images_before_converse_without_retries():
    client = Mock()
    client.count_tokens.return_value = {"inputTokens": 1000}
    client.converse.return_value = {"synthetic": "response"}
    wrapped = PricedRuntime(client, pricing(), lambda: True)
    system, messages = (
        [{"text": "fixed system"}],
        [{"role": "user", "content": [{"text": "fixed"}]}],
    )
    assert wrapped.converse(
        modelId="synthetic-model",
        system=system,
        messages=messages,
        inferenceConfig={"maxTokens": 1024},
    ) == {"synthetic": "response"}
    client.count_tokens.assert_called_once_with(
        modelId="synthetic-model", input={"converse": {"system": system, "messages": messages}}
    )
    assert wrapped.count_calls == wrapped.converse_calls == 1


def test_late_token_count_cannot_start_a_paid_call_after_timeout():
    state = {"active": True}
    client = Mock()

    def count(**kwargs):
        state["active"] = False
        return {"inputTokens": 10}

    client.count_tokens.side_effect = count
    wrapped = PricedRuntime(client, pricing(), lambda: state["active"])
    with pytest.raises(ExtractionBoundaryError, match="budget_exhausted"):
        wrapped.converse(
            modelId="synthetic-model", system=[], messages=[], inferenceConfig={"maxTokens": 1024}
        )
    client.converse.assert_not_called()
    assert wrapped.count_calls == 1 and wrapped.converse_calls == 0


def test_priced_boundary_failure_preserves_code_through_execution():
    call = Mock(side_effect=ExtractionBoundaryError("budget_exhausted"))
    result = asyncio.run(run(call, ledger()))
    assert result.code == "budget_exhausted"
    assert len(result.attempts) == 1
    assert result.attempts[0].input_tokens is None
    call.assert_called_once()


@pytest.mark.parametrize("later_failure", [False, True], ids=["two-pages", "later-failure"])
@pytest.mark.parametrize(
    "input_tokens,output_tokens,expected_cost",
    [
        (100, 20, "0.0006"),
        (100, None, "0.0003"),
        (None, 20, "0.0003"),
        (None, None, "0"),
        (0, 0, "0"),
    ],
    ids=["complete", "input-only", "output-only", "unknown", "zero"],
)
def test_probe_retains_each_known_cost_component(
    monkeypatch, tmp_path, input_tokens, output_tokens, expected_cost, later_failure
):
    path = Path(__file__).resolve().parents[2] / "cloud_tests/extraction_smoke.py"
    spec = importlib.util.spec_from_file_location("synthetic_probe_regression", path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    data = payload("candidate.json")
    data["telemetry"]["attempts"][0].update(input_tokens=input_tokens, output_tokens=output_tokens)
    first = PageOutcome.model_validate(data)
    second = first.request.model_copy(update={"page": 2})
    monkeypatch.setattr(module, "synthetic_source", lambda _: (None, None, (first.request, second)))
    monkeypatch.setattr(module, "DocumentSnapshotResolver", lambda _: None)
    monkeypatch.setattr(module, "BedrockSnapshotBackend", lambda **_: None)
    second_outcome = (
        ExtractionBoundaryError("source_changed")
        if later_failure
        else first.model_copy(update={"request": second})
    )
    service = SimpleNamespace(extract=AsyncMock(side_effect=[first, second_outcome]))
    monkeypatch.setattr(module, "AuthorizedExtractionService", lambda **_: service)
    report = asyncio.run(
        module.probe(
            "synthetic-profile",
            SimpleNamespace(pricing=pricing(), policy=None, extraction=None, budget=None),
            tmp_path,
        )
    )
    assert report["all_candidates"] is (not later_failure)
    assert report["pages"][0]["attempts"] == [
        a.model_dump(mode="json") for a in first.telemetry.attempts
    ]
    if later_failure:
        assert report["pages"][1] == {
            "page": 2,
            "status": "failed",
            "failure": "source_changed",
            "attempts": None,
        }
    else:
        assert report["pages"][1]["attempts"] == report["pages"][0]["attempts"]
    expected = Decimal(expected_cost) * (1 if later_failure else 2)
    assert Decimal(report["known_token_cost_usd"]) == expected
    if later_failure or input_tokens is None or output_tokens is None:
        assert report["estimated_token_cost_usd"] is None
    else:
        assert Decimal(report["estimated_token_cost_usd"]) == expected
    assert report["scheduled_pages"] == 2 and report["not_run_pages"] == 0
