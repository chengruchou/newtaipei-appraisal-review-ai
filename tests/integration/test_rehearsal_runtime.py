"""Actual loopback HTTP probes; synthetic transport never supplies browser evidence."""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from appraisal_review.domain.service_contracts import ServiceErrorCode, ServiceProblem

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts/run_rehearsal.py"


def run_runner(*args):
    result = subprocess.run(
        [sys.executable, str(RUNNER), *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert not result.stderr
    report = json.loads(result.stdout)
    assert report["publication"] == "Draft"
    assert report["live_acceptance"] is False
    assert "SYNTHETIC_REHEARSAL_TRANSPORT_CANARY" not in result.stdout
    return result.returncode, report


@contextmanager
def serve(module, *, root=ROOT, factory=False, expected_health=200):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        listener.listen(16)
        command = [
            sys.executable,
            "-m",
            "uvicorn",
            module,
            "--fd",
            str(listener.fileno()),
            "--no-access-log",
        ]
        if factory:
            command.append("--factory")
        process = subprocess.Popen(
            command,
            pass_fds=(listener.fileno(),),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=root,
            env={
                **os.environ,
                "PYTHONPATH": str(root / "src") + os.pathsep + str(root),
                "PYTHONDONTWRITEBYTECODE": "1",
                "AWS_EC2_METADATA_DISABLED": "true",
            },
        )
        try:
            # The first HTTP call blocks until uvicorn takes ownership of the socket;
            # the listener is already bound, so there is no free-port race.
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise AssertionError("Runtime HTTP process exited before readiness")
                import http.client

                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=0.25)
                try:
                    connection.request("GET", "/ping")
                    if connection.getresponse().status == expected_health:
                        break
                except (OSError, http.client.HTTPException):
                    time.sleep(0.05)
                finally:
                    connection.close()
            else:
                raise AssertionError("Runtime HTTP process did not become ready")
            yield port
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def test_runner_plan_and_live_unavailability_are_honest():
    code, report = run_runner("--execution", "plan")
    assert code == 0
    assert report["validation_passed"] is False
    assert all(check["status"] == "not_run" for check in report["checks"])
    assert {check["execution_class"] for check in report["checks"]} == {
        "live_browser_automation",
        "live_browser_network_capture",
        "live_aws_observation",
        "local_privacy_and_pdf",
        "exact_revision_ci",
        "independent_human_acceptance",
    }
    code, report = run_runner("--execution", "live")
    assert code == 2
    assert report["reason"] == "live_collectors_not_integrated"


def test_actual_localhost_synthetic_runtime_does_not_satisfy_private_error_boundary():
    # This existing synthetic server returns FastAPI validation input in 422 errors.
    # It supplies real TCP rejection evidence, and explicitly fails the privacy probe.
    with serve("cloud_tests.runtime:app") as port:
        code, report = run_runner(
            "--execution", "localhost", "--port", str(port), "--expect-runtime", "healthy"
        )
    assert code == 1
    assert report["scope"] == "localhost_http_rejection_only"
    probes = {probe["probe"]: probe for probe in report["probes"]}
    assert probes["ping"]["status"] == "passed"
    assert probes["empty_command"]["reason"] == "unexpected_problem_response"
    assert probes["unknown_command"]["reason"] == "raw_input_leaked"
    assert probes["health_after_rejection"]["status"] == "passed"


def test_integrated_runtime_http_rejects_untrusted_commands():
    # This stacked delivery includes the actual Runtime; absence must fail CI.
    assert (ROOT / "src/appraisal_review/adapters/aws/runtime_app.py").is_file()
    with serve(
        "appraisal_review.adapters.aws.runtime_app:app", root=ROOT, expected_health=503
    ) as port:
        code, report = run_runner("--execution", "localhost", "--port", str(port))
    assert code == 0
    assert report["validation_passed"] is True
    assert report["production_ready"] is False
    assert report["runtime_expectation"] == "unready"
    assert len(report["probes"]) == 5
    assert all(probe["status"] == "passed" for probe in report["probes"])


def test_localhost_runner_rejects_redirect_targets_and_arbitrary_urls():
    code, report = run_runner("--execution", "localhost", "--port", "https://private.invalid")
    assert code == 2
    assert report["reason"] == "invalid_arguments"


def test_localhost_rejects_escaped_leak_hidden_by_duplicate_json_key():
    canary = "SYNTHETIC_REHEARSAL_TRANSPORT_CANARY"
    escaped = "".join(f"\\u{ord(character):04x}" for character in canary)
    expected = ServiceProblem(code=ServiceErrorCode.VALIDATION).model_dump_json()
    malicious = ('{"message":"' + escaped + '",' + expected[1:]).encode()
    assert canary.encode() not in malicious
    # The old parser silently discarded the leaked first message.
    assert json.loads(malicious) == json.loads(expected)

    class AdversarialServer(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_GET(self):
            self.send_response(503)
            self.end_headers()
            self.wfile.write(
                ServiceProblem(code=ServiceErrorCode.CAPABILITY).model_dump_json().encode()
            )

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_response(422)
            self.end_headers()
            self.wfile.write(malicious)

    with HTTPServer(("127.0.0.1", 0), AdversarialServer) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            code, report = run_runner("--execution", "localhost", "--port", str(server.server_port))
        finally:
            server.shutdown()
            worker.join(timeout=5)
    assert code == 1
    assert report["validation_passed"] is False
    probes = {probe["probe"]: probe for probe in report["probes"]}
    assert probes["ping"]["status"] == "passed"
    assert probes["health_after_rejection"]["status"] == "passed"
    for name in ("empty_command", "unknown_command", "malformed_command"):
        assert probes[name]["status"] == "failed"
        assert probes[name]["reason"] == "invalid_response_json"
