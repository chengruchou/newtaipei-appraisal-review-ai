"""Transport for fact adoption: turn confirmed candidates into adopted facts.

Every rule lives in the service: the CORRECT permission, the named-human
requirement, the CAS on the pinned revision, batch atomicity and idempotent
replay. The route merely transports the command, and a composition that wires
no adoption plane answers capability_unavailable.

The integrator wires ``app.state.fact_adoption`` with a configured
``FactAdoptionService`` and, in api/app.py, includes this router and adds
``ADOPTION_ENDPOINTS`` to the sanitized RequestValidationError branch so a
rejected payload is never echoed.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from appraisal_review.api.dependencies import get_principal
from appraisal_review.application.fact_adoption import (
    AdoptFactsCommand,
    AdoptionResult,
    FactAdoptionService,
)
from appraisal_review.application.service_guards import Principal, ServiceFault
from appraisal_review.domain.service_contracts import ServiceErrorCode, ServiceProblem

router = APIRouter(prefix="/v1/review-jobs/{job_id}/adoptions", tags=["fact-adoption"])

ADOPTION_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: {"model": ServiceProblem, "description": "Missing case or correction permission"},
    404: {"model": ServiceProblem, "description": "No such job for this principal"},
    409: {
        "model": ServiceProblem,
        "description": "Stale pinned revision, busy job, no base snapshot, or a reused key",
    },
    422: {"model": ServiceProblem, "description": "Invalid body or a refused candidate"},
    503: {"model": ServiceProblem, "description": "Fact adoption is not configured"},
}


def get_fact_adoption(request: Request) -> FactAdoptionService:
    """An unwired adoption plane reports capability_unavailable, never fake adoption."""
    service: FactAdoptionService | None = getattr(request.app.state, "fact_adoption", None)
    if service is None:
        raise ServiceFault(ServiceErrorCode.CAPABILITY)
    return service


ServiceDependency = Annotated[FactAdoptionService, Depends(get_fact_adoption)]
PrincipalDependency = Annotated[Principal, Depends(get_principal)]


@router.post(
    "",
    status_code=201,
    response_model=AdoptionResult,
    responses={
        201: {
            "model": AdoptionResult,
            "description": "The atomic adoption record, new or exact replay",
        },
        **ADOPTION_RESPONSES,
    },
)
async def adopt_facts(
    job_id: UUID,
    command: AdoptFactsCommand,
    principal: PrincipalDependency,
    service: ServiceDependency,
) -> AdoptionResult:
    """Adopt a batch of confirmed candidates: new snapshot version, new case revision."""
    return await service.adopt(principal, job_id, command)


ADOPTION_ENDPOINTS = frozenset({adopt_facts})
