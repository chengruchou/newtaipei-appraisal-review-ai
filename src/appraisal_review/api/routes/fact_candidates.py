"""Transport for fact candidates: register, list, and human-confirm them.

Rules live in the service. The dependency getter lives here and follows the
case-intake contract: a composition that wires no candidate plane answers
capability_unavailable. The integrator wires ``app.state.fact_candidates`` with
a configured ``CandidateService`` and, in api/app.py, adds
``FACT_CANDIDATE_ENDPOINTS`` to the sanitized RequestValidationError branch so
a rejected payload is never echoed.

Confirmation is the trust boundary: the route merely transports the command;
the service refuses non-human principals and any echo that does not match the
stored candidate exactly, and the store commits the status flip with its
receipt in one transaction.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from appraisal_review.api.dependencies import get_principal
from appraisal_review.application.fact_candidates import (
    CandidateConfirmation,
    CandidateList,
    CandidateService,
    ConfirmCandidateCommand,
    FactCandidate,
    RegisterCandidateCommand,
)
from appraisal_review.application.service_guards import Principal, ServiceFault
from appraisal_review.domain.service_contracts import ServiceErrorCode, ServiceProblem

router = APIRouter(prefix="/v1/cases", tags=["fact-candidates"])

CANDIDATE_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: {"model": ServiceProblem, "description": "Unauthenticated or not authorized"},
    404: {"model": ServiceProblem, "description": "No such case or candidate"},
    409: {"model": ServiceProblem, "description": "Revision, echo or idempotency conflict"},
    422: {"model": ServiceProblem, "description": "Invalid body"},
    503: {"model": ServiceProblem, "description": "Fact candidates are not configured"},
}


def get_fact_candidates(request: Request) -> CandidateService:
    """An unwired candidate plane reports capability_unavailable, never fake data."""
    service: CandidateService | None = getattr(request.app.state, "fact_candidates", None)
    if service is None:
        raise ServiceFault(ServiceErrorCode.CAPABILITY)
    return service


ServiceDependency = Annotated[CandidateService, Depends(get_fact_candidates)]
PrincipalDependency = Annotated[Principal, Depends(get_principal)]


@router.post(
    "/{case_id}/candidates",
    status_code=201,
    response_model=FactCandidate,
    responses={
        201: {"model": FactCandidate, "description": "The stored candidate, new or exact replay"},
        **CANDIDATE_RESPONSES,
    },
)
async def register_candidate(
    case_id: UUID,
    command: RegisterCandidateCommand,
    principal: PrincipalDependency,
    service: ServiceDependency,
) -> FactCandidate:
    """Record one fetched value as a CANDIDATE only; nothing is adopted here."""
    return await service.register_candidate(principal, str(case_id), command)


@router.get(
    "/{case_id}/candidates",
    response_model=CandidateList,
    responses=CANDIDATE_RESPONSES,
)
async def list_candidates(
    case_id: UUID,
    principal: PrincipalDependency,
    service: ServiceDependency,
) -> CandidateList:
    """List every candidate for the case, whatever its status."""
    return await service.list_candidates(principal, str(case_id))


@router.get(
    "/{case_id}/candidates/confirmed-unadopted",
    response_model=CandidateList,
    responses=CANDIDATE_RESPONSES,
)
async def list_confirmed_unadopted(
    case_id: UUID,
    principal: PrincipalDependency,
    service: ServiceDependency,
) -> CandidateList:
    """Confirmed values still awaiting adoption into a snapshot or table."""
    return await service.confirmed_unadopted(principal, str(case_id))


@router.post(
    "/{case_id}/candidates/{candidate_id}/confirm",
    status_code=201,
    response_model=CandidateConfirmation,
    responses={
        201: {
            "model": CandidateConfirmation,
            "description": "The decision receipt, new or exact replay",
        },
        **CANDIDATE_RESPONSES,
    },
)
async def confirm_candidate(
    case_id: UUID,
    candidate_id: UUID,
    command: ConfirmCandidateCommand,
    principal: PrincipalDependency,
    service: ServiceDependency,
) -> CandidateConfirmation:
    """Accept or reject one exact candidate value as a named human."""
    return await service.confirm(principal, str(case_id), str(candidate_id), command)


FACT_CANDIDATE_ENDPOINTS = frozenset(
    {register_candidate, list_candidates, list_confirmed_unadopted, confirm_candidate}
)
