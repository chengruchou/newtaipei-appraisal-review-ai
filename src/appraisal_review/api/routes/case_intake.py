"""Transport for case intake: create a case, upload its materials, list them.

Rules live in the service. The dependency getter lives here (not in
api/dependencies.py, which another change owns right now) and follows the same
contract: a composition that wires no intake plane answers capability_unavailable.
The integrator wires ``app.state.case_intake`` with a configured
``CaseIntakeService`` and, in api/app.py, adds ``CASE_INTAKE_ENDPOINTS`` to the
sanitized RequestValidationError branch so a rejected payload is never echoed.

Uploads are a raw request body, not multipart: python-multipart is not a project
dependency, and the content routes already treat document bytes as an opaque body
in the other direction. Filename and idempotency key travel in headers
(X-Upload-Filename, X-Idempotency-Key); the media type is the request's own
Content-Type.
"""

from typing import Annotated, Any
from urllib.parse import unquote
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request

from appraisal_review.api.dependencies import get_principal
from appraisal_review.application.case_intake import (
    CaseIntakeService,
    CaseMaterialList,
    CaseRecord,
    CreateCaseCommand,
    MaterialRecord,
)
from appraisal_review.application.service_guards import Principal, ServiceFault
from appraisal_review.domain.service_contracts import ServiceErrorCode, ServiceProblem

router = APIRouter(prefix="/v1/cases", tags=["case-intake"])

INTAKE_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: {"model": ServiceProblem, "description": "Unauthenticated or not a case member"},
    404: {"model": ServiceProblem, "description": "No such intake case"},
    409: {"model": ServiceProblem, "description": "The idempotency key names a different payload"},
    422: {"model": ServiceProblem, "description": "Invalid body, header metadata or size"},
    503: {"model": ServiceProblem, "description": "Case intake is not configured"},
}


def get_case_intake(request: Request) -> CaseIntakeService:
    """An unwired intake plane reports capability_unavailable, never a fake case."""
    service: CaseIntakeService | None = getattr(request.app.state, "case_intake", None)
    if service is None:
        raise ServiceFault(ServiceErrorCode.CAPABILITY)
    return service


ServiceDependency = Annotated[CaseIntakeService, Depends(get_case_intake)]
PrincipalDependency = Annotated[Principal, Depends(get_principal)]


@router.post(
    "",
    status_code=201,
    response_model=CaseRecord,
    responses={
        201: {"model": CaseRecord, "description": "The case record, new or exact replay"},
        **INTAKE_RESPONSES,
    },
)
async def create_case(
    command: CreateCaseCommand,
    principal: PrincipalDependency,
    service: ServiceDependency,
) -> CaseRecord:
    """Create a real case; the caller becomes its first and only member."""
    return await service.create_case(principal, command)


@router.post(
    "/{case_id}/materials",
    status_code=201,
    response_model=MaterialRecord,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
        }
    },
    responses={
        201: {
            "model": MaterialRecord,
            "description": "Stored material with the server-computed sha256, new or exact replay",
        },
        **INTAKE_RESPONSES,
    },
)
async def upload_material(
    case_id: UUID,
    request: Request,
    principal: PrincipalDependency,
    service: ServiceDependency,
    x_upload_filename: Annotated[str, Header(min_length=1, max_length=255)],
    x_idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
    content_type: Annotated[str, Header(min_length=3, max_length=255)] = "application/octet-stream",
) -> MaterialRecord:
    """Store one raw-body upload durably; the response sha256 verifies the bytes."""
    data = await request.body()
    # Browsers cannot put non-ISO-8859-1 header values on the wire, so the client
    # percent-encodes Chinese filenames; decode here so the stored record carries
    # the real name. A plain ASCII name passes through unchanged.
    filename = unquote(x_upload_filename)
    return await service.add_material(
        principal,
        str(case_id),
        filename=filename,
        media_type=content_type,
        data=data,
        idempotency_key=x_idempotency_key,
    )


@router.get(
    "/{case_id}/materials",
    response_model=CaseMaterialList,
    responses=INTAKE_RESPONSES,
)
async def list_materials(
    case_id: UUID,
    principal: PrincipalDependency,
    service: ServiceDependency,
) -> CaseMaterialList:
    """List material records - metadata only, never bytes."""
    return await service.list_materials(principal, str(case_id))


CASE_INTAKE_ENDPOINTS = frozenset({create_case, upload_material, list_materials})
