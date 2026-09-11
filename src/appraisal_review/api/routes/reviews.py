"""Transport only: the synchronous review endpoint does not calculate values."""

from typing import Annotated

from fastapi import APIRouter, Depends

from appraisal_review.api.dependencies import get_controller_factory
from appraisal_review.application.bootstrap import ControllerFactory
from appraisal_review.application.entrypoint import EntryProblemResponse, execute_review
from appraisal_review.domain.factor_models import AgentReviewRequest, AgentReviewRun

router = APIRouter()


@router.post(
    "/v1/reviews",
    response_model=AgentReviewRun,
    response_model_exclude={"case_review": {"findings": {"__all__": {"originating_field_ids"}}}},
    responses={
        422: {"model": EntryProblemResponse, "description": "Invalid review request"},
        503: {"model": EntryProblemResponse, "description": "Review service is not configured"},
        500: {"model": EntryProblemResponse, "description": "Review execution failed"},
    },
)
async def review(
    request: AgentReviewRequest,
    factory: Annotated[ControllerFactory, Depends(get_controller_factory)],
) -> AgentReviewRun:
    return await execute_review(request, factory)
