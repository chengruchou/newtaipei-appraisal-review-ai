"""FastAPI entry point for local validation and future AWS deployment."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from appraisal_review.api.routes.content import CONTENT_ENDPOINTS
from appraisal_review.api.routes.content import router as content_router
from appraisal_review.api.routes.human_tasks import HUMAN_TASK_ENDPOINTS
from appraisal_review.api.routes.human_tasks import router as human_task_router
from appraisal_review.api.routes.review_jobs import JOB_ENDPOINTS
from appraisal_review.api.routes.review_jobs import router as job_router
from appraisal_review.api.routes.reviews import review, router
from appraisal_review.application.bootstrap import (
    ControllerFactory,
    ReviewAdapters,
    build_controller,
)
from appraisal_review.application.entrypoint import EntryError, EntryProblem
from appraisal_review.application.human_tasks import HumanTaskService
from appraisal_review.application.review_jobs import ReviewJobService
from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.config import Settings
from appraisal_review.domain.models import CanonicalCase, ReviewResult, RuleSet
from appraisal_review.domain.rule_engine import RuleEngine
from appraisal_review.domain.service_contracts import ServiceErrorCode
from appraisal_review.ports.content import ContentPlane
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
    human_task_service: HumanTaskService | None = None,
    principal_resolver: PrincipalResolver | None = None,
    content_plane: ContentPlane | None = None,
) -> FastAPI:
    if controller_factory is not None and (settings is not None or adapters is not None):
        raise ValueError("Choose an explicit factory or settings/adapters, not both")
    # Neither authenticated plane may be mounted without an authenticator: the job routes
    # would expose other principals' jobs, and answering a task is a write, so an
    # unauthenticated caller could confirm another case's observations. An unmounted plane
    # reports capability_unavailable rather than answering as if work were accepted.
    #
    # These are implications, not biconditionals. Two planes now share one resolver, and a
    # resolver on its own mounts nothing and grants nothing, so demanding a job store would
    # refuse both a human-task-only composition and the deliberately unconfigured one whose
    # 503 the contract promises.
    if job_service is not None and principal_resolver is None:
        raise ValueError("Durable jobs require both a store and a principal resolver")
    if human_task_service is not None and principal_resolver is None:
        raise ValueError("Human tasks require a principal resolver")
    # Content delivery reads case-scoped bytes, so it needs the same authenticator as the
    # other two planes. Its routes stay mounted either way: the contract promises a 503 for
    # an unwired plane, and a missing route would instead read as a missing artifact.
    if content_plane is not None and principal_resolver is None:
        raise ValueError("Content delivery requires a principal resolver")
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
    app.state.human_task_service = human_task_service
    app.state.principal_resolver = principal_resolver
    app.state.content_plane = content_plane
    app.include_router(router)
    app.include_router(job_router)
    app.include_router(human_task_router)
    app.include_router(content_router)

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
        if (
            endpoint in JOB_ENDPOINTS
            or endpoint in HUMAN_TASK_ENDPOINTS
            or endpoint in CONTENT_ENDPOINTS
        ):
            # These routes answer with the sanitized service envelope and never echo the
            # rejected payload, which may quote document text or a proposed correction.
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
