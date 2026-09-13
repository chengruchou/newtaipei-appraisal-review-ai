"""Transport for email login: request a code, verify it, or sign in by password.

Rules live in the service. This module mounts EXACTLY three endpoints, all
unauthenticated by design (they exist to create authentication):

- ``POST /v1/auth/request-code`` answers 202 with one fixed body for every
  outcome - known, unknown, rate-limited and undeliverable addresses look the
  same, so the route is not an account-existence oracle.
- ``POST /v1/auth/verify`` exchanges a still-valid code for a session exactly
  once; every refusal is the same generic 403. An optional ``new_password``
  (8..128 characters) additionally registers or resets the password account for
  the proven mailbox in the same call - that is both first-time registration
  and forgot-password.
- ``POST /v1/auth/login`` exchanges email + password for a session; wrong
  password, unknown email and an over-budget caller all answer the same
  generic 403. The integrator must exempt this path in the composed app's
  authentication middleware exactly like request-code and verify.

No response or validation error ever echoes a password: the success body is
the plain session grant, refusals are the fixed generic envelope, and the
composed app's sanitized 422 branch (keyed on ``EMAIL_LOGIN_ENDPOINTS``)
swallows rejected payloads for all three routes.

Deliberately NOT here: ``GET /v1/session`` and ``DELETE /v1/session`` already
exist on the composed app - logout of an email-login session is the existing
``DELETE /v1/session`` (revokes the presented bearer token). The operator-facing
delivery-status listing is a service method the integrator mounts behind an
authenticated operator surface; it must never join these public routes.

The dependency getter follows the plane pattern: a composition that wires no
``app.state.email_login`` answers capability_unavailable. The integrator adds
``EMAIL_LOGIN_ENDPOINTS`` to the sanitized RequestValidationError branch in
api/app.py so a rejected payload is never echoed back.

The per-caller rate-limit key is ``request.client.host``. Behind a reverse proxy
that is the proxy's address unless the deployment terminates and rewrites the
client address; the integrator owns that proxy configuration.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from appraisal_review.application.email_login import (
    EmailLoginService,
    PasswordLoginCommand,
    RequestCodeAccepted,
    RequestCodeCommand,
    SessionGrant,
    VerifyCodeCommand,
)
from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.domain.service_contracts import ServiceErrorCode, ServiceProblem

router = APIRouter(prefix="/v1/auth", tags=["email-login"])

EMAIL_LOGIN_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: {
        "model": ServiceProblem,
        "description": "Code or password invalid, expired, consumed, over budget or unknown",
    },
    422: {"model": ServiceProblem, "description": "Invalid email, code or password format"},
    503: {"model": ServiceProblem, "description": "Email login is not configured"},
}


def get_email_login(request: Request) -> EmailLoginService:
    """An unwired login plane reports capability_unavailable, never a fake accept."""
    service: EmailLoginService | None = getattr(request.app.state, "email_login", None)
    if service is None:
        raise ServiceFault(ServiceErrorCode.CAPABILITY)
    return service


ServiceDependency = Annotated[EmailLoginService, Depends(get_email_login)]


def _caller(request: Request) -> str:
    client = request.client
    return client.host if client is not None and client.host else "unknown"


@router.post(
    "/request-code",
    status_code=202,
    response_model=RequestCodeAccepted,
    responses={
        202: {
            "model": RequestCodeAccepted,
            "description": "The one fixed body; no outcome is distinguishable",
        },
        **EMAIL_LOGIN_RESPONSES,
    },
)
async def request_code(
    command: RequestCodeCommand,
    request: Request,
    service: ServiceDependency,
) -> RequestCodeAccepted:
    """Request a sign-in code by email; the response never says whether one went out."""
    return await service.request_code(command, caller=_caller(request))


@router.post(
    "/verify",
    status_code=200,
    response_model=SessionGrant,
    responses={
        200: {"model": SessionGrant, "description": "Session issued; the code is now consumed"},
        **EMAIL_LOGIN_RESPONSES,
    },
)
async def verify_code(
    command: VerifyCodeCommand,
    service: ServiceDependency,
) -> SessionGrant:
    """Exchange a still-valid code for a session token, exactly once per code.

    With ``new_password`` the call also registers (or resets) the password
    account for the proven mailbox before the session is issued.
    """
    return await service.verify_code(command)


@router.post(
    "/login",
    status_code=200,
    response_model=SessionGrant,
    responses={
        200: {"model": SessionGrant, "description": "Session issued for email and password"},
        **EMAIL_LOGIN_RESPONSES,
    },
)
async def login(
    command: PasswordLoginCommand,
    request: Request,
    service: ServiceDependency,
) -> SessionGrant:
    """Sign in with email and password; every refusal is the same generic 403."""
    return await service.login(command, caller=_caller(request))


EMAIL_LOGIN_ENDPOINTS = frozenset({request_code, verify_code, login})
