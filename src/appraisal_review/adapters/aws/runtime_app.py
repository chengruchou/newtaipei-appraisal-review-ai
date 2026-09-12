"""HTTP Runtime protocol. Missing reviewed execution dependencies fail closed."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from appraisal_review.adapters.aws.runtime_jobs import RuntimeDispatch
from appraisal_review.application.runtime_worker import RuntimeWorker
from appraisal_review.domain.service_contracts import ServiceErrorCode, ServiceProblem


def create_app(*, worker: RuntimeWorker | None = None, concurrency: int = 2) -> FastAPI:
    if not 1 <= concurrency <= 16:
        raise ValueError("Invalid Runtime concurrency")
    running: set[asyncio.Task[object]] = set()
    stopping = False

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        nonlocal stopping
        yield
        stopping = True
        for task in tuple(running):
            task.cancel()
        await asyncio.gather(*tuple(running), return_exceptions=True)

    app = FastAPI(title="Review Runtime", lifespan=lifespan)

    def problem(code: ServiceErrorCode, status: int) -> JSONResponse:
        return JSONResponse(
            status_code=status, content=ServiceProblem(code=code).model_dump(mode="json")
        )

    @app.get("/ping", response_model=None)
    async def ping() -> dict[str, str] | JSONResponse:
        # No SDK, storage, model call or private dependency detail on the health path.
        if stopping or worker is None:
            return problem(ServiceErrorCode.CAPABILITY, 503)
        return {"status": "HealthyBusy" if running else "Healthy"}

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, error: RequestValidationError) -> JSONResponse:
        return problem(ServiceErrorCode.VALIDATION, 422)

    @app.post(
        "/invocations",
        response_model=None,
        responses={
            422: {"model": ServiceProblem},
            503: {"model": ServiceProblem},
            500: {"model": ServiceProblem},
        },
    )
    async def invocation(payload: RuntimeDispatch) -> dict[str, str] | JSONResponse:
        if worker is None or stopping or len(running) >= concurrency:
            return problem(ServiceErrorCode.CAPABILITY, 503)
        current = asyncio.current_task()
        if current is None:
            return problem(ServiceErrorCode.EXECUTION, 500)
        running.add(current)
        try:
            outcome = await worker.process(payload.message())
            return {
                "job_id": str(payload.job_id),
                "run_id": str(payload.run_id),
                "outcome": outcome,
            }
        except Exception:
            return problem(ServiceErrorCode.EXECUTION, 500)
        finally:
            running.discard(current)

    return app


# Reviewed revision, human-task and publication providers are not on this baseline.
# An empty deployment must not substitute local fixtures or report a healthy reviewer.
app = create_app()
