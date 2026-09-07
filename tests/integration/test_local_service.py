from __future__ import annotations

import asyncio
import hashlib
import json
import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

from appraisal_review.adapters.aws.agentcore.runtime import invoke
from appraisal_review.adapters.local.approval import LocalApprovalStore
from appraisal_review.adapters.local.pdf_writer import LocalPDFWriter
from appraisal_review.adapters.local.service import (
    LocalReviewService,
    load_service,
)
from appraisal_review.api.app import create_app
from appraisal_review.application.bootstrap import ConfigurationError
from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.domain.factor_models import AgentReviewRequest, ReviewMaterial
from appraisal_review.domain.review_contracts import content_digest
from appraisal_review.domain.service_contracts import ServiceResult
from appraisal_review.local_service import app_from_environment

ROOT = Path(__file__).resolve().parents[2]
CREATE = runpy.run_path(str(ROOT / "scripts/local_service_fixture.py"))["create_fixture"]


@pytest.fixture
def configured(tmp_path):
    config = asyncio.run(CREATE(tmp_path / "fixture"))
    request = AgentReviewRequest.model_validate_json(
        (tmp_path / "fixture/request-write.json").read_bytes()
    )
    return config, request


def test_real_parser_controller_writer_manifest_and_low_confidence_confirmation(configured):
    config, request = configured
    service = LocalReviewService(config)
    sources = [d.path for d in config.inputs.documents] + [config.writer.template_path]
    before = {p: p.read_bytes() for p in sources}
    for observation in (
        service.snapshot.material.facts.pairs[0].pair.target,
        service.snapshot.material.facts.pairs[0].pair.comparable,
    ):
        assert observation.confidence == 0
        assert observation.evidence[0].confidence == 0
    result = asyncio.run(service.run(request))
    assert result.execution_status == "succeeded"
    assert result.business_status == "completed" and result.artifact_status == "written"
    assert result.durable is False
    output = config.writer.output_directory / "completed.pdf"
    pdf = PdfReader(output)
    assert len(pdf.pages) == 1 and "+5.00%" in pdf.pages[0].extract_text()
    manifest = result.artifacts[0]
    assert manifest.content_hash == hashlib.sha256(output.read_bytes()).hexdigest()
    assert manifest.field_ids == ("road-rate",)
    assert manifest.context == service.snapshot.material.facts.pairs[0].context
    assert manifest.template_hash == config.writer.template_policy.template_sha256
    assert manifest.field_map_hash == config.writer.template_policy.field_map_sha256
    assert manifest.page_count == len(pdf.pages)
    assert "file:///" not in result.model_dump_json()
    assert "Codex" not in str(pdf.metadata)
    assert {p: p.read_bytes() for p in sources} == before


def test_needs_review_preserves_findings_and_never_calls_writer(configured, monkeypatch):
    config, request = configured
    calls = []

    async def forbidden(*args, **kwargs):
        calls.append(args)
        raise AssertionError("Blocked material must not reach writer")

    monkeypatch.setattr(LocalPDFWriter, "write_pdf", forbidden)
    service = load_service(config.material_path.parent / "config-needs-review.json")
    result = asyncio.run(service.run(request))
    assert result.execution_status == "succeeded"
    assert result.business_status == "needs_review"
    assert any(f.status == "needs_review" for f in result.findings)
    assert result.artifacts == () and not list(config.writer.output_directory.iterdir())
    assert calls == []


def test_http_invocation_share_config_and_legacy_schemas(configured):
    config, request = configured
    service = LocalReviewService(config)
    payload = request.model_dump(mode="json")
    payload.update(output_pdf_uri=None, pdf_template_uri=None, field_map=None)
    with TestClient(create_app(controller_factory=service.controller_factory)) as client:
        actual = client.post("/v1/reviews", json=payload)
        invoked = asyncio.run(invoke(payload, controller_factory=service.controller_factory))
        assert actual.status_code == 200
        assert set(actual.json()) == set(invoked)
        assert actual.json()["status"] == invoked["status"] == "verified"
        assert actual.json()["artifact_status"] == "not_requested"
        assert "business_status" not in actual.json()
        assert client.get("/health").json() == {"status": "ok"}
        assert "detail" in client.post("/v1/validate", json={}).json()
        for route in ("/v1/review-jobs", "/v1/human-tasks", "/v1/artifacts"):
            assert client.get(route).status_code == 404
    assert (
        create_app().openapi()
        == create_app(controller_factory=service.controller_factory).openapi()
    )


def test_lazy_http_configuration_validation_and_safe_errors(configured, monkeypatch):
    config, request = configured
    monkeypatch.delenv("APPRAISAL_LOCAL_CONFIG", raising=False)
    with TestClient(app_from_environment()) as client:
        assert client.post("/v1/reviews", json={}).status_code == 422
        response = client.post("/v1/reviews", json=request.model_dump(mode="json"))
        assert response.status_code == 503 and set(response.json()) == {"error"}
        monkeypatch.setenv("APPRAISAL_LOCAL_CONFIG", str(config.material_path))
        assert client.post("/v1/reviews", json=request.model_dump(mode="json")).status_code == 503
        monkeypatch.setenv(
            "APPRAISAL_LOCAL_CONFIG", str(config.material_path.parent / "config.json")
        )
        data = request.model_dump(mode="json")
        data["case_document_uri"] = "file:///private/not-allowed.pdf"
        response = client.post("/v1/reviews", json=data)
        assert response.status_code == 200
        assert response.json()["status"] == "failed"
        assert response.json()["output_pdf_uri"] is None
        assert response.json()["verification"]["critical_errors"] == [
            "source_binding: requested and reviewed source must match"
        ]
        assert "private" not in response.text and "not-allowed" not in response.text


@pytest.mark.parametrize(
    "change", ["digest", "case", "version", "source_hash", "duplicate", "relative", "font"]
)
def test_configuration_rejects_invalid_bindings_without_fallback(configured, change):
    config, _ = configured
    data = config.model_dump(mode="json")
    if change == "digest":
        data["expected_material_digest"] = "f" * 64
    elif change == "case":
        data["inputs"]["identity"]["case_id"] = "other-case"
    elif change == "version":
        data["inputs"]["documents"][0]["version"] = "other-version"
    elif change == "source_hash":
        data["inputs"]["documents"][0]["expected_hash"] = "f" * 64
    elif change == "duplicate":
        data["inputs"]["documents"].append(data["inputs"]["documents"][0])
    elif change == "relative":
        data["material_path"] = "material.json"
    elif change == "font":
        del data["writer"]["render"]["font_path"]
    path = config.material_path.parent / "invalid-config.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ConfigurationError, match="invalid_local_service_configuration"):
        load_service(path)


def test_no_writer_requires_no_font_and_never_claims_completed(configured):
    config, request = configured
    service = LocalReviewService(config.model_copy(update={"writer": None}))
    result = asyncio.run(service.run(request))
    assert result.business_status == "verified" and result.artifact_status == "unavailable"
    assert not result.artifacts
    assert not list(config.writer.output_directory.iterdir())


@pytest.mark.parametrize("change", ["template", "map", "outside", "source_change", "missing_font"])
def test_output_policy_and_source_changes_fail_closed(configured, change):
    config, request = configured
    if change == "template":
        request.pdf_template_uri = config.inputs.documents[0].path.as_uri()
    elif change == "map":
        request.field_map.fields[0].bounding_box = (161, 75, 290, 110)
    elif change == "outside":
        request.output_pdf_uri = (config.material_path.parent / "outside.pdf").as_uri()
    elif change == "source_change":
        config.inputs.documents[1].path.write_bytes(b"%PDF-invalid-changed-source")
    elif change == "missing_font":
        writer = config.writer.model_copy(
            update={
                "render": config.writer.render.model_copy(
                    update={"font_path": config.material_path.parent / "missing.ttf"}
                )
            }
        )
        config = config.model_copy(update={"writer": writer})
    result = asyncio.run(LocalReviewService(config).run(request))
    assert result.business_status != "completed" and result.artifacts == ()
    assert not list(config.writer.output_directory.iterdir())
    assert not (config.material_path.parent / "outside.pdf").exists()


def test_revision_and_changed_material_cannot_reuse_old_receipt(configured):
    config, request = configured
    material = ReviewMaterial.model_validate_json(config.material_path.read_bytes())
    authority = LocalApprovalStore(config.approval_store)
    assert authority.permits(material)
    receipts_before = {p.name: p.read_bytes() for p in config.approval_store.iterdir()}
    wrong_side = material.model_copy(deep=True)
    wrong_side.facts.pairs[0].target_reliability.confirmation = wrong_side.facts.pairs[
        0
    ].comparable_reliability.confirmation
    assert not authority.permits(wrong_side)
    assert {p.name: p.read_bytes() for p in config.approval_store.iterdir()} == receipts_before
    assert authority.permits(material)  # An invalid copy does not revoke the exact original.
    snapshot = RevisionSnapshot.capture(material, "r1")
    child = snapshot.revise(material, "r2")
    assert authority.permits(child.material) is False
    changed = material.model_copy(deep=True)
    changed.facts.pairs[0].pair.target.raw_text += " changed"
    assert authority.permits(changed) is False
    path = config.material_path.parent / "changed.json"
    path.write_text(changed.model_dump_json())
    altered = config.model_copy(
        update={"material_path": path, "expected_material_digest": content_digest(changed)}
    )
    result = asyncio.run(LocalReviewService(altered).run(request))
    assert result.business_status == "needs_review" and not result.artifacts
    assert not list(config.writer.output_directory.iterdir())


@pytest.mark.parametrize("command", ["invoke", "run"])
def test_actual_cli_json_stdout_with_real_parser(configured, command):
    config, _ = configured
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "appraisal_review.local_service",
            command,
            "--config",
            str(config.material_path.parent / "config.json"),
            "--request",
            str(config.material_path.parent / "request.json"),
        ],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    if command == "invoke":
        assert payload["status"] == "verified" and payload["artifact_status"] == "not_requested"
    else:
        assert ServiceResult.model_validate(payload).business_status == "verified"


def test_import_and_http_factory_do_not_read_documents_or_create_cloud_clients():
    code = """
import sys
import fastapi  # Load third-party plugin metadata before monitoring application imports.
from pathlib import Path
from unittest.mock import patch
# No configured service is loaded by importing modules or building the HTTP app.
with patch.object(Path, "read_bytes", side_effect=AssertionError("document read")), \\
     patch.object(Path, "read_text", side_effect=AssertionError("document read")):
    from appraisal_review.local_service import app_from_environment
    app = app_from_environment()
assert "boto3" not in sys.modules
assert "botocore.session" not in sys.modules
assert "pymupdf" not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        timeout=20,
    )
    assert result.returncode == 0, result.stderr


def test_factory_cannot_silently_override_explicit_adapters():
    with pytest.raises(ValueError):
        create_app(settings=object(), controller_factory=lambda: None)


def test_factory_execution_error_is_shared_http_invocation_envelope(configured, monkeypatch):
    config, request = configured
    service = LocalReviewService(config)
    controller = service.controller_factory()

    async def broken(_):
        raise RuntimeError("private source or credential value")

    monkeypatch.setattr(controller, "review", broken)

    def factory():
        return controller

    with TestClient(create_app(controller_factory=factory)) as client:
        response = client.post("/v1/reviews", json=request.model_dump(mode="json"))
    assert response.status_code == 500
    assert response.json() == asyncio.run(
        invoke(request.model_dump(mode="json"), controller_factory=factory)
    )
    assert response.json() == {
        "error": {"code": "review_execution_failed", "message": "Review execution failed."}
    }


def test_new_envelope_never_turns_fake_writer_into_a_manifest(configured, monkeypatch):
    from appraisal_review.adapters.local.fake_pdf import FakePDFWriter

    config, request = configured
    service = LocalReviewService(config)
    controller = service.controller_factory()
    writer = FakePDFWriter()
    controller.pdf_writer = writer
    monkeypatch.setattr(service, "controller_factory", lambda: controller)
    result = asyncio.run(service.run(request))
    assert result.business_status == "verified" and result.artifact_status == "simulated"
    assert result.artifacts == () and len(writer.calls) == 1
    assert not list(config.writer.output_directory.iterdir())


def test_manifest_reopen_failure_cannot_advertise_completed_artifact(configured, monkeypatch):
    config, request = configured
    service = LocalReviewService(config)

    def unreadable(*args, **kwargs):
        raise OSError("private path")

    monkeypatch.setattr(service, "_manifest", unreadable)
    result = asyncio.run(service.run(request))
    assert result.execution_status == "failed" and result.business_status == "failed"
    assert result.artifacts == () and result.problem.code == "execution_failed"
    assert result.findings
    assert "private path" not in result.model_dump_json()


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("wrong-case", "invalid_request"),
        ("missing-factory", "capability_unavailable"),
        ("broken-controller", "execution_failed"),
    ],
)
def test_facade_preserves_machine_error_categories(configured, monkeypatch, case, expected):
    config, request = configured
    service = LocalReviewService(config)
    if case == "wrong-case":
        request.case_id = "another-case"
    elif case == "missing-factory":

        def unavailable():
            raise ConfigurationError("private setting")

        monkeypatch.setattr(service, "controller_factory", unavailable)
    else:
        controller = service.controller_factory()

        async def broken(_):
            raise RuntimeError("private source")

        monkeypatch.setattr(controller, "review", broken)
        monkeypatch.setattr(service, "controller_factory", lambda: controller)
    result = asyncio.run(service.run(request))
    assert result.problem.code == expected
    assert result.execution_status == "failed" and result.artifacts == ()
    assert "private" not in result.model_dump_json()
    assert not list(config.writer.output_directory.iterdir())


def test_old_controller_result_cannot_claim_existing_pdf_for_new_run(configured, monkeypatch):
    config, request = configured
    service = LocalReviewService(config)
    controller = service.controller_factory()
    old = asyncio.run(controller.review(request))
    assert old.artifact_status == "written"
    output = config.writer.output_directory / "completed.pdf"
    before = output.read_bytes()

    async def replay(_):
        return old

    monkeypatch.setattr(controller, "review", replay)
    monkeypatch.setattr(service, "controller_factory", lambda: controller)
    result = asyncio.run(service.run(request))
    assert result.execution_status == "failed"
    assert result.artifacts == () and result.business_status != "completed"
    assert output.read_bytes() == before


def test_same_page_replacement_after_write_cannot_become_manifest(configured, monkeypatch):
    config, request = configured
    service = LocalReviewService(config)
    controller = service.controller_factory()
    review = controller.review
    output = config.writer.output_directory / "completed.pdf"

    async def replaced(payload):
        result = await review(payload)
        assert result.artifact_status == "written"
        # The blank template is a different, valid PDF with the same page count.
        output.write_bytes(config.writer.template_path.read_bytes())
        return result

    monkeypatch.setattr(controller, "review", replaced)
    monkeypatch.setattr(service, "controller_factory", lambda: controller)
    result = asyncio.run(service.run(request))
    assert result.execution_status == "failed" and result.artifacts == ()
    assert output.exists()  # Failure never deletes a destination to hide the mismatch.


@pytest.mark.parametrize(
    "mode", ["needs_review", "unavailable", "not_requested", "simulated", "failed"]
)
def test_nonwriting_run_preserves_existing_success_without_claiming_it(
    configured, monkeypatch, mode
):
    from appraisal_review.adapters.local.fake_pdf import FakePDFWriter

    config, request = configured
    first = asyncio.run(LocalReviewService(config).run(request))
    assert first.artifact_status == "written"
    output = config.writer.output_directory / "completed.pdf"
    before = output.read_bytes()
    service = LocalReviewService(config)
    if mode == "needs_review":
        service = load_service(config.material_path.parent / "config-needs-review.json")
    elif mode == "unavailable":
        service = LocalReviewService(config.model_copy(update={"writer": None}))
    elif mode == "not_requested":
        request.output_pdf_uri = request.pdf_template_uri = request.field_map = None
    elif mode == "simulated":
        controller = service.controller_factory()
        controller.pdf_writer = FakePDFWriter()
        monkeypatch.setattr(service, "controller_factory", lambda: controller)
    result = asyncio.run(service.run(request))
    assert result.run.run_id != first.run.run_id
    assert result.artifacts == () and result.business_status != "completed"
    assert result.business_status == (mode if mode in {"needs_review", "failed"} else "verified")
    assert result.artifact_status == (
        "not_requested" if mode in {"needs_review", "failed"} else mode
    )
    if mode == "needs_review":
        assert result.findings
    assert output.read_bytes() == before


def test_writer_return_without_publication_cannot_relabel_existing_output(configured, monkeypatch):
    from appraisal_review.domain.pdf_models import PDFWriteResult

    config, request = configured
    service = LocalReviewService(config)
    assert asyncio.run(service.run(request)).artifact_status == "written"
    output = config.writer.output_directory / "completed.pdf"
    before = output.read_bytes()

    async def no_write(self, payload):
        return PDFWriteResult(
            output_uri=payload.destination_uri, page_count=1, written_field_ids=["road-rate"]
        )

    monkeypatch.setattr(LocalPDFWriter, "write_pdf", no_write)
    result = asyncio.run(service.run(request))
    assert result.execution_status == "failed" and not result.artifacts
    assert output.read_bytes() == before


def test_explicit_overwrite_still_produces_a_new_verified_run(configured):
    config, request = configured
    first = asyncio.run(LocalReviewService(config).run(request))
    render = config.writer.render.model_copy(update={"overwrite_existing": True})
    writer = config.writer.model_copy(update={"render": render})
    second = asyncio.run(
        LocalReviewService(config.model_copy(update={"writer": writer})).run(request)
    )
    assert first.artifact_status == second.artifact_status == "written"
    assert first.run.revision == second.run.revision
    assert first.run.run_id != second.run.run_id
    assert first.artifacts[0].artifact_id != second.artifacts[0].artifact_id
    assert first.artifacts[0].content_hash == second.artifacts[0].content_hash


@pytest.mark.parametrize("command", ["invoke", "run"])
@pytest.mark.parametrize("failure", ["validation", "configuration"])
def test_actual_cli_error_channels_and_exit_contract(configured, command, failure):
    config, _ = configured
    root = config.material_path.parent
    request_path = root / "request.json"
    config_path = root / "config.json"
    if failure == "validation":
        request_path = root / "invalid-request.json"
        request_path.write_text('{"private-field":"private-value"}')
    else:
        config_path = root / "private-missing.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "appraisal_review.local_service",
            command,
            "--config",
            str(config_path),
            "--request",
            str(request_path),
        ],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
        timeout=30,
    )
    if command == "invoke":
        assert result.returncode == 0 and result.stderr == ""
        data = json.loads(result.stdout)
        assert data["error"]["code"] == (
            "invalid_request" if failure == "validation" else "invalid_local_service_configuration"
        )
    else:
        assert result.returncode == 2 and result.stdout == ""
        assert json.loads(result.stderr)["error"]["code"] == "invalid_local_request"
    assert "private" not in result.stdout + result.stderr
