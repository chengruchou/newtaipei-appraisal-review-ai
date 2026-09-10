"""Offline infrastructure contracts and image verification; no AWS or Docker daemon calls."""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import io
import json
import re
import sys
import tarfile
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
INFRA = ROOT / "infra" / "runtime"


class CfnLoader(yaml.SafeLoader):
    pass


def intrinsic(loader: CfnLoader, tag: str, node: yaml.Node) -> dict[str, Any]:
    if isinstance(node, yaml.ScalarNode):
        value: Any = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node)
    else:
        assert isinstance(node, yaml.MappingNode)
        value = loader.construct_mapping(node)
    if tag == "GetAtt" and isinstance(value, str):
        value = value.split(".", 1)
    return {tag if tag == "Ref" else f"Fn::{tag}": value}


CfnLoader.add_multi_constructor("!", intrinsic)


def template(name: str = "runtime-stack.yaml") -> dict[str, Any]:
    result: dict[str, Any] = yaml.load((INFRA / name).read_text(), Loader=CfnLoader)
    return result


def statements(role: str) -> list[dict[str, Any]]:
    policies = template()["Resources"][role]["Properties"]["Policies"]
    result = []
    for policy in policies:
        for statement in policy["PolicyDocument"]["Statement"]:
            result.append(statement["Fn::If"][1] if "Fn::If" in statement else statement)
    return result


def actions(role: str) -> set[str]:
    result: set[str] = set()
    for statement in statements(role):
        action = statement["Action"]
        result.update([action] if isinstance(action, str) else action)
    return result


def load_build_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "runtime_image_builder", ROOT / "scripts/build_runtime_image.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", ["image-stack.yaml", "runtime-stack.yaml"])
def test_templates_are_inline_size_and_context_complete(name: str) -> None:
    data = template(name)
    assert (INFRA / name).stat().st_size < 51200
    assert 0 < len(data["Description"].encode()) <= 1024
    assert set(data["Metadata"]) == {"com.aws.cloudformation.Context"}
    for resource in data["Resources"].values():
        context = resource["Metadata"]["com.aws.cloudformation.Context"]
        assert context["why"] and context["must"]
    assert all("Export" not in output for output in data["Outputs"].values())
    # Native CloudFormation does not accept YAML aliases; require a directly deployable file.
    assert not any(
        isinstance(token, yaml.tokens.AliasToken) for token in yaml.scan((INFRA / name).read_text())
    )


def test_no_implicit_account_region_environment_or_mutable_image() -> None:
    data = template()
    parameters = data["Parameters"]
    for name in (
        "DeploymentPrefix",
        "Environment",
        "Owner",
        "ExpiresOn",
        "RuntimeImageDigest",
        "LambdaImageDigest",
        "SanitizedBucketArn",
        "SanitizedObjectPrefix",
        "ModelResourceArns",
    ):
        assert "Default" not in parameters[name]
    assert parameters["Environment"]["AllowedValues"] == ["sandbox"]
    for name in ("RuntimeImageDigest", "LambdaImageDigest"):
        pattern = parameters[name]["AllowedPattern"]
        assert re.fullmatch(pattern, "sha256:" + "a" * 64)
        assert not re.fullmatch(pattern, "latest")
    assert not re.search(
        r"arn:aws:[a-z-]+:(?:us|eu|ap)-[a-z]+-\d:", (INFRA / "runtime-stack.yaml").read_text()
    )


@pytest.mark.parametrize(
    "bad",
    [
        "*",
        "arn:aws:bedrock:us-west-2::foundation-model/*",
        "arn:aws:bedrock:*::foundation-model/model-v1",
    ],
)
def test_model_permissions_reject_wildcard_parameters(bad: str) -> None:
    pattern = template()["Parameters"]["ModelResourceArns"]["AllowedPattern"]
    assert not re.fullmatch(pattern, bad)
    assert re.fullmatch(pattern, "arn:aws:bedrock:us-west-2::foundation-model/model-v1:0")


def test_durable_table_uses_sparse_keys_only_recovery_index() -> None:
    jobs = template()["Resources"]["Jobs"]
    props = jobs["Properties"]
    assert props["KeySchema"] == [
        {"AttributeName": "pk", "KeyType": "HASH"},
        {"AttributeName": "sk", "KeyType": "RANGE"},
    ]
    assert {d["AttributeName"]: d["AttributeType"] for d in props["AttributeDefinitions"]} == {
        "pk": "S",
        "sk": "S",
        "recovery_pk": "S",
        "recovery_at": "N",
    }
    assert props["GlobalSecondaryIndexes"] == [
        {
            "IndexName": "job-recovery",
            "KeySchema": [
                {"AttributeName": "recovery_pk", "KeyType": "HASH"},
                {"AttributeName": "recovery_at", "KeyType": "RANGE"},
            ],
            "Projection": {"ProjectionType": "KEYS_ONLY"},
        }
    ]
    assert "TimeToLiveSpecification" not in props
    assert props["DeletionProtectionEnabled"]
    assert props["PointInTimeRecoverySpecification"]["PointInTimeRecoveryEnabled"]
    assert jobs["DeletionPolicy"] == jobs["UpdateReplacePolicy"] == "Retain"


def test_transport_timeouts_concurrency_acknowledgement_and_schedule() -> None:
    data = template()
    resources, parameters = data["Resources"], data["Parameters"]
    worker = resources["Worker"]["Properties"]
    mapping = resources["WorkMapping"]["Properties"]
    assert worker["Timeout"] == 120
    assert parameters["QueueVisibilitySeconds"]["MinValue"] >= 6 * worker["Timeout"]
    assert (
        parameters["QueueVisibilitySeconds"]["Default"]
        >= parameters["QueueVisibilitySeconds"]["MinValue"]
    )
    assert mapping["BatchSize"] == 1 and mapping["MaximumBatchingWindowInSeconds"] == 0
    assert mapping["FunctionResponseTypes"] == ["ReportBatchItemFailures"]
    assert mapping["ScalingConfig"]["MaximumConcurrency"] == worker["ReservedConcurrentExecutions"]
    assert resources["Dispatcher"]["Properties"]["ReservedConcurrentExecutions"] == 1
    assert parameters["EnableTriggers"]["Default"] == "false"
    assert mapping["Enabled"] == {"Fn::If": ["TriggersEnabled", True, False]}
    schedule = resources["DispatchSchedule"]["Properties"]
    assert json.loads(schedule["Targets"][0]["Input"]) == {"schema_version": "reconcile-v1"}
    assert schedule["State"] == {"Fn::If": ["TriggersEnabled", "ENABLED", "DISABLED"]}
    assert schedule["Targets"][0]["DeadLetterConfig"]["Arn"] == {
        "Fn::GetAtt": ["DispatchDLQ", "Arn"]
    }
    assert resources["WorkQueue"]["Properties"]["RedrivePolicy"]["maxReceiveCount"] == 5


def test_handlers_match_synchronous_adapters_and_environment_contract() -> None:
    from appraisal_review.adapters.aws import runtime_jobs

    resources = template()["Resources"]
    for logical, handler in (("Dispatcher", "dispatch_handler"), ("Worker", "worker_handler")):
        props = resources[logical]["Properties"]
        assert props["ImageConfig"]["Command"] == [
            f"appraisal_review.adapters.aws.runtime_jobs.{handler}"
        ]
        assert not inspect.iscoroutinefunction(getattr(runtime_jobs, handler))
        assert props["Architectures"] == ["arm64"]
        assert props["Environment"]["Variables"]["REVIEW_REGION"] == {"Ref": "AWS::Region"}
    dispatcher = resources["Dispatcher"]["Properties"]["Environment"]["Variables"]
    worker = resources["Worker"]["Properties"]["Environment"]["Variables"]
    runtime = resources["Runtime"]["Properties"]["EnvironmentVariables"]
    assert "REVIEW_RUNTIME_ARN" in worker
    assert "REVIEW_RUNTIME_ARN" not in dispatcher and "REVIEW_RUNTIME_ARN" not in runtime
    assert set(dispatcher) == {
        "REVIEW_REGION",
        "REVIEW_ACCOUNT_ID",
        "REVIEW_JOB_TABLE",
        "REVIEW_QUEUE_URL",
    }
    assert runtime["REVIEW_JOB_TABLE"] == {"Ref": "Jobs"}
    assert runtime["REVIEW_RESULT_BUCKET"] == {"Ref": "Results"}
    for env in (dispatcher, worker, runtime):
        assert not (
            {"AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"}
            & set(env)
        )


def test_separate_least_privilege_roles() -> None:
    assert actions("DispatcherRole") == {
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:UpdateItem",
        "dynamodb:DeleteItem",
        "dynamodb:Query",
        "sqs:SendMessage",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
    }
    assert actions("WorkerRole") == {
        "sqs:ReceiveMessage",
        "sqs:DeleteMessage",
        "sqs:GetQueueAttributes",
        "sqs:ChangeMessageVisibility",
        "bedrock-agentcore:InvokeAgentRuntime",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
    }
    for role in ("DispatcherRole", "WorkerRole", "RuntimeRole", "RetentionRole"):
        assert all("*" not in action for action in actions(role))
        assert not ({"dynamodb:Scan", "iam:PassRole", "s3:ListAllMyBuckets"} & actions(role))
        for statement in statements(role):
            resource = statement["Resource"]
            if resource == "*" or (isinstance(resource, list) and "*" in resource):
                assert role == "RuntimeRole"
                assert statement["Action"] == "ecr:GetAuthorizationToken"
    runtime = statements("RuntimeRole")
    model = next(s for s in runtime if "bedrock:InvokeModel" in s["Action"])
    assert model["Resource"] == {"Ref": "ModelResourceArns"}
    sanitized = next(s for s in runtime if s["Action"] == ["s3:GetObjectVersion"])
    assert sanitized["Resource"] == {"Fn::Sub": "${SanitizedBucketArn}/${SanitizedObjectPrefix}*"}
    results = next(s for s in runtime if "s3:PutObject" in s["Action"])
    assert results["Resource"] == {"Fn::Sub": "${Results.Arn}/results/*"}
    assert "s3:GetObject" not in sanitized["Action"]
    index = next(s for s in statements("DispatcherRole") if s["Action"] == "dynamodb:Query")
    assert index["Resource"] == {"Fn::Sub": "${Jobs.Arn}/index/job-recovery"}


def test_encryption_retention_and_single_queue_policy() -> None:
    resources = template()["Resources"]
    props = resources["Results"]["Properties"]
    assert all(props["PublicAccessBlockConfiguration"].values())
    assert props["VersioningConfiguration"] == {"Status": "Enabled"}
    assert (
        props["BucketEncryption"]["ServerSideEncryptionConfiguration"][0][
            "ServerSideEncryptionByDefault"
        ]["SSEAlgorithm"]
        == "AES256"
    )
    assert (
        resources["Results"]["DeletionPolicy"]
        == resources["Results"]["UpdateReplacePolicy"]
        == "Retain"
    )
    queues = [r for r in resources.values() if r["Type"] == "AWS::SQS::Queue"]
    assert len(queues) == 3 and all(q["Properties"]["SqsManagedSseEnabled"] for q in queues)
    # Two independently managed queue policies on the same queue overwrite one another.
    policies = [r for r in resources.values() if r["Type"] == "AWS::SQS::QueuePolicy"]
    assert len(policies) == 1 and len(policies[0]["Properties"]["Queues"]) == 3
    for statement in policies[0]["Properties"]["PolicyDocument"]["Statement"]:
        if statement["Effect"] == "Allow":
            assert statement["Condition"]["ArnEquals"]["aws:SourceArn"] == {
                "Fn::GetAtt": ["DispatchSchedule", "Arn"]
            }
            assert statement["Condition"]["StringEquals"]["aws:SourceAccount"] == {
                "Ref": "AWS::AccountId"
            }
    assert resources["DispatchPermission"]["Properties"]["SourceAccount"] == {
        "Ref": "AWS::AccountId"
    }


def test_ecr_bootstrap_and_runtime_properties() -> None:
    images = template("image-stack.yaml")["Resources"]["Images"]
    assert images["DeletionPolicy"] == images["UpdateReplacePolicy"] == "Retain"
    props = images["Properties"]
    assert props["ImageTagMutability"] == "IMMUTABLE"
    assert props["ImageScanningConfiguration"]["ScanOnPush"]
    assert props["EncryptionConfiguration"]["EncryptionType"] == "AES256"
    assert not props["EmptyOnDelete"]
    runtime = template()["Resources"]["Runtime"]["Properties"]
    assert runtime["ProtocolConfiguration"] == "HTTP"
    assert runtime["NetworkConfiguration"] == {"NetworkMode": "PUBLIC"}
    assert not (
        {"Cpu", "MemorySize", "Architectures", "ReservedConcurrentExecutions"} & set(runtime)
    )
    assert runtime["AgentRuntimeArtifact"]["ContainerConfiguration"]["ContainerUri"][
        "Fn::Sub"
    ].endswith("@${RuntimeImageDigest}")


@pytest.mark.parametrize(
    "request_type,exists,expected",
    [
        ("Create", False, "SUCCESS"),
        ("Create", True, "SUCCESS"),
        ("Update", True, "SUCCESS"),
        ("Delete", True, "SUCCESS"),
    ],
)
def test_retention_adopts_existing_group_and_retains_on_delete(
    monkeypatch: pytest.MonkeyPatch, request_type: str, exists: bool, expected: str
) -> None:
    resources = template()["Resources"]
    assert "RuntimeLogs" not in resources
    assert resources["WorkMapping"]["DependsOn"] == "RuntimeLogRetention"
    response = SimpleNamespace(SUCCESS="SUCCESS", FAILED="FAILED", send=Mock())
    monkeypatch.setitem(sys.modules, "cfnresponse", response)
    client = Mock()
    client.exceptions.ResourceAlreadyExistsException = FileExistsError
    if exists:
        client.create_log_group.side_effect = FileExistsError
    factory = Mock(return_value=client)
    monkeypatch.setattr("boto3.client", factory)
    monkeypatch.setenv("REVIEW_REGION", "us-west-2")
    monkeypatch.setenv(
        "REVIEW_LOG_GROUP", "/aws/bedrock-agentcore/runtimes/test_runtime-identifier-DEFAULT"
    )
    code: dict[str, Any] = {}
    exec(
        compile(
            resources["RetentionHandler"]["Properties"]["Code"]["ZipFile"], "retention", "exec"
        ),
        code,
    )
    event: dict[str, Any] = {
        "RequestType": request_type,
        "ResourceProperties": {
            "LogGroupName": "/aws/bedrock-agentcore/runtimes/test_runtime-identifier-DEFAULT",
            "RetentionInDays": 14,
        },
    }
    code["handler"](event, object())
    assert response.send.call_args.args[2] == expected
    if request_type == "Delete":
        factory.assert_not_called()
    else:
        client.put_retention_policy.assert_called_once_with(
            logGroupName=event["ResourceProperties"]["LogGroupName"], retentionInDays=14
        )
        client.put_retention_policy.side_effect = RuntimeError("internal diagnostic")
        code["handler"](event, object())
        assert response.send.call_args.args[2] == "FAILED"
        assert "internal diagnostic" not in str(response.send.call_args)
    client.delete_log_group.assert_not_called()


def archive(
    tmp_path: Path, architecture: str = "arm64", os_name: str = "linux"
) -> tuple[Path, str]:
    config = json.dumps({"architecture": architecture, "os": os_name}).encode()
    digest = hashlib.sha256(config).hexdigest()
    path = tmp_path / "image.tar"
    with tarfile.open(path, "w") as output:
        for name, data in (
            (f"{digest}.json", config),
            (
                "manifest.json",
                json.dumps([{"Config": f"{digest}.json", "Layers": ["layer.tar"]}]).encode(),
            ),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            output.addfile(info, io.BytesIO(data))
    return path, f"sha256:{digest}"


def test_manifest_verifies_actual_architecture_and_content_hash(tmp_path: Path) -> None:
    build = load_build_script()
    path, digest = archive(tmp_path)
    result = build.verify_archive(path, digest)
    assert result["exported_manifest_verified"] and result["platform"] == "linux/arm64"
    with pytest.raises(ValueError, match="differs"):
        build.verify_archive(path, "sha256:" + "0" * 64)


@pytest.mark.parametrize("tamper_config", [False, True])
def test_containerd_manifest_image_id_binds_verified_config(
    tmp_path: Path, tamper_config: bool
) -> None:
    path, config_digest = archive(tmp_path)
    descriptor = json.dumps(
        {
            "schemaVersion": 2,
            "config": {"digest": "sha256:" + "0" * 64 if tamper_config else config_digest},
            "layers": [{"digest": "sha256:" + "1" * 64}],
        }
    ).encode()
    digest = hashlib.sha256(descriptor).hexdigest()
    with tarfile.open(path, "a") as output:
        member = tarfile.TarInfo("blobs/sha256/" + digest)
        member.size = len(descriptor)
        output.addfile(member, io.BytesIO(descriptor))
    if tamper_config:
        with pytest.raises(ValueError, match="does not bind"):
            load_build_script().verify_archive(path, "sha256:" + digest)
    else:
        assert (
            load_build_script().verify_archive(path, "sha256:" + digest)["config_digest"]
            == config_digest
        )


@pytest.mark.parametrize("architecture,os_name", [("amd64", "linux"), ("arm64", "windows")])
def test_manifest_rejects_wrong_actual_platform(
    tmp_path: Path, architecture: str, os_name: str
) -> None:
    build = load_build_script()
    path, digest = archive(tmp_path, architecture, os_name)
    with pytest.raises(ValueError, match="linux/arm64"):
        build.verify_archive(path, digest)


@pytest.mark.parametrize("key", ["profile", "profile_name", "awsProfile", "AWS_SECRET_ACCESS_KEY"])
def test_private_config_refuses_profile_and_credentials(tmp_path: Path, key: str) -> None:
    path = tmp_path / "operator.json"
    path.write_text(json.dumps({"settings": {key: "test-value"}}))
    path.chmod(0o600)
    with pytest.raises(ValueError, match="credentials or a local profile"):
        load_build_script().private_config(path)


def test_build_context_allowlist_and_container_entry(tmp_path: Path) -> None:
    build = load_build_script()
    build.build_context(tmp_path)
    paths = [p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()]
    assert all(
        p.startswith("src/appraisal_review/") or p.startswith("infra/runtime/") for p in paths
    )
    assert not any("runtime.json" in p or "/.tools/" in p or p.endswith(".pdf") for p in paths)
    dockerfile = (INFRA / "Dockerfile").read_text()
    assert "appraisal_review.adapters.aws.runtime_app:app" in dockerfile
    assert '"--port", "8080"' in dockerfile and "USER 10001:10001" in dockerfile
    assert "--require-hashes" in dockerfile and "required=true" in dockerfile
    assert "runtime_config" not in (INFRA / "lambda.Dockerfile").read_text()
    script = (ROOT / "scripts/build_runtime_image.py").read_text()
    assert '"--load"' in script and '"--push"' not in script


def test_lock_is_complete_and_hash_pinned() -> None:
    lines = [
        line
        for line in (INFRA / "requirements.lock").read_text().splitlines()
        if not line.startswith("#")
    ]
    assert len(lines) >= 20
    assert all(
        re.fullmatch(r"[A-Za-z0-9-]+==[^ ]+ --hash=sha256:[a-f0-9]{64}", line) for line in lines
    )
