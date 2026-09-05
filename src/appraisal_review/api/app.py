"""FastAPI entry point for local validation and future AWS deployment."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from appraisal_review.api.routes.reviews import router
from appraisal_review.application.bootstrap import ReviewAdapters, build_controller
from appraisal_review.application.entrypoint import EntryError, EntryProblem
from appraisal_review.config import Settings
from appraisal_review.domain.models import CanonicalCase, ReviewResult, RuleSet
from appraisal_review.domain.rule_engine import RuleEngine


class ValidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case: CanonicalCase
    rule_set: RuleSet


def health() -> dict[str, str]:
    return {"status": "ok"}


def validate(request: ValidationRequest) -> ReviewResult:
    return RuleEngine(request.rule_set).evaluate(request.case)


def create_app(
    *, settings: Settings | None = None, adapters: ReviewAdapters | None = None
) -> FastAPI:
    app = FastAPI(
        title="Agentic AI Real Estate Valuation Reviewer",
        version="0.1.0",
        description="Synchronous factor review entry; document extraction requires configuration.",
    )
    app.state.controller_factory = lambda: build_controller(settings, adapters=adapters)
    app.add_api_route("/health", health, methods=["GET"])
    app.add_api_route("/v1/validate", validate, methods=["POST"], response_model=ReviewResult)
    app.include_router(router)

    @app.exception_handler(EntryError)
    async def entry_error(request: Request, error: EntryError) -> JSONResponse:
        return JSONResponse(status_code=error.status_code, content=error.problem.response())

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
        # FastAPI's default detail includes raw input; document payloads may be sensitive.
        return JSONResponse(
            status_code=422,
            content=EntryProblem(
                code="invalid_request", message="Invalid review request."
            ).response(),
        )

    return app


app = create_app()
