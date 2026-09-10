"""Separate loopback privacy application; never mount these routes in a cloud API."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import os
import stat
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import ParamSpec, Protocol, TypeVar
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from appraisal_review.adapters.local.privacy_document_sink import CloudExportSink
from appraisal_review.application.privacy_bundle import (
    LocalSanitizedBundleBuilder,
    LocalSanitizedVerifier,
)
from appraisal_review.application.privacy_export import (
    LocalPrivacyExportGate,
    sanitize_reviewer_text,
)
from appraisal_review.application.privacy_guards import PrivacyFault
from appraisal_review.application.privacy_mapping import LocalMappingService
from appraisal_review.application.privacy_refill import LocalPrivacyRefillExecutor, validate_refill
from appraisal_review.application.privacy_review import LocalPrivacyReviewService
from appraisal_review.domain.privacy_bundle import SanitizedBundle
from appraisal_review.domain.privacy_export import PrivacyExportPayload
from appraisal_review.domain.privacy_mapping import LocalMappingHandle, MappingFault
from appraisal_review.domain.privacy_models import (
    LocalPrivacyApproval,
    LocalSourceSnapshot,
    PrivacyReviewCommand,
    RehydrationPlan,
    privacy_review_digest,
    public_manifest_json,
)
from appraisal_review.domain.privacy_refill import PublishedRefillArtifact
from appraisal_review.domain.privacy_review import (
    AddPrivacyRegion,
    ConfirmPrivacyReview,
    EditPrivacyRegion,
    PrivacyReviewView,
    RemovePrivacyRegion,
    ReviewPrivacyPage,
)
from appraisal_review.ports.privacy import (
    LocalSnapshotReader,
    PrivacyOutputOCR,
    RehydrationAuthority,
)
from appraisal_review.ports.privacy_export import PrivacyExportConfirmation, PrivacyExportSink
from appraisal_review.ports.privacy_refill import PrivacyRefillProcessor, PrivacyRefillPublisher


@dataclass(frozen=True, repr=False)
class PrivacyBridgeSource:
    """Server configuration only; the HTTP client can select only its UUID handle."""

    path: Path
    snapshot: LocalSourceSnapshot
    policy_digest: str


@dataclass(frozen=True, repr=False)
class PrivacyBridgeConfig:
    workspace: Path
    origin: str
    authority: str  # Exact numeric-loopback Host, including port.
    sources: Mapping[UUID, PrivacyBridgeSource]
    max_body_bytes: int = 65536
    preview_lifetime_seconds: int = 300


class BridgeReviewConfirmation:
    """One explicit, authenticated UI command supplies one local review confirmation."""

    def __init__(self, principal_id: str) -> None:
        self.principal_id = principal_id
        self._expected: str | None = None

    def confirm(self, command: PrivacyReviewCommand) -> str | None:
        expected, self._expected = self._expected, None
        return self.principal_id if expected == privacy_review_digest(command) else None


@dataclass(frozen=True, repr=False)
class PrivacyBridgeRestore:
    """Trusted local resolver result; never reconstructed from a request body."""

    plan: RehydrationPlan
    publisher: PrivacyRefillPublisher
    authority: RehydrationAuthority
    processor: PrivacyRefillProcessor
    ocr: PrivacyOutputOCR
    download_path: Path


class PrivacyBridgeResultResolver(Protocol):
    def resolve(self, principal_id: str, result_id: UUID) -> PrivacyBridgeRestore:
        """Authorize the backend result for this principal and return local refill ports.

        Backend requests carry only the authorized result ID, never mappings or paths.
        Recheck current publication/ownership through the returned publisher port.
        """
        ...


@dataclass(frozen=True, repr=False)
class _RestoredDownload:
    result_id: UUID
    plan: RehydrationPlan
    path: Path
    digest: str


@dataclass(repr=False)
class PrivacyBridgeSession:
    principal_id: str
    source_ids: frozenset[UUID]
    human: BridgeReviewConfirmation
    review: LocalPrivacyReviewService
    builder: LocalSanitizedBundleBuilder
    verifier: LocalSanitizedVerifier
    mappings: LocalMappingService
    key_reference: UUID
    sink: PrivacyExportSink | CloudExportSink
    sources: LocalSnapshotReader
    results: PrivacyBridgeResultResolver
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _source_id: UUID | None = field(default=None, init=False)
    _approval: LocalPrivacyApproval | None = field(default=None, init=False)
    _preview: _Preview | None = field(default=None, init=False)
    _maps: dict[UUID, LocalMappingHandle] = field(default_factory=dict, init=False)
    _restored: dict[UUID, _RestoredDownload] = field(default_factory=dict, init=False)


@dataclass(repr=False)
class _Preview:
    identifier: UUID
    command: PrivacyReviewCommand
    approval: LocalPrivacyApproval
    bundle: SanitizedBundle
    payload: PrivacyExportPayload
    digest: str
    deadline: float
    viewed: bool = False
    confirmed: bool = False
    attempted: bool = False


class _BridgeFault(Exception):
    def __init__(self, status: int = 409) -> None:
        self.status = status


def _problem(status: int) -> JSONResponse:
    return JSONResponse({"code": "local_privacy_request_rejected"}, status_code=status)


def _private_directory(path: Path, workspace: Path) -> None:
    if (
        not path.is_absolute()
        or not workspace.is_absolute()
        or workspace.resolve(strict=True) != workspace
        or path.resolve(strict=True) != path
        or not path.is_relative_to(workspace)
    ):
        raise _BridgeFault()
    for current in (workspace, *reversed(path.relative_to(workspace).parents)):
        candidate = current if current.is_absolute() else workspace / current
        info = candidate.stat(follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise _BridgeFault()
    info = path.stat(follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise _BridgeFault()


def _read_bound(path: Path, workspace: Path, digest: str) -> bytes:
    _private_directory(path.parent, workspace)
    if path.resolve(strict=True) != path:
        raise _BridgeFault()
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != os.getuid()
            or before.st_size > 64 * 1024 * 1024
        ):
            raise _BridgeFault()
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            data = stream.read(64 * 1024 * 1024 + 1)
        after, linked = os.fstat(descriptor), path.stat(follow_symlinks=False)
        if (
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            or (after.st_dev, after.st_ino) != (linked.st_dev, linked.st_ino)
            or len(data) != before.st_size
            or hashlib.sha256(data).hexdigest() != digest
        ):
            raise _BridgeFault()
        return data
    finally:
        os.close(descriptor)


def _payload_digest(payload: PrivacyExportPayload) -> str:
    return hashlib.sha256(
        json.dumps(
            [
                hashlib.sha256(payload.pdf).hexdigest(),
                payload.manifest_json,
                payload.reviewer_text,
                payload.filename,
            ],
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


class _Boundary:
    def __init__(
        self,
        app: ASGIApp,
        *,
        config: PrivacyBridgeConfig,
        sessions: Mapping[str, PrivacyBridgeSession],
    ) -> None:
        self.app, self.config = app, config
        self.sessions = tuple(
            (hashlib.sha256(token.encode()).digest(), session)
            for token, session in sessions.items()
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers: dict[bytes, list[bytes]] = {}
        for key, value in scope["headers"]:
            headers.setdefault(key.lower(), []).append(value)

        def exact(key: bytes) -> str:
            values = headers.get(key, [])
            if len(values) != 1:
                raise _BridgeFault(403)
            return values[0].decode("ascii")

        async def guarded_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = [
                    *message.get("headers", []),
                    (b"cache-control", b"no-store"),
                    (b"pragma", b"no-cache"),
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
                    (b"access-control-allow-origin", self.config.origin.encode()),
                    (b"vary", b"Origin"),
                    (b"access-control-expose-headers", b"X-Privacy-Review-Digest"),
                ]
            await send(message)

        try:
            client = scope.get("client")
            if (
                not client
                or not ipaddress.ip_address(client[0]).is_loopback
                or exact(b"host") != self.config.authority
                or exact(b"origin") != self.config.origin
                or scope.get("query_string")
                or any(key == b"forwarded" or key.startswith(b"x-forwarded-") for key in headers)
            ):
                raise _BridgeFault(403)
            if scope["method"] == "OPTIONS":
                if exact(b"access-control-request-method") not in {"GET", "POST"}:
                    raise _BridgeFault(403)
                requested = exact(b"access-control-request-headers").lower().split(",")
                if not set(map(str.strip, requested)) <= {"authorization", "content-type"}:
                    raise _BridgeFault(403)
                response = Response(
                    status_code=204,
                    headers={
                        "Access-Control-Allow-Methods": "GET, POST",
                        "Access-Control-Allow-Headers": "Authorization, Content-Type",
                        "Access-Control-Max-Age": "0",
                    },
                )
                await response(scope, receive, guarded_send)
                return
            authorization = exact(b"authorization")
            if not authorization.startswith("Bearer "):
                raise _BridgeFault(401)
            token_hash = hashlib.sha256(authorization[7:].encode()).digest()
            session = next(
                (s for expected, s in self.sessions if hmac.compare_digest(expected, token_hash)),
                None,
            )
            if session is None:
                raise _BridgeFault(401)
            if scope["method"] not in {"GET", "POST"}:
                raise _BridgeFault(405)
            if (
                scope["method"] == "POST"
                and exact(b"content-type").split(";")[0] != "application/json"
            ):
                raise _BridgeFault(415)
            body = bytearray()
            async with asyncio.timeout(10):
                while True:
                    event = await receive()
                    if event["type"] != "http.request":
                        raise _BridgeFault(400)
                    body.extend(event.get("body", b""))
                    if len(body) > self.config.max_body_bytes:
                        raise _BridgeFault(413)
                    if not event.get("more_body", False):
                        break
            if scope["method"] == "GET" and body:
                raise _BridgeFault(400)
            scope.setdefault("state", {})["privacy_session"] = session
            sent = False

            async def replay() -> Message:
                nonlocal sent
                if not sent:
                    sent = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()

            await self.app(scope, replay, guarded_send)
        except _BridgeFault as error:
            await _problem(error.status)(scope, receive, guarded_send)
        except (ValueError, UnicodeError, TimeoutError):
            await _problem(400)(scope, receive, guarded_send)
        except Exception:
            await _problem(409)(scope, receive, guarded_send)


class _ExactBuilder(LocalSanitizedBundleBuilder):
    def __init__(self, preview: _Preview) -> None:
        self.preview = preview

    def build(
        self, command: PrivacyReviewCommand, approval: LocalPrivacyApproval
    ) -> SanitizedBundle:
        if command != self.preview.command or approval != self.preview.approval:
            raise _BridgeFault()
        return self.preview.bundle


class _ExactConfirmation:
    def __init__(self, preview: _Preview) -> None:
        self.preview = preview

    def confirm(self, payload: PrivacyExportPayload) -> bool:
        return (
            self.preview.confirmed
            and self.preview.viewed
            and time.monotonic() < self.preview.deadline
            and payload == self.preview.payload
            and _payload_digest(payload) == self.preview.digest
        )


class _PinnedPublisher:
    def __init__(
        self, result: PrivacyBridgeRestore, workspace: Path, expected: PublishedRefillArtifact
    ) -> None:
        self.result, self.workspace, self.expected = result, workspace, expected

    def current(self, plan: RehydrationPlan) -> PublishedRefillArtifact:
        artifact = self.result.publisher.current(plan)
        if artifact != self.expected or not self.permits(plan, artifact):
            raise _BridgeFault()
        return artifact

    def permits(self, plan: RehydrationPlan, artifact: PublishedRefillArtifact) -> bool:
        return (
            artifact == self.expected
            and self.result.publisher.permits(plan, artifact) is True
            and _read_bound(
                self.result.download_path, self.workspace, artifact.descriptor.artifact_digest
            )
            == artifact.pdf
        )


def _read_restored(
    session: PrivacyBridgeSession, entry: _RestoredDownload, workspace: Path
) -> bytes:
    def require_current() -> None:
        result = session.results.resolve(session.principal_id, entry.result_id)
        if result.plan != entry.plan or result.authority.permits(entry.plan) is not True:
            raise _BridgeFault()
        handle = session._maps.get(entry.plan.map_id)
        if handle is None:
            raise _BridgeFault()
        mapping = session.mappings.read(handle)
        artifact = result.publisher.current(entry.plan)
        validate_refill(mapping, entry.plan, artifact)
        if not _PinnedPublisher(result, workspace, artifact).permits(entry.plan, artifact):
            raise _BridgeFault()

    require_current()
    data = _read_bound(entry.path, workspace, entry.digest)
    require_current()
    return data


def _save_new(workspace: Path, data: bytes) -> tuple[UUID, Path]:
    _private_directory(workspace, workspace)
    identifier = uuid4()
    container = f"restored-{identifier}"
    root = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.mkdir(container, mode=0o700, dir_fd=root)
        directory = os.open(container, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
        os.fsync(root)
    finally:
        os.close(root)
    name, temporary = "final-local.pdf", f".restoring-{uuid4()}.tmp"
    descriptor = -1
    try:
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory
        )
        with os.fdopen(os.dup(descriptor), "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        info = os.fstat(descriptor)
        linked = os.stat(temporary, dir_fd=directory, follow_symlinks=False)
        if info.st_nlink != 1 or (info.st_dev, info.st_ino) != (linked.st_dev, linked.st_ino):
            raise _BridgeFault()
        os.link(temporary, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
        os.unlink(temporary, dir_fd=directory)
        os.fsync(directory)
        path = workspace / container / name
        if _read_bound(path, workspace, hashlib.sha256(data).hexdigest()) != data:
            raise _BridgeFault()
        return identifier, path
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        # Only the unpredictable temporary file created by this invocation is removable.
        with suppress(FileNotFoundError):
            os.unlink(temporary, dir_fd=directory)
        os.close(directory)


P = ParamSpec("P")
T = TypeVar("T")


async def _run_sync(function: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    # Do not release a session lock while a cancelled request's worker still mutates it.
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await asyncio.shield(task)
        finally:
            raise


M = TypeVar("M", bound=BaseModel)


async def _model(request: Request, cls: type[M]) -> M:
    return cls.model_validate_json(await request.body())


async def _object(request: Request, allowed: set[str]) -> dict[str, object]:
    data = json.loads(await request.body())
    if not isinstance(data, dict) or not set(data) <= allowed:
        raise _BridgeFault(400)
    return data


def create_privacy_bridge(
    config: PrivacyBridgeConfig, sessions: Mapping[str, PrivacyBridgeSession]
) -> FastAPI:
    """Create only the local application. Bind uvicorn to loopback, proxy_headers=False.

    Session tokens are server-provisioned development credentials, not production SSO.
    Composition must use each session's human in its review service and the same
    live review authority/verifier in its builder and mapping service.
    """
    _private_directory(config.workspace, config.workspace)
    target, origin = urlsplit("http://" + config.authority), urlsplit(config.origin)
    if (
        target.hostname not in {"127.0.0.1", "::1"}
        or not target.port
        or target.path
        or target.query
        or target.fragment
        or target.username
        or target.password
        or origin.scheme not in {"http", "https"}
        or not origin.netloc
        or origin.path
        or origin.query
        or origin.fragment
        or origin.username
        or origin.password
        or not 1 <= config.max_body_bytes <= 65536
        or not 1 <= config.preview_lifetime_seconds <= 900
    ):
        raise ValueError("Invalid local privacy configuration")
    source_configs = dict(config.sources)
    for source in source_configs.values():
        _read_bound(source.path, config.workspace, source.snapshot.source_digest)
    if len({id(s) for s in sessions.values()}) != len(sessions):
        raise ValueError("Session instances must not be shared")
    for attribute in ("review", "human", "builder", "verifier", "mappings"):
        if len({id(getattr(s, attribute)) for s in sessions.values()}) != len(sessions):
            raise ValueError("Local review dependencies must be session-specific")
    for token, session in sessions.items():
        if (
            len(token) < 32
            or session.human.principal_id != session.principal_id
            or not session.source_ids <= source_configs.keys()
        ):
            raise ValueError("Invalid local privacy session")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(_Boundary, config=config, sessions=dict(sessions))

    def view_response(view: PrivacyReviewView) -> JSONResponse:
        return JSONResponse(
            view.model_dump(mode="json"),
            headers={
                "X-Privacy-Review-Digest": privacy_review_digest(view.command),
            },
        )

    async def rejected(request: Request, error: Exception) -> JSONResponse:
        return _problem(error.status if isinstance(error, _BridgeFault) else 409)

    for error_type in (
        _BridgeFault,
        PrivacyFault,
        MappingFault,
        ValueError,
        OSError,
        RequestValidationError,
    ):
        app.add_exception_handler(error_type, rejected)

    def session_for(request: Request) -> PrivacyBridgeSession:
        return request.state.privacy_session  # type: ignore[no-any-return]

    def selected(session: PrivacyBridgeSession) -> PrivacyBridgeSource:
        if session._source_id is None:
            raise _BridgeFault()
        source = source_configs[session._source_id]
        raw = _read_bound(source.path, config.workspace, source.snapshot.source_digest)
        if len(raw) != source.snapshot.byte_size or session.sources.read(source.snapshot) != raw:
            raise _BridgeFault()
        return source

    def invalidate(session: PrivacyBridgeSession) -> None:
        session._approval, session._preview = None, None

    def preview_for(session: PrivacyBridgeSession, identifier: UUID) -> _Preview:
        selected(session)
        preview = session._preview
        if (
            preview is None
            or preview.identifier != identifier
            or time.monotonic() >= preview.deadline
            or session.review.list().command != preview.command
            or not session.review.permits(preview.approval, preview.command, now=datetime.now(UTC))
        ):
            raise _BridgeFault()
        return preview

    @app.get("/local-privacy/sources")
    async def list_sources(request: Request) -> object:
        session = session_for(request)
        return {
            "sources": [
                {"source_id": str(identifier)} for identifier in sorted(session.source_ids, key=str)
            ]
        }

    @app.post("/local-privacy/sources/{source_id}/open")
    async def open_source(source_id: UUID, request: Request) -> object:
        await _object(request, set())
        session = session_for(request)
        async with session._lock:
            if source_id not in session.source_ids:
                raise _BridgeFault(404)
            invalidate(session)
            session._source_id = source_id
            source = selected(session)
            return view_response(
                await session.review.rescan(source.snapshot, policy_digest=source.policy_digest)
            )

    @app.get("/local-privacy/review")
    async def review(request: Request) -> object:
        session = session_for(request)
        async with session._lock:
            selected(session)
            return view_response(session.review.list())

    @app.get("/local-privacy/pages/{page}")
    async def page_preview(page: int, request: Request) -> Response:
        session = session_for(request)
        async with session._lock:
            selected(session)
            command = session.review.list().command
            rendered = session.review.preview(
                ReviewPrivacyPage(
                    case_id=command.source.case_id,
                    snapshot_id=command.source.snapshot_id,
                    revision=command.selection_revision,
                    page=page,
                )
            )
            return Response(rendered.png, media_type="image/png")

    @app.post("/local-privacy/review/pages")
    async def review_page(request: Request) -> object:
        value = await _model(request, ReviewPrivacyPage)
        session = session_for(request)
        async with session._lock:
            selected(session)
            invalidate(session)
            return view_response(session.review.review_page(value))

    @app.post("/local-privacy/review/add")
    async def add(request: Request) -> object:
        value = await _model(request, AddPrivacyRegion)
        session = session_for(request)
        async with session._lock:
            selected(session)
            invalidate(session)
            return view_response(session.review.add(value))

    @app.post("/local-privacy/review/edit")
    async def edit(request: Request) -> object:
        value = await _model(request, EditPrivacyRegion)
        session = session_for(request)
        async with session._lock:
            selected(session)
            invalidate(session)
            return view_response(session.review.edit(value))

    @app.post("/local-privacy/review/remove")
    async def remove(request: Request) -> object:
        value = await _model(request, RemovePrivacyRegion)
        session = session_for(request)
        async with session._lock:
            selected(session)
            invalidate(session)
            return view_response(session.review.remove(value))

    @app.post("/local-privacy/review/confirm")
    async def confirm_review(request: Request) -> object:
        value = await _model(request, ConfirmPrivacyReview)
        session = session_for(request)
        async with session._lock:
            selected(session)
            invalidate(session)
            command = session.review.list().command
            session.human._expected = privacy_review_digest(command)
            try:
                session._approval = session.review.confirm(value)
            finally:
                session.human._expected = None
            return view_response(session.review.list())

    @app.post("/local-privacy/exports/preview")
    async def prepare(request: Request) -> object:
        data = await _object(request, {"reviewer_text"})
        text = data.get("reviewer_text")
        if text is not None and type(text) is not str:
            raise _BridgeFault(400)
        session = session_for(request)
        async with session._lock:
            selected(session)
            session._preview = None
            if session._approval is None:
                raise _BridgeFault()
            command, approval = session.review.list().command, session._approval
            prepared_text = (
                sanitize_reviewer_text(text, command).text if isinstance(text, str) else None
            )
            bundle = await _run_sync(session.builder.build, command, approval)
            payload = PrivacyExportPayload(
                bundle.pdf, public_manifest_json(bundle.manifest), prepared_text
            )
            preview = _Preview(
                uuid4(),
                command,
                approval,
                bundle,
                payload,
                _payload_digest(payload),
                time.monotonic() + config.preview_lifetime_seconds,
            )
            session._preview = preview
            return {
                "preview_id": str(preview.identifier),
                "manifest": bundle.manifest,
                "reviewer_text": payload.reviewer_text,
                "payload_digest": preview.digest,
            }

    @app.get("/local-privacy/exports/{preview_id}/pdf")
    async def sanitized_pdf(preview_id: UUID, request: Request) -> Response:
        session = session_for(request)
        async with session._lock:
            preview = preview_for(session, preview_id)
            preview.viewed = True
            return Response(preview.payload.pdf, media_type="application/pdf")

    @app.post("/local-privacy/exports/{preview_id}/confirm")
    async def confirm_export(preview_id: UUID, request: Request) -> object:
        data = await _object(request, {"payload_digest"})
        session = session_for(request)
        async with session._lock:
            preview = preview_for(session, preview_id)
            if (
                not preview.viewed
                or preview.attempted
                or data.get("payload_digest") != preview.digest
            ):
                raise _BridgeFault()
            preview.confirmed = True
            return {"preview_id": str(preview.identifier), "state": "confirmed"}

    @app.post("/local-privacy/exports/{preview_id}/transfer")
    async def transfer(preview_id: UUID, request: Request) -> object:
        await _object(request, set())
        session = session_for(request)
        async with session._lock:
            preview = preview_for(session, preview_id)
            if not preview.confirmed or preview.attempted:
                raise _BridgeFault()
            preview.attempted = True
            confirmation: PrivacyExportConfirmation = _ExactConfirmation(preview)
            sink: PrivacyExportSink
            if isinstance(session.sink, CloudExportSink):
                channel = session.sink.bind_confirmation(confirmation)
                confirmation, sink = channel, channel
            else:
                sink = session.sink
            gate = LocalPrivacyExportGate(
                builder=_ExactBuilder(preview),
                verifier=session.verifier,
                authority=session.review,
                confirmation=confirmation,
                sink=sink,
                mapping_service=session.mappings,
                key_reference=session.key_reference,
            )
            try:
                manifest = await _run_sync(
                    gate.export,
                    preview.command,
                    preview.approval,
                    reviewer_text=preview.payload.reviewer_text,
                )
            finally:
                if gate.mapping_handle is not None:
                    session._maps[gate.mapping_handle.map_id] = gate.mapping_handle
            return {"manifest": manifest, "state": "transferred"}

    @app.post("/local-privacy/restore/{result_id}")
    async def restore(result_id: UUID, request: Request) -> object:
        await _object(request, set())
        session = session_for(request)
        async with session._lock:
            source = selected(session)
            result = session.results.resolve(session.principal_id, result_id)
            handle = session._maps.get(result.plan.map_id)
            if handle is None:
                raise _BridgeFault(404)
            mapping = session.mappings.read(handle)
            if mapping.command.source != source.snapshot:
                raise _BridgeFault()
            artifact = result.publisher.current(result.plan)
            publisher = _PinnedPublisher(result, config.workspace, artifact)
            if not publisher.permits(result.plan, artifact):
                raise _BridgeFault()
            executor = LocalPrivacyRefillExecutor(
                mappings=session.mappings,
                sources=session.sources,
                publisher=publisher,
                authority=result.authority,
                processor=result.processor,
                ocr=result.ocr,
            )
            final = await _run_sync(executor.execute, handle, result.plan)
            selected(session)
            if not publisher.permits(result.plan, artifact):
                raise _BridgeFault()
            identifier, path = _save_new(config.workspace, final.pdf)
            session._restored[identifier] = _RestoredDownload(
                result_id,
                RehydrationPlan.model_validate_json(result.plan.model_dump_json()),
                path,
                final.manifest.final_digest,
            )
            return {"local_id": str(identifier), "manifest": final.manifest}

    @app.get("/local-privacy/restored/{local_id}")
    async def restored(local_id: UUID, request: Request) -> Response:
        session = session_for(request)
        async with session._lock:
            entry = session._restored.get(local_id)
            if entry is None:
                raise _BridgeFault(404)
            return Response(
                await _run_sync(_read_restored, session, entry, config.workspace),
                media_type="application/pdf",
            )

    return app
