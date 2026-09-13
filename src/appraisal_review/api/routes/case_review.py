"""Transport for the start-a-review read: what a case member may submit.

Rules live in the service; an unwired plane answers capability_unavailable. The
submission itself goes to the existing review-jobs route, which re-checks
everything - this read is a description, never an authorization.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from appraisal_review.api.dependencies import get_principal
from appraisal_review.application.case_review import (
    CaseReviewBasis,
    CaseReviewService,
    ReviewableCaseList,
)
from appraisal_review.application.service_guards import Principal, ServiceFault
from appraisal_review.domain.service_contracts import ServiceErrorCode, ServiceProblem

router = APIRouter(tags=["case-review"])

REVIEW_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: {"model": ServiceProblem, "description": "Unauthenticated or not a case member"},
    404: {"model": ServiceProblem, "description": "No such case for this principal"},
    422: {"model": ServiceProblem, "description": "Invalid case identifier"},
    503: {"model": ServiceProblem, "description": "Case review lookup is not configured"},
}


def get_case_review(request: Request) -> CaseReviewService:
    service: CaseReviewService | None = getattr(request.app.state, "case_review", None)
    if service is None:
        raise ServiceFault(ServiceErrorCode.CAPABILITY)
    return service


ServiceDependency = Annotated[CaseReviewService, Depends(get_case_review)]
PrincipalDependency = Annotated[Principal, Depends(get_principal)]


@router.get(
    "/v1/review-cases",
    response_model=ReviewableCaseList,
    responses=REVIEW_RESPONSES,
)
async def list_reviewable_cases(
    principal: PrincipalDependency,
    service: ServiceDependency,
) -> ReviewableCaseList:
    """Cases this principal is a member of that have admitted material to review."""
    return await service.reviewable(principal)


@router.get(
    "/v1/cases/{case_id}/review-basis",
    response_model=CaseReviewBasis,
    responses=REVIEW_RESPONSES,
)
async def read_review_basis(
    case_id: UUID,
    principal: PrincipalDependency,
    service: ServiceDependency,
) -> CaseReviewBasis:
    """The exact revision and documents a review submission for this case must pin."""
    return await service.basis(principal, str(case_id))


CASE_REVIEW_ENDPOINTS = frozenset({list_reviewable_cases, read_review_basis})
