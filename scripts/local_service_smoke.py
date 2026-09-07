"""Real loopback HTTP and invocation acceptance using freshly generated synthetic PDFs."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
from local_service_fixture import create_fixture, digest
from pypdf import PdfReader

from appraisal_review.domain.service_contracts import ServiceResult


@contextmanager
def server(config: Path | None):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    env = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        "PYMUPDF_SUGGEST_LAYOUT_ANALYZER": "0",
        "PYMUPDF_MESSAGE": "fd:2",
    }
    env.pop("APPRAISAL_LOCAL_CONFIG", None)
    if config is not None:
        env["APPRAISAL_LOCAL_CONFIG"] = str(config)
    child = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "appraisal_review.local_service:app_from_environment",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "error",
        ],
        env=env,
    )
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}", timeout=15, trust_env=False
        ) as client:
            for _ in range(100):
                if child.poll() is not None:
                    raise RuntimeError("Local HTTP child exited")
                try:
                    if client.get("/health").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError("Local HTTP startup timed out")
            yield client
    finally:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)


def smoke(directory: Path) -> dict:
    os.environ["PYMUPDF_SUGGEST_LAYOUT_ANALYZER"] = "0"
    config = asyncio.run(create_fixture(directory))
    request = json.loads((directory / "request.json").read_text())
    write = json.loads((directory / "request-write.json").read_text())
    sources = [d.path for d in config.inputs.documents] + [config.writer.template_path]
    hashes = {path: digest(path) for path in sources}
    with server(None) as client:
        assert client.post("/v1/reviews", json={}).status_code == 422
        assert client.post("/v1/reviews", json=request).status_code == 503
        assert "detail" in client.post("/v1/validate", json={}).json()
    with server(directory / "config.json") as client:
        response = client.post("/v1/reviews", json=request)
        assert response.status_code == 200 and response.json()["status"] == "verified"
        invoke = subprocess.run(
            [
                sys.executable,
                "-m",
                "appraisal_review.local_service",
                "invoke",
                "--config",
                str(directory / "config.json"),
                "--request",
                str(directory / "request.json"),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        invoked = json.loads(invoke.stdout)
        assert set(invoked) == set(response.json())
        assert invoked["case_review"] == response.json()["case_review"]
        assert invoked["artifact_status"] == "not_requested"
        denied = {**request, "case_document_uri": "file:///private/not-allowed.pdf"}
        denied_path = directory / "request-source-mismatch.json"
        denied_path.write_text(json.dumps(denied) + "\n")
        denied_http = client.post("/v1/reviews", json=denied)
        assert denied_http.status_code == 200 and denied_http.json()["status"] == "failed"
        assert denied_http.json()["case_review"] is None
        for command in ("invoke", "run"):
            rejected = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "appraisal_review.local_service",
                    command,
                    "--config",
                    str(directory / "config.json"),
                    "--request",
                    str(denied_path),
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
            assert rejected.stderr == ""
            assert "private" not in rejected.stdout and "not-allowed" not in rejected.stdout
            if command == "invoke":
                assert (
                    json.loads(rejected.stdout)["verification"]
                    == denied_http.json()["verification"]
                )
            else:
                rejected_envelope = ServiceResult.model_validate_json(rejected.stdout)
                assert rejected_envelope.execution_status == "succeeded"
                assert rejected_envelope.business_status == "failed"
                assert rejected_envelope.problem is None and not rejected_envelope.findings
                assert not rejected_envelope.artifacts
                assert rejected_envelope.verification.critical_errors[0].code == "source_binding"
                (directory / "result-source-binding-failed.json").write_text(
                    rejected_envelope.model_dump_json(indent=2) + "\n"
                )
        assert not list(config.writer.output_directory.iterdir())
        written = client.post("/v1/reviews", json=write)
        assert written.status_code == 200
        assert (
            written.json()["status"] == "completed"
            and written.json()["artifact_status"] == "written"
        )
        (directory / "http-written.json").write_text(json.dumps(written.json(), indent=2) + "\n")
        pdf = PdfReader(directory / "output/completed.pdf")
        assert len(pdf.pages) == 1 and "+5.00%" in pdf.pages[0].extract_text()
        for route in ("/v1/review-jobs", "/v1/human-tasks", "/v1/artifacts"):
            assert client.get(route).status_code == 404
    # New envelope is a separate local boundary; use a new output to avoid overwriting.
    write["output_pdf_uri"] = (directory / "output/manifest.pdf").as_uri()
    (directory / "request-manifest.json").write_text(json.dumps(write) + "\n")
    run_command = [
        sys.executable,
        "-m",
        "appraisal_review.local_service",
        "run",
        "--config",
        str(directory / "config.json"),
        "--request",
        str(directory / "request-manifest.json"),
    ]
    executed = subprocess.run(run_command, capture_output=True, text=True, timeout=30, check=True)
    assert executed.stderr == ""
    envelope = ServiceResult.model_validate_json(executed.stdout)
    assert envelope.business_status == "completed" and envelope.durable is False
    assert envelope.artifacts[0].content_hash == digest(directory / "output/manifest.pdf")
    assert envelope.artifacts[0].field_ids == ("road-rate",)
    (directory / "result-written.json").write_text(envelope.model_dump_json(indent=2))
    assert envelope.run.revision.case_id == config.inputs.identity.case_id
    assert envelope.run.revision.revision_id == config.revision_id
    assert envelope.run.revision.material_digest == config.expected_material_digest
    old_outputs = {
        name: digest(directory / "output" / name) for name in ("completed.pdf", "manifest.pdf")
    }
    # Retrying this successful destination fails without claiming or deleting the old PDF.
    retried = subprocess.run(run_command, capture_output=True, text=True, timeout=30, check=True)
    assert retried.stderr == ""
    failed = ServiceResult.model_validate_json(retried.stdout)
    assert failed.execution_status == "failed" and failed.problem.code == "execution_failed"
    assert not failed.artifacts and failed.run.run_id != envelope.run.run_id
    (directory / "result-retry-failed.json").write_text(failed.model_dump_json(indent=2))
    write["output_pdf_uri"] = (directory / "output/blocked.pdf").as_uri()
    with server(directory / "config-needs-review.json") as client:
        blocked = client.post("/v1/reviews", json=write)
        assert blocked.status_code == 200 and blocked.json()["status"] == "needs_review"
        assert blocked.json()["case_review"]["findings"]
        assert blocked.json()["pdf_result"] is None
        assert not (directory / "output/blocked.pdf").exists()
        write["output_pdf_uri"] = (directory / "output/manifest.pdf").as_uri()
        reused = client.post("/v1/reviews", json=write)
        assert reused.status_code == 200 and reused.json()["status"] == "needs_review"
        assert reused.json()["output_pdf_uri"] is None and reused.json()["pdf_result"] is None
    assert old_outputs == {name: digest(directory / "output" / name) for name in old_outputs}
    assert hashes == {path: digest(path) for path in sources}
    report = {
        "fixture": "synthetic-real-parser-local-writer",
        "durable": False,
        "http_statuses_checked": [200, 422, 503],
        "legacy_shape_preserved": True,
        "invocation_parity": True,
        "source_binding_diagnostic_preserved": True,
        "source_binding_http_invocation_parity": True,
        "source_binding_no_path_echo": True,
        "reopened_pdfs": 2,
        "source_hashes_unchanged": True,
        "needs_review_has_findings": True,
        "blocked_output_exists": False,
        "artifact_sha256": envelope.artifacts[0].content_hash,
        "run_cli_exit_code": executed.returncode,
        "failed_run_cli_exit_code": retried.returncode,
        "json_channels_clean": True,
        "existing_success_preserved": True,
        "outputs": {
            "output/completed.pdf": {
                "producer": "POST /v1/reviews",
                "response_file": "http-written.json",
                "case_id": written.json()["case_id"],
                "sha256": old_outputs["completed.pdf"],
                "byte_size": (directory / "output/completed.pdf").stat().st_size,
            },
            "output/manifest.pdf": {
                "producer": "local_service run",
                "response_file": "result-written.json",
                "manifest_pointer": "/artifacts/0",
                "run": envelope.run.model_dump(mode="json"),
                "artifact_id": str(envelope.artifacts[0].artifact_id),
                "sha256": old_outputs["manifest.pdf"],
                "byte_size": (directory / "output/manifest.pdf").stat().st_size,
                "page_count": envelope.artifacts[0].page_count,
                "field_ids": list(envelope.artifacts[0].field_ids),
            },
        },
    }
    (directory / "smoke-report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> None:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument(
        "--directory",
        type=Path,
        help="Fresh directory; defaults to automatically removed temp data",
    )
    args = cli.parse_args()
    if args.directory:
        report = smoke(args.directory.resolve())
    else:
        with TemporaryDirectory(prefix="appraisal-service-") as root:
            report = smoke(Path(root) / "fixture")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
