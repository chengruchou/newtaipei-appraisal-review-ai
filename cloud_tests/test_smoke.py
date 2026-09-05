import io
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from cloud_tests.smoke import exercise, resolve_runtime, verify_identity

# Pure synthetic account string; never selects or accesses a real AWS account.
ACCOUNT = "000000000000"
REGION = "test-region"
SMOKE_ID = "test0001"
ARN = (
    f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT}:runtime/"
    f"appraisal_review_smoke_{SMOKE_ID}-example"
)


def test_identity_and_tagged_scope_must_match_before_invocation() -> None:
    identity = {"Account": ACCOUNT, "Arn": f"arn:aws:sts::{ACCOUNT}:assumed-role/TestRole/session"}
    verify_identity(identity, ACCOUNT, "TestRole")
    with pytest.raises(ValueError):
        verify_identity(identity, ACCOUNT, "UnrelatedRole")
    session = Mock()
    stack = {
        "Tags": [
            {"Key": "Project", "Value": "appraisal-review"},
            {"Key": "SmokeId", "Value": SMOKE_ID},
        ],
        "Outputs": [{"OutputKey": "RuntimeArn", "OutputValue": ARN}],
    }
    session.client.return_value.describe_stacks.return_value = {"Stacks": [stack]}
    assert resolve_runtime(session, SMOKE_ID, REGION, ACCOUNT) == ARN
    stack["Tags"] = []
    with pytest.raises(ValueError, match="not tagged"):
        resolve_runtime(session, SMOKE_ID, REGION, ACCOUNT)


def test_live_client_requires_terminal_result_and_preserves_synthetic_warning() -> None:
    client = Mock()
    seen = {}
    streams = []

    def invoke(**kwargs):
        payload = json.loads(kwargs["payload"])
        key = kwargs["runtimeSessionId"]
        seen[key] = seen.get(key, 0) + 1
        scenario = payload["scenario"]
        result = {
            "status": "verified" if scenario == "completed" else scenario,
            "output_pdf_uri": None,
        }
        if scenario == "completed":
            result["pdf_result"] = {
                "artifact_created": False,
                "warnings": ["Synthetic PDF writer: no file was created."],
            }
            result["artifact_status"] = "simulated"
        response = {
            "execution_status": "running" if seen[key] < 3 else "succeeded",
            "result": result,
        }
        stream = io.BytesIO(json.dumps(response).encode())
        streams.append(stream)
        return {"response": stream}

    client.invoke_agent_runtime.side_effect = invoke
    output = exercise(client, ARN, poll_limit=1, poll_delay=0)
    assert [item["scenario"] for item in output] == ["verified", "completed", "needs_review"]
    assert len(seen) == 3 and set(seen.values()) == {3}
    assert all(stream.closed for stream in streams)
    client.invoke_agent_runtime.side_effect = lambda **kw: {
        "response": io.BytesIO(b'{"execution_status":"running"}')
    }
    with pytest.raises(TimeoutError):
        exercise(client, ARN, poll_limit=1, poll_delay=0)


def test_templates_are_separate_from_production_data_resources() -> None:
    root = Path(__file__).parent
    templates = [
        json.loads((root / name).read_text()) for name in ("image-stack.json", "runtime-stack.json")
    ]
    types = {
        resource["Type"] for template in templates for resource in template["Resources"].values()
    }
    assert types == {"AWS::ECR::Repository", "AWS::IAM::Role", "AWS::BedrockAgentCore::Runtime"}
    runtime = templates[1]["Resources"]["Runtime"]["Properties"]
    assert runtime["ProtocolConfiguration"] == "HTTP"
    assert runtime["LifecycleConfiguration"]["MaxLifetime"] == 600
    assert runtime["Tags"]["Project"] == "appraisal-review"
    policies = str(templates[1]["Resources"]["ExecutionRole"])
    assert "bedrock:InvokeModel" not in policies
    assert "s3:" not in policies
