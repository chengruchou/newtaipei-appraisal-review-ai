"""Build, start, inspect and stop the explicit local synthetic Docker stack."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
BASE_IMAGE = "python@sha256:3949e4271b0a3ff82afac7306764c313dcc8edeeb89c0376a3c2ac6007c66b1d"
LABEL = "appraisal.local-validation"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def private_directory(path: Path) -> Path:
    path = path.absolute()
    if path.resolve() != path:
        raise ValueError("Workspace must not use symlinks")
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    if path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
        raise ValueError("Workspace must be owned and private (0700)")
    return path


def write_json(path: Path, value: Any) -> None:
    if path.is_symlink():
        raise ValueError("Output must not be a symlink")
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    with temporary.open("x", encoding="utf8") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def clean_environment() -> dict[str, str]:
    excluded = (
        "AWS_",
        "REVIEW_",
        "APPRAISAL_",
        "VITE_",
        "NPM_CONFIG_",
        "npm_config_",
        "BUILDX_",
        "BUILDKIT_",
    )
    return {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(excluded)
        and k not in {"NODE_OPTIONS", "PYTHONPATH", "DOCKER_HOST", "DOCKER_CONTEXT"}
    }


class Commands:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.directory = private_directory(workspace / "commands")
        self.docker_endpoint: str | None = None

    def pin_local_docker(self) -> str:
        if self.docker_endpoint is not None:
            return self.docker_endpoint
        if os.environ.get("BUILDX_BUILDER") or os.environ.get("BUILDKIT_HOST"):
            raise ValueError("External builder overrides are not supported")
        endpoint = os.environ.get("DOCKER_HOST")
        if endpoint and os.environ.get("DOCKER_CONTEXT"):
            raise ValueError("Ambiguous Docker endpoint overrides are not supported")
        if not endpoint:
            context = os.environ.get("DOCKER_CONTEXT") or self._run("docker", "context", "show")
            endpoint = json.loads(
                self._run(
                    "docker",
                    "context",
                    "inspect",
                    context,
                    "--format",
                    "{{json .Endpoints.docker.Host}}",
                )
            )
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "unix"
            or parsed.netloc
            or parsed.query
            or parsed.fragment
            or not Path(parsed.path).is_absolute()
            or endpoint != "unix://" + parsed.path
        ):
            raise ValueError("Only a local Unix-socket Docker endpoint is supported")
        socket = Path(parsed.path).resolve(strict=True)
        info = socket.stat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid not in {0, os.getuid()}:
            raise ValueError("Docker endpoint must be an owned local Unix socket")
        self.docker_endpoint = "unix://" + str(socket)
        return self.docker_endpoint

    def require_endpoint(self, record: dict[str, Any]) -> None:
        if self.pin_local_docker() != record.get("docker_endpoint"):
            raise ValueError("Recorded stack and current local Docker endpoint differ")

    def run(self, *args: str, cwd: Path | None = None, timeout: int = 900) -> str:
        if args[0] == "docker":
            args = ("docker", "--host", self.pin_local_docker(), *args[1:])
        return self._run(*args, cwd=cwd, timeout=timeout)

    def _run(self, *args: str, cwd: Path | None = None, timeout: int = 900) -> str:
        name = uuid4().hex
        write_json(self.directory / f"{name}.json", {"command": args, "cwd": str(cwd or ROOT)})
        started = time.monotonic()
        result = subprocess.run(
            args,
            cwd=cwd or ROOT,
            env=clean_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
        log = self.directory / f"{name}.log"
        log.write_bytes(result.stdout)
        log.chmod(0o600)
        write_json(
            self.directory / f"{name}-result.json",
            {"exit_code": result.returncode, "seconds": round(time.monotonic() - started, 3)},
        )
        if result.returncode:
            raise RuntimeError(f"Command failed; inspect private command log {log.name}")
        return result.stdout.decode().strip()


def copy_regular(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_file() or source.resolve() != source.absolute():
        raise ValueError("Build input must be a regular non-aliased file")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def verify_default_builder(commands: Commands) -> None:
    builder = commands.run("docker", "buildx", "inspect", "default")
    drivers = [
        line.split(":", 1)[1].strip() for line in builder.splitlines() if line.startswith("Driver:")
    ]
    endpoints = [
        line.split(":", 1)[1].strip()
        for line in builder.splitlines()
        if line.startswith("Endpoint:")
    ]
    # The daemon-bound driver reports its CLI default context alias on Desktop.
    if endpoints == ["default"]:
        endpoints = [
            json.loads(
                commands.run(
                    "docker",
                    "context",
                    "inspect",
                    "default",
                    "--format",
                    "{{json .Endpoints.docker.Host}}",
                )
            )
        ]
    if drivers != ["docker"] or endpoints != [commands.docker_endpoint]:
        raise ValueError("Default builder must be bound to the verified local Docker socket")


def build(workspace: Path, commands: Commands) -> None:
    if platform.system() not in {"Darwin", "Linux"} or platform.machine() not in {
        "arm64",
        "aarch64",
    }:
        raise ValueError("Only ARM64 macOS/Linux hosts are supported by this launcher")
    if commands.run("docker", "info", "--format", "{{.Architecture}} {{.OSType}}") != (
        "aarch64 linux"
    ):
        raise ValueError("A running local Linux ARM64 Docker daemon is required")
    verify_default_builder(commands)
    node = commands.run("node", "--version")
    major, minor = map(int, node.removeprefix("v").split(".")[:2])
    if not (major >= 24 or (major == 22 and minor >= 13)):
        raise ValueError("Node 22.13+ (22.x) or Node 24+ is required")
    destination = private_directory(workspace / ("build-" + uuid4().hex))
    frontend = private_directory(destination / "frontend")
    # Build in an isolated directory; never copy a caller's .env, dist or node_modules.
    for name in (
        "package.json",
        "package-lock.json",
        "openapi.json",
        "tsconfig.json",
        "vite.config.ts",
        "playwright.config.ts",
        "playwright.real.config.ts",
        "playwright.local-stack.config.ts",
        "index.html",
    ):
        copy_regular(ROOT / "web" / name, frontend / name)
    for folder in ("src", "tests", "e2e", "e2e-real", "e2e-local-stack", "scripts"):
        for path in (ROOT / "web" / folder).rglob("*"):
            if path.is_file() and path.suffix in {".ts", ".tsx", ".json", ".css", ".mjs"}:
                copy_regular(path, frontend / path.relative_to(ROOT / "web"))
    for name in ("npm-user.conf", "npm-global.conf"):
        (destination / name).write_text("")
    commands.run(
        "npm",
        "ci",
        "--cache",
        str(workspace / "npm-cache"),
        "--userconfig",
        str(destination / "npm-user.conf"),
        "--globalconfig",
        str(destination / "npm-global.conf"),
        cwd=frontend,
    )
    commands.run("npm", "run", "build", cwd=frontend)
    commands.run("npm", "run", "check:artifact", cwd=frontend)
    context = private_directory(destination / "context")
    for path in (ROOT / "src").rglob("*.py"):
        copy_regular(path, context / path.relative_to(ROOT))
    for name in ("services-20260722.json", "quotas-20260722.json"):
        path = Path("src/appraisal_review/data/competition") / name
        copy_regular(ROOT / path, context / path)
    for path in (frontend / "dist").rglob("*"):
        if path.is_file():
            if path.suffix not in {".html", ".js", ".mjs", ".css"}:
                raise ValueError("Unexpected frontend build artifact")
            copy_regular(path, context / "web" / path.relative_to(frontend / "dist"))
    for name in ("requirements.lock", "build-tooling.lock"):
        copy_regular(ROOT / "infra/runtime" / name, context / name)
    for name in ("Dockerfile", "Dockerfile.dockerignore", "server.py"):
        copy_regular(ROOT / "infra/local-validation" / name, context / name)
    manifest = {
        p.relative_to(context).as_posix(): digest(p)
        for p in sorted(context.rglob("*"))
        if p.is_file()
    }
    write_json(destination / "context-manifest.json", manifest)
    tag = "appraisal-local-validation:" + uuid4().hex
    commands.run(
        "docker",
        "buildx",
        "build",
        "--builder",
        "default",
        "--platform",
        "linux/arm64",
        "--load",
        "--provenance=false",
        "--sbom=false",
        "--tag",
        tag,
        "--build-arg",
        "BASE_IMAGE=" + BASE_IMAGE,
        str(context),
    )
    info = json.loads(commands.run("docker", "image", "inspect", tag))[0]
    if (info["Architecture"], info["Os"], info["Config"]["User"]) != (
        "arm64",
        "linux",
        "10001:10001",
    ):
        raise ValueError("Actual image architecture or non-root identity differs")
    write_json(
        workspace / "build.json",
        {
            "image_id": info["Id"],
            "docker_endpoint": commands.docker_endpoint,
            "tag": tag,
            "base_image": BASE_IMAGE,
            "architecture": info["Architecture"],
            "os": info["Os"],
            "node": node,
            "source_commit": commands.run("git", "rev-parse", "HEAD"),
            "context": str(context),
            "context_manifest": str(destination / "context-manifest.json"),
            "context_manifest_sha256": digest(destination / "context-manifest.json"),
            "security": (
                "Unresolved base OS findings remain tracked in issue 50; not cloud approval"
            ),
        },
    )
    print(
        "Built local Linux ARM64 workbench/API image; build.json records exact inputs.", flush=True
    )


def read_record(workspace: Path, name: str) -> dict[str, Any]:
    path = workspace / name
    if path.is_symlink() or path.stat().st_mode & 0o077 or path.stat().st_uid != os.getuid():
        raise ValueError("Private owned launcher record required")
    value: dict[str, Any] = json.loads(path.read_bytes())
    return value


def checked_container(commands: Commands, record: dict[str, Any]) -> dict[str, Any]:
    commands.require_endpoint(record)
    info: dict[str, Any] = json.loads(
        commands.run("docker", "container", "inspect", record["container"])
    )[0]
    if (
        info["Config"]["Labels"].get(LABEL) != record["identity"]
        or info["Image"] != record["image_id"]
        or info["Config"]["User"] != "10001:10001"
    ):
        raise ValueError("Container no longer matches this private stack record")
    return info


def wait_ready(commands: Commands, record: dict[str, Any]) -> None:
    deadline = time.monotonic() + 180
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.monotonic() < deadline:
        try:
            with opener.open(record["origin"] + "/readyz", timeout=2) as response:
                if json.load(response) == {"status": "ready", "mode": "synthetic-demo"}:
                    return
        except (OSError, urllib.error.HTTPError):
            pass
        info = checked_container(commands, record)
        if not info["State"]["Running"]:
            raise RuntimeError("Local API startup failed; retained container and private state")
        time.sleep(1)
    raise RuntimeError("Readiness timed out; retained container and private state")


def start(workspace: Path, commands: Commands, *, demo: bool, port: int) -> None:
    if not demo or not 1024 <= port <= 65535:
        raise ValueError("Start requires --demo and a nonprivileged loopback port")
    if (workspace / "stack.json").exists():
        raise ValueError("Stack already exists; use status or restart to retain state")
    built = read_record(workspace, "build.json")
    commands.require_endpoint(built)
    identity = uuid4().hex
    name = "appraisal-local-" + identity
    record = {
        "identity": identity,
        "container": name,
        "network": name,
        "volume": name,
        "image_id": built["image_id"],
        "docker_endpoint": commands.docker_endpoint,
        "origin": f"http://127.0.0.1:{port}",
    }
    write_json(workspace / "stack.json", record)
    commands.run(
        "docker",
        "network",
        "create",
        "--opt",
        "com.docker.network.bridge.enable_ip_masquerade=false",
        "--label",
        LABEL + "=" + identity,
        name,
    )
    commands.run("docker", "volume", "create", "--label", LABEL + "=" + identity, name)
    commands.run(
        "docker",
        "run",
        "--detach",
        "--name",
        name,
        "--label",
        LABEL + "=" + identity,
        "--network",
        name,
        "--publish",
        f"127.0.0.1:{port}:8080",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "128",
        "--memory",
        "1536m",
        "--cpus",
        "2",
        "--restart",
        "no",
        "--log-driver",
        "none",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,noexec,size=256m,mode=1777",
        "--mount",
        f"type=volume,src={name},dst=/state",
        built["image_id"],
        "--mode",
        "synthetic-demo",
        "--origin",
        record["origin"],
        "--state",
        "/state",
    )
    wait_ready(commands, record)
    copy_session(workspace, commands, record)
    print("Ready: " + record["origin"] + "; private session.json is never served.", flush=True)


def copy_session(workspace: Path, commands: Commands, record: dict[str, Any]) -> None:
    target = workspace / "session.json"
    if target.is_symlink():
        raise ValueError("Private session destination must not be a symlink")
    commands.run("docker", "cp", record["container"] + ":/state/fixture.json", str(target))
    target.chmod(0o600)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("build", "start", "status", "restart", "stop", "export-ui")
    )
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--port", type=int, default=4174)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        workspace = private_directory(args.workspace)
        commands = Commands(workspace)
        if args.action == "build":
            build(workspace, commands)
        elif args.action == "start":
            start(workspace, commands, demo=args.demo, port=args.port)
        else:
            record = read_record(workspace, "stack.json")
            info = checked_container(commands, record)
            if args.action == "stop":
                commands.run("docker", "stop", "--time", "30", record["container"])
                print("Stopped; container, private volume and evidence retained.")
            elif args.action == "restart":
                commands.run("docker", "restart", "--time", "30", record["container"])
                wait_ready(commands, record)
                copy_session(workspace, commands, record)
                print("Ready after restart: " + record["origin"])
            elif args.action == "export-ui":
                target = workspace / "ui"
                if target.exists():
                    raise ValueError("UI export destination already exists")
                commands.run(
                    "docker", "cp", record["container"] + ":/opt/appraisal/web", str(target)
                )
                print("Built static UI exported for the host companion server.")
            else:
                print(
                    json.dumps(
                        {
                            "running": info["State"]["Running"],
                            "origin": record["origin"],
                            "mode": "synthetic-demo",
                            "state_retained": True,
                        }
                    )
                )
    except Exception as error:
        # Never print an exception containing request/configuration material.
        parser.exit(
            1, f"Local stack operation failed ({type(error).__name__}); inspect private logs.\n"
        )


if __name__ == "__main__":
    main()
