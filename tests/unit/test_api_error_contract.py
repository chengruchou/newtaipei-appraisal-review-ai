import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, ValidationError

from appraisal_review.adapters.aws.agentcore.runtime import invoke
from appraisal_review.adapters.local.synthetic import synthetic_request
from appraisal_review.api.app import create_app
from appraisal_review.application.bootstrap import ConfigurationError


def response_validator(client: TestClient, path: str, status: int) -> Draft202012Validator:
    document = client.get("/openapi.json").json()
    schema = document["paths"][path]["post"]["responses"][str(status)]["content"][
        "application/json"
    ]["schema"]
    return Draft202012Validator({**schema, "components": document["components"]})


@pytest.mark.parametrize(
    "request_options",
    [
        {"json": {}},
        {"json": {"case": "private-document-text", "rule_set": []}},
        {"content": '{"private-document-text":', "headers": {"content-type": "application/json"}},
    ],
)
def test_invalid_legacy_input_preserves_detail_and_matches_schema(request_options: dict) -> None:
    app = create_app()
    factory = Mock()
    app.state.controller_factory = factory
    client = TestClient(app)
    response = client.post("/v1/validate", **request_options)
    assert response.status_code == 422
    assert set(response.json()) == {"detail"}
    assert response.json()["detail"]
    for detail in response.json()["detail"]:
        assert set(detail) == {"loc", "type", "msg"}
        assert detail["loc"][0] == "body"
        assert isinstance(detail["msg"], str) and detail["msg"]
    assert "private-document-text" not in response.text
    response_validator(client, "/v1/validate", 422).validate(response.json())
    factory.assert_not_called()
    if request_options == {"json": {}}:
        assert response.json() == {
            "detail": [
                {"type": "missing", "loc": ["body", "case"], "msg": "Field required"},
                {"type": "missing", "loc": ["body", "rule_set"], "msg": "Field required"},
            ]
        }


@pytest.mark.parametrize("failure", ["invalid", "configuration", "unexpected_factory", "execution"])
def test_review_error_schema_matches_http_and_invocation(failure: str) -> None:
    payload = synthetic_request("verified").model_dump(mode="json")
    controller = Mock(review=AsyncMock(side_effect=RuntimeError("private-document-text")))
    factory = Mock(return_value=controller)
    if failure == "invalid":
        payload = {"case_id": "private-document-text"}
        status, code = 422, "invalid_request"
    elif failure == "configuration":
        factory.side_effect = ConfigurationError("missing_adapters")
        status, code = 503, "missing_adapters"
    elif failure == "unexpected_factory":
        factory.side_effect = RuntimeError("private-document-text")
        status, code = 503, "invalid_configuration"
    else:
        status, code = 500, "review_execution_failed"
    app = create_app()
    app.state.controller_factory = factory
    client = TestClient(app)
    response = client.post("/v1/reviews", json=payload)
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert "private-document-text" not in response.text
    assert factory.call_count == (0 if failure == "invalid" else 1)
    assert controller.review.await_count == (1 if failure == "execution" else 0)
    assert response.json() == asyncio.run(invoke(payload, controller_factory=factory))
    validator = response_validator(client, "/v1/reviews", status)
    validator.validate(response.json())
    with pytest.raises(ValidationError):
        validator.validate({"detail": []})
    with pytest.raises(ValidationError):
        validator.validate({"error": {"code": code}})


def test_malformed_json_and_mounted_review_use_declared_envelope() -> None:
    app = create_app()
    factory = Mock()
    app.state.controller_factory = factory
    outer = FastAPI()
    outer.mount("/service", app)
    client = TestClient(outer)
    response = client.post(
        "/service/v1/reviews", content="{", headers={"content-type": "application/json"}
    )
    assert response.status_code == 422
    assert response.json() == {
        "error": {"code": "invalid_request", "message": "Invalid review request."}
    }
    factory.assert_not_called()
    direct_client = TestClient(app)
    response_validator(direct_client, "/v1/reviews", 422).validate(response.json())
    schema = direct_client.get("/openapi.json").json()
    for status in ("422", "503", "500"):
        assert schema["paths"]["/v1/reviews"]["post"]["responses"][status]["content"][
            "application/json"
        ]["schema"] == {"$ref": "#/components/schemas/EntryProblemResponse"}
    legacy = schema["paths"]["/v1/validate"]["post"]["responses"]["422"]
    assert legacy["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/HTTPValidationError"
    }
