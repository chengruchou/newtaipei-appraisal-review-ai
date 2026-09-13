"""Expiring, revocable local sessions (gate A02).

Each test targets a way the session gate could be cheated: an expired token
authenticating, an expiry rejection revealing that the token exists, a revoked
token coming back after directory reload, or the session view echoing the
bearer secret back to the caller.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from appraisal_review.adapters.local.integrated_service import (
    LocalDirectory,
    LocalMaterialCatalog,
    create_integrated_service,
)
from appraisal_review.adapters.local.sqlite_review_store import SQLiteReviewStore
from appraisal_review.application.service_guards import Principal, ServiceFault
from appraisal_review.domain.service_contracts import ActorReference, Permission

TOKEN = "session-" + "t" * 40
AUTHORITY = "127.0.0.1:8799"


def person() -> Principal:
    return Principal(
        ActorReference(actor_id=str(uuid4()), kind="human"),
        frozenset({str(uuid4())}),
        frozenset(Permission),
    )


class _Clock:
    def __init__(self, now: int) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now


class _StubDocuments:
    """Session routes never touch document transfer; any access is a test failure."""

    authorization = None

    def read(self, principal: object, reference: object) -> object:
        raise AssertionError("unexpected document read")

    def create_snapshot(self, principal: object, run: object, revision: object) -> object:
        raise AssertionError("unexpected snapshot creation")

    def read_snapshot(self, principal: object, run: object, reference: object) -> object:
        raise AssertionError("unexpected snapshot read")


class _StubExecution:
    async def execute(self, record: object, attempt: object) -> object:
        raise AssertionError("unexpected execution")


def _app(tmp_path: Path, directory: LocalDirectory) -> object:
    store = SQLiteReviewStore(tmp_path / "state" / "review.sqlite3")
    return create_integrated_service(
        authority=AUTHORITY,
        directory=directory,
        catalog=LocalMaterialCatalog(store),
        documents=_StubDocuments(),
        execution=_StubExecution(),
        resolver=None,
        worker_enabled=False,
    )


def _client(app: object) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=f"http://{AUTHORITY}")


def test_expired_token_is_rejected_exactly_like_an_unknown_one() -> None:
    directory = LocalDirectory({TOKEN: (person(), 100)}, clock=_Clock(100))
    with pytest.raises(ServiceFault) as expired:
        directory.authenticate("Bearer " + TOKEN)
    with pytest.raises(ServiceFault) as unknown:
        directory.authenticate("Bearer " + "u" * len(TOKEN))
    assert expired.value.problem.model_dump() == unknown.value.problem.model_dump()


def test_token_authenticates_until_expiry_under_a_fake_clock() -> None:
    clock = _Clock(50)
    directory = LocalDirectory({TOKEN: (person(), 100)}, clock=clock)
    assert directory.authenticate("Bearer " + TOKEN).actor.kind == "human"
    clock.now = 99
    assert directory.authenticate("Bearer " + TOKEN)
    clock.now = 100
    with pytest.raises(ServiceFault):
        directory.authenticate("Bearer " + TOKEN)


def test_legacy_none_expiry_token_still_authenticates() -> None:
    # Plain Principal values (the baked fixture token shape) never expire.
    directory = LocalDirectory({TOKEN: person()}, clock=_Clock(2**62))
    assert directory.authenticate("Bearer " + TOKEN)


def test_revocation_survives_directory_reload_from_persisted_state(tmp_path: Path) -> None:
    path = tmp_path / "revoked_tokens.json"
    first = LocalDirectory({TOKEN: person()}, revocations_path=path)
    assert first.authenticate("Bearer " + TOKEN)
    first.revoke_token(TOKEN)
    with pytest.raises(ServiceFault):
        first.authenticate("Bearer " + TOKEN)
    # A reload from bootstrap state re-supplies the session; the digest list wins.
    reloaded = LocalDirectory({TOKEN: person()}, revocations_path=path)
    with pytest.raises(ServiceFault):
        reloaded.authenticate("Bearer " + TOKEN)
    assert TOKEN not in path.read_text()


def test_session_endpoints_report_revoke_and_never_echo_the_token(tmp_path: Path) -> None:
    asyncio.run(_session_round_trip(tmp_path))


async def _session_round_trip(tmp_path: Path) -> None:
    clock = _Clock(50)
    directory = LocalDirectory(
        {TOKEN: (person(), 1_000)},
        clock=clock,
        revocations_path=tmp_path / "revoked_tokens.json",
    )
    app = _app(tmp_path, directory)
    headers = {"Authorization": f"Bearer {TOKEN}"}
    async with _client(app) as client:
        anonymous = await client.get("/v1/session")
        assert anonymous.status_code == 403
        read = await client.get("/v1/session", headers=headers)
        assert read.status_code == 200
        body = read.json()
        assert body["expires_at"] == 1_000
        assert body["kind"] == "human"
        assert set(body["permissions_summary"]) == {"case_count", "permission_count"}
        assert TOKEN not in read.text
        revoke = await client.delete("/v1/session", headers=headers)
        assert revoke.status_code == 204
        # Reuse after revocation answers exactly like an unknown token.
        assert (await client.get("/v1/session", headers=headers)).status_code == 403
        assert (await client.delete("/v1/session", headers=headers)).status_code == 403


def test_session_expiry_is_enforced_at_the_http_boundary(tmp_path: Path) -> None:
    asyncio.run(_http_expiry(tmp_path))


async def _http_expiry(tmp_path: Path) -> None:
    clock = _Clock(50)
    directory = LocalDirectory({TOKEN: (person(), 100)}, clock=clock)
    app = _app(tmp_path, directory)
    headers = {"Authorization": f"Bearer {TOKEN}"}
    async with _client(app) as client:
        assert (await client.get("/v1/session", headers=headers)).status_code == 200
        clock.now = 100
        assert (await client.get("/v1/session", headers=headers)).status_code == 403
