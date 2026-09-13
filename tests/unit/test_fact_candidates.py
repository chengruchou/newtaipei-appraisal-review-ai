"""Fact candidate regressions: registration, named-human receipts, replay, routes."""

import asyncio
import hashlib
import sqlite3
from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

from appraisal_review.adapters.local.candidate_store import SQLiteCandidateStore
from appraisal_review.api.app import FAULT_STATUS
from appraisal_review.api.routes.fact_candidates import router as fact_candidates_router
from appraisal_review.application.fact_candidates import (
    CandidateService,
    ConfirmCandidateCommand,
    FactCandidate,
    RegisterCandidateCommand,
    distance_m,
)
from appraisal_review.application.service_guards import Principal, ServiceFault
from appraisal_review.domain.service_contracts import (
    ActorReference,
    Permission,
    ServiceErrorCode,
)

NOW = 1_757_600_000
CASE_ID = str(uuid4())
EVIDENCE_SHA = hashlib.sha256(b'[{"name": "example park"}]').hexdigest()


class Database:
    """Minimal ReviewDatabase substitute: same connection discipline, no policy."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, isolation_level=None)

    def _decode(self, payload: str) -> BaseModel:
        raise NotImplementedError


class Resolver:
    """Adapter-bound principal; tests swap it to model actors or strangers."""

    def __init__(self, principal: Principal) -> None:
        self.principal = principal

    async def current_principal(self) -> Principal:
        return self.principal


class RefusingResolver:
    async def current_principal(self) -> Principal:
        raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)


def person(
    actor_id: str = "reviewer-1",
    kind: str = "human",
    *,
    permissions: frozenset[Permission] | None = None,
    case_ids: frozenset[str] | None = None,
) -> Principal:
    return Principal(
        actor=ActorReference(actor_id=actor_id, kind=kind),  # type: ignore[arg-type]
        case_ids=frozenset({CASE_ID}) if case_ids is None else case_ids,
        permissions=(
            frozenset({Permission.REVIEW, Permission.CONFIRM})
            if permissions is None
            else permissions
        ),
    )


def harness(tmp_path: Path) -> tuple[CandidateService, SQLiteCandidateStore]:
    store = SQLiteCandidateStore(Database(tmp_path / "candidates.sqlite"))
    return CandidateService(store=store, clock=lambda: NOW), store


def command(
    key: str = "cand-1", value: str = "342", field_key: str = "table_5.P002.market_distance"
) -> RegisterCandidateCommand:
    return RegisterCandidateCommand(
        idempotency_key=key,
        revision_id="rev-1",
        subject_id="P002",
        field_key=field_key,
        value=value,
        unit="m",
        applicable_date="2026-09-12",
        source_id="ntpc-parks",
        evidence={
            "url": "https://data.ntpc.gov.tw/api/datasets/x/json?page=0&size=1000",
            "sha256": EVIDENCE_SHA,
            "retrieved_at": NOW - 60,
            "excerpt": '{"name": "example park"}',
            "row_locator": "page=0,row=17",
        },
    )


def confirmation(
    candidate: FactCandidate,
    *,
    key: str = "confirm-1",
    decision: str = "accept",
    reason: str | None = None,
    **overrides: object,
) -> ConfirmCandidateCommand:
    payload: dict[str, object] = {
        "idempotency_key": key,
        "decision": decision,
        "reason": reason,
        "expected_revision": candidate.revision_id,
        "accepted_value": candidate.value,
        "accepted_unit": candidate.unit,
        "accepted_applicable_date": candidate.applicable_date,
        "evidence_sha256": candidate.evidence.sha256,
    }
    payload.update(overrides)
    return ConfirmCandidateCommand.model_validate(payload)


def register(service: CandidateService, principal: Principal, **kwargs: object) -> FactCandidate:
    return asyncio.run(service.register_candidate(principal, CASE_ID, command(**kwargs)))  # type: ignore[arg-type]


def build_app(service: CandidateService | None, resolver: object) -> FastAPI:
    app = FastAPI()
    app.state.fact_candidates = service
    app.state.principal_resolver = resolver
    app.include_router(fact_candidates_router)

    @app.exception_handler(ServiceFault)
    async def service_fault(request: Request, fault: ServiceFault) -> JSONResponse:
        return JSONResponse(
            status_code=FAULT_STATUS[fault.problem.code],
            content=fault.problem.model_dump(mode="json"),
        )

    return app


def test_register_stores_a_candidate_and_only_a_candidate(tmp_path: Path) -> None:
    service, store = harness(tmp_path)
    principal = person()

    candidate = register(service, principal)

    assert candidate.status == "candidate"
    assert candidate.case_id == CASE_ID
    assert candidate.field_key == "table_5.P002.market_distance"
    assert candidate.value == "342"
    assert candidate.created_by == principal.actor
    assert candidate.created_at == NOW
    assert store.read(CASE_ID, candidate.candidate_id) == candidate
    listing = asyncio.run(service.list_candidates(principal, CASE_ID))
    assert listing.candidates == (candidate,)
    # Nothing is adopted: a fresh candidate never appears in the confirmed list.
    assert asyncio.run(service.confirmed_unadopted(principal, CASE_ID)).candidates == ()


def test_register_replay_and_payload_conflict(tmp_path: Path) -> None:
    service, _store = harness(tmp_path)
    principal = person()

    first = register(service, principal)
    replayed = register(service, principal)
    assert replayed == first

    with pytest.raises(ServiceFault) as fault:
        register(service, principal, value="999")
    assert fault.value.problem.code == ServiceErrorCode.CONFLICT


def test_model_actor_may_register_but_never_confirm(tmp_path: Path) -> None:
    service, _store = harness(tmp_path)
    model = person(actor_id="bedrock-tool", kind="model")

    candidate = register(service, model)
    assert candidate.created_by.kind == "model"

    with pytest.raises(ServiceFault) as fault:
        asyncio.run(
            service.confirm(model, CASE_ID, candidate.candidate_id, confirmation(candidate))
        )
    assert fault.value.problem.code == ServiceErrorCode.UNAUTHORIZED
    system = person(actor_id="pipeline", kind="system")
    with pytest.raises(ServiceFault):
        asyncio.run(
            service.confirm(system, CASE_ID, candidate.candidate_id, confirmation(candidate))
        )
    assert asyncio.run(service.confirmed_unadopted(person(), CASE_ID)).candidates == ()


def test_non_member_and_missing_permission_are_refused(tmp_path: Path) -> None:
    service, _store = harness(tmp_path)
    stranger = person(actor_id="stranger", case_ids=frozenset())
    with pytest.raises(ServiceFault) as fault:
        register(service, stranger)
    assert fault.value.problem.code == ServiceErrorCode.UNAUTHORIZED

    candidate = register(service, person())
    reviewer_only = person(permissions=frozenset({Permission.REVIEW}))
    with pytest.raises(ServiceFault) as confirm_fault:
        asyncio.run(
            service.confirm(reviewer_only, CASE_ID, candidate.candidate_id, confirmation(candidate))
        )
    assert confirm_fault.value.problem.code == ServiceErrorCode.UNAUTHORIZED


def test_confirm_binds_the_exact_value_evidence_and_revision(tmp_path: Path) -> None:
    service, store = harness(tmp_path)
    principal = person()
    candidate = register(service, principal)

    receipt = asyncio.run(
        service.confirm(principal, CASE_ID, candidate.candidate_id, confirmation(candidate))
    )

    assert receipt.decision == "accept"
    assert receipt.candidate_id == candidate.candidate_id
    assert receipt.case_id == CASE_ID
    assert receipt.subject_id == "P002"
    assert receipt.field_key == candidate.field_key
    assert receipt.expected_revision == candidate.revision_id
    assert receipt.accepted_value == candidate.value
    assert receipt.accepted_unit == candidate.unit
    assert receipt.accepted_applicable_date == candidate.applicable_date
    assert receipt.evidence_sha256 == candidate.evidence.sha256
    assert receipt.actor == principal.actor
    assert receipt.actor.kind == "human"
    assert receipt.decided_at == NOW

    stored = store.read(CASE_ID, candidate.candidate_id)
    assert stored is not None and stored.status == "confirmed"
    # Confirmed is still NOT adopted; the honest listing carries it.
    unadopted = asyncio.run(service.confirmed_unadopted(principal, CASE_ID))
    assert [c.candidate_id for c in unadopted.candidates] == [candidate.candidate_id]


def test_confirm_replay_returns_the_same_receipt_and_conflicts_on_change(tmp_path: Path) -> None:
    service, _store = harness(tmp_path)
    principal = person()
    candidate = register(service, principal)

    first = asyncio.run(
        service.confirm(principal, CASE_ID, candidate.candidate_id, confirmation(candidate))
    )
    replayed = asyncio.run(
        service.confirm(principal, CASE_ID, candidate.candidate_id, confirmation(candidate))
    )
    assert replayed == first

    with pytest.raises(ServiceFault) as fault:
        asyncio.run(
            service.confirm(
                principal,
                CASE_ID,
                candidate.candidate_id,
                confirmation(candidate, decision="reject", reason="different payload"),
            )
        )
    assert fault.value.problem.code == ServiceErrorCode.CONFLICT


def test_confirmed_candidate_cannot_be_decided_again_with_a_new_key(tmp_path: Path) -> None:
    service, _store = harness(tmp_path)
    principal = person()
    candidate = register(service, principal)
    asyncio.run(
        service.confirm(principal, CASE_ID, candidate.candidate_id, confirmation(candidate))
    )

    other_human = person(actor_id="reviewer-2")
    with pytest.raises(ServiceFault) as fault:
        asyncio.run(
            service.confirm(
                other_human, CASE_ID, candidate.candidate_id, confirmation(candidate, key="again")
            )
        )
    assert fault.value.problem.code == ServiceErrorCode.CONFLICT


def test_wrong_revision_echo_or_evidence_digest_conflicts(tmp_path: Path) -> None:
    service, _store = harness(tmp_path)
    principal = person()
    candidate = register(service, principal)

    cases = (
        confirmation(candidate, expected_revision="rev-9"),
        confirmation(candidate, accepted_value="343"),
        confirmation(candidate, accepted_unit="km"),
        confirmation(candidate, accepted_applicable_date="2026-01-01"),
        confirmation(candidate, evidence_sha256=hashlib.sha256(b"other").hexdigest()),
    )
    for wrong in cases:
        with pytest.raises(ServiceFault) as fault:
            asyncio.run(service.confirm(principal, CASE_ID, candidate.candidate_id, wrong))
        assert fault.value.problem.code == ServiceErrorCode.CONFLICT
    # None of the refused echoes may have decided the candidate.
    accepted = asyncio.run(
        service.confirm(principal, CASE_ID, candidate.candidate_id, confirmation(candidate))
    )
    assert accepted.decision == "accept"


def test_reject_requires_a_reason_and_records_it(tmp_path: Path) -> None:
    service, store = harness(tmp_path)
    principal = person()
    candidate = register(service, principal)

    with pytest.raises(ValidationError):
        confirmation(candidate, decision="reject", reason=None)

    receipt = asyncio.run(
        service.confirm(
            principal,
            CASE_ID,
            candidate.candidate_id,
            confirmation(candidate, decision="reject", reason="Row does not match the parcel"),
        )
    )
    assert receipt.decision == "reject"
    assert receipt.reason == "Row does not match the parcel"
    stored = store.read(CASE_ID, candidate.candidate_id)
    assert stored is not None and stored.status == "rejected"
    assert asyncio.run(service.confirmed_unadopted(principal, CASE_ID)).candidates == ()


def test_unknown_candidate_is_not_found(tmp_path: Path) -> None:
    service, _store = harness(tmp_path)
    principal = person()
    ghost = FactCandidate(
        candidate_id=str(uuid4()),
        case_id=CASE_ID,
        revision_id="rev-1",
        subject_id="P002",
        field_key="table_5.P002.market_distance",
        value="342",
        source_id="ntpc-parks",
        evidence={"url": "https://x", "sha256": EVIDENCE_SHA, "retrieved_at": NOW},
        created_by=principal.actor,
        created_at=NOW,
    )
    with pytest.raises(ServiceFault) as fault:
        asyncio.run(service.confirm(principal, CASE_ID, ghost.candidate_id, confirmation(ghost)))
    assert fault.value.problem.code == ServiceErrorCode.NOT_FOUND


def test_routes_register_list_confirm_and_confirmed_unadopted(tmp_path: Path) -> None:
    service, _store = harness(tmp_path)
    resolver = Resolver(person())
    client = TestClient(build_app(service, resolver))

    created = client.post(
        f"/v1/cases/{CASE_ID}/candidates",
        json=command().model_dump(mode="json"),
    )
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "candidate"
    candidate_id = body["candidate_id"]

    listing = client.get(f"/v1/cases/{CASE_ID}/candidates")
    assert listing.status_code == 200
    assert [c["candidate_id"] for c in listing.json()["candidates"]] == [candidate_id]

    candidate = FactCandidate.model_validate(body)
    confirmed = client.post(
        f"/v1/cases/{CASE_ID}/candidates/{candidate_id}/confirm",
        json=confirmation(candidate).model_dump(mode="json"),
    )
    assert confirmed.status_code == 201
    assert confirmed.json()["decision"] == "accept"
    assert confirmed.json()["actor"]["kind"] == "human"

    unadopted = client.get(f"/v1/cases/{CASE_ID}/candidates/confirmed-unadopted")
    assert unadopted.status_code == 200
    assert [c["candidate_id"] for c in unadopted.json()["candidates"]] == [candidate_id]

    # A rejected reason-less decision never reaches the service.
    payload = confirmation(candidate, key="k2").model_dump(mode="json")
    payload["decision"] = "reject"
    payload["reason"] = None
    refused = client.post(
        f"/v1/cases/{CASE_ID}/candidates/{candidate_id}/confirm",
        json=payload,
    )
    assert refused.status_code == 422


def test_routes_refuse_unauthenticated_and_unmembered_callers(tmp_path: Path) -> None:
    service, _store = harness(tmp_path)
    client = TestClient(build_app(service, RefusingResolver()))
    candidate_id = str(UUID(int=1, version=4))

    responses = (
        client.post(f"/v1/cases/{CASE_ID}/candidates", json=command().model_dump(mode="json")),
        client.get(f"/v1/cases/{CASE_ID}/candidates"),
        client.get(f"/v1/cases/{CASE_ID}/candidates/confirmed-unadopted"),
        client.post(
            f"/v1/cases/{CASE_ID}/candidates/{candidate_id}/confirm",
            json={
                "idempotency_key": "k",
                "decision": "accept",
                "expected_revision": "rev-1",
                "accepted_value": "342",
                "evidence_sha256": EVIDENCE_SHA,
            },
        ),
    )
    assert {response.status_code for response in responses} == {403}

    member = Resolver(person())
    stranger_client = TestClient(build_app(service, member))
    member.principal = replace(member.principal, case_ids=frozenset())
    refused = stranger_client.get(f"/v1/cases/{CASE_ID}/candidates")
    assert refused.status_code == 403
    assert refused.json()["code"] == "unauthorized"


def test_unwired_candidate_plane_reports_capability_unavailable() -> None:
    client = TestClient(build_app(None, Resolver(person())))
    response = client.get(f"/v1/cases/{CASE_ID}/candidates")
    assert response.status_code == 503
    assert response.json()["code"] == "capability_unavailable"


def test_distance_m_is_a_straight_line_haversine_in_whole_meters() -> None:
    # One degree of longitude on the equator on the WGS84 mean radius.
    assert distance_m(0.0, 0.0, 0.0, 1.0) == 111195
    # Symmetric and zero at coincident points.
    assert distance_m(25.0132, 121.4623, 25.0221, 121.4534) == distance_m(
        25.0221, 121.4534, 25.0132, 121.4623
    )
    assert distance_m(25.0132, 121.4623, 25.0132, 121.4623) == 0
    # A realistic Banqiao-scale hop lands in a sane sub-2km band.
    assert 1000 < distance_m(25.0132, 121.4623, 25.0221, 121.4534) < 2000


def test_distance_m_refuses_out_of_range_coordinates() -> None:
    with pytest.raises(ValueError):
        distance_m(91.0, 0.0, 0.0, 0.0)
    with pytest.raises(ValueError):
        distance_m(0.0, 0.0, -90.5, 0.0)
    with pytest.raises(ValueError):
        distance_m(0.0, 180.5, 0.0, 0.0)
    with pytest.raises(ValueError):
        distance_m(0.0, 0.0, 0.0, -181.0)
