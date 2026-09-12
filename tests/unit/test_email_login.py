"""Email one-time-code login regressions: oracle-free requests, single-use codes.

Service-level checks run against the real SQLite store on a private tmp root with
a fake mailer and a fake clock; the HTTP checks mount only the login router on a
minimal app with the same fault-status mapping the composed app installs. No test
touches the network: the SES adapter is exercised with a recording fake client.
"""

import asyncio
import logging
import sqlite3
from pathlib import Path
from typing import Literal

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import BaseModel

from appraisal_review.adapters.aws.ses_mailer import SesMailer
from appraisal_review.adapters.local.email_login_store import SQLiteEmailLoginStore
from appraisal_review.api.app import FAULT_STATUS
from appraisal_review.api.routes.email_login import (
    EMAIL_LOGIN_ENDPOINTS,
)
from appraisal_review.api.routes.email_login import (
    router as email_login_router,
)
from appraisal_review.application.email_login import (
    EmailLoginService,
    MailDeliveryUnavailable,
    RequestCodeCommand,
    VerifyCodeCommand,
)
from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.domain.service_contracts import ServiceErrorCode

NOW = 1_757_600_000
TOKEN = "issued-session-token-0123456789abcdef"


class Database:
    """Minimal ReviewDatabase substitute: same connection discipline, no policy."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, isolation_level=None)

    def _decode(self, payload: str) -> BaseModel:
        raise NotImplementedError


class FakeMailer:
    """Records every send; optionally refuses like the SES sandbox would."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.failure: MailDeliveryUnavailable | None = None

    def send_login_code(self, email: str, code: str) -> None:
        self.calls.append((email, code))
        if self.failure is not None:
            raise self.failure


class SessionIssuer:
    """Stands in for the directory-backed issue_session callable."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, email: str) -> tuple[str, int, str]:
        self.calls.append(email)
        return TOKEN, NOW + 3600, f"email:{email}"


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> int:
        return self.now


def harness(
    tmp_path: Path,
) -> tuple[EmailLoginService, FakeMailer, SessionIssuer, Clock, SQLiteEmailLoginStore]:
    store = SQLiteEmailLoginStore(Database(tmp_path / "login.sqlite"))
    mailer = FakeMailer()
    issuer = SessionIssuer()
    clock = Clock()
    service = EmailLoginService(store=store, mailer=mailer, issue_session=issuer, clock=clock)
    return service, mailer, issuer, clock, store


def request_code(service: EmailLoginService, email: str, caller: str = "203.0.113.7") -> dict:
    accepted = asyncio.run(service.request_code(RequestCodeCommand(email=email), caller=caller))
    return accepted.model_dump(mode="json")


def verify(service: EmailLoginService, email: str, code: str) -> dict:
    grant = asyncio.run(service.verify_code(VerifyCodeCommand(email=email, code=code)))
    return grant.model_dump(mode="json")


def refused(service: EmailLoginService, email: str, code: str) -> ServiceErrorCode:
    with pytest.raises(ServiceFault) as failure:
        asyncio.run(service.verify_code(VerifyCodeCommand(email=email, code=code)))
    return failure.value.problem.code


def build_app(service: EmailLoginService | None) -> FastAPI:
    app = FastAPI()
    app.state.email_login = service
    app.include_router(email_login_router)

    @app.exception_handler(ServiceFault)
    async def service_fault(request: Request, fault: ServiceFault) -> JSONResponse:
        return JSONResponse(
            status_code=FAULT_STATUS[fault.problem.code],
            content=fault.problem.model_dump(mode="json"),
        )

    return app


def test_request_code_response_identical_for_every_outcome(tmp_path: Path) -> None:
    """Deliverable, undeliverable and repeat addresses all get one exact body."""
    service, mailer, _, _, _ = harness(tmp_path)
    deliverable = request_code(service, "reviewer@example.com")
    mailer.failure = MailDeliveryUnavailable("delivery_restricted")
    undeliverable = request_code(service, "stranger@example.org")
    assert deliverable == undeliverable
    assert len(mailer.calls) == 2


def test_rate_limit_per_email_mints_and_sends_nothing_extra(tmp_path: Path) -> None:
    service, mailer, _, _, store = harness(tmp_path)
    bodies = [
        request_code(service, "reviewer@example.com", caller=f"198.51.100.{index}")
        for index in range(4)
    ]
    assert all(body == bodies[0] for body in bodies)
    assert len(mailer.calls) == 3
    assert len(store.delivery_statuses()) == 3


def test_rate_limit_per_caller_covers_distinct_emails(tmp_path: Path) -> None:
    service, mailer, _, _, _ = harness(tmp_path)
    for index in range(4):
        request_code(service, f"reviewer{index}@example.com", caller="203.0.113.7")
    assert len(mailer.calls) == 3


def test_rate_limit_window_reopens_with_the_clock(tmp_path: Path) -> None:
    service, mailer, _, clock, _ = harness(tmp_path)
    for _ in range(3):
        request_code(service, "reviewer@example.com")
    clock.now = NOW + 601
    request_code(service, "reviewer@example.com")
    assert len(mailer.calls) == 4


def test_verify_success_issues_session_via_injected_callable_exactly_once(
    tmp_path: Path,
) -> None:
    service, mailer, issuer, _, _ = harness(tmp_path)
    request_code(service, "Reviewer@Example.com ")
    email, code = mailer.calls[-1]
    assert email == "reviewer@example.com"
    grant = verify(service, "reviewer@example.com", code)
    assert grant == {
        "schema_version": "service-v1",
        "token": TOKEN,
        "expires_at": NOW + 3600,
        "actor_id": "email:reviewer@example.com",
    }
    assert issuer.calls == ["reviewer@example.com"]


def test_code_is_single_use_and_replay_is_refused(tmp_path: Path) -> None:
    service, mailer, issuer, _, _ = harness(tmp_path)
    request_code(service, "reviewer@example.com")
    _, code = mailer.calls[-1]
    verify(service, "reviewer@example.com", code)
    assert refused(service, "reviewer@example.com", code) == ServiceErrorCode.UNAUTHORIZED
    assert len(issuer.calls) == 1


def test_wrong_code_attempts_exhaust_and_kill_the_record(tmp_path: Path) -> None:
    service, mailer, issuer, _, _ = harness(tmp_path)
    request_code(service, "reviewer@example.com")
    _, code = mailer.calls[-1]
    wrong = "000000" if code != "000000" else "000001"
    for _ in range(5):
        assert refused(service, "reviewer@example.com", wrong) == ServiceErrorCode.UNAUTHORIZED
    # The exhausted record refuses even the correct code, exactly like an unknown one.
    assert refused(service, "reviewer@example.com", code) == ServiceErrorCode.UNAUTHORIZED
    assert issuer.calls == []


def test_expiry_is_honored_with_the_injected_clock(tmp_path: Path) -> None:
    service, mailer, issuer, clock, _ = harness(tmp_path)
    request_code(service, "reviewer@example.com")
    _, code = mailer.calls[-1]
    clock.now = NOW + 600
    assert refused(service, "reviewer@example.com", code) == ServiceErrorCode.UNAUTHORIZED
    assert issuer.calls == []


def test_unknown_email_and_stale_code_share_one_generic_refusal(tmp_path: Path) -> None:
    service, mailer, _, _, _ = harness(tmp_path)
    assert refused(service, "nobody@example.com", "123456") == ServiceErrorCode.UNAUTHORIZED
    request_code(service, "reviewer@example.com")
    old_code = mailer.calls[-1][1]
    request_code(service, "reviewer@example.com")
    new_code = mailer.calls[-1][1]
    if old_code != new_code:
        # Only the latest minted code is live; a superseded one refuses generically.
        assert refused(service, "reviewer@example.com", old_code) == (ServiceErrorCode.UNAUTHORIZED)
    verify(service, "reviewer@example.com", new_code)


def test_sandbox_failure_marks_undeliverable_without_leaking_to_requester(
    tmp_path: Path,
) -> None:
    service, mailer, _, _, store = harness(tmp_path)
    mailer.failure = MailDeliveryUnavailable("delivery_restricted")
    body = request_code(service, "unverified@example.org")
    assert body["status"] == "accepted"
    statuses = store.delivery_statuses()
    assert len(statuses) == 1
    assert statuses[0].delivery == "undeliverable"
    assert statuses[0].failure_reason == "delivery_restricted"
    # The requester-visible body carries no delivery information at all.
    assert "undeliverable" not in str(body) and "delivery" not in str(body)


def test_delivery_listing_and_stored_rows_carry_no_code_material(tmp_path: Path) -> None:
    service, mailer, _, _, store = harness(tmp_path)
    request_code(service, "reviewer@example.com")
    _, code = mailer.calls[-1]
    listing = [status.model_dump_json() for status in store.delivery_statuses()]
    assert listing and all(code not in item for item in listing)
    connection = sqlite3.connect(tmp_path / "login.sqlite")
    try:
        for table in ("email_login_codes", "email_login_requests", "email_login_send_audit"):
            for row in connection.execute(f"SELECT * FROM {table}").fetchall():
                assert code not in "".join(str(value) for value in row)
    finally:
        connection.close()


def test_codes_never_appear_in_responses_or_log_records(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    service, mailer, _, _, _ = harness(tmp_path)
    with caplog.at_level(logging.DEBUG):
        request_body = request_code(service, "reviewer@example.com")
        _, code = mailer.calls[-1]
        grant_body = verify(service, "reviewer@example.com", code)
    assert code not in str(request_body)
    assert code not in str(grant_body)
    assert all(code not in record.getMessage() for record in caplog.records)


def test_mail_delivery_reason_is_always_sanitized() -> None:
    assert MailDeliveryUnavailable("delivery_restricted").reason == "delivery_restricted"
    raw = MailDeliveryUnavailable("Email address is not verified. user@example.com")
    assert raw.reason == "provider_unavailable"


def test_normalization_refuses_addresses_that_are_not_plain_mailboxes() -> None:
    for bad in ("reviewer", "reviewer@", "@example.com", "a@b@c.d", "a b@example.com", "a@b"):
        with pytest.raises(ValueError):
            RequestCodeCommand(email=bad)
    with pytest.raises(ValueError):
        VerifyCodeCommand(email="reviewer@example.com", code="12345")


# --- HTTP surface -------------------------------------------------------------


def test_route_module_mounts_exactly_the_two_public_login_endpoints() -> None:
    paths = {route.path for route in email_login_router.routes}
    assert paths == {"/v1/auth/request-code", "/v1/auth/verify"}
    assert len(EMAIL_LOGIN_ENDPOINTS) == 2


def test_http_login_round_trip_is_unauthenticated_and_single_use(tmp_path: Path) -> None:
    service, mailer, _, _, _ = harness(tmp_path)
    client = TestClient(build_app(service))
    first = client.post("/v1/auth/request-code", json={"email": "reviewer@example.com"})
    second = client.post("/v1/auth/request-code", json={"email": "nobody@example.org"})
    assert first.status_code == 202 and second.status_code == 202
    assert first.json() == second.json()
    _, code = mailer.calls[0]
    assert code not in first.text
    granted = client.post("/v1/auth/verify", json={"email": "reviewer@example.com", "code": code})
    assert granted.status_code == 200
    assert granted.json()["token"] == TOKEN
    replay = client.post("/v1/auth/verify", json={"email": "reviewer@example.com", "code": code})
    assert replay.status_code == 403
    assert replay.json()["code"] == "unauthorized"


def test_unwired_login_plane_answers_capability_unavailable(tmp_path: Path) -> None:
    client = TestClient(build_app(None))
    response = client.post("/v1/auth/request-code", json={"email": "reviewer@example.com"})
    assert response.status_code == 503
    assert response.json()["code"] == "capability_unavailable"


def test_http_wrong_code_is_the_same_generic_403_as_unknown_email(tmp_path: Path) -> None:
    service, mailer, _, _, _ = harness(tmp_path)
    client = TestClient(build_app(service))
    client.post("/v1/auth/request-code", json={"email": "reviewer@example.com"})
    _, code = mailer.calls[-1]
    wrong = "000000" if code != "000000" else "000001"
    mismatch = client.post("/v1/auth/verify", json={"email": "reviewer@example.com", "code": wrong})
    unknown = client.post("/v1/auth/verify", json={"email": "nobody@example.org", "code": wrong})
    assert mismatch.status_code == unknown.status_code == 403
    assert mismatch.json() == unknown.json()


# --- SES adapter ---------------------------------------------------------------


class FakeSesClient:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.sent: list[dict] = []

    def send_email(self, **kwargs: object) -> dict:
        if self.error is not None:
            raise self.error
        self.sent.append(dict(kwargs))
        return {"MessageId": "fake"}


class FakeSesError(Exception):
    def __init__(self, code: str) -> None:
        self.response = {"Error": {"Code": code, "Message": "raw provider detail"}}
        super().__init__(code)


def test_ses_mailer_sends_one_zh_tw_plaintext_message(tmp_path: Path) -> None:
    client = FakeSesClient()
    mailer = SesMailer(lambda: client, sender="login@example.com")
    mailer.send_login_code("reviewer@example.com", "123456")
    assert len(client.sent) == 1
    message = client.sent[0]
    assert message["FromEmailAddress"] == "login@example.com"
    assert message["Destination"] == {"ToAddresses": ["reviewer@example.com"]}
    body = message["Content"]["Simple"]["Body"]["Text"]["Data"]
    assert "123456" in body and "10 分鐘" in body
    assert "Html" not in message["Content"]["Simple"]["Body"]


@pytest.mark.parametrize(
    ("code", "reason"),
    [
        ("MessageRejected", "delivery_restricted"),
        ("SendingPausedException", "delivery_restricted"),
        ("TooManyRequestsException", "throttled"),
        ("LimitExceededException", "throttled"),
        ("SomethingElse", "provider_unavailable"),
    ],
)
def test_ses_errors_map_to_typed_sanitized_failures(
    code: str, reason: Literal["delivery_restricted", "throttled", "provider_unavailable"]
) -> None:
    mailer = SesMailer(lambda: FakeSesClient(error=FakeSesError(code)), sender="login@example.com")
    with pytest.raises(MailDeliveryUnavailable) as failure:
        mailer.send_login_code("reviewer@example.com", "123456")
    assert failure.value.reason == reason
    assert "raw provider detail" not in str(failure.value)


def test_ses_mailer_requires_a_configured_sender() -> None:
    with pytest.raises(ValueError):
        SesMailer(lambda: FakeSesClient(), sender="not-an-address")


def test_ses_mailer_builds_its_client_lazily_and_once() -> None:
    built: list[FakeSesClient] = []

    def factory() -> FakeSesClient:
        client = FakeSesClient()
        built.append(client)
        return client

    mailer = SesMailer(factory, sender="login@example.com")
    assert built == []
    mailer.send_login_code("reviewer@example.com", "123456")
    mailer.send_login_code("reviewer@example.com", "654321")
    assert len(built) == 1
    assert len(built[0].sent) == 2
