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
When the verify body also carries ``new_password`` (8..128 characters, no other
composition rules), the freshly proven mailbox gets a password account in the
same flow: the password is scrypt-hashed (n=2**14, r=8, p=1, 32-byte random
salt) and upserted BEFORE the session is issued, so registration and the first
sign-in are one step. Forgot-password is the identical flow - a new code plus a
new password replaces the old hash atomically. Verify without ``new_password``
keeps the original code-only sign-in behavior.

``POST /v1/auth/login`` signs an existing account in with email + password.
Refusals are the same generic 403 as a bad code, an unknown email burns the same
scrypt work against a fixed fake salt so timing does not reveal account
existence, and attempts are rate-limited per email and per caller like
request-code. Passwords are never logged, never stored in plain form and never
echoed by any response or validation error.

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
_PASSWORD_MIN_LENGTH = 8
_PASSWORD_MAX_LENGTH = 128
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_SALT_BYTES = 32
_SCRYPT_DKLEN = 32
#: Fixed fake salt for the unknown-email login path: the same KDF cost is burned
#: against it so a login refusal takes the same time whether the account exists.
_TIMING_EQUALIZER_SALT = bytes(_SCRYPT_SALT_BYTES)
#: Impossible stored hash the burned digest is compared against (still in
#: constant time); "f"*64 keeps the refusal path shaped like the real one.
_TIMING_EQUALIZER_HASH = "f" * (_SCRYPT_DKLEN * 2)


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
    """Untrusted body; a malformed code or password is refused before any read.

    ``new_password`` is optional: absent keeps the original code-only sign-in;
    present (8..128 characters, no other composition rules) registers or resets
    the password account for the freshly proven mailbox in the same flow. The
    composed app's sanitized 422 branch guarantees a rejected body - password
    included - is never echoed back.
    """

    email: str = Field(min_length=3, max_length=_EMAIL_MAX_LENGTH)
    code: str = Field(pattern=r"^[0-9]{6,8}$")
    new_password: str | None = Field(
        default=None, min_length=_PASSWORD_MIN_LENGTH, max_length=_PASSWORD_MAX_LENGTH
    )

    normalized = field_validator("email")(_normalized_email)


class PasswordLoginCommand(ServiceModel):
    """Untrusted body for email+password sign-in; length gates only, no oracle."""

    email: str = Field(min_length=3, max_length=_EMAIL_MAX_LENGTH)
    password: str = Field(min_length=_PASSWORD_MIN_LENGTH, max_length=_PASSWORD_MAX_LENGTH)

    normalized = field_validator("email")(_normalized_email)


class SessionGrant(ServiceModel):
    """The one success response of verify and login.

    ``password_set`` is True only when this grant also registered or reset a
    password (verify with ``new_password``); it never restates stored state.
    """

    token: str = Field(min_length=32)
    expires_at: int = Field(ge=1, strict=True)
    actor_id: str = Field(min_length=1)
    password_set: bool = False


class PasswordAccountRecord(ServiceModel):
    """Server-side password account; only the scrypt salt and hash are stored."""

    email: str = Field(min_length=3, max_length=_EMAIL_MAX_LENGTH)
    password_scrypt_salt: str = Field(pattern=r"^[0-9a-f]{64}$")
    password_scrypt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: int = Field(ge=0, strict=True)
    updated_at: int = Field(ge=0, strict=True)
    password_set_count: int = Field(ge=1, strict=True)

    normalized = field_validator("email")(_normalized_email)


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

    Password accounts: ``read_account`` returns the stored scrypt material for a
    normalized email or ``None``; ``upsert_password`` creates or replaces it in
    one atomic write (register and reset are the same operation);
    ``reserve_login_attempt`` mirrors ``reserve_request`` for the login plane so
    password guessing is budgeted separately from code requests.
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

    def read_account(self, email: str) -> PasswordAccountRecord | None: ...

    def upsert_password(self, *, email: str, salt_hex: str, hash_hex: str, now: int) -> None: ...

    def reserve_login_attempt(
        self,
        *,
        email: str,
        caller: str,
        now: int,
        window_start: int,
        max_per_email: int,
        max_per_caller: int,
    ) -> bool: ...


def _default_mint_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _code_hash(salt: str, code: str) -> str:
    return hashlib.sha256(f"{salt}:{code}".encode()).hexdigest()


def _scrypt_password_hash(password: bytes, salt: bytes) -> bytes:
    """Interactive-login scrypt (16 MiB, ~tens of ms); the seam tests inject."""
    return hashlib.scrypt(
        password,
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        maxmem=2**26,
        dklen=_SCRYPT_DKLEN,
    )


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
        max_logins_per_email: int = 5,
        max_logins_per_caller: int = 10,
        password_kdf: Callable[[bytes, bytes], bytes] = _scrypt_password_hash,
    ) -> None:
        for name, value in (
            ("code_ttl_seconds", code_ttl_seconds),
            ("max_attempts", max_attempts),
            ("rate_window_seconds", rate_window_seconds),
            ("max_requests_per_email", max_requests_per_email),
            ("max_requests_per_caller", max_requests_per_caller),
            ("max_logins_per_email", max_logins_per_email),
            ("max_logins_per_caller", max_logins_per_caller),
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
        self.max_logins_per_email = max_logins_per_email
        self.max_logins_per_caller = max_logins_per_caller
        # password_kdf(password_bytes, salt_bytes) -> derived bytes. The default is
        # scrypt; tests inject a counting seam. Never call it with a plain string.
        self.password_kdf = password_kdf

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
        """One generic 403 for every refusal; success is single-use by construction.

        With ``new_password`` the proven mailbox's password account is created or
        replaced BEFORE the session is issued - registration, first sign-in and
        forgot-password are all this one flow. Without it, behavior is unchanged.
        """
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
        if command.new_password is not None:
            salt = secrets.token_bytes(_SCRYPT_SALT_BYTES)
            derived = self.password_kdf(command.new_password.encode(), salt)
            self.store.upsert_password(
                email=command.email, salt_hex=salt.hex(), hash_hex=derived.hex(), now=now
            )
        token, expires_at, actor_id = self.issue_session(command.email)
        return SessionGrant(
            token=token,
            expires_at=expires_at,
            actor_id=actor_id,
            password_set=command.new_password is not None,
        )

    async def login(self, command: PasswordLoginCommand, *, caller: str) -> SessionGrant:
        """Email+password sign-in: one generic refusal, no timing or rate oracle.

        Every attempt (known or unknown email, right or wrong password) spends
        one unit of the per-email and per-caller login budget first. An unknown
        email then burns the same KDF cost against a fixed fake salt as a wrong
        password does against the real one, so neither timing nor the refusal
        body reveals whether an account exists. The password itself is never
        logged, stored or echoed.
        """
        command = PasswordLoginCommand.model_validate_json(command.model_dump_json())
        now = self.clock()
        allowed = self.store.reserve_login_attempt(
            email=command.email,
            caller=caller if caller else "unknown",
            now=now,
            window_start=now - self.rate_window_seconds,
            max_per_email=self.max_logins_per_email,
            max_per_caller=self.max_logins_per_caller,
        )
        if not allowed:
            # Over-budget refusals reuse the one generic 403: a distinct status
            # would let a caller probe which addresses attract sign-in traffic.
            raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
        account = self.store.read_account(command.email)
        if account is None:
            self._password_matches(
                command.password, _TIMING_EQUALIZER_SALT.hex(), _TIMING_EQUALIZER_HASH
            )
            raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
        if not self._password_matches(
            command.password, account.password_scrypt_salt, account.password_scrypt_hash
        ):
            raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
        token, expires_at, actor_id = self.issue_session(command.email)
        return SessionGrant(token=token, expires_at=expires_at, actor_id=actor_id)

    def _password_matches(self, password: str, salt_hex: str, hash_hex: str) -> bool:
        derived = self.password_kdf(password.encode(), bytes.fromhex(salt_hex))
        return hmac.compare_digest(derived.hex(), hash_hex)

    def delivery_statuses(self, *, limit: int = 50) -> tuple[LoginDeliveryStatus, ...]:
        """OPERATOR-facing only: the integrator must mount this behind an
        authenticated operator surface, never on the public login routes."""
        if type(limit) is not int or limit < 1:
            raise ServiceFault(ServiceErrorCode.VALIDATION)
        return self.store.delivery_statuses(limit=limit)
