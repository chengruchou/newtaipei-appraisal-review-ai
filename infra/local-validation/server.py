"""Local static workbench and canonical API composition; no recovery control routes."""

from __future__ import annotations

import argparse
import asyncio
import http.client
import json
import os
import stat
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, closing
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import uvicorn
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_BYTES = 32 * 1024 * 1024


def loopback_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.port is None
        or not 1024 <= parsed.port <= 65535
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or value != f"http://127.0.0.1:{parsed.port}"
    ):
        raise ValueError("An exact numeric loopback Origin with a nonprivileged port is required")
    return value


def private_path(path: Path, *, directory: bool = False) -> None:
    info = path.lstat()
    valid_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if (
        not path.is_absolute()
        or not valid_type
        or path.resolve() != path
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
        or (not directory and info.st_nlink != 1)
    ):
        raise ValueError("An owned private non-aliased local path is required")


class Boundary:
    """Exact Host/Origin, bounded bodies and canonical API dispatch before static routing."""

    def __init__(self, app: ASGIApp, owner: LocalStack) -> None:
        self.app, self.owner = app, owner

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = scope.get("headers", [])
        hosts = [v.decode("latin1") for k, v in headers if k.lower() == b"host"]
        origins = [v.decode("latin1") for k, v in headers if k.lower() == b"origin"]
        if hosts != [self.owner.origin.removeprefix("http://")] or (
            origins and origins != [self.owner.origin]
        ):
            await Response(status_code=403)(scope, receive, send)
            return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > MAX_BYTES:
                await Response(status_code=413)(scope, receive, send)
                return
            if not message.get("more_body", False):
                break
        delivered = False

        async def bounded_receive() -> Message:
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        async def secure_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = list(message.get("headers", []))
                response_headers.extend(
                    [
                        (b"x-content-type-options", b"nosniff"),
                        (b"referrer-policy", b"no-referrer"),
                        (b"cache-control", b"no-store"),
                        (b"x-frame-options", b"DENY"),
                    ]
                )
                message = {**message, "headers": response_headers}
            await send(message)

        if scope["path"].startswith("/v1/") and self.owner.api is not None:
            await self.owner.api(scope, bounded_receive, secure_send)
        else:
            await self.app(scope, bounded_receive, secure_send)


class LocalStack:
    def __init__(
        self,
        *,
        mode: str,
        origin: str,
        assets: Path,
        state: Path | None = None,
        api_fixture: Path | None = None,
        privacy_base: str | None = None,
    ) -> None:
        self.origin = loopback_origin(origin)
        if mode not in {"synthetic-demo", "host-companion"}:
            raise ValueError("An explicit supported local mode is required")
        if not assets.is_absolute() or assets.resolve() != assets or not assets.is_dir():
            raise ValueError("Built frontend assets must be an existing non-aliased directory")
        files = list(assets.rglob("*"))
        if not (assets / "index.html").is_file() or any(
            p.is_symlink() or (p.is_file() and p.suffix not in {".html", ".js", ".mjs", ".css"})
            for p in files
        ):
            raise ValueError("Only the built static frontend may be served")
        self.mode, self.assets, self.state = mode, assets, state
        self.privacy_base = loopback_origin(privacy_base) if privacy_base else None
        self.fixture: dict[str, Any] | None = None
        if mode == "synthetic-demo":
            if state is None or api_fixture is not None or privacy_base is not None:
                raise ValueError("Container demo requires private state and no host companion")
            private_path(state, directory=True)
            bootstrap = state / "bootstrap.json"
            if bootstrap.exists():
                private_path(bootstrap)
                prior = json.loads(bootstrap.read_bytes())
                if (
                    prior.get("dataset_kind") != "synthetic-workbench-v1"
                    or prior.get("model_mode") != "fixed_mock_converse"
                    or prior.get("registered")
                ):
                    raise ValueError("This container only reopens its fixed synthetic core state")
        else:
            if state is not None or api_fixture is None:
                raise ValueError("Host companion mode requires private API configuration")
            private_path(api_fixture)
            self.fixture = json.loads(api_fixture.read_bytes())
            loopback_origin(self.fixture["api_base_url"])
            if (
                not isinstance(self.fixture.get("session_token"), str)
                or len(self.fixture["session_token"]) < 32
            ):
                raise ValueError("An explicit host API session is required")
        self.api: ASGIApp | None = None
        self.workbench: Any = None
        self.started = False
        self.static = StaticFiles(directory=str(assets), follow_symlink=False)
        self.app = Starlette(
            routes=[
                Route("/livez", self.live),
                Route("/readyz", self.ready),
                Route("/local-config.json", self.config),
                Route("/v1/{path:path}", self.proxy, methods=["GET", "POST", "HEAD"]),
                Route("/{path:path}", self.frontend),
            ],
            lifespan=self.lifespan,
        )
        self.app.add_middleware(Boundary, owner=self)

    @asynccontextmanager
    async def lifespan(self, app: Starlette) -> AsyncIterator[None]:
        if self.mode == "synthetic-demo":
            from appraisal_review.adapters.local.synthetic_workbench import (
                _private_json,
                prepare_workbench,
            )

            assert self.state is not None
            workbench = await prepare_workbench(self.state, port=urlsplit(self.origin).port or 0)
            await workbench.settle()
            _private_json(self.state / "fixture.json", workbench.manifest())
            self.workbench, self.api = workbench, workbench.app
            async with workbench.app.router.lifespan_context(workbench.app):
                self.started = True
                try:
                    yield
                finally:
                    self.started = False
        else:
            self.started = True
            try:
                yield
            finally:
                self.started = False

    async def live(self, request: Request) -> Response:
        return JSONResponse({"status": "alive", "mode": self.mode})

    async def ready(self, request: Request) -> Response:
        ready = self.started and (self.assets / "index.html").is_file()
        try:
            if self.mode == "synthetic-demo":
                ready = ready and self.workbench.app.state.worker_problem is None
                with closing(self.workbench.store._connect()) as connection:
                    ready = (
                        ready
                        and connection.execute("SELECT COUNT(*) FROM review_state").fetchone()[0]
                        == 1
                    )
            else:
                assert self.fixture is not None
                from uuid import UUID

                from appraisal_review.domain.job_contracts import JobStatusView

                job = str(
                    UUID(self.fixture.get("configured_job_id", self.fixture.get("empty_job_id")))
                )
                status, _, body = await asyncio.to_thread(
                    self.forward,
                    "GET",
                    f"/v1/review-jobs/{job}",
                    {"Authorization": "Bearer " + self.fixture["session_token"]},
                    b"",
                )
                ready = (
                    ready
                    and status == 200
                    and str(JobStatusView.model_validate_json(body).job.job_id) == job
                )
        except Exception:
            ready = False
        return JSONResponse(
            {"status": "ready" if ready else "unavailable", "mode": self.mode},
            status_code=200 if ready else 503,
        )

    async def config(self, request: Request) -> Response:
        if self.privacy_base is None or request.url.query:
            return Response(status_code=404)
        return JSONResponse({"privacy_bridge_base": self.privacy_base})

    def forward(
        self, method: str, path: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, dict[str, str], bytes]:
        assert self.fixture is not None
        target = urlsplit(self.fixture["api_base_url"])
        with closing(http.client.HTTPConnection(target.hostname, target.port, timeout=30)) as conn:
            conn.request(method, path, body=body, headers=headers)
            result = conn.getresponse()
            data = result.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise ValueError("Upstream response exceeds the local limit")
            allowed = {"content-type", "content-disposition", "cache-control"}
            return (
                result.status,
                {k: v for k, v in result.getheaders() if k.lower() in allowed},
                data,
            )

    async def proxy(self, request: Request) -> Response:
        if self.mode != "host-companion":
            return Response(status_code=503)
        try:
            headers = {
                name: request.headers[name]
                for name in ("authorization", "accept", "content-type")
                if name in request.headers
            }
            path = request.url.path + ("?" + request.url.query if request.url.query else "")
            status, returned_headers, body = await asyncio.to_thread(
                self.forward, request.method, path, headers, await request.body()
            )
            return Response(body, status_code=status, headers=returned_headers)
        except Exception:
            return Response(status_code=502)

    async def frontend(self, request: Request) -> Response:
        path = request.path_params["path"]
        if path == "" or path == "privacy" or path.startswith(("jobs/", "tasks/")):
            return FileResponse(self.assets / "index.html", media_type="text/html")
        if not path.startswith("assets/"):
            return Response(status_code=404)
        try:
            return await self.static.get_response(path, request.scope)
        except HTTPException:
            return Response(status_code=404)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=("synthetic-demo", "host-companion"))
    parser.add_argument("--origin", required=True)
    parser.add_argument("--assets", type=Path, default=Path("/opt/appraisal/web"))
    parser.add_argument("--state", type=Path)
    parser.add_argument("--api-fixture", type=Path)
    parser.add_argument("--privacy-base")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        owner = LocalStack(**vars(args))
    except Exception:
        parser.exit(2, "Local validation configuration rejected.\n")
    # Docker publishes the container listener only on numeric host loopback.
    host = "0.0.0.0" if args.mode == "synthetic-demo" else "127.0.0.1"
    port = 8080 if args.mode == "synthetic-demo" else urlsplit(owner.origin).port
    uvicorn.run(
        owner.app,
        host=host,
        port=port or 0,
        access_log=False,
        log_level="critical",
        limit_concurrency=64,
        timeout_keep_alive=5,
    )


if __name__ == "__main__":
    main()
