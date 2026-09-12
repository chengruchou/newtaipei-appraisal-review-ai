"""Email one-time-code login: prove mailbox control, then let the directory decide.

The smallest honest login path. ``POST /v1/auth/request-code`` always answers the
same accepted body - known, unknown, rate-limited and undeliverable addresses are
indistinguishable to the requester, so the endpoint is not an account-existence
oracle. Internally it rate-limits per email and per caller, mints a short numeric
code, stores only a salted hash, and hands the plain code to an injected
:class:`MailerPort` exactly once. A send failure (SES sandbox restriction,
throttle) marks the record undeliverable for an OPERATOR-facing status listing;
it is never surfaced to the requester. The code is never logged and never
returned by any API.

``POST /v1/auth/verify`` compares in constant time against the stored hash of the
LATEST code for that email. A wrong code spends one of five attempts; an expired,
consumed or exhausted record answers the same generic 403 as an unknown email.
Success consumes the record atomically (single use - a replay of the same code is
refused) and issues a session through the injected ``issue_session`` callable.

Authority boundary (explicit by design): email verification proves mailbox
control ONLY. This service grants nothing - the issued principal's shape, case
memberships and permissions are decided entirely by the injected
``issue_session``; the composition root is expected to mint a principal with NO
case memberships and NO elevated permissions by default.

Logout: the composed app already serves ``GET /v1/session`` and
``DELETE /v1/session`` (revocation of the presented bearer token through
``LocalDirectory.revoke_token``). This plane deliberately mounts no session
routes of its own - logging out of an email-login session uses the existing
``DELETE /v1/session`` endpoint.

Integration contract (the composition root wires all of it):
- ``store``: any :class:`EmailLoginStore`;
  ``appraisal_review.adapters.local.email_login_store.SQLiteEmailLoginStore`` is
  the provided SQLite implementation (it takes the shared ReviewDatabase).
- ``mailer``: any :class:`MailerPort`;
  ``appraisal_review.adapters.aws.ses_mailer.SesMailer`` is the SES v2 adapter.
- ``issue_session``: called as ``issue_session(email)`` after a code verifies;
  returns ``(token, expires_at, actor_id)``. Over ``LocalDirectory`` this means:
  mint a token of at least 32 characters, build a ``Principal`` with an empty
  ``case_ids`` set, call ``add_session(token, principal, expires_at)`` and
  return the triple. Revocation stays with the directory.
- ``app.state.email_login`` carries the configured service; the router lives in
  ``appraisal_review.api.routes.email_login``.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections.abc import Callable
from typing import Literal, Protocol
from uuid import uuid4

from pydantic import Field, field_validator

from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.domain.document_models import Digest
from appraisal_review.domain.service_contracts import (
    OpaqueID,
    ServiceErrorCode,
    ServiceModel,
)

#: Sanitized delivery-failure reasons; raw provider messages never cross this boundary.
DELIVERY_FAILURE_REASONS = frozenset({"delivery_restricted", "throttled", "provider_unavailable"})

_EMAIL_MAX_LENGTH = 254


class MailDeliveryUnavailable(Exception):
    """Typed send failure with a sanitized reason code, safe for operator listings."""

    def __init__(self, reason: str) -> None:
        self.reason = reason if reason in DELIVERY_FAILURE_REASONS else "provider_unavailable"
        super().__init__(self.reason)


class MailerPort(Protocol):
    """Outbound login-code mail; implementations raise MailDeliveryUnavailable."""

    def send_login_code(self, email: str, code: str) -> None: ...


def _normalized_email(value: str) -> str:
    candidate = value.strip().lower()
    local, separator, domain = candidate.partition("@")
    if (
        not separator
        or not local
        or not domain
        or "@" in domain
        or "." not in domain
        or ".." in candidate
        or domain.startswith(".")
        or domain.endswith(".")
        or len(candidate) > _EMAIL_MAX_LENGTH
        or any(character.isspace() or ord(character) < 33 for character in candidate)
    ):
        raise ValueError("A plain mailbox address is required")
    return candidate


class RequestCodeCommand(ServiceModel):
    """Untrusted body; the address is normalized before any lookup or counter."""

    email: str = Field(min_length=3, max_length=_EMAIL_MAX_LENGTH)

    normalized = field_validator("email")(_normalized_email)


class RequestCodeAccepted(ServiceModel):
    """The one fixed 202 body: identical for every request-code outcome."""

    status: Literal["accepted"] = "accepted"
    detail: Literal["If the address can receive mail, a sign-in code is on its way."] = (
        "If the address can receive mail, a sign-in code is on its way."
    )


class VerifyCodeCommand(ServiceModel):
    """Untrusted body; a malformed code is refused before any record is read."""

    email: str = Field(min_length=3, max_length=_EMAIL_MAX_LENGTH)
    code: str = Field(pattern=r"^[0-9]{6,8}$")

    normalized = field_validator("email")(_normalized_email)


class SessionGrant(ServiceModel):
    """The one success response of verify; issued exactly once per code."""

    token: str = Field(min_length=32)
    expires_at: int = Field(ge=1, strict=True)
    actor_id: str = Field(min_length=1)


class LoginCodeRecord(ServiceModel):
    """Server-side record of one minted code; only the salted hash is stored."""

    record_id: OpaqueID
    email: str = Field(min_length=3, max_length=_EMAIL_MAX_LENGTH)
    code_hash: Digest
    salt: str = Field(pattern=r"^[0-9a-f]{16,64}$")
    purpose: Literal["login"] = "login"
    created_at: int = Field(ge=0, strict=True)
    expires_at: int = Field(ge=1, strict=True)
    attempts_left: int = Field(ge=0, strict=True)
    consumed: bool = False
    delivery: Literal["pending", "sent", "undeliverable"] = "pending"
    failure_reason: str | None = None

    normalized = field_validator("email")(_normalized_email)


class LoginDeliveryStatus(ServiceModel):
    """Operator-facing send outcome; carries no code material in any field."""

    record_id: OpaqueID
    email: str = Field(min_length=3, max_length=_EMAIL_MAX_LENGTH)
    delivery: Literal["pending", "sent", "undeliverable"]
    failure_reason: str | None = None
    created_at: int = Field(ge=0, strict=True)
    expires_at: int = Field(ge=1, strict=True)
    attempts_left: int = Field(ge=0, strict=True)
    consumed: bool = False


class EmailLoginStore(Protocol):
    """Durable codes, rate-limit counters and a send audit without code material.

    ``reserve_request`` counts and records atomically: it returns ``False`` when
    the email or the caller is over its window budget, without recording another
    attempt. ``consume`` is a conditional single-use commit - it returns ``True``
    for exactly one caller of an unconsumed, unexpired record with attempts left.
    ``spend_attempt`` decrements only while attempts remain.
    """

    def reserve_request(
        self,
        *,
        email: str,
        caller: str,
        now: int,
        window_start: int,
        max_per_email: int,
        max_per_caller: int,
    ) -> bool: ...

    def create_code(self, record: LoginCodeRecord) -> None: ...

    def latest_code(self, email: str) -> LoginCodeRecord | None: ...

    def spend_attempt(self, record_id: str) -> None: ...

    def consume(self, record_id: str, *, now: int) -> bool: ...

    def mark_delivery(
        self,
        record_id: str,
        delivery: Literal["sent", "undeliverable"],
        *,
        at: int,
        reason: str | None = None,
    ) -> None: ...

    def delivery_statuses(self, *, limit: int = 50) -> tuple[LoginDeliveryStatus, ...]: ...


def _default_mint_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _code_hash(salt: str, code: str) -> str:
    return hashlib.sha256(f"{salt}:{code}".encode()).hexdigest()


class EmailLoginService:
    def __init__(
        self,
        *,
        store: EmailLoginStore,
        mailer: MailerPort,
        issue_session: Callable[[str], tuple[str, int, str]],
        clock: Callable[[], int] = lambda: int(time.time()),
        new_id: Callable[[], str] = lambda: str(uuid4()),
        mint_code: Callable[[], str] = _default_mint_code,
        code_ttl_seconds: int = 600,
        max_attempts: int = 5,
        rate_window_seconds: int = 600,
        max_requests_per_email: int = 3,
        max_requests_per_caller: int = 3,
    ) -> None:
        for name, value in (
            ("code_ttl_seconds", code_ttl_seconds),
            ("max_attempts", max_attempts),
            ("rate_window_seconds", rate_window_seconds),
            ("max_requests_per_email", max_requests_per_email),
            ("max_requests_per_caller", max_requests_per_caller),
        ):
            if type(value) is not int or value < 1:
                raise ValueError(f"A positive {name} is required")
        self.store = store
        self.mailer = mailer
        # issue_session(email) -> (token, expires_at, actor_id). The callable owns
        # the principal's shape; this service never grants memberships or permissions.
        self.issue_session = issue_session
        self.clock = clock
        self.new_id = new_id
        self.mint_code = mint_code
        self.code_ttl_seconds = code_ttl_seconds
        self.max_attempts = max_attempts
        self.rate_window_seconds = rate_window_seconds
        self.max_requests_per_email = max_requests_per_email
        self.max_requests_per_caller = max_requests_per_caller

    async def request_code(
        self, command: RequestCodeCommand, *, caller: str
    ) -> RequestCodeAccepted:
        """Always the same accepted body; every internal outcome stays internal."""
        command = RequestCodeCommand.model_validate_json(command.model_dump_json())
        now = self.clock()
        allowed = self.store.reserve_request(
            email=command.email,
            caller=caller if caller else "unknown",
            now=now,
            window_start=now - self.rate_window_seconds,
            max_per_email=self.max_requests_per_email,
            max_per_caller=self.max_requests_per_caller,
        )
        if not allowed:
            # A rate-limited request mints and sends nothing, but the response is
            # byte-identical: refusal here would leak request activity per address.
            return RequestCodeAccepted()
        code = self.mint_code()
        if not code.isascii() or not code.isdigit() or not 6 <= len(code) <= 8:
            raise ServiceFault(ServiceErrorCode.EXECUTION)
        salt = secrets.token_hex(16)
        record = LoginCodeRecord(
            record_id=self.new_id(),
            email=command.email,
            code_hash=_code_hash(salt, code),
            salt=salt,
            created_at=now,
            expires_at=now + self.code_ttl_seconds,
            attempts_left=self.max_attempts,
        )
        self.store.create_code(record)
        try:
            self.mailer.send_login_code(command.email, code)
        except MailDeliveryUnavailable as failure:
            # Visible to operators through delivery_statuses, never to the requester.
            self.store.mark_delivery(
                record.record_id, "undeliverable", at=self.clock(), reason=failure.reason
            )
        else:
            self.store.mark_delivery(record.record_id, "sent", at=self.clock())
        return RequestCodeAccepted()

    async def verify_code(self, command: VerifyCodeCommand) -> SessionGrant:
        """One generic 403 for every refusal; success is single-use by construction."""
        command = VerifyCodeCommand.model_validate_json(command.model_dump_json())
        now = self.clock()
        record = self.store.latest_code(command.email)
        if (
            record is None
            or record.consumed
            or record.attempts_left < 1
            or now >= record.expires_at
        ):
            raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
        presented = _code_hash(record.salt, command.code)
        if not hmac.compare_digest(presented, record.code_hash):
            self.store.spend_attempt(record.record_id)
            raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
        if not self.store.consume(record.record_id, now=now):
            # A concurrent verify or an intervening expiry won; never issue twice.
            raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
        token, expires_at, actor_id = self.issue_session(command.email)
        return SessionGrant(token=token, expires_at=expires_at, actor_id=actor_id)

    def delivery_statuses(self, *, limit: int = 50) -> tuple[LoginDeliveryStatus, ...]:
        """OPERATOR-facing only: the integrator must mount this behind an
        authenticated operator surface, never on the public login routes."""
        if type(limit) is not int or limit < 1:
            raise ServiceFault(ServiceErrorCode.VALIDATION)
        return self.store.delivery_statuses(limit=limit)
