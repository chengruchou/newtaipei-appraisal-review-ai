"""Owned-byte export tests with real PDF workers and explicit local authority/OCR doubles."""

import hashlib
import io
import json
import logging
import traceback
from dataclasses import FrozenInstanceError
from unittest.mock import Mock

import pymupdf
import pytest
from privacy_export_support import CANARIES, canary_raster, inspect_pdf, report, scan
from pypdf import PdfWriter
from test_privacy_bundle import approval, output_ocr, setup

from appraisal_review.application.privacy_bundle import (
    LocalSanitizedBundleBuilder,
    LocalSanitizedVerifier,
)
from appraisal_review.application.privacy_export import (
    LocalPrivacyExportGate,
    sanitize_reviewer_text,
)
from appraisal_review.application.privacy_guards import PrivacyFault
from appraisal_review.domain.privacy_models import PrivacyManifest, placeholder_text


def gate_fixture(tmp_path):
    store, command, processor = setup(tmp_path)
    bundle = processor.build(command, store.read(command.source))
    verifier = LocalSanitizedVerifier(processor, output_ocr(bundle))
    authority = Mock()
    authority.permits.return_value = True
    builder = LocalSanitizedBundleBuilder(
        sources=store,
        authority=authority,
        processor=processor,
        verifier=verifier,
    )
    confirmation, sink = Mock(), Mock()
    confirmation.confirm.return_value = True
    gate = LocalPrivacyExportGate(
        builder=builder,
        verifier=verifier,
        authority=authority,
        confirmation=confirmation,
        sink=sink,
    )
    return gate, command, authority, confirmation, sink, verifier


def test_exact_confirmed_snapshot_is_exported_and_canary_surfaces_are_clean(
    tmp_path, capsys, caplog
):
    gate, command, _, confirmation, sink, _ = gate_fixture(tmp_path)
    manifest = gate.export(
        command, approval(command), reviewer_text=f"Please verify {CANARIES['c01']}"
    )
    payload = sink.accept.call_args.args[0]
    assert payload is confirmation.confirm.call_args.args[0]
    assert hashlib.sha256(payload.pdf).hexdigest() == manifest.sanitized_digest
    assert PrivacyManifest.model_validate_json(payload.manifest_json) == manifest
    assert payload.filename == "sanitized.pdf"
    assert placeholder_text(command.selections[0].entity_id) in payload.reviewer_text
    with pytest.raises(FrozenInstanceError):
        payload.pdf = b"changed"
    captured = capsys.readouterr()
    checks = [
        *inspect_pdf(payload.pdf),
        scan("serializer", payload.manifest_json + payload.reviewer_text),
        scan("stdout", captured.out),
        scan("stderr", captured.err),
        scan("log", caplog.text),
    ]
    evidence = report(checks)
    assert not any(check.hits for check in evidence.checks)
    assert evidence.status == "blocked"  # Network/exception probes are separate evidence.
    assert not scan("serializer", evidence.model_dump_json()).hits


@pytest.mark.parametrize(
    "change", ["deny", "non-boolean", "revoke", "verification", "pdf", "text", "name"]
)
def test_confirmation_changes_fail_before_sink(tmp_path, change):
    gate, command, authority, confirmation, sink, verifier = gate_fixture(tmp_path)

    def confirm(payload):
        if change == "deny":
            return False
        if change == "non-boolean":
            return 1
        if change == "revoke":
            authority.permits.return_value = False
        elif change == "verification":
            verifier._entry = None
        elif change == "pdf":
            object.__setattr__(payload, "pdf", b"replacement")
        elif change == "text":
            object.__setattr__(payload, "reviewer_text", CANARIES["c01"])
        elif change == "name":
            object.__setattr__(payload, "filename", CANARIES["c01"])
        return True

    confirmation.confirm.side_effect = confirm
    with pytest.raises(PrivacyFault):
        gate.export(command, approval(command))
    sink.accept.assert_not_called()


@pytest.mark.parametrize("surface", ["confirmation", "sink", "authority"])
def test_errors_do_not_echo_adapter_values(tmp_path, surface, capsys, caplog):
    gate, command, authority, confirmation, sink, _ = gate_fixture(tmp_path)
    target = {
        "confirmation": confirmation.confirm,
        "sink": sink.accept,
        "authority": authority.permits,
    }[surface]
    target.side_effect = RuntimeError(CANARIES["c02"])
    with pytest.raises(PrivacyFault) as failure:
        gate.export(command, approval(command))
    public_error = failure.value.problem.model_dump_json()
    assert not scan("exception", "".join(traceback.format_exception(failure.value))).hits
    assert not scan("serializer", public_error).hits
    captured = capsys.readouterr()
    assert not scan("stdout", captured.out).hits
    assert not scan("stderr", captured.err).hits
    assert not scan("log", caplog.text).hits


@pytest.mark.parametrize("variant", ["original", "lower", "spaces", "width"])
def test_shared_text_api_replaces_known_variants_but_never_grants_authority(tmp_path, variant):
    _, command, _ = setup(tmp_path)
    value = CANARIES["c01"]
    if variant == "lower":
        value = value.lower()
    elif variant == "spaces":
        value = " \n".join(value)
    elif variant == "width":
        value = "".join(chr(ord(c) + 0xFEE0) for c in value)
    draft = sanitize_reviewer_text(f"Check {value}", command)
    assert draft.state == "needs_review"
    assert draft.text == f"Check {placeholder_text(command.selections[0].entity_id)}"
    assert CANARIES["c01"] not in repr(draft)


@pytest.mark.parametrize(
    "value",
    ["\x00", "\u200b", "a" * 16385, b"bytes"],
    ids=["null", "zero-width", "oversized", "wrong-type"],
)
def test_shared_text_api_rejects_unsupported_inputs(tmp_path, value):
    _, command, _ = setup(tmp_path)
    with pytest.raises(PrivacyFault):
        sanitize_reviewer_text(value, command)


def test_original_source_digest_cannot_return_through_reviewer_text(tmp_path):
    gate, command, _, confirmation, sink, _ = gate_fixture(tmp_path)
    with pytest.raises(PrivacyFault):
        gate.export(
            command, approval(command), reviewer_text=" ".join(command.source.source_digest.upper())
        )
    confirmation.confirm.assert_not_called()
    sink.accept.assert_not_called()


def test_unknown_free_text_requires_explicit_final_review(tmp_path):
    gate, command, _, confirmation, sink, _ = gate_fixture(tmp_path)
    confirmation.confirm.return_value = False
    with pytest.raises(PrivacyFault):
        gate.export(command, approval(command), reviewer_text=CANARIES["c03"])
    assert confirmation.confirm.call_args.args[0].reviewer_text == CANARIES["c03"]
    sink.accept.assert_not_called()


def test_no_arbitrary_path_filename_metadata_or_serialized_grant_api(tmp_path):
    gate, command, _, _, sink, _ = gate_fixture(tmp_path)
    for keyword in ("path", "pdf", "manifest", "filename", "metadata", "approved"):
        with pytest.raises(TypeError):
            gate.export(command, approval(command), **{keyword: "untrusted"})
    sink.accept.assert_not_called()


@pytest.mark.parametrize(
    "surface", ["stdout", "stderr", "log", "exception", "serializer", "http", "sdk", "telemetry"]
)
def test_positive_channel_controls_emit_only_canary_ids_in_report(surface, capsys, caplog):
    value = CANARIES["c02"]
    if surface == "stdout":
        print(value)
        value = capsys.readouterr().out
    elif surface == "stderr":
        import sys

        print(value, file=sys.stderr)
        value = capsys.readouterr().err
    elif surface == "log":
        logging.warning(value)
        value = caplog.text
        caplog.clear()
    elif surface == "exception":
        value = str(RuntimeError(value))
    elif surface == "serializer":
        value = json.dumps({"text": value})
    # HTTP/SDK/telemetry here are captured payload scans; transport probes are separate.
    evidence = report([scan(surface, value)])
    assert evidence.status == "failed"
    assert any(h.canary_id == "c02" for c in evidence.checks for h in c.hits)
    assert not scan("serializer", evidence.model_dump_json()).hits


def test_compressed_pdf_positive_control_requires_stream_decoding():
    writer = PdfWriter()
    writer.add_blank_page(320, 420)
    # A real compressed page content stream independently exercises hidden text.
    from pypdf.generic import DecodedStreamObject, NameObject

    stream = DecodedStreamObject()
    stream.set_data(f"% {CANARIES['c01']}".encode())
    writer.pages[0][NameObject("/Contents")] = writer._add_object(stream.flate_encode())
    data = io.BytesIO()
    writer.write(data)
    checks = inspect_pdf(data.getvalue())
    assert not next(c for c in checks if c.surface == "bundle_bytes").hits
    assert next(c for c in checks if c.surface == "pdf_streams").hits


@pytest.mark.parametrize("identifier", list(CANARIES))
def test_scanned_image_positive_control_without_text_layer(identifier):
    raster = canary_raster(identifier)
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=320, height=420)
        page.insert_image(pymupdf.Rect(10, 10, 250, 50), stream=raster.tobytes("png"))
        data = pdf.tobytes(deflate=True)
    checks = inspect_pdf(data)
    assert not next(c for c in checks if c.surface == "bundle_bytes").hits
    assert not next(c for c in checks if c.surface == "pdf_text").hits
    assert any(
        h.canary_id == identifier for h in next(c for c in checks if c.surface == "pdf_images").hits
    )


@pytest.mark.parametrize("surface", ["http", "sdk", "telemetry"])
def test_injected_egress_attempt_is_detected_and_never_reaches_export_sink(
    tmp_path, monkeypatch, surface
):
    import httpx

    gate, command, _, _, sink, _ = gate_fixture(tmp_path)
    captured = []

    def capture(client, request, **kwargs):
        captured.append(request.content)
        raise RuntimeError("Synthetic transport denied")

    monkeypatch.setattr(httpx.Client, "send", capture)

    class SyntheticSDK:
        def send(self, value):
            httpx.post("https://synthetic.invalid", content=value.encode(), trust_env=False)

    class SyntheticTelemetry(SyntheticSDK):
        pass

    def injected(*args):
        value = CANARIES["c03"]
        if surface == "sdk":
            SyntheticSDK().send(value)
        elif surface == "telemetry":
            SyntheticTelemetry().send(value)
        else:
            httpx.post("https://synthetic.invalid", content=value.encode(), trust_env=False)

    monkeypatch.setattr(gate._builder._processor, "build", injected)
    with pytest.raises(PrivacyFault):
        gate.export(command, approval(command))
    sink.accept.assert_not_called()
    evidence = report([scan(surface, b"".join(captured))])
    assert evidence.status == "failed"
    assert not scan("serializer", evidence.model_dump_json()).hits


def test_report_schema_rejects_false_pass_and_regenerates_fixtures(tmp_path):
    import runpy
    from pathlib import Path

    from pydantic import ValidationError

    from appraisal_review.domain.privacy_export import LeakScanReport

    with pytest.raises(ValidationError):
        LeakScanReport(scope="python_hooks", checks=(), status="passed")
    root = Path(__file__).resolve().parents[2]
    runpy.run_path(str(root / "scripts/export_privacy_export_contracts.py"))["export"](tmp_path)
    for path in (tmp_path / "examples/privacy-export-v1").glob("*.json"):
        expected = root / path.relative_to(tmp_path)
        assert path.read_bytes() == expected.read_bytes()
        parsed = LeakScanReport.model_validate_json(path.read_bytes())
        assert not scan("serializer", parsed.model_dump_json()).hits
    assert (tmp_path / "schemas/privacy-leak-report-v1.json").read_bytes() == (
        root / "schemas/privacy-leak-report-v1.json"
    ).read_bytes()


@pytest.mark.parametrize(
    "defect",
    [
        "duplicate-hit",
        "uninspected-hit",
        "unknown-id",
        "duplicate-surface",
        "missing-surface",
        "uninspected-surface",
        "concealed-hit",
        "false-failure",
    ],
)
def test_report_rejects_inconsistent_or_unbounded_evidence(defect):
    from pathlib import Path

    from pydantic import ValidationError

    from appraisal_review.domain.privacy_export import LeakScanReport

    root = Path(__file__).resolve().parents[2]
    value = json.loads((root / "examples/privacy-export-v1/synthetic-passed.json").read_bytes())
    check = value["checks"][0]
    hit = {"canary_id": "c01", "count": 1}
    if defect == "duplicate-hit":
        check["hits"] = [hit, hit]
        value["status"] = "failed"
    elif defect == "uninspected-hit":
        check.update(inspected=False, hits=[hit])
        value["status"] = "failed"
    elif defect == "unknown-id":
        check["hits"] = [{"canary_id": "caller-value", "count": 1}]
        value["status"] = "failed"
    elif defect == "duplicate-surface":
        value["checks"].append(check)
    elif defect == "missing-surface":
        value["checks"].pop()
    elif defect == "uninspected-surface":
        check["inspected"] = False
    elif defect == "concealed-hit":
        check["hits"] = [hit]
    else:
        value["status"] = "failed"
    with pytest.raises(ValidationError):
        LeakScanReport.model_validate_json(json.dumps(value))
