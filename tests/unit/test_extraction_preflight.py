"""Synthetic preflight and authorized-assembly tests; no AWS clients or credentials."""

import asyncio
import json
import sys
from argparse import Namespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError
from test_document_extraction import PNG
from test_extraction_contracts import payload, snapshot

from appraisal_review.adapters.aws.bedrock import BedrockExplanationGenerator
from appraisal_review.adapters.aws.document_extraction import (
    BedrockDocumentExtractor,
    ExtractionConfig,
    ExtractionError,
)
from appraisal_review.adapters.aws.extraction_preflight import (
    BedrockAccessPolicy,
    WorkstationClients,
    preflight,
)
from appraisal_review.adapters.aws.snapshot_extraction import BedrockSnapshotBackend
from appraisal_review.adapters.aws.textract import TextractDocumentAnalyzer
from appraisal_review.application.extraction import AuthorizedExtractionService
from appraisal_review.application.service_guards import Principal
from appraisal_review.document_cli import main, preparation
from appraisal_review.domain.extraction_contracts import (
    ExecutionBudget,
    PageRequest,
)
from appraisal_review.domain.service_contracts import ActorReference, Permission
from appraisal_review.ports.document_extraction import (
    AuthorizedSanitizedSnapshot,
    ExtractionBoundaryError,
)


def policy(**changes):
    return BedrockAccessPolicy.model_validate(
        {
            "account": "123456789012",
            "role_name": "ProjectReviewer",
            "region": "us-east-1",
            "model_id": "vendor.synthetic-v1",
            "model_kind": "foundation",
            "allowed_regions": ["us-east-1"],
            "allowed_foundation_models": ["vendor.synthetic-v1"],
            "converse_capability_digest": "a" * 64,
            "timeout_seconds": 1,
            "max_output_tokens": 1024,
            "max_image_bytes": 3750000,
            "max_image_width": 8000,
            "max_image_height": 8000,
            "max_image_pixels": 16000000,
        }
        | changes
    )


def model(region="us-east-1"):
    return {
        "modelDetails": {
            "modelId": "vendor.synthetic-v1",
            "modelArn": f"arn:aws:bedrock:{region}::foundation-model/vendor.synthetic-v1",
            "inputModalities": ["TEXT", "IMAGE"],
            "outputModalities": ["TEXT"],
            "inferenceTypesSupported": ["ON_DEMAND"],
            "modelLifecycle": {"status": "ACTIVE"},
        }
    }


def clients():
    result = Mock()
    mapping = {}
    for region in ("us-east-1", "us-west-2"):
        for service in ("sts", "bedrock", "bedrock-runtime"):
            item = Mock()
            item.meta.region_name = region
            mapping[service, region] = item
    result.client.side_effect = lambda service, region, timeout: mapping[service, region]
    mapping["sts", "us-east-1"].get_caller_identity.return_value = {
        "Account": "123456789012",
        "Arn": "arn:aws:sts::123456789012:assumed-role/ProjectReviewer/session",
    }
    for region in ("us-east-1", "us-west-2"):
        mapping["bedrock", region].get_foundation_model.return_value = model(region)
    mapping["bedrock-runtime", "us-east-1"].converse.return_value = {
        "stopReason": "end_turn",
        "output": {"message": {"content": [{"text": "{}"}]}},
        "usage": {"inputTokens": 1, "outputTokens": 1},
    }
    return result, mapping


def profile_setup(kind="system_profile"):
    factory, mapping = clients()
    prefix = "inference-profile" if kind == "system_profile" else "application-inference-profile"
    profile_id = "us.synthetic" if kind == "system_profile" else "synthetic-app"
    mapping["bedrock", "us-east-1"].get_inference_profile.return_value = {
        "inferenceProfileArn": f"arn:aws:bedrock:us-east-1:123456789012:{prefix}/{profile_id}",
        "inferenceProfileId": profile_id,
        "status": "ACTIVE",
        "type": "SYSTEM_DEFINED" if kind == "system_profile" else "APPLICATION",
        "models": [
            {"modelArn": model(region)["modelDetails"]["modelArn"]}
            for region in ("us-east-1", "us-west-2")
        ],
    }
    config = policy(
        model_kind=kind,
        model_id=profile_id,
        allowed_regions=["us-east-1", "us-west-2"],
        allow_cross_region=True,
    )
    return factory, mapping, config


@pytest.mark.parametrize("kind", ["system_profile", "application_profile"])
def test_every_profile_destination_is_checked_without_invocation(kind):
    factory, mapping, config = profile_setup(kind)
    assert preflight(factory, config) is mapping["bedrock-runtime", "us-east-1"]
    for region in ("us-east-1", "us-west-2"):
        mapping["bedrock", region].get_foundation_model.assert_called_once_with(
            modelIdentifier="vendor.synthetic-v1"
        )
    mapping["bedrock-runtime", "us-east-1"].converse.assert_not_called()


@pytest.mark.parametrize(
    "problem",
    [
        "second_region",
        "second_model",
        "second_modality",
        "type",
        "status",
        "identity",
        "empty",
        "duplicate",
    ],
)
def test_profile_denials_never_create_runtime_client(problem):
    factory, mapping, config = profile_setup()
    data = mapping["bedrock", "us-east-1"].get_inference_profile.return_value
    if problem == "second_region":
        data["models"][1]["modelArn"] = data["models"][1]["modelArn"].replace(
            "us-west-2", "eu-west-1"
        )
    elif problem == "second_model":
        data["models"][1]["modelArn"] += "-other"
    elif problem == "second_modality":
        mapping["bedrock", "us-west-2"].get_foundation_model.return_value["modelDetails"][
            "inputModalities"
        ] = ["TEXT"]
    elif problem == "empty":
        data["models"] = []
    elif problem == "duplicate":
        data["models"] *= 2
    elif problem == "identity":
        data["inferenceProfileArn"] = data["inferenceProfileArn"].replace(
            "123456789012", "000000000000"
        )
    else:
        data[problem] = "unexpected"
    with pytest.raises(ExtractionBoundaryError):
        preflight(factory, config)
    assert all(call.args[0] != "bedrock-runtime" for call in factory.client.call_args_list)


@pytest.mark.parametrize("problem", ["account", "role", "region", "capability", "provider_error"])
def test_identity_region_and_capability_denials_are_safe(problem):
    factory, mapping = clients()
    if problem == "account":
        mapping["sts", "us-east-1"].get_caller_identity.return_value["Account"] = "000000000000"
    elif problem == "role":
        mapping["sts", "us-east-1"].get_caller_identity.return_value["Arn"] += "/forged"
    elif problem == "region":
        mapping["sts", "us-east-1"].meta.region_name = "us-west-2"
    elif problem == "provider_error":
        mapping["sts", "us-east-1"].get_caller_identity.side_effect = RuntimeError("PRIVATE-CANARY")
    else:
        mapping["bedrock", "us-east-1"].get_foundation_model.return_value["modelDetails"][
            "inputModalities"
        ] = ["TEXT"]
    with pytest.raises(ExtractionBoundaryError) as caught:
        preflight(factory, policy())
    assert "PRIVATE-CANARY" not in str(caught.value)
    assert all(call.args[0] != "bedrock-runtime" for call in factory.client.call_args_list)


def test_workstation_profile_is_explicit_and_sdk_creation_is_lazy(monkeypatch):
    boto = Mock()
    monkeypatch.setitem(sys.modules, "boto3", boto)
    monkeypatch.setitem(sys.modules, "botocore.config", Mock(Config=lambda **kw: kw))
    for profile in ("", " ", "default", " project"):
        with pytest.raises(ExtractionBoundaryError):
            WorkstationClients(profile=profile)
    factory = WorkstationClients(profile="project")
    boto.Session.assert_not_called()
    factory.client("sts", "us-east-1", 2)
    boto.Session.assert_called_once_with(profile_name="project", region_name="us-east-1")
    options = boto.Session.return_value.client.call_args.kwargs
    assert options["config"] == {
        "connect_timeout": 1,
        "read_timeout": 1,
        "retries": {"total_max_attempts": 1},
    }


def principal():
    return Principal(
        ActorReference(actor_id="synthetic", kind="human"),
        frozenset({"synthetic-case"}),
        frozenset({Permission.REVIEW}),
    )


def backend_setup():
    factory, mapping = clients()
    config = ExtractionConfig(
        model_id="vendor.synthetic-v1",
        region="us-east-1",
        timeout_seconds=1,
        max_output_tokens=1024,
        attempts=1,
    )
    budget = ExecutionBudget.model_validate(
        payload("evaluation.json")["budget"]
        | {
            "max_context_characters": 200000,
        }
    )
    renderer = AsyncMock()
    renderer.render.return_value = PNG
    backend = BedrockSnapshotBackend(
        clients=factory, policy=policy(), config=config, budget=budget, renderer=renderer
    )
    return backend, factory, mapping, renderer


def test_authorized_path_renders_only_snapshot_and_omits_original_metadata():
    backend, _factory, mapping, renderer = backend_setup()
    item = snapshot()
    source = item.source
    source.uri = "file:///PRIVATE-CANARY.pdf"
    source.document_date = "PRIVATE-CANARY"
    item = AuthorizedSanitizedSnapshot(
        item.reference, item.content, source.model_dump_json().encode()
    )
    resolver = AsyncMock()
    resolver.resolve.return_value = item
    service = AuthorizedExtractionService(resolver=resolver, backend=backend)
    request = PageRequest.model_validate(payload())
    result = asyncio.run(service.extract(principal(), request))
    assert result.request.page == 1 and result.status == "candidate"
    renderer.render.assert_awaited_once_with(item, 1)
    outgoing = mapping["bedrock-runtime", "us-east-1"].converse.call_args.kwargs
    assert "PRIVATE-CANARY" not in str(outgoing)
    context = json.loads(outgoing["messages"][0]["content"][0]["text"])["case_context"]
    assert json.loads(context) == request.context.model_dump(mode="json")


@pytest.mark.parametrize(
    "problem",
    [
        "missing_resolver",
        "principal",
        "digest",
        "page",
        "purpose",
        "privacy_version",
        "uri",
        "context",
        "renderer",
        "image",
        "budget",
    ],
)
def test_denied_source_or_invalid_image_causes_zero_aws_clients(problem):
    backend, factory, _, renderer = backend_setup()
    resolver = AsyncMock()
    resolver.resolve.return_value = snapshot()
    request = PageRequest.model_validate(payload())
    actor = principal()
    if problem == "missing_resolver":
        resolver = None
    elif problem == "principal":
        actor = Principal(actor.actor, frozenset(), actor.permissions)
    elif problem in {"digest", "purpose", "privacy_version", "uri", "context", "page"}:
        data = payload()
        if problem == "digest":
            data["source"]["document"]["content_hash"] = "0" * 64
        elif problem == "purpose":
            data["source"]["document"]["purpose"] = "criteria"
        elif problem == "privacy_version":
            data["source"]["privacy_contract_version"] = "unknown"
        elif problem == "uri":
            data["source"]["document"]["uri"] = "file:///PRIVATE-CANARY"
        elif problem == "context":
            data["context"]["case_context"] = "PRIVATE-CANARY"
        else:
            data["page"] = 3
        try:
            request = PageRequest.model_validate(data)
        except ValidationError:
            factory.client.assert_not_called()
            return
    elif problem == "renderer":
        renderer.render.side_effect = ValueError("PRIVATE-CANARY")
    elif problem == "image":
        renderer.render.return_value = b"PRIVATE-CANARY"
    elif problem == "budget":
        backend.budget = backend.budget.model_copy(update={"max_input_bytes": 1})
    service = AuthorizedExtractionService(resolver=resolver, backend=backend)
    if problem in {"renderer", "image"}:
        result = asyncio.run(service.extract(actor, request))
        assert result.status == "failed" and result.failure == "unsupported_input"
        assert result.telemetry.attempts == ()
        assert "PRIVATE-CANARY" not in result.model_dump_json()
    else:
        with pytest.raises(ExtractionBoundaryError) as caught:
            asyncio.run(service.extract(actor, request))
        assert "PRIVATE-CANARY" not in str(caught.value)
    factory.client.assert_not_called()


def test_legacy_entries_are_closed_before_file_or_credential_access():
    client = Mock()
    with pytest.raises(ExtractionBoundaryError, match="privacy_unavailable"):
        asyncio.run(preparation(Namespace(command="extract")))
    with pytest.raises(ExtractionError, match="privacy_unavailable"):
        asyncio.run(
            BedrockDocumentExtractor(
                client, ExtractionConfig(model_id="x", region="x")
            ).extract_page(snapshot().source, 1, PNG)
        )
    with pytest.raises(ExtractionBoundaryError):
        asyncio.run(BedrockExplanationGenerator(client, model_id="x").explain(None, []))
    for action in (
        lambda: BedrockExplanationGenerator.from_default_session(region_name="x", model_id="x"),
        lambda: TextractDocumentAnalyzer.from_default_session(region_name="x"),
        lambda: TextractDocumentAnalyzer(client).start(bucket="private", key="private"),
        lambda: TextractDocumentAnalyzer(client).get_page(job_id="private"),
    ):
        with pytest.raises(ExtractionBoundaryError):
            action()
    assert client.mock_calls == []


def test_dry_run_cli_needs_no_profile_documents_or_sdk(tmp_path, monkeypatch, capsys):
    fixture = payload("evaluation.json")
    argv = ["documents", "extract-plan"]
    for name, value in (
        ("request", payload()),
        ("configuration", fixture["configuration"]),
        ("budget", fixture["budget"]),
    ):
        path = tmp_path / (name + ".json")
        path.write_text(json.dumps(value), encoding="utf-8")
        argv.extend(["--" + name, str(path)])
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setitem(sys.modules, "boto3", None)
    main()
    result = json.loads(capsys.readouterr().out)
    assert result["provider_calls"] == 0 and result["ready_for_live"] is False
    assert result["privacy_integration"] == "unavailable"
    assert "privacy_manifest_digest" not in result


def test_global_and_cross_region_routing_require_separate_opt_in():
    with pytest.raises(ValidationError):
        policy(allowed_regions=["us-east-1", "us-west-2"])
    with pytest.raises(ValidationError):
        policy(model_kind="system_profile", model_id="global.synthetic")
    assert policy(model_kind="system_profile", model_id="global.synthetic", allow_global=True)


def test_snapshot_pdf_renderer_uses_bytes_and_checks_page_geometry():
    import pymupdf

    from appraisal_review.adapters.local.snapshot_renderer import _render

    with pymupdf.open() as pdf:
        for _ in range(2):
            page = pdf.new_page(width=100, height=100)
            page.insert_text((10, 20), "0")
        content = pdf.tobytes()
    assert _render(content, 1, 2, 100, 100).startswith(b"\x89PNG\r\n\x1a\n")
    for values in ((1, 3, 100, 100), (1, 2, 99, 100), (1, 2, 100, 99)):
        with pytest.raises(ValueError):
            _render(content, *values)


def test_cli_legacy_extract_returns_only_static_error_before_manifest_read(monkeypatch, capsys):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "documents",
            "extract",
            "--manifest",
            "PRIVATE-CANARY",
            "--output",
            "PRIVATE-CANARY",
            "--profile",
            "project",
            "--region",
            "us-east-1",
            "--expected-account",
            "123456789012",
            "--expected-role",
            "ProjectReviewer",
            "--model-id",
            "synthetic",
            "--pages",
            "forms:1",
        ],
    )
    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == "privacy_unavailable\n"


def test_cli_malformed_dry_run_does_not_print_input_or_path(tmp_path, monkeypatch, capsys):
    path = tmp_path / "PRIVATE-CANARY.json"
    path.write_text('{"PRIVATE-CANARY": true}', encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "documents",
            "extract-plan",
            "--request",
            str(path),
            "--configuration",
            str(path),
            "--budget",
            str(path),
        ],
    )
    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code == 2
    assert capsys.readouterr().err == "configuration_error\n"


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": "bedrock-access-v2"},
        {"api": "invoke_model"},
        {"allowed_regions": ["us-east-1", "us-east-1"]},
        {"allowed_foundation_models": ["vendor.synthetic-v1", "vendor.synthetic-v1"]},
        {"allowed_foundation_models": ["https://private.invalid"]},
    ],
)
def test_invalid_policy_is_rejected_without_sdk(changes):
    with pytest.raises(ValidationError):
        policy(**changes)


@pytest.mark.parametrize(
    "problem", ["empty", "identity", "modality", "lifecycle", "inference_type", "arn_region"]
)
def test_foundation_capability_is_bound_to_exact_model_and_region(problem):
    factory, mapping = clients()
    details = mapping["bedrock", "us-east-1"].get_foundation_model.return_value["modelDetails"]
    if problem == "empty":
        details.clear()
    elif problem == "identity":
        details["modelId"] = "other"
    elif problem == "modality":
        details["outputModalities"] = ["IMAGE"]
    elif problem == "lifecycle":
        details["modelLifecycle"]["status"] = "LEGACY"
    elif problem == "inference_type":
        details["inferenceTypesSupported"] = ["PROVISIONED"]
    else:
        details["modelArn"] = model("us-west-2")["modelDetails"]["modelArn"]
    with pytest.raises(ExtractionBoundaryError):
        preflight(factory, policy())
    assert all(call.args[0] != "bedrock-runtime" for call in factory.client.call_args_list)
