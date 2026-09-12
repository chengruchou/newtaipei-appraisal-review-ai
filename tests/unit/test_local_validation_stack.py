"""Local packaging boundaries without a Docker daemon or cloud dependency."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


S = load("local_validation_server", "infra/local-validation/server.py")
L = load("local_validation_launcher", "scripts/local_validation_stack.py")


@pytest.fixture
def configured(tmp_path):
    tmp_path.chmod(0o700)
    assets = tmp_path / "ui"
    assets.mkdir()
    (assets / "index.html").write_text("<html>Local workbench</html>")
    (assets / "assets").mkdir()
    (assets / "assets/main.js").write_text("export const local = true;")
    fixture = tmp_path / "session.json"
    fixture.write_text(
        json.dumps(
            {
                "api_base_url": "http://127.0.0.1:8766",
                "session_token": "s" * 36,
                "empty_job_id": "f2e3be0f-1670-43bb-b6a9-f9491f476a05",
            }
        )
    )
    fixture.chmod(0o600)
    return S.LocalStack(
        mode="host-companion",
        origin="http://127.0.0.1:4174",
        assets=assets,
        api_fixture=fixture,
        privacy_base="http://127.0.0.1:8788",
    )


@pytest.mark.parametrize(
    "origin",
    [
        "https://127.0.0.1:4174",
        "http://localhost:4174",
        "http://0.0.0.0:4174",
        "http://127.0.0.1:4174/",
        "http://127.0.0.1:4174?x=1",
        "http://user@127.0.0.1:4174",
        "http://127.0.0.1:80",
        "http://127.0.0.1:99999",
    ],
)
def test_origin_requires_exact_numeric_nonprivileged_loopback(origin):
    with pytest.raises(ValueError):
        S.loopback_origin(origin)


@pytest.mark.parametrize(
    "path",
    [
        "/session.json",
        "/fixture.json",
        "/bootstrap.json",
        "/.env",
        "/server.py",
        "/__test__/drop-next-response",
        "/__test__/gateway-next-response",
        "/assets/missing.js",
        "/assets/../../session.json",
        "/readyz/extra",
    ],
)
def test_private_and_test_control_paths_never_become_spa_success(configured, path):
    with TestClient(configured.app, base_url=configured.origin) as client:
        assert client.get(path).status_code == 404


def test_static_deep_routes_and_config_do_not_expose_session(configured):
    with TestClient(configured.app, base_url=configured.origin) as client:
        for path in ("/", "/jobs/example", "/tasks/example", "/privacy"):
            reply = client.get(path)
            assert reply.status_code == 200
            assert reply.headers["content-type"].startswith("text/html")
            assert reply.headers["x-content-type-options"] == "nosniff"
        assert client.get("/assets/main.js").status_code == 200
        config = client.get("/local-config.json")
        assert config.json() == {"privacy_bridge_base": "http://127.0.0.1:8788"}
        assert configured.fixture["session_token"] not in config.text
        assert client.post("/local-config.json").status_code == 405
        assert client.get("/local-config.json?other=1").status_code == 404


def test_wrong_host_or_cross_origin_cannot_reach_api(configured):
    calls = []
    configured.forward = lambda *args: calls.append(args) or (200, {}, b"ok")
    with TestClient(configured.app, base_url=configured.origin) as client:
        assert client.get("/v1/review-jobs", headers={"Host": "localhost:4174"}).status_code == 403
        assert (
            client.post("/v1/review-jobs", headers={"Origin": "https://example.org"}).status_code
            == 403
        )
        assert (
            client.get("/", headers=[("Host", "127.0.0.1:4174"), ("Host", "evil")]).status_code
            == 403
        )
    assert calls == []


def test_proxy_preserves_client_auth_and_canonical_response_not_fixture_grant(configured):
    calls = []

    def forwarded(*args):
        calls.append(args)
        return 409, {"content-type": "application/json"}, b'{"code":"version_conflict"}'

    configured.forward = forwarded
    with TestClient(configured.app, base_url=configured.origin) as client:
        response = client.post(
            "/v1/review-tasks/task/responses",
            content=b'{"exact":true}',
            headers={
                "Authorization": "Bearer " + "t" * 36,
                "Content-Type": "application/json",
                "Cookie": "private=x",
            },
        )
        assert response.status_code == 409
        assert response.content == b'{"code":"version_conflict"}'
        assert calls[0] == (
            "POST",
            "/v1/review-tasks/task/responses",
            {
                "authorization": "Bearer " + "t" * 36,
                "accept": "*/*",
                "content-type": "application/json",
            },
            b'{"exact":true}',
        )
        client.get("/v1/review-jobs")
        assert "authorization" not in calls[1][2]


def test_readiness_requires_current_upstream_authorization(configured):
    configured.forward = lambda *args: (403, {}, b"")
    with TestClient(configured.app, base_url=configured.origin) as client:
        assert client.get("/livez").status_code == 200
        assert client.get("/readyz").status_code == 503
        configured.forward = lambda *args: (200, {}, b"{}")
        assert client.get("/readyz").status_code == 503
        ready_body = json.dumps(
            {
                "job": {"job_id": configured.fixture["empty_job_id"], "case_id": "synthetic-check"},
                "job_status": "queued",
            }
        ).encode()
        configured.forward = lambda *args: (200, {}, ready_body)
        assert client.get("/readyz").json() == {"status": "ready", "mode": "host-companion"}
        configured.forward = lambda *args: (503, {}, b"")
        assert client.get("/readyz").status_code == 503


def test_bounded_request_refuses_before_upstream(configured, monkeypatch):
    monkeypatch.setattr(S, "MAX_BYTES", 8)
    calls = []
    configured.forward = lambda *args: calls.append(args)
    with TestClient(configured.app, base_url=configured.origin) as client:
        assert client.post("/v1/review-jobs", content=b"x" * 9).status_code == 413
    assert calls == []


def test_private_session_and_static_symlinks_fail_startup(configured, tmp_path):
    fixture = tmp_path / "session.json"
    fixture.chmod(0o644)
    with pytest.raises(ValueError):
        S.LocalStack(
            mode="host-companion",
            origin=configured.origin,
            assets=configured.assets,
            api_fixture=fixture,
            privacy_base=configured.privacy_base,
        )
    fixture.chmod(0o600)
    (configured.assets / "assets/leak.js").symlink_to(fixture)
    with pytest.raises(ValueError):
        S.LocalStack(
            mode="host-companion",
            origin=configured.origin,
            assets=configured.assets,
            api_fixture=fixture,
            privacy_base=configured.privacy_base,
        )


def test_demo_requires_private_state_and_rejects_host_registrations(configured, tmp_path):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    bootstrap = state / "bootstrap.json"
    bootstrap.write_text(
        json.dumps(
            {
                "dataset_kind": "synthetic-workbench-v1",
                "model_mode": "fixed_mock_converse",
                "registered": {"original": {"path": "/private"}},
            }
        )
    )
    bootstrap.chmod(0o600)
    with pytest.raises(ValueError):
        S.LocalStack(
            mode="synthetic-demo", origin=configured.origin, assets=configured.assets, state=state
        )
    with pytest.raises(ValueError):
        S.LocalStack(mode="production", origin=configured.origin, assets=configured.assets)


def test_build_environment_does_not_embed_host_configuration(monkeypatch):
    for name in ("VITE_API_BASE_URL", "AWS_PROFILE", "REVIEW_BROWSER_FIXTURE", "NODE_OPTIONS"):
        monkeypatch.setenv(name, "private")
    assert (
        not {"VITE_API_BASE_URL", "AWS_PROFILE", "REVIEW_BROWSER_FIXTURE", "NODE_OPTIONS"}
        & L.clean_environment().keys()
    )


def test_start_without_explicit_demo_does_not_create_anything(tmp_path):
    with pytest.raises(ValueError):
        L.start(tmp_path, None, demo=False, port=4174)
    assert list(tmp_path.iterdir()) == []


def test_image_recipe_has_no_runtime_configuration_copy():
    recipe = (ROOT / "infra/local-validation/Dockerfile").read_text()
    assert "runtime_config" not in recipe
    assert "USER 10001:10001" in recipe
    assert "CMD []" in recipe
    assert "runtime_app:app" not in recipe
    assert "browser-proxy" not in recipe


@pytest.fixture
def local_docker(tmp_path, monkeypatch):
    import socket

    for name in ("DOCKER_HOST", "DOCKER_CONTEXT", "BUILDX_BUILDER", "BUILDKIT_HOST"):
        monkeypatch.delenv(name, raising=False)
    tmp_path.chmod(0o700)
    endpoint = tmp_path / "docker.sock"
    monkeypatch.chdir(tmp_path)
    with socket.socket(socket.AF_UNIX) as connection:
        connection.bind(endpoint.name)
        commands = L.Commands(tmp_path)
        calls = []

        def native_metadata(*args, **kwargs):
            calls.append(args)
            if args == ("docker", "context", "show"):
                return "desktop-linux"
            if args[1:3] == ("context", "inspect"):
                return json.dumps("unix://" + str(endpoint.resolve()))
            return ""

        monkeypatch.setattr(commands, "_run", native_metadata)
        yield commands, calls, endpoint


@pytest.mark.parametrize("endpoint", ["ssh://example.invalid", "tcp://127.0.0.1:2375"])
def test_remote_active_context_denied_before_any_daemon_call(local_docker, monkeypatch, endpoint):
    commands, calls, _ = local_docker
    original = commands._run

    def remote_metadata(*args, **kwargs):
        if args[1:3] == ("context", "inspect"):
            calls.append(args)
            return json.dumps(endpoint)
        return original(*args, **kwargs)

    monkeypatch.setattr(commands, "_run", remote_metadata)
    with pytest.raises(ValueError, match="Unix-socket"):
        commands.run("docker", "network", "create", "never-created")
    assert [call[1] for call in calls] == ["context", "context"]
    assert commands.docker_endpoint is None


@pytest.mark.parametrize("name", ["DOCKER_HOST", "BUILDX_BUILDER", "BUILDKIT_HOST"])
def test_remote_environment_override_denied_before_daemon(local_docker, monkeypatch, name):
    commands, calls, _ = local_docker
    monkeypatch.setenv(name, "tcp://example.invalid:2375")
    with pytest.raises(ValueError):
        commands.run("docker", "volume", "create", "never-created")
    assert calls == []


def test_every_docker_operation_uses_pinned_socket_despite_changed_selection(
    local_docker, monkeypatch
):
    commands, calls, endpoint = local_docker
    expected = "unix://" + str(endpoint.resolve())
    for operation in ("info", "buildx", "run", "stop", "restart", "cp", "network", "volume"):
        commands.run("docker", operation, "example")
        assert calls[-1] == ("docker", "--host", expected, operation, "example")
        monkeypatch.setenv("DOCKER_CONTEXT", "remote-selected-later")
    assert len([call for call in calls if call[1] == "context"]) == 2
    assert "DOCKER_CONTEXT" not in L.clean_environment()
    with pytest.raises(ValueError, match="differ"):
        commands.require_endpoint({"docker_endpoint": "unix:///different.sock"})


def test_default_builder_alias_resolves_to_same_pinned_daemon(local_docker, monkeypatch):
    commands, calls, endpoint = local_docker
    expected = commands.pin_local_docker()

    def builder(*args, **kwargs):
        calls.append(args)
        if args[3:6] == ("buildx", "inspect", "default"):
            return "Name: default\nDriver: docker\nEndpoint: default\n"
        if args[3:5] == ("context", "inspect"):
            return json.dumps(expected)
        raise AssertionError("Unexpected daemon call")

    monkeypatch.setattr(commands, "_run", builder)
    L.verify_default_builder(commands)
    assert calls[-2][:3] == ("docker", "--host", "unix://" + str(endpoint.resolve()))
    assert calls[-1][3:6] == ("context", "inspect", "default")


@pytest.mark.parametrize(
    "driver,endpoint",
    [("remote", "tcp://example.invalid:1234"), ("docker", "ssh://example.invalid")],
)
def test_nonlocal_builder_rejected_before_build(local_docker, monkeypatch, driver, endpoint):
    commands, calls, _ = local_docker
    commands.pin_local_docker()

    def builder(*args, **kwargs):
        calls.append(args)
        return f"Name: default\nDriver: {driver}\nEndpoint: {endpoint}\n"

    monkeypatch.setattr(commands, "_run", builder)
    with pytest.raises(ValueError, match="Default builder"):
        L.verify_default_builder(commands)
    assert calls[-1][3:] == ("buildx", "inspect", "default")


def test_build_command_explicitly_uses_daemon_default_builder(tmp_path, monkeypatch):
    class AtBuild(Exception):
        pass

    class BuildCommands:
        docker_endpoint = "unix:///local.sock"

        def __init__(self):
            self.calls = []

        def run(self, *args, **kwargs):
            self.calls.append(args)
            if args[:3] == ("docker", "buildx", "build"):
                raise AtBuild
            if args[:3] == ("docker", "buildx", "inspect"):
                return "Driver: docker\nEndpoint: unix:///local.sock\n"
            if args[:2] == ("docker", "info"):
                return "aarch64 linux"
            if args[0] == "node":
                return "v26.7.0"
            return ""

    tmp_path.chmod(0o700)
    monkeypatch.setattr(L, "ROOT", tmp_path)
    monkeypatch.setattr(L, "copy_regular", lambda *args: None)
    monkeypatch.setattr(L.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(L.platform, "machine", lambda: "arm64")
    commands = BuildCommands()
    with pytest.raises(AtBuild):
        L.build(tmp_path, commands)
    assert commands.calls[-1][:5] == ("docker", "buildx", "build", "--builder", "default")
