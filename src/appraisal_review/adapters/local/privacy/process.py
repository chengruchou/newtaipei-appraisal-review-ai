"""Bounded pipe transport. Threads handle pipes only, never PDF library calls."""

from __future__ import annotations

import math
import os
import signal
import subprocess
import sys
import threading
from contextlib import suppress
from pathlib import Path

from appraisal_review.domain.privacy_scan import ScanIssue


class ScanFailure(Exception):
    def __init__(self, issue: ScanIssue) -> None:
        self.issue = issue
        super().__init__(issue.value)


def run_bounded(
    command: list[str],
    payload: bytes,
    *,
    cwd: Path,
    timeout: float,
    max_output: int,
) -> bytes:
    """Kill the owned Linux process group on deadline or excess output.

    No shell, inherited credentials, raw stderr or on-disk input/output. This
    transport is not an OS network sandbox. Executables and cwd are trusted config.
    """
    if not math.isfinite(timeout) or max_output <= 0:
        raise ScanFailure(ScanIssue.INVALID_INPUT)
    if timeout <= 0:
        raise ScanFailure(ScanIssue.TIMEOUT)
    environment = {key: os.environ[key] for key in ("SystemRoot", "WINDIR") if key in os.environ}
    environment.update(TEMP=str(cwd), TMP=str(cwd), OMP_THREAD_LIMIT="1")
    guarded_workers = {
        "appraisal_review.adapters.local.privacy.pdf_worker",
        "appraisal_review.adapters.local.privacy.sanitize_worker",
        "appraisal_review.adapters.local.privacy.refill_worker",
    }
    if (
        command[:3] == [sys.executable, "-I", "-m"]
        and len(command) == 4
        and command[3] in guarded_workers
    ):
        command = [
            *command[:3],
            "appraisal_review.adapters.local.privacy.offline_worker",
            command[3],
        ]
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=cwd,
            env=environment,
            start_new_session=os.name == "posix",
            creationflags=0x08000000 if os.name == "nt" else 0,
        )
    except OSError:
        raise ScanFailure(ScanIssue.OCR_FAILED) from None
    assert process.stdin is not None and process.stdout is not None
    input_pipe, output_pipe = process.stdin, process.stdout
    timed_out = threading.Event()

    def kill() -> None:
        if process.returncode is not None:
            return
        try:
            if sys.platform == "win32":
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass

    def expire() -> None:
        timed_out.set()
        kill()

    def feed() -> None:
        try:
            input_pipe.write(payload)
            input_pipe.flush()
        except (OSError, ValueError):
            pass
        finally:
            with suppress(OSError, ValueError):
                input_pipe.close()

    writer = threading.Thread(target=feed, daemon=True)
    timer = threading.Timer(timeout, expire)
    output = bytearray()
    writer.start()
    timer.start()
    try:
        while chunk := output_pipe.read(65536):
            if len(output) + len(chunk) > max_output:
                raise ScanFailure(ScanIssue.RESOURCE_LIMIT)
            output.extend(chunk)
        process.wait()
        if timed_out.is_set():
            raise ScanFailure(ScanIssue.TIMEOUT)
        if process.returncode:
            raise ScanFailure(ScanIssue.INVALID_INPUT)
        return bytes(output)
    finally:
        timer.cancel()
        kill()
        process.wait()
        writer.join(timeout=1)
        output_pipe.close()
