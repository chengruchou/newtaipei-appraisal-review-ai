import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

from appraisal_review.adapters.aws.agentcore.runtime import invoke
from appraisal_review.adapters.local.synthetic import synthetic_request
from appraisal_review.api.app import create_app
from appraisal_review.api.dependencies import get_controller_factory
from appraisal_review.application.bootstrap import build_controller
from appraisal_review.config import Settings
from appraisal_review.domain.factor_models import AgentReviewRequest, AgentReviewRun
from appraisal_review.domain.models import CanonicalCase
from appraisal_review.domain.rule_engine import RuleEngine


def controller_spy() -> Mock:
    controller = Mock()
    controller.review = AsyncMock(
        return_value=AgentReviewRun(case_id="synthetic-case", status="verified")
    )
    return controller


def client_with(controller: Mock) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_controller_factory] = lambda: lambda: controller
    return TestClient(app)


def test_valid_request_invokes_controller_once_and_preserves_none() -> None:
    controller = controller_spy()
    response = client_with(controller).post(
        "/v1/reviews", json=synthetic_request("verified").model_dump(mode="json")
    )
    assert response.status_code == 200
    controller.review.assert_awaited_once()
    assert isinstance(controller.review.call_args.args[0], AgentReviewRequest)
    assert response.json()["status"] == "verified"
    assert response.json()["pdf_result"] is None


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"case_id": "secret-document-text"},
        {**synthetic_request("verified").model_dump(mode="json"), "case_id": "  "},
        {**synthetic_request("verified").model_dump(mode="json"), "unknown": 1},
        {**synthetic_request("verified").model_dump(mode="json"), "case_document_uri": 42},
    ],
)
def test_invalid_body_rejected_before_controller(payload: object) -> None:
    controller = controller_spy()
    response = client_with(controller).post("/v1/reviews", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert "secret-document-text" not in response.text
    controller.review.assert_not_called()


def test_legacy_health_and_actual_validation_behavior() -> None:
    client = TestClient(create_app())
    assert client.get("/health").json() == {"status": "ok"}
    root = Path(__file__).parents[2]
    case = CanonicalCase.model_validate_json(
        (root / "tests/fixtures/canonical_case.json").read_text()
    )
    rules = RuleEngine.from_yaml(root / "configs/rules/demo.yaml").rule_set
    payload = {"case": case.model_dump(mode="json"), "rule_set": rules.model_dump(mode="json")}
    result = client.post("/v1/validate", json=payload)
    assert result.status_code == 200
    assert result.json() == RuleEngine(rules).evaluate(case).model_dump(mode="json")
    assert {finding["status"] for finding in result.json()["findings"]} == {"pass"}
    payload["case"]["fields"]["summary.region_total_adjustment"]["value"] = 4.0
    failed = client.post("/v1/validate", json=payload).json()
    assert failed["findings"][0]["status"] == "fail"
    assert any(finding["status"] == "fail" for finding in failed["findings"])
    assert failed["findings"][0]["expected"] == "2.5"
    assert failed["findings"][0]["actual"] == "4.0"


@pytest.mark.parametrize("scenario", ["verified", "completed", "needs_review"])
def test_http_and_invocation_json_parity(scenario: str) -> None:
    settings = Settings(_env_file=None, runtime_mode="local", synthetic_demo=True)
    request = synthetic_request(scenario).model_dump(mode="json")
    http = TestClient(create_app(settings=settings)).post("/v1/reviews", json=request)
    invoked = asyncio.run(invoke(request, controller_factory=lambda: build_controller(settings)))
    assert http.status_code == 200
    assert http.json() == invoked
    assert AgentReviewRun.model_validate(invoked).status.value == scenario
    assert "pdf_error" in invoked


@pytest.mark.parametrize("payload", [None, {"bad": 1}, {"x": {1, 2}}, {"x": float("nan")}, b"{}"])
def test_invocation_invalid_input_never_constructs_controller(payload: object) -> None:
    factory = Mock()
    result = asyncio.run(invoke(payload, controller_factory=factory))
    assert result["error"]["code"] == "invalid_request"
    factory.assert_not_called()


def test_invocation_calls_controller_once_and_sanitizes_failures() -> None:
    controller = controller_spy()
    payload = synthetic_request("verified").model_dump(mode="json")
    result = asyncio.run(invoke(payload, controller_factory=lambda: controller))
    controller.review.assert_awaited_once()
    assert result["status"] == "verified"
    controller.review.side_effect = RuntimeError("secret-key and private-document")
    http = client_with(controller).post("/v1/reviews", json=payload)
    invoked = asyncio.run(invoke(payload, controller_factory=lambda: controller))
    assert http.status_code == 500
    assert http.json() == invoked
    assert "secret" not in http.text


def test_unconfigured_service_is_explicit_and_invalid_payload_still_returns_422() -> None:
    settings = Settings(_env_file=None, runtime_mode="aws", aws_region="", synthetic_demo=False)
    client = TestClient(create_app(settings=settings))
    request = synthetic_request("verified").model_dump(mode="json")
    result = client.post("/v1/reviews", json=request)
    assert result.status_code == 503
    assert result.json() == asyncio.run(
        invoke(request, controller_factory=lambda: build_controller(settings))
    )
    assert result.json()["error"]["code"] == "missing_aws_configuration"
    assert client.post("/v1/reviews", json={}).status_code == 422
    assert (
        client.post(
            "/v1/reviews", content="{", headers={"content-type": "application/json"}
        ).status_code
        == 422
    )
