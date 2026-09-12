"""Run privacy core tests under Python denial or a Linux network namespace."""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python-hooks", action="store_true")
    parser.add_argument("--suite", choices=("core", "export"), default="core")
    parser.add_argument("--inside-namespace", help=argparse.SUPPRESS)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    work = root / "artifacts/issue-22-p7/offline"
    work.mkdir(parents=True, exist_ok=True)
    os.environ.update(TEMP=str(work), TMP=str(work), PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    if not args.python_hooks and not args.inside_namespace:
        unshare = shutil.which("unshare") if sys.platform == "linux" else None
        if unshare is None:
            print(json.dumps({"scope": "linux_network_namespace", "status": "blocked"}))
            return 2
        parent = os.readlink("/proc/self/ns/net")
        result = subprocess.run(
            [
                unshare,
                "--user",
                "--map-root-user",
                "--net",
                "--",
                sys.executable,
                "-I",
                str(Path(__file__).resolve()),
                "--inside-namespace",
                parent,
                "--suite",
                args.suite,
            ],
            cwd=root,
            check=False,
            capture_output=True,
        )
        try:
            summary = json.loads(result.stdout)
            if summary["scope"] != "linux_network_namespace" or set(summary) != {
                "scope",
                "status",
                "passed",
                "failed",
                "errors",
                "skipped",
                "platform_blocked",
                "suite",
            }:
                raise ValueError("Unexpected report")
            print(json.dumps(summary))
        except (ValueError, KeyError, TypeError):
            print(json.dumps({"scope": "linux_network_namespace", "status": "blocked"}))
        return result.returncode
    if args.inside_namespace:
        if sys.platform != "linux" or os.readlink("/proc/self/ns/net") == args.inside_namespace:
            return 2
        # Native libc bypasses Python socket hooks and proves the kernel boundary.
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        import socket

        descriptor = libc.socket(socket.AF_INET, socket.SOCK_DGRAM, 0)
        if descriptor < 0:
            return 2
        import struct

        address = (
            struct.pack("=H", socket.AF_INET)
            + struct.pack("!H", 9)
            + socket.inet_aton("192.0.2.1")
            + b"\0" * 8
        )
        try:
            outcome = libc.connect(descriptor, address, len(address))
            import errno

            if outcome != -1 or ctypes.get_errno() != errno.ENETUNREACH:
                return 2
        finally:
            libc.close(descriptor)
        child = subprocess.run(
            [sys.executable, "-I", "-c", "import os; print(os.readlink('/proc/self/ns/net'))"],
            check=True,
            capture_output=True,
            timeout=10,
        )
        if child.stdout.decode().strip() != os.readlink("/proc/self/ns/net"):
            return 2
    from appraisal_review.adapters.local.privacy.offline_worker import install_network_guard

    install_network_guard()
    import pytest

    os.chdir(root)

    class Counts:
        def __init__(self):
            self.counts = dict(passed=0, failed=0, errors=0, skipped=0, platform_blocked=0)

        def pytest_runtest_logreport(self, report):
            if report.skipped:
                self.counts["skipped"] += 1
            elif report.failed:
                self.counts["failed" if report.when == "call" else "errors"] += 1
                detail = str(report.longrepr)
                if (
                    sys.platform == "win32"
                    and "socketpair" in detail
                    and "Local privacy network access denied" in detail
                    and "PermissionError" in detail
                ):
                    self.counts["platform_blocked"] += 1
            elif report.when == "call":
                self.counts["passed"] += 1

        def pytest_collectreport(self, report):
            if report.failed:
                self.counts["errors"] += 1

    counts = Counts()
    # Never publish tracebacks, captured canary values or parameterized node IDs.
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        outcome = pytest.main(
            [
                *map(
                    str,
                    sorted(
                        (root / "tests/unit").glob(
                            "test_privacy_export*.py"
                            if args.suite == "export"
                            else "test_privacy_*.py"
                        )
                    ),
                ),
                "-p",
                "no:terminal",
                f"--basetemp={work / 'tests'}",
                "-o",
                f"cache_dir={work / 'cache'}",
                "-o",
                "addopts=",
            ],
            plugins=[counts],
        )
    print(
        json.dumps(
            {
                "scope": "linux_network_namespace" if args.inside_namespace else "python_hooks",
                "suite": args.suite,
                "status": "passed"
                if outcome == 0
                else (
                    "blocked"
                    if counts.counts["platform_blocked"] > 0
                    and counts.counts["platform_blocked"]
                    == counts.counts["failed"] + counts.counts["errors"]
                    else "failed"
                ),
                **counts.counts,
            }
        )
    )
    return int(outcome)


if __name__ == "__main__":
    raise SystemExit(main())
