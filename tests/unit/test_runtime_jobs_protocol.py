"""Offline wire and SDK-double evidence; never presented as deployed Runtime acceptance."""

import asyncio
import io
import json
from unittest.mock import Mock
from uuid import uuid4

import boto3
import pytest
from botocore.client import BaseClient
from fastapi.testclient import TestClient

from appraisal_review.adapters.aws import runtime_jobs
from appraisal_review.adapters.aws.runtime_app import create_app
from appraisal_review.adapters.aws.runtime_jobs import (
    RuntimeDispatch,
    SQSDispatchSender,
    bridge_record,
)
from appraisal_review.application.outbox import TransientDispatchError


def dispatch():
    return RuntimeDispatch(
        job_id=uuid4(), run_id=uuid4(), outbox_seq=1, dispatch_token=uuid4(), enqueued_at=100
    )


def test_reference_only_queue_rejects_embedded_principal_and_paths():
    for field in ("principal", "source_uri", "approval", "result"):
        data = dispatch().model_dump(mode="json") | {field: "PRIVATE-PATH-CANARY"}
        client = TestClient(create_app())
        response = client.post("/invocations", json=data)
        assert response.status_code == 422
        assert "PRIVATE-PATH-CANARY" not in response.text
        assert response.json()["code"] == "invalid_request"


def test_unconfigured_runtime_is_unready_with_no_sdk_calls(monkeypatch):
    create = Mock(side_effect=AssertionError("AWS must not be contacted"))
    monkeypatch.setattr(runtime_jobs, "workload_client", create)
    with TestClient(create_app()) as client:
        assert client.get("/ping").status_code == 503
        result = client.post("/invocations", json=dispatch().model_dump(mode="json"))
        assert result.status_code == 503
        assert result.json()["code"] == "capability_unavailable"
        schema = client.get("/openapi.json").json()
        for status in ("422", "503", "500"):
            assert schema["paths"]["/invocations"]["post"]["responses"][status]["content"][
                "application/json"
            ]["schema"]["$ref"].endswith("/ServiceProblem")
    create.assert_not_called()


def test_sender_passes_only_strict_references_and_failure_leaves_outbox_owned():
    client = Mock()
    client.send_message.return_value = {"MessageId": "synthetic-message"}
    sender = SQSDispatchSender(
        client, queue_url="https://sqs.us-east-1.amazonaws.com/111122223333/test"
    )
    payload = dispatch()
    asyncio.run(sender(payload.message()))
    assert json.loads(client.send_message.call_args.kwargs["MessageBody"]) == payload.model_dump(
        mode="json"
    )
    client.send_message.side_effect = RuntimeError("PRIVATE-FAILURE")
    with pytest.raises(TransientDispatchError) as error:
        asyncio.run(sender(payload.message()))
    assert "PRIVATE-FAILURE" not in str(error.value)


def result_stream(payload, **changes):
    return io.BytesIO(
        json.dumps(
            {"job_id": str(payload.job_id), "run_id": str(payload.run_id), "outcome": "published"}
            | changes
        ).encode()
    )


@pytest.mark.parametrize("change", [{}, {"job_id": str(uuid4())}, {"outcome": "unknown"}])
def test_runtime_bridge_requires_bounded_same_job_ack_and_closes_stream(change):
    payload = dispatch()
    stream = result_stream(payload, **change)
    client = Mock()
    client.invoke_agent_runtime.return_value = {"statusCode": 200, "response": stream}
    record = {"body": payload.model_dump_json()}
    if change:
        with pytest.raises(ValueError):
            bridge_record(
                record,
                client,
                runtime_arn="arn:aws:bedrock-agentcore:us-east-1:111122223333:runtime/test",
            )
    else:
        bridge_record(
            record,
            client,
            runtime_arn="arn:aws:bedrock-agentcore:us-east-1:111122223333:runtime/test",
        )
        assert client.invoke_agent_runtime.call_args.kwargs["runtimeSessionId"] == str(
            payload.dispatch_token
        )
    assert stream.closed


def test_partial_batch_retries_only_unacknowledged_records(monkeypatch):
    first, second = dispatch(), dispatch()
    client = Mock()
    stream = result_stream(first)
    client.invoke_agent_runtime.side_effect = [
        {"statusCode": 200, "response": stream},
        RuntimeError("PRIVATE-CLOUD-CANARY"),
    ]
    monkeypatch.setattr(runtime_jobs, "workload_client", lambda *a, **kw: client)
    monkeypatch.setenv(
        "REVIEW_RUNTIME_ARN", "arn:aws:bedrock-agentcore:us-east-1:111122223333:runtime/test"
    )
    result = runtime_jobs.worker_handler(
        {
            "Records": [
                {"messageId": str(i), "eventSource": "aws:sqs", "body": p.model_dump_json()}
                for i, p in enumerate((first, second))
            ]
        },
        None,
    )
    assert result == {"batchItemFailures": [{"itemIdentifier": "1"}]}
    assert "PRIVATE" not in str(result)


@pytest.mark.parametrize("override", ["AWS_ENDPOINT_URL", "AWS_ENDPOINT_URL_DYNAMODB"])
def test_workload_client_ignores_configured_endpoint_overrides_without_requests(
    monkeypatch, override
):
    for variable in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE"):
        monkeypatch.delenv(variable, raising=False)
    # Use only synthetic credentials and empty config inputs. Constructing the client
    # must neither inspect workstation credentials nor make a service request.
    monkeypatch.setenv("AWS_CONFIG_FILE", "/dev/null")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", "/dev/null")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("REVIEW_REGION", "us-east-1")
    monkeypatch.setenv(override, "http://127.0.0.1:9")
    request = Mock(side_effect=AssertionError("Client configuration must make no AWS requests"))
    monkeypatch.setattr(BaseClient, "_make_api_call", request)
    endpoint_override_policies = []
    create_client = boto3.Session.client

    def capture_configuration(session, *args, **kwargs):
        endpoint_override_policies.append(
            session._session.get_config_variable("ignore_configured_endpoint_urls")
        )
        return create_client(session, *args, **kwargs)

    monkeypatch.setattr(boto3.Session, "client", capture_configuration)
    client = runtime_jobs.workload_client("dynamodb")
    try:
        assert client.meta.endpoint_url == "https://dynamodb.us-east-1.amazonaws.com"
        # Check the effective CoreSession policy as well as actual endpoint resolution;
        # the SDK does not retain this resolution setting in client.meta.config.
        assert endpoint_override_policies == [True]
        assert client.meta.config.retries["total_max_attempts"] == 1
    finally:
        client.close()
    request.assert_not_called()
