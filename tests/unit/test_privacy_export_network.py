"""Python hook probes; Linux native/network namespace acceptance is a separate runner."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from appraisal_review.adapters.local.privacy.process import run_bounded


@pytest.mark.parametrize(
    "probe", ["socket", "dns", "http", "httpx", "sdk", "telemetry", "model_import"]
)
def test_network_attempts_are_denied_before_transport_and_errors_are_fixed(tmp_path, probe):
    # Entire probe is a child process: the irreversible audit hook never modifies pytest.
    code = """
import json, sys
from appraisal_review.adapters.local.privacy.offline_worker import install_network_guard
install_network_guard()
probe = sys.argv[1]
try:
    if probe == "socket":
        import socket
        socket.socket()
    elif probe == "dns":
        import socket
        socket.getaddrinfo("synthetic.example.invalid", 443)
    elif probe == "httpx":
        import httpx
        httpx.get("http://127.0.0.1:9", trust_env=False)
    elif probe == "model_import":
        import importlib.util
        spec = importlib.util.spec_from_file_location("synthetic_model_loader", sys.argv[2])
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    else:
        import urllib.request
        class SyntheticSDK:
            def send(self):
                urllib.request.urlopen("http://127.0.0.1:9", data=b"synthetic")
        class SyntheticTelemetry(SyntheticSDK):
            pass
        if probe == "sdk":
            SyntheticSDK().send()
        elif probe == "telemetry":
            SyntheticTelemetry().send()
        else:
            urllib.request.urlopen("http://127.0.0.1:9")
except Exception as error:
    pending, visited, denied = [error], set(), False
    while pending:
        current = pending.pop()
        if current is None or id(current) in visited:
            continue
        visited.add(id(current))
        denied |= (isinstance(current, PermissionError) and
                   str(current) == "Local privacy network access denied")
        pending.extend([getattr(current, "reason", None), current.__cause__, current.__context__])
    print(json.dumps({"denied": denied}))
else:
    print(json.dumps({"denied": False}))
"""
    loader = tmp_path / "synthetic_loader.py"
    loader.write_text(
        "import socket\nsocket.create_connection(('127.0.0.1', 9))\n", encoding="utf-8"
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-c", code, probe, str(loader)],
        capture_output=True,
        timeout=15,
        check=True,
    )
    assert json.loads(completed.stdout) == {"denied": True}
    assert completed.stderr == b""


def test_real_worker_launches_install_guard_before_target_imports(tmp_path, monkeypatch):
    import appraisal_review.adapters.local.privacy.process as transport

    original = transport.subprocess.Popen
    commands = []

    def record(command, **kwargs):
        commands.append(command)
        return original(command, **kwargs)

    monkeypatch.setattr(transport.subprocess, "Popen", record)
    from uuid import uuid4

    import pymupdf

    from appraisal_review.adapters.local.privacy.source import (
        IsolatedPrivacyPDF,
        LocalSnapshotStore,
    )

    source = tmp_path / "synthetic.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page()
        pdf.save(source)
    store = LocalSnapshotStore(tmp_path, IsolatedPrivacyPDF(tmp_path))
    store.capture(source.name, case_id=uuid4())
    assert commands and all(
        command[3] == "appraisal_review.adapters.local.privacy.offline_worker"
        for command in commands
    )


def test_transport_discards_stderr_and_does_not_inherit_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNTHETIC_PRIVATE_CREDENTIAL", "synthetic-secret")
    code = (
        "import os,sys; sys.stderr.write('synthetic-private-error'); "
        "sys.stdout.write(str('SYNTHETIC_PRIVATE_CREDENTIAL' in os.environ))"
    )
    result = run_bounded(
        [sys.executable, "-I", "-c", code],
        b"",
        cwd=Path(tmp_path),
        timeout=10,
        max_output=100,
    )
    assert result == b"False"


@pytest.mark.skipif(sys.platform != "linux", reason="Linux anonymous socketpair acceptance")
def test_linux_asyncio_local_socketpair_works_under_denial():
    code = """
from appraisal_review.adapters.local.privacy.offline_worker import install_network_guard
install_network_guard()
import asyncio, socket
async def run():
    await asyncio.sleep(0)
asyncio.run(run())
try:
    socket.socket(socket.AF_UNIX).connect('/synthetic-not-present')
except PermissionError:
    print('denied')
else:
    raise RuntimeError('Local proxy connection allowed')
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", code],
        capture_output=True,
        timeout=10,
        check=True,
    )
    assert result.stdout.strip() == b"denied"
    assert result.stderr == b""
