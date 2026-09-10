"""Transport only for durable review jobs. No route here executes or inspects a review.

Submission returns as soon as the work is durably owned, so a long review can never be
run inside a request. Retry and dead-letter redrive are deliberately absent: they are
operator actions bound to a runbook, and an HTTP endpoint able to reschedule arbitrary
jobs would be exactly the unauthenticated general-purpose admin surface #29 forbids.
Cancel is offered because it is bound to one principal and one case.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Response

from appraisal_review.api.dependencies import get_job_service, get_principal
from appraisal_review.application.review_jobs import ReviewJobService
from appraisal_review.application.service_guards import Principal
from appraisal_review.domain.job_contracts import JobAcceptance, JobStatusView
from appraisal_review.domain.service_contracts import (
    ReviewSubmission,
    ServiceProblem,
    ServiceResult,
)

router = APIRouter(prefix="/v1/review-jobs", tags=["review-jobs"])

PROBLEM_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: {"model": ServiceProblem, "description": "The principal lacks the case permission"},
    404: {"model": ServiceProblem, "description": "No such job for this principal"},
    409: {"model": ServiceProblem, "description": "The job is not in a compatible state"},
    422: {"model": ServiceProblem, "description": "Invalid submission"},
    503: {"model": ServiceProblem, "description": "No durable job store is configured"},
}

ServiceDependency = Annotated[ReviewJobService, Depends(get_job_service)]
PrincipalDependency = Annotated[Principal, Depends(get_principal)]


@router.post(
    "",
    status_code=202,
    response_model=None,
    responses={
        202: {"model": JobAcceptance, "description": "Durable responsibility accepted"},
        200: {"model": JobStatusView, "description": "Exact replay of a known submission"},
        **PROBLEM_RESPONSES,
    },
)
async def submit_review_job(
    submission: ReviewSubmission,
    response: Response,
    service: ServiceDependency,
    principal: PrincipalDependency,
) -> JobAcceptance | JobStatusView:
    """202 means the job and its outbox entry are persisted, never that a review ran.

    An exact replay returns 200 with the job's real status, because reporting the pinned
    acceptance for a job that has already moved on would be a fabricated response.
    """
    outcome = await service.submit(principal, submission)
    if outcome.acceptance is None:
        response.status_code = 200
        return outcome.status
    return outcome.acceptance


@router.get("/{job_id}", response_model=JobStatusView, responses=PROBLEM_RESPONSES)
async def read_review_job(
    job_id: UUID, service: ServiceDependency, principal: PrincipalDependency
) -> JobStatusView:
    return await service.status(principal, job_id)


@router.get("/{job_id}/result", response_model=ServiceResult, responses=PROBLEM_RESPONSES)
async def read_review_job_result(
    job_id: UUID, service: ServiceDependency, principal: PrincipalDependency
) -> ServiceResult:
    """409 until a result is committed; a terminal failure is reported by the status route."""
    return await service.result(principal, job_id)


@router.post(
    "/{job_id}/cancel",
    status_code=202,
    response_model=JobStatusView,
    responses=PROBLEM_RESPONSES,
)
async def cancel_review_job(
    job_id: UUID, service: ServiceDependency, principal: PrincipalDependency
) -> JobStatusView:
    """A running attempt is asked to stop cooperatively, never killed mid-write."""
    return await service.cancel(principal, job_id)


JOB_ENDPOINTS = frozenset(
    {submit_review_job, read_review_job, read_review_job_result, cancel_review_job}
)
