"""Shared entry execution and sanitized error contract for transports."""

from pydantic import BaseModel, ConfigDict, JsonValue

from appraisal_review.application.bootstrap import ConfigurationError, ControllerFactory
from appraisal_review.domain.factor_models import AgentReviewRequest, AgentReviewRun


class EntryProblem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    message: str

    def response(self) -> dict[str, JsonValue]:
        return {"error": self.model_dump(mode="json")}


class EntryError(Exception):
    def __init__(self, problem: EntryProblem, status_code: int) -> None:
        self.problem = problem
        self.status_code = status_code
        super().__init__(problem.code)


async def execute_review(request: AgentReviewRequest, factory: ControllerFactory) -> AgentReviewRun:
    try:
        controller = factory()
    except ConfigurationError as error:
        raise EntryError(
            EntryProblem(code=error.code, message="Review service is not configured."), 503
        ) from error
    except Exception as error:
        raise EntryError(
            EntryProblem(code="invalid_configuration", message="Review service is not configured."),
            503,
        ) from error
    try:
        result = await controller.review(request)
        if not isinstance(result, AgentReviewRun) or result.case_id != request.case_id:
            raise ValueError("Invalid controller response")
        return AgentReviewRun.model_validate_json(result.model_dump_json())
    except Exception as error:
        raise EntryError(
            EntryProblem(code="review_execution_failed", message="Review execution failed."), 500
        ) from error
