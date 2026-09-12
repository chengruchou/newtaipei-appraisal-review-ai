"""Case intake regressions: real case creation, membership, durable uploads.

Service-level checks run against the real SQLite store on a private tmp root; the
HTTP checks mount only the intake router on a minimal app with stub wiring, using
the same fault-status mapping the composed app installs.
"""

import asyncio
import hashlib
import sqlite3
from dataclasses import replace
from datetime import date
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import BaseModel

from appraisal_review.adapters.local.case_intake_store import SQLiteCaseIntakeStore
from appraisal_review.api.app import FAULT_STATUS
from appraisal_review.api.routes.case_intake import router as case_intake_router
from appraisal_review.application.case_intake import (
    CaseIntakeService,
    CreateCaseCommand,
)
from appraisal_review.application.service_guards import Principal, ServiceFault
from appraisal_review.domain.service_contracts import (
    ActorReference,
    Permission,
    ServiceErrorCode,
)

NOW = 1_757_600_000


class Database:
    """Minimal ReviewDatabase substitute: same connection discipline, no policy."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, isolation_level=None)

    def _decode(self, payload: str) -> BaseModel:
        raise NotImplementedError


class Directory:
    """Stands in for LocalDirectory: grant_case is the injected membership grant."""

    def __init__(self, principal: Principal) -> None:
        self.principal = principal
        self.calls: list[tuple[str, str]] = []

    def grant_case(self, actor_id: str, case_id: str) -> Principal:
        if str(UUID(case_id)) != case_id or UUID(case_id).version != 4:
            raise ValueError("A canonical case identifier is required")
        self.calls.append((actor_id, case_id))
        if self.principal.actor.actor_id == actor_id:
            self.principal = replace(self.principal, case_ids=self.principal.case_ids | {case_id})
        return self.principal

    async def current_principal(self) -> Principal:
        return self.principal


class RefusingResolver:
    """An unauthenticated session: the adapter refuses before any route logic."""

    async def current_principal(self) -> Principal:
        raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)


def person(actor_id: str = "reviewer-1", kind: str = "human") -> Principal:
    return Principal(
        actor=ActorReference(actor_id=actor_id, kind=kind),  # type: ignore[arg-type]
        case_ids=frozenset(),
        permissions=frozenset({Permission.REVIEW, Permission.CONFIRM, Permission.CORRECT}),
    )


def harness(
    tmp_path: Path, *, principal: Principal | None = None, max_material_bytes: int = 1024
) -> tuple[CaseIntakeService, Directory, SQLiteCaseIntakeStore, Path]:
    root = tmp_path / "workbench"
    store = SQLiteCaseIntakeStore(Database(tmp_path / "intake.sqlite"), root=root)
    directory = Directory(principal or person())
    service = CaseIntakeService(
        store=store,
        grant=directory.grant_case,
        clock=lambda: NOW,
        max_material_bytes=max_material_bytes,
    )
    return service, directory, store, root


def build_app(service: CaseIntakeService | None, resolver: object) -> FastAPI:
    app = FastAPI()
    app.state.case_intake = service
    app.state.principal_resolver = resolver
    app.include_router(case_intake_router)

    @app.exception_handler(ServiceFault)
    async def service_fault(request: Request, fault: ServiceFault) -> JSONResponse:
        return JSONResponse(
            status_code=FAULT_STATUS[fault.problem.code],
            content=fault.problem.model_dump(mode="json"),
        )

    return app


def command(key: str = "create-1", title: str = "Xindian district appraisal") -> CreateCaseCommand:
    return CreateCaseCommand(
        idempotency_key=key, title=title, district="Xindian", valuation_date=date(2026, 9, 1)
    )


def created_case(client: TestClient, key: str = "create-1") -> str:
    response = client.post(
        "/v1/cases",
        json={"idempotency_key": key, "title": "Xindian district appraisal", "district": "Xindian"},
    )
    assert response.status_code == 201
    case_id: str = response.json()["case_id"]
    return case_id


def upload(
    client: TestClient,
    case_id: str,
    data: bytes,
    *,
    key: str = "upload-1",
    filename: str = "estimate.pdf",
    media_type: str = "application/pdf",
) -> httpx.Response:
    return client.post(
        f"/v1/cases/{case_id}/materials",
        content=data,
        headers={
            "X-Upload-Filename": filename,
            "X-Idempotency-Key": key,
            "Content-Type": media_type,
        },
    )


def test_create_case_grants_membership_to_the_caller_only(tmp_path: Path) -> None:
    service, directory, store, _root = harness(tmp_path)

    record = asyncio.run(service.create_case(directory.principal, command()))

    assert UUID(record.case_id).version == 4
    assert record.title == "Xindian district appraisal"
    assert record.district == "Xindian"
    assert record.created_at == NOW
    assert record.created_by == directory.principal.actor
    assert directory.calls == [("reviewer-1", record.case_id)]
    assert record.case_id in directory.principal.case_ids
    assert store.read_case(record.case_id) == record


def test_create_replay_with_same_key_returns_the_same_case(tmp_path: Path) -> None:
    service, directory, _store, _root = harness(tmp_path)

    first = asyncio.run(service.create_case(directory.principal, command()))
    second = asyncio.run(service.create_case(directory.principal, command()))
    other = asyncio.run(service.create_case(directory.principal, command(key="create-2")))

    assert second == first
    assert other.case_id != first.case_id
    # The replay re-grants the same membership; an in-memory directory reconverges.
    assert directory.calls[:2] == [("reviewer-1", first.case_id)] * 2


def test_create_same_key_with_different_payload_conflicts(tmp_path: Path) -> None:
    service, directory, _store, _root = harness(tmp_path)
    asyncio.run(service.create_case(directory.principal, command()))

    with pytest.raises(ServiceFault) as fault:
        asyncio.run(service.create_case(directory.principal, command(title="Renamed case")))
    assert fault.value.problem.code == ServiceErrorCode.CONFLICT

    (tmp_path / "http").mkdir()
    http_service, http_directory, _http_store, _http_root = harness(tmp_path / "http")
    client = TestClient(build_app(http_service, http_directory))
    created_case(client, key="k1")
    response = client.post(
        "/v1/cases",
        json={"idempotency_key": "k1", "title": "Different title", "district": "Xindian"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "version_conflict"


def test_non_human_principal_cannot_create_a_case(tmp_path: Path) -> None:
    service, directory, _store, _root = harness(
        tmp_path, principal=person(actor_id="pipeline", kind="system")
    )
    with pytest.raises(ServiceFault) as fault:
        asyncio.run(service.create_case(directory.principal, command()))
    assert fault.value.problem.code == ServiceErrorCode.UNAUTHORIZED
    assert directory.calls == []


def test_upload_stores_exact_bytes_and_reports_their_sha256(tmp_path: Path) -> None:
    service, directory, _store, root = harness(tmp_path)
    client = TestClient(build_app(service, directory))
    case_id = created_case(client)
    data = b"%PDF-1.7 fake estimate body"

    response = upload(client, case_id, data)

    assert response.status_code == 201
    record = response.json()
    assert record["sha256"] == hashlib.sha256(data).hexdigest()
    assert record["size"] == len(data)
    assert record["filename"] == "estimate.pdf"
    assert record["media_type"] == "application/pdf"
    assert record["uploaded_by"]["actor_id"] == "reviewer-1"
    stored = root / case_id / record["material_id"]
    assert stored.read_bytes() == data

    replayed = upload(client, case_id, data)
    assert replayed.status_code == 201
    assert replayed.json()["material_id"] == record["material_id"]

    changed = upload(client, case_id, b"different bytes")
    assert changed.status_code == 409


def test_oversize_and_empty_uploads_are_refused(tmp_path: Path) -> None:
    service, directory, _store, root = harness(tmp_path, max_material_bytes=8)
    client = TestClient(build_app(service, directory))
    case_id = created_case(client)

    oversize = upload(client, case_id, b"123456789")
    assert oversize.status_code == 422
    assert oversize.json()["code"] == "invalid_request"
    empty = upload(client, case_id, b"", key="upload-2")
    assert empty.status_code == 422
    assert not (root / case_id).exists()


def test_unauthenticated_caller_is_refused_with_403(tmp_path: Path) -> None:
    service, _directory, _store, _root = harness(tmp_path)
    client = TestClient(build_app(service, RefusingResolver()))
    case_id = str(UUID(int=1, version=4))

    creation = client.post(
        "/v1/cases",
        json={"idempotency_key": "k1", "title": "T", "district": "D"},
    )
    listing = client.get(f"/v1/cases/{case_id}/materials")
    sending = upload(client, case_id, b"data")

    assert {creation.status_code, listing.status_code, sending.status_code} == {403}
    assert creation.json()["code"] == "unauthorized"


def test_non_member_cannot_upload_or_list(tmp_path: Path) -> None:
    service, directory, _store, _root = harness(tmp_path)
    client = TestClient(build_app(service, directory))
    case_id = created_case(client)

    directory.principal = person(actor_id="stranger")
    refused = upload(client, case_id, b"data")
    assert refused.status_code == 403
    assert client.get(f"/v1/cases/{case_id}/materials").status_code == 403


def test_upload_to_an_unknown_intake_case_is_not_found(tmp_path: Path) -> None:
    service, directory, _store, _root = harness(tmp_path)
    client = TestClient(build_app(service, directory))
    # Membership in a case the intake store never recorded is not an intake case.
    foreign = str(UUID(int=7, version=4))
    directory.principal = replace(
        directory.principal, case_ids=directory.principal.case_ids | {foreign}
    )
    assert upload(client, foreign, b"data").status_code == 404
    assert client.get(f"/v1/cases/{foreign}/materials").status_code == 404


def test_list_returns_records_without_bytes(tmp_path: Path) -> None:
    service, directory, _store, _root = harness(tmp_path)
    client = TestClient(build_app(service, directory))
    case_id = created_case(client)
    upload(client, case_id, b"first bytes", key="u1", filename="a.pdf")
    upload(
        client,
        case_id,
        b"second bytes",
        key="u2",
        filename="b.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    response = client.get(f"/v1/cases/{case_id}/materials")

    assert response.status_code == 200
    body = response.json()
    assert body["case_id"] == case_id
    assert [item["filename"] for item in body["materials"]] == ["a.pdf", "b.xlsx"]
    for item in body["materials"]:
        assert set(item) == {
            "schema_version",
            "material_id",
            "case_id",
            "filename",
            "media_type",
            "sha256",
            "size",
            "uploaded_by",
            "uploaded_at",
        }


def test_path_like_filename_is_refused(tmp_path: Path) -> None:
    service, directory, _store, _root = harness(tmp_path)
    client = TestClient(build_app(service, directory))
    case_id = created_case(client)
    refused = upload(client, case_id, b"data", filename="../escape.pdf")
    assert refused.status_code == 422
    assert refused.json()["code"] == "invalid_request"


def test_unwired_intake_plane_reports_capability_unavailable(tmp_path: Path) -> None:
    _service, directory, _store, _root = harness(tmp_path)
    client = TestClient(build_app(None, directory))
    response = client.post(
        "/v1/cases",
        json={"idempotency_key": "k1", "title": "T", "district": "D"},
    )
    assert response.status_code == 503
    assert response.json()["code"] == "capability_unavailable"
