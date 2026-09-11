"""Bounded restoration diagnostics retain failures without exposing local values."""

import asyncio
import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID, uuid4

import httpx
import pymupdf
import pytest
from test_privacy_bridge import bridge as bridge
from test_privacy_bridge import prepared
from test_privacy_refill_review_http import confirm_stage, pending

from appraisal_review.adapters.local import privacy_bridge as bridge_module
from appraisal_review.adapters.local.privacy_bridge import _BridgeFault, create_privacy_bridge
from appraisal_review.domain.privacy_mapping import MappingError, MappingFault

CANARY = "private diagnostic canary /private/source.pdf"


def request(bridge, path, records, *, method="GET", diagnostic=None, headers=None):
    config = replace(bridge.config, diagnostic=diagnostic or records.append)
    app = create_privacy_bridge(config, {bridge.token: bridge.owner})

    async def send():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 30001)),
            base_url="http://127.0.0.1/local-privacy",
            headers={
                "Host": config.authority,
                "Origin": config.origin,
                "Authorization": f"Bearer {bridge.token}",
                "X-Privacy-Request-Id": "untrusted-caller-reference",
                **(headers or {}),
            },
        ) as client:
            return await client.request(method, path, **({"json": {}} if method == "POST" else {}))

    return asyncio.run(send())


def assert_failure(response, records, stage, code):
    assert response.status_code == 409
    assert response.json() == {"code": "local_privacy_request_rejected"}
    assert response.headers.get("X-Privacy-Failure-Stage") == stage
    assert response.headers.get("X-Privacy-Failure-Code") == code
    identifier = response.headers["X-Privacy-Request-Id"]
    assert UUID(identifier).version == 4
    assert len(records) == 1
    record = records[0]
    assert record["request_id"] == identifier
    assert record["status"] == response.status_code
    assert record["failure"] == {"stage": stage, "code": code}
    assert len(json.dumps(record)) < 8192
    assert len(record["timings"]) <= 24
    assert CANARY not in json.dumps(record)
    assert "untrusted-caller-reference" not in json.dumps(record) + response.text


@pytest.mark.parametrize(
    ("fault", "code"),
    [
        (_BridgeFault(), "request_rejected"),
        (ValueError(CANARY), "validation_failed"),
        (MappingFault(MappingError.EXPIRED), "mapping_expired"),
        (TimeoutError(CANARY), "operation_timed_out"),
        (RuntimeError(CANARY), "operation_failed"),
    ],
)
def test_restore_resolver_failures_are_distinct_and_sanitized(bridge, fault, code):
    prepared(bridge)
    records = []

    def fail(*_):
        raise fault

    bridge.owner.results.resolve = fail
    response = request(bridge, f"/restore/{uuid4()}", records, method="POST")
    assert_failure(response, records, "publication", code)
    assert bridge.token not in json.dumps(records)
    assert not list(bridge.path.glob("restored-*/final-local.pdf"))


@pytest.fixture
def completed(bridge):
    state, route, writer = pending(bridge)
    confirm_stage(bridge, route)
    assert bridge.client.post(f"/restore/{state.id}", json={}).status_code == 409
    confirm_stage(bridge, route)
    response = bridge.client.post(f"/restore/{state.id}", json={})
    assert response.status_code == 200
    result = response.json()
    review = bridge.owner._ocr_reviews[UUID(route.rsplit("/", 1)[1])].review
    observations = dict(review._stage_observations)
    images = {stage: dict(pages) for stage, pages in review._stage_images.items()}
    path = next(bridge.path.glob("restored-*/final-local.pdf"))
    original = bridge.config.sources[bridge.source_id].path.read_bytes()
    downloads = {p: p.read_bytes() for p in bridge.path.glob("authorized-download-*/published.pdf")}
    yield state, review, "/restored/" + result["local_id"], path, result
    assert review._stage_observations == observations
    assert review._stage_images == images
    assert bridge.config.sources[bridge.source_id].path.read_bytes() == original
    assert downloads and all(p.read_bytes() == value for p, value in downloads.items())
    assert hashlib.sha256(path.read_bytes()).hexdigest() == result["manifest"]["final_digest"]
    writer.assert_called_once()


@pytest.mark.parametrize(
    ("fault", "code"),
    [
        (_BridgeFault(), "request_rejected"),
        (ValueError(CANARY), "validation_failed"),
        (MappingFault(MappingError.EXPIRED), "mapping_expired"),
    ],
)
def test_completed_get_resolver_failures_are_distinct(bridge, completed, fault, code):
    _, _, route, _, _ = completed
    records = []

    def fail(*_):
        raise fault

    bridge.owner.results.resolve = fail
    response = request(bridge, route, records)
    assert_failure(response, records, "publication", code)
    assert response.headers["Content-Type"].startswith("application/json")


@pytest.mark.parametrize("condition", ["expired", "revoked"])
def test_completed_review_lifetime_has_exact_reason(bridge, completed, condition):
    _, review, route, _, _ = completed
    if condition == "expired":
        review.now = lambda: review.expires_at + timedelta(seconds=1)
    else:
        review._revoked = True
    records = []
    response = request(bridge, route, records)
    assert_failure(response, records, "review_lifetime", "review_" + condition)


def test_swallowed_mapping_fault_keeps_actual_failure(bridge, completed):
    state, _, route, _, _ = completed
    coordinator = bridge.owner.results
    result = coordinator.resolve(bridge.owner.principal_id, state.id)
    bridge.owner.results = type("PinnedTestResolver", (), {"resolve": lambda *_: result})()

    def fail(*_):
        raise MappingFault(MappingError.EXPIRED)

    coordinator._checked = fail
    records = []
    response = request(bridge, route, records)
    assert_failure(response, records, "publication", "mapping_expired")


@pytest.mark.parametrize(
    "fault", ["none", "wall_expiry", "monotonic_expiry", "review_revoked", "publication_revoked"]
)
def test_slow_completed_file_read_rechecks_both_authorities(bridge, completed, monkeypatch, fault):
    state, review, route, path, result = completed
    entered, release = Event(), Event()
    real_fdopen, expected = os.fdopen, path.stat()
    checks = Mock(wraps=review.permits_completed)
    monkeypatch.setattr(review, "permits_completed", checks)

    # Pause the actual final stream read, after the entry trust checks. All
    # other real source/mapping/publication I/O continues unchanged.
    def fdopen(descriptor, *args, **kwargs):
        stream = real_fdopen(descriptor, *args, **kwargs)
        info = os.fstat(descriptor)
        if (info.st_dev, info.st_ino) != (expected.st_dev, expected.st_ino):
            return stream

        def read(limit):
            entered.set()
            assert release.wait(10), "Test did not release the final read"
            return stream.read(limit)

        @contextmanager
        def held():
            with stream:
                yield SimpleNamespace(read=read)

        return held()

    monkeypatch.setattr(bridge_module.os, "fdopen", fdopen)
    records = []
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(request, bridge, route, records)
        try:
            assert entered.wait(10), "Final read was never reached"
            assert not future.done()
            assert bridge.owner._lock.locked()
            assert checks.call_count == 1
            start = time.monotonic()
            if fault == "wall_expiry":
                review.now = lambda: review.expires_at + timedelta(seconds=1)
            elif fault == "monotonic_expiry":
                review._deadline = time.monotonic() - 1
            elif fault == "review_revoked":
                review._revoked = True
            elif fault == "publication_revoked":
                state.active = False
            # A short real wait makes the deliberately slow I/O observable;
            # correctness uses barriers and current guards, not a fast timeout.
            assert not release.wait(0.05)
            held_ms = (time.monotonic() - start) * 1000
        finally:
            release.set()
        response = future.result(timeout=20)
    assert not bridge.owner._lock.locked()
    if fault == "none":
        assert response.status_code == 200
        assert response.headers["Content-Type"] == "application/pdf"
        assert hashlib.sha256(response.content).hexdigest() == result["manifest"]["final_digest"]
        with pymupdf.open(stream=response.content, filetype="pdf") as reopened:
            assert len(reopened) == 1
        assert checks.call_count == 2
        assert len(records) == 1 and records[0]["failure"] is None
        assert "X-Privacy-Failure-Code" not in response.headers
    elif fault == "publication_revoked":
        assert_failure(response, records, "publication", "validation_failed")
        assert checks.call_count == 1
    else:
        code = "review_revoked" if fault == "review_revoked" else "review_expired"
        assert_failure(response, records, "review_lifetime", code)
        assert checks.call_count == 2
    timings = {item["stage"]: item for item in records[0]["timings"]}
    assert timings["file_read"]["elapsed_ms"] >= held_ms - 1
    assert timings["final_read"]["elapsed_ms"] >= timings["file_read"]["elapsed_ms"]
    assert {"engine", "publication", "mapping", "plan", "review_evidence"} <= timings.keys()


def test_completed_file_io_failure_is_sanitized(bridge, completed, monkeypatch):
    _, _, route, path, _ = completed
    open_file = bridge_module.os.open

    def fail(candidate, *args, **kwargs):
        if candidate == path:
            raise OSError(CANARY)
        return open_file(candidate, *args, **kwargs)

    monkeypatch.setattr(bridge_module.os, "open", fail)
    records = []
    response = request(bridge, route, records)
    assert_failure(response, records, "file_read", "local_io_failed")


@pytest.mark.parametrize("outcome", ["success", "denial"])
def test_failing_diagnostic_sink_cannot_change_access_or_bytes(bridge, completed, outcome):
    state, _, route, path, _ = completed
    records = []
    if outcome == "denial":
        state.active = False

    def failing_sink(record):
        records.append(record)
        raise ValueError(CANARY)

    response = request(bridge, route, records, diagnostic=failing_sink)
    if outcome == "success":
        assert response.status_code == 200 and response.content == path.read_bytes()
        assert len(records) == 1 and records[0]["failure"] is None
    else:
        assert_failure(response, records, "publication", "validation_failed")
    assert bridge.token not in json.dumps(records)


@pytest.mark.parametrize(
    "headers", [{"Authorization": "Bearer unauthorized"}, {"Origin": "https://other.invalid"}]
)
def test_untrusted_boundary_has_no_detailed_diagnostics(bridge, headers):
    records = []
    response = request(bridge, f"/restored/{uuid4()}", records, headers=headers)
    assert response.status_code in (401, 403)
    assert response.json() == {"code": "local_privacy_request_rejected"}
    assert "X-Privacy-Failure-Code" not in response.headers
    assert "X-Privacy-Failure-Stage" not in response.headers
    assert records == []


def test_cancelled_request_keeps_worker_fence_and_cannot_report_success(bridge):
    records, messages = [], []
    started, release = Event(), Event()
    config = replace(bridge.config, diagnostic=records.append)

    def worker():
        started.set()
        assert release.wait(10), "Cancelled worker was never released"

    async def operation(scope, receive, send):
        async with bridge.owner._lock:
            await bridge_module._run_sync(worker)
        pytest.fail("Cancelled operation must not send a success response")

    boundary = bridge_module._Boundary(
        operation, config=config, sessions={bridge.token: bridge.owner}
    )

    async def run():
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        task = asyncio.create_task(
            boundary(
                {
                    "type": "http",
                    "method": "GET",
                    "path": f"/local-privacy/restored/{uuid4()}",
                    "client": ("127.0.0.1", 30001),
                    "query_string": b"",
                    "headers": [
                        (b"host", config.authority.encode()),
                        (b"origin", config.origin.encode()),
                        (b"authorization", f"Bearer {bridge.token}".encode()),
                    ],
                },
                receive,
                send,
            )
        )
        try:
            assert await asyncio.to_thread(started.wait, 10)
            task.cancel()
            await asyncio.sleep(0)
            assert bridge.owner._lock.locked() and not task.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert not bridge.owner._lock.locked()
    assert not messages
    assert len(records) == 1
    assert records[0]["status"] is None
    assert records[0]["failure"] == {"stage": "request", "code": "operation_failed"}
    assert records[0]["code"] != "ok"
    assert bridge_module.privacy_request_id() is None
    assert bridge.token not in json.dumps(records)
