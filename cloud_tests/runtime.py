"""Bounded synthetic Runtime HTTP server with explicit background health tracking."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal
from uuid import UUID

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict

from appraisal_review.adapters.aws.agentcore.runtime import invoke
from appraisal_review.adapters.local.synthetic import synthetic_adapters, synthetic_request
from appraisal_review.application.bootstrap import build_controller
from appraisal_review.config import Settings
from appraisal_review.domain.factor_models import AgentReviewRun


class SmokeInvocation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["start", "status"]
    run_id: UUID
    scenario: Literal["verified", "completed", "needs_review"] = "verified"


class SmokeRun(BaseModel):
    execution_status: Literal["running", "succeeded", "failed"]
    scenario: Literal["verified", "completed", "needs_review"]
    result: AgentReviewRun | None = None
    error_code: str | None = None
    synthetic: Literal[True] = True
    durable: Literal[False] = False


def create_app() -> FastAPI:
    runs: dict[UUID, SmokeRun] = {}
    tasks: set[asyncio.Task[None]] = set()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        for task in list(tasks):
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    app = FastAPI(lifespan=lifespan)

    async def execute(payload: SmokeInvocation) -> None:
        run = runs[payload.run_id]
        try:
            # Yield to health and status handlers. Actual blocking tools must use threads/async I/O.
            await asyncio.sleep(0)
            result = await invoke(
                synthetic_request(payload.scenario).model_dump(mode="json"),
                controller_factory=lambda: build_controller(
                    Settings(runtime_mode="local", synthetic_demo=False),
                    adapters=synthetic_adapters(),
                ),
            )
            if "error" in result:
                raise RuntimeError("Synthetic invocation failed")
            run.result = AgentReviewRun.model_validate(result)
            run.execution_status = "succeeded"
        except asyncio.CancelledError:
            run.execution_status = "failed"
            run.error_code = "smoke_cancelled"
            raise
        except Exception:
            run.execution_status = "failed"
            run.error_code = "smoke_execution_failed"
        finally:
            # Terminal state ends busy health even on failure/cancellation.
            if run.execution_status == "running":
                run.execution_status = "failed"
                run.error_code = "smoke_interrupted"

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        busy = any(run.execution_status == "running" for run in runs.values())
        return {"status": "HealthyBusy" if busy else "Healthy"}

    @app.post("/invocations", response_model=SmokeRun)
    async def invocation(payload: SmokeInvocation) -> SmokeRun:
        existing = runs.get(payload.run_id)
        if payload.action == "status":
            if existing is None:
                raise HTTPException(404, "Unknown smoke run in this Runtime session")
            return existing
        if existing is not None:
            if existing.scenario != payload.scenario:
                raise HTTPException(409, "run_id already used for a different scenario")
            return existing
        if len(runs) >= 16:
            raise HTTPException(429, "Synthetic session run limit reached")
        runs[payload.run_id] = SmokeRun(execution_status="running", scenario=payload.scenario)
        task = asyncio.create_task(execute(payload))
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return runs[payload.run_id]

    return app


app = create_app()
