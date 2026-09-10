"""FastAPI entry point for local validation and future AWS deployment."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from appraisal_review.api.routes.review_jobs import JOB_ENDPOINTS
from appraisal_review.api.routes.review_jobs import router as job_router
from appraisal_review.api.routes.reviews import review, router
from appraisal_review.application.bootstrap import (
    ControllerFactory,
    ReviewAdapters,
    build_controller,
)
from appraisal_review.application.entrypoint import EntryError, EntryProblem
from appraisal_review.application.review_jobs import ReviewJobService
from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.config import Settings
from appraisal_review.domain.models import CanonicalCase, ReviewResult, RuleSet
from appraisal_review.domain.rule_engine import RuleEngine
from appraisal_review.domain.service_contracts import ServiceErrorCode
from appraisal_review.ports.service import PrincipalResolver

# Reserved mapping from docs/service-contracts.md. The durable plane answers with the
# sanitized ServiceProblem envelope; the legacy routes keep their own EntryProblem shape.
FAULT_STATUS: dict[ServiceErrorCode, int] = {
    ServiceErrorCode.VALIDATION: 422,
    ServiceErrorCode.UNAUTHORIZED: 403,
    ServiceErrorCode.NOT_FOUND: 404,
    ServiceErrorCode.CONFLICT: 409,
    ServiceErrorCode.CAPABILITY: 503,
    ServiceErrorCode.EXECUTION: 500,
}


class ValidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case: CanonicalCase
    rule_set: RuleSet


def health() -> dict[str, str]:
    return {"status": "ok"}


def validate(request: ValidationRequest) -> ReviewResult:
    return RuleEngine(request.rule_set).evaluate(request.case)


def create_app(
    *,
    settings: Settings | None = None,
    adapters: ReviewAdapters | None = None,
    controller_factory: ControllerFactory | None = None,
    job_service: ReviewJobService | None = None,
    principal_resolver: PrincipalResolver | None = None,
) -> FastAPI:
    if controller_factory is not None and (settings is not None or adapters is not None):
        raise ValueError("Choose an explicit factory or settings/adapters, not both")
    # A durable job plane without authentication would expose other principals' jobs, so
    # neither half is usable alone; the routes report capability_unavailable instead.
    if (job_service is None) != (principal_resolver is None):
        raise ValueError("Durable jobs require both a store and a principal resolver")
    app = FastAPI(
        title="Agentic AI Real Estate Valuation Reviewer",
        version="0.1.0",
        description="Synchronous factor review entry; document extraction requires configuration.",
    )
    app.state.controller_factory = controller_factory or (
        lambda: build_controller(settings, adapters=adapters)
    )
    app.add_api_route("/health", health, methods=["GET"])
    app.add_api_route("/v1/validate", validate, methods=["POST"], response_model=ReviewResult)
    app.state.job_service = job_service
    app.state.principal_resolver = principal_resolver
    app.include_router(router)
    app.include_router(job_router)

    @app.exception_handler(ServiceFault)
    async def service_fault(request: Request, fault: ServiceFault) -> JSONResponse:
        return JSONResponse(
            status_code=FAULT_STATUS[fault.problem.code],
            content=fault.problem.model_dump(mode="json"),
        )

    @app.exception_handler(EntryError)
    async def entry_error(request: Request, error: EntryError) -> JSONResponse:
        return JSONResponse(status_code=error.status_code, content=error.problem.response())

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
        # Match the routed endpoint, so mounting under a root path preserves the contract.
        endpoint = getattr(request.scope.get("route"), "endpoint", None)
        if endpoint is review:
            return JSONResponse(
                status_code=422,
                content=EntryProblem(
                    code="invalid_request", message="Invalid review request."
                ).response(),
            )
        if endpoint in JOB_ENDPOINTS:
            # Job routes answer with the sanitized service envelope and never echo the
            # rejected payload, which may quote document text.
            return JSONResponse(
                status_code=422,
                content=ServiceFault(ServiceErrorCode.VALIDATION).problem.model_dump(mode="json"),
            )
        # Preserve the legacy detail structure without raw input or validator context.
        # Custom value/assertion messages may interpolate document text.
        detail = [
            {
                "loc": item["loc"],
                "type": item["type"],
                "msg": (
                    "Invalid value."
                    if item["type"] in {"value_error", "assertion_error"}
                    else item["msg"]
                ),
            }
            for item in error.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={"detail": detail},
        )

    return app


app = create_app()
