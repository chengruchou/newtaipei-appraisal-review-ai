"""Email login regressions: oracle-free requests, single-use codes, passwords.

Service-level checks run against the real SQLite store on a private tmp root with
a fake mailer and a fake clock; the HTTP checks mount only the login router on a
minimal app with the same fault-status mapping AND the same sanitized 422 branch
(keyed on EMAIL_LOGIN_ENDPOINTS membership) the composed app installs. No test
touches the network: the SES adapter is exercised with a recording fake client.
Password checks use the real scrypt KDF unless a test injects the counting seam.
"""

import asyncio
import hashlib
import logging
import sqlite3
from pathlib import Path
from typing import Literal

import pytest
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
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
    _TIMING_EQUALIZER_SALT,
    EmailLoginService,
    MailDeliveryUnavailable,
    PasswordLoginCommand,
    RequestCodeCommand,
    VerifyCodeCommand,
)
from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.domain.service_contracts import ServiceErrorCode

NOW = 1_757_600_000
TOKEN = "issued-session-token-0123456789abcdef"
PASSWORD = "correct-horse-battery-staple"
NEW_PASSWORD = "brand-new-secret-passphrase"


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


class CountingKdf:
    """Fast KDF seam: records every salt so tests can see the timing-dummy path."""

    def __init__(self) -> None:
        self.salts: list[bytes] = []

    def __call__(self, password: bytes, salt: bytes) -> bytes:
        self.salts.append(salt)
        return hashlib.sha256(password + salt).digest()


def harness(
    tmp_path: Path, **overrides: object
) -> tuple[EmailLoginService, FakeMailer, SessionIssuer, Clock, SQLiteEmailLoginStore]:
    store = SQLiteEmailLoginStore(Database(tmp_path / "login.sqlite"))
    mailer = FakeMailer()
    issuer = SessionIssuer()
    clock = Clock()
    service = EmailLoginService(
        store=store,
        mailer=mailer,
        issue_session=issuer,
        clock=clock,
        **overrides,  # type: ignore[arg-type]
    )
    return service, mailer, issuer, clock, store


def request_code(service: EmailLoginService, email: str, caller: str = "203.0.113.7") -> dict:
    accepted = asyncio.run(service.request_code(RequestCodeCommand(email=email), caller=caller))
    return accepted.model_dump(mode="json")


def verify(
    service: EmailLoginService, email: str, code: str, new_password: str | None = None
) -> dict:
    grant = asyncio.run(
        service.verify_code(VerifyCodeCommand(email=email, code=code, new_password=new_password))
    )
    return grant.model_dump(mode="json")


def refused(service: EmailLoginService, email: str, code: str) -> ServiceErrorCode:
    with pytest.raises(ServiceFault) as failure:
        asyncio.run(service.verify_code(VerifyCodeCommand(email=email, code=code)))
    return failure.value.problem.code


def login(
    service: EmailLoginService, email: str, password: str, caller: str = "203.0.113.7"
) -> dict:
    grant = asyncio.run(
        service.login(PasswordLoginCommand(email=email, password=password), caller=caller)
    )
    return grant.model_dump(mode="json")


def refused_login(
    service: EmailLoginService, email: str, password: str, caller: str = "203.0.113.7"
) -> ServiceErrorCode:
    with pytest.raises(ServiceFault) as failure:
        asyncio.run(
            service.login(PasswordLoginCommand(email=email, password=password), caller=caller)
        )
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

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
        # Mirror of the composed app's sanitized branch: membership in
        # EMAIL_LOGIN_ENDPOINTS is exactly what keeps a rejected payload
        # (which may carry a password) out of the 422 body.
        endpoint = getattr(request.scope.get("route"), "endpoint", None)
        assert endpoint in EMAIL_LOGIN_ENDPOINTS
        return JSONResponse(
            status_code=422,
            content=ServiceFault(ServiceErrorCode.VALIDATION).problem.model_dump(mode="json"),
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
        "password_set": False,
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


# --- Password accounts ---------------------------------------------------------


def test_register_with_code_and_password_creates_account_and_signs_in(tmp_path: Path) -> None:
    """Code + new password = mailbox proven, account upserted, session issued."""
    service, mailer, issuer, _, store = harness(tmp_path)
    request_code(service, "reviewer@example.com")
    _, code = mailer.calls[-1]
    grant = verify(service, "reviewer@example.com", code, new_password=PASSWORD)
    assert grant["token"] == TOKEN
    assert grant["password_set"] is True
    assert issuer.calls == ["reviewer@example.com"]
    account = store.read_account("reviewer@example.com")
    assert account is not None
    assert account.password_set_count == 1
    assert account.created_at == NOW and account.updated_at == NOW
    # Only scrypt material is stored, never anything derived trivially from it.
    assert PASSWORD not in account.model_dump_json()
    assert len(account.password_scrypt_salt) == 64 and len(account.password_scrypt_hash) == 64


def test_code_only_verify_still_works_and_creates_no_account(tmp_path: Path) -> None:
    service, mailer, _, _, store = harness(tmp_path)
    request_code(service, "reviewer@example.com")
    _, code = mailer.calls[-1]
    grant = verify(service, "reviewer@example.com", code)
    assert grant["password_set"] is False
    assert store.read_account("reviewer@example.com") is None
    # No account means password login refuses with the one generic code.
    assert refused_login(service, "reviewer@example.com", PASSWORD) == (
        ServiceErrorCode.UNAUTHORIZED
    )


def test_password_login_issues_session_with_the_right_password(tmp_path: Path) -> None:
    service, mailer, issuer, _, _ = harness(tmp_path)
    request_code(service, "reviewer@example.com")
    _, code = mailer.calls[-1]
    verify(service, "reviewer@example.com", code, new_password=PASSWORD)
    grant = login(service, "reviewer@example.com", PASSWORD)
    assert grant == {
        "schema_version": "service-v1",
        "token": TOKEN,
        "expires_at": NOW + 3600,
        "actor_id": "email:reviewer@example.com",
        "password_set": False,
    }
    assert issuer.calls == ["reviewer@example.com", "reviewer@example.com"]


def test_wrong_password_and_unknown_email_share_one_generic_refusal(tmp_path: Path) -> None:
    service, mailer, issuer, _, _ = harness(tmp_path)
    request_code(service, "reviewer@example.com")
    _, code = mailer.calls[-1]
    verify(service, "reviewer@example.com", code, new_password=PASSWORD)
    wrong = refused_login(service, "reviewer@example.com", "not-the-password")
    unknown = refused_login(service, "nobody@example.org", "not-the-password")
    assert wrong == unknown == ServiceErrorCode.UNAUTHORIZED
    assert issuer.calls == ["reviewer@example.com"]


def test_unknown_email_burns_the_same_kdf_against_the_fixed_fake_salt(tmp_path: Path) -> None:
    """The timing-dummy path really executes: one KDF call, fixed equalizer salt."""
    kdf = CountingKdf()
    service, mailer, _, _, _ = harness(tmp_path, password_kdf=kdf)
    request_code(service, "reviewer@example.com")
    _, code = mailer.calls[-1]
    verify(service, "reviewer@example.com", code, new_password=PASSWORD)
    calls_before = len(kdf.salts)
    refused_login(service, "nobody@example.org", PASSWORD)
    assert len(kdf.salts) == calls_before + 1
    assert kdf.salts[-1] == _TIMING_EQUALIZER_SALT
    # The known-email path spends exactly the same single KDF call.
    refused_login(service, "reviewer@example.com", "not-the-password")
    assert len(kdf.salts) == calls_before + 2
    assert kdf.salts[-1] != _TIMING_EQUALIZER_SALT


def test_password_reset_via_second_code_flow_replaces_the_old_password(tmp_path: Path) -> None:
    """Forgot-password IS the registration flow; the old password stops working."""
    service, mailer, _, _, store = harness(tmp_path)
    request_code(service, "reviewer@example.com")
    _, first_code = mailer.calls[-1]
    verify(service, "reviewer@example.com", first_code, new_password=PASSWORD)
    assert login(service, "reviewer@example.com", PASSWORD)["token"] == TOKEN
    request_code(service, "reviewer@example.com")
    _, second_code = mailer.calls[-1]
    grant = verify(service, "reviewer@example.com", second_code, new_password=NEW_PASSWORD)
    assert grant["password_set"] is True
    assert login(service, "reviewer@example.com", NEW_PASSWORD)["token"] == TOKEN
    assert refused_login(service, "reviewer@example.com", PASSWORD) == (
        ServiceErrorCode.UNAUTHORIZED
    )
    account = store.read_account("reviewer@example.com")
    assert account is not None and account.password_set_count == 2
    assert account.created_at == NOW and account.updated_at == NOW


def test_login_rate_limit_covers_email_and_caller_and_reopens_with_the_clock(
    tmp_path: Path,
) -> None:
    kdf = CountingKdf()
    service, mailer, issuer, clock, _ = harness(
        tmp_path, password_kdf=kdf, max_logins_per_email=2, max_logins_per_caller=3
    )
    request_code(service, "reviewer@example.com")
    _, code = mailer.calls[-1]
    verify(service, "reviewer@example.com", code, new_password=PASSWORD)
    for caller in ("198.51.100.1", "198.51.100.2"):
        refused_login(service, "reviewer@example.com", "not-the-password", caller=caller)
    kdf_spent = len(kdf.salts)
    # Per-email budget exhausted: even the correct password is refused, and the
    # over-budget refusal spends no KDF work (it is not a guessing oracle either).
    assert refused_login(service, "reviewer@example.com", PASSWORD, caller="198.51.100.3") == (
        ServiceErrorCode.UNAUTHORIZED
    )
    assert len(kdf.salts) == kdf_spent
    # Per-caller budget is separate: three attempts from one caller, then refusal
    # without KDF work for a fourth, regardless of which email it names.
    for index in range(3):
        refused_login(service, f"other{index}@example.org", PASSWORD, caller="203.0.113.99")
    kdf_spent = len(kdf.salts)
    refused_login(service, "other3@example.org", PASSWORD, caller="203.0.113.99")
    assert len(kdf.salts) == kdf_spent
    # The window reopens with the clock; the correct password signs in again.
    clock.now = NOW + 601
    assert login(service, "reviewer@example.com", PASSWORD)["token"] == TOKEN
    assert issuer.calls.count("reviewer@example.com") == 2


def test_passwords_never_reach_storage_listings_or_log_records(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    service, mailer, _, _, store = harness(tmp_path)
    with caplog.at_level(logging.DEBUG):
        request_code(service, "reviewer@example.com")
        _, code = mailer.calls[-1]
        grant = verify(service, "reviewer@example.com", code, new_password=PASSWORD)
        login_grant = login(service, "reviewer@example.com", PASSWORD)
    assert PASSWORD not in str(grant) and PASSWORD not in str(login_grant)
    assert all(PASSWORD not in record.getMessage() for record in caplog.records)
    listing = [status.model_dump_json() for status in store.delivery_statuses()]
    assert listing and all(PASSWORD not in item for item in listing)
    connection = sqlite3.connect(tmp_path / "login.sqlite")
    try:
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        assert "auth_accounts" in tables and "auth_login_attempts" in tables
        for table in tables:
            for row in connection.execute(f"SELECT * FROM {table}").fetchall():
                flattened = "".join(str(value) for value in row)
                assert PASSWORD not in flattened and code not in flattened
    finally:
        connection.close()


# --- HTTP surface -------------------------------------------------------------


def test_route_module_mounts_exactly_the_three_public_login_endpoints() -> None:
    paths = {route.path for route in email_login_router.routes}
    assert paths == {"/v1/auth/request-code", "/v1/auth/verify", "/v1/auth/login"}
    # All three members feed the composed app's sanitized 422 branch in api/app.py.
    assert len(EMAIL_LOGIN_ENDPOINTS) == 3


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


def test_http_register_then_password_login_round_trip(tmp_path: Path) -> None:
    service, mailer, _, _, _ = harness(tmp_path)
    client = TestClient(build_app(service))
    client.post("/v1/auth/request-code", json={"email": "reviewer@example.com"})
    _, code = mailer.calls[-1]
    registered = client.post(
        "/v1/auth/verify",
        json={"email": "reviewer@example.com", "code": code, "new_password": PASSWORD},
    )
    assert registered.status_code == 200
    assert registered.json()["password_set"] is True
    assert PASSWORD not in registered.text
    signed_in = client.post(
        "/v1/auth/login", json={"email": "reviewer@example.com", "password": PASSWORD}
    )
    assert signed_in.status_code == 200
    assert signed_in.json()["token"] == TOKEN
    assert signed_in.json()["password_set"] is False
    assert PASSWORD not in signed_in.text


def test_http_login_refusals_are_identical_for_wrong_password_and_unknown_email(
    tmp_path: Path,
) -> None:
    service, mailer, _, _, _ = harness(tmp_path)
    client = TestClient(build_app(service))
    client.post("/v1/auth/request-code", json={"email": "reviewer@example.com"})
    _, code = mailer.calls[-1]
    client.post(
        "/v1/auth/verify",
        json={"email": "reviewer@example.com", "code": code, "new_password": PASSWORD},
    )
    wrong = client.post(
        "/v1/auth/login", json={"email": "reviewer@example.com", "password": "not-the-password"}
    )
    unknown = client.post(
        "/v1/auth/login", json={"email": "nobody@example.org", "password": "not-the-password"}
    )
    assert wrong.status_code == unknown.status_code == 403
    assert wrong.json() == unknown.json()
    assert wrong.json()["code"] == "unauthorized"


def test_http_short_password_is_a_sanitized_422_that_never_echoes_it(tmp_path: Path) -> None:
    """Both password-carrying bodies refuse short secrets with the fixed envelope."""
    service, mailer, _, _, _ = harness(tmp_path)
    client = TestClient(build_app(service))
    client.post("/v1/auth/request-code", json={"email": "reviewer@example.com"})
    _, code = mailer.calls[-1]
    short = "2short!"
    rejected_verify = client.post(
        "/v1/auth/verify",
        json={"email": "reviewer@example.com", "code": code, "new_password": short},
    )
    rejected_login = client.post(
        "/v1/auth/login", json={"email": "reviewer@example.com", "password": short}
    )
    for rejected in (rejected_verify, rejected_login):
        assert rejected.status_code == 422
        assert rejected.json()["code"] == "invalid_request"
        assert short not in rejected.text and "new_password" not in rejected.text
    # The refused registration spent no code: the still-valid code plus a real
    # password completes afterwards.
    completed = client.post(
        "/v1/auth/verify",
        json={"email": "reviewer@example.com", "code": code, "new_password": PASSWORD},
    )
    assert completed.status_code == 200 and completed.json()["password_set"] is True


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
