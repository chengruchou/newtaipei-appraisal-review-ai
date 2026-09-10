"""HTTP boundary for human tasks.

Two properties are asserted repeatedly: another principal's task is indistinguishable from
one that does not exist, and a rejected body never comes back in the response. A validation
error here can quote a proposed correction, which is case content.

These use an in-loop ASGI transport rather than TestClient because the in-memory stores
hold asyncio locks. Seeding on one loop and then serving on another binds a lock to a loop
that is already closed, which fails for a reason that has nothing to do with the contract.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from appraisal_review.adapters.local.human_task_store import LocalHumanTaskStore
from appraisal_review.adapters.local.job_store import InMemoryJobStore
from appraisal_review.api.app import create_app
from appraisal_review.application.human_tasks import HumanTaskService
from appraisal_review.application.service_guards import Principal
from appraisal_review.domain.service_contracts import (
    HumanTask,
    Permission,
    ServiceErrorCode,
)
from appraisal_review.testing.job_store_contract import NOW
from tests.unit.test_human_task_service import Harness, caller, confirming, fact_task


@dataclass
class StubResolver:
    """Stands in for the adapter-bound authenticator; a body never asserts identity."""

    principal: Principal

    async def current_principal(self) -> Principal:
        return self.principal


@dataclass
class Mounted:
    client: AsyncClient
    harness: Harness
    task: HumanTask


@asynccontextmanager
async def mounted(
    *, viewer: Principal | None = None, configured: bool = True
) -> AsyncIterator[Mounted]:
    harness = Harness()
    owner = caller()
    task = fact_task(harness.snapshot, harness.run_id)
    await harness.setup((task,), principal=owner)
    service = HumanTaskService(harness.tasks, clock=lambda: NOW, new_revision_id=lambda: "r2")
    app = create_app(
        human_task_service=service if configured else None,
        principal_resolver=StubResolver(viewer or owner),
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://service") as client:
        yield Mounted(client=client, harness=harness, task=task)


def test_a_task_is_readable_by_its_owner() -> None:
    async def scenario() -> None:
        async with mounted() as env:
            response = await env.client.get(f"/v1/review-tasks/{env.task.task_id}")

            assert response.status_code == 200
            assert response.json()["task_id"] == str(env.task.task_id)
            assert response.json()["state"] == "open"

    asyncio.run(scenario())


def test_another_principals_task_is_not_found_rather_than_forbidden() -> None:
    async def scenario() -> None:
        async with mounted(viewer=caller("reviewer-two")) as env:
            response = await env.client.get(f"/v1/review-tasks/{env.task.task_id}")

            assert response.status_code == 404
            assert response.json()["code"] == ServiceErrorCode.NOT_FOUND.value

    asyncio.run(scenario())


def test_a_missing_permission_is_forbidden_on_the_write_only() -> None:
    async def scenario() -> None:
        reader = caller(permissions=frozenset({Permission.REVIEW}))
        async with mounted(viewer=reader) as env:
            readable = await env.client.get(f"/v1/review-tasks/{env.task.task_id}")
            refused = await env.client.post(
                f"/v1/review-tasks/{env.task.task_id}/responses",
                json=confirming(env.harness, env.task).model_dump(mode="json"),
            )

            assert readable.status_code == 200
            assert refused.status_code == 403
            assert refused.json()["code"] == ServiceErrorCode.UNAUTHORIZED.value

    asyncio.run(scenario())


def test_answering_returns_a_receipt_and_an_exact_replay_returns_the_same_one() -> None:
    async def scenario() -> None:
        async with mounted() as env:
            body = confirming(env.harness, env.task, key="http-key").model_dump(mode="json")
            path = f"/v1/review-tasks/{env.task.task_id}/responses"

            first = await env.client.post(path, json=body)
            again = await env.client.post(path, json=body)

            assert first.status_code == 200 and again.status_code == 200
            assert first.json() == again.json()
            assert first.json()["revision"]["revision_id"] == "r2"
            assert first.json()["resumed_run"]["revision"]["revision_id"] == "r2"

    asyncio.run(scenario())


def test_a_rejected_body_is_not_echoed_back() -> None:
    async def scenario() -> None:
        async with mounted() as env:
            secret = "12 Nangang Road, plot 44"

            response = await env.client.post(
                f"/v1/review-tasks/{env.task.task_id}/responses",
                json={"task_id": str(env.task.task_id), "action": "confirm", "raw_text": secret},
            )

            assert response.status_code == 422
            assert response.json() == {
                "schema_version": "service-v1",
                "code": ServiceErrorCode.VALIDATION.value,
                "message": "Service operation could not be completed.",
            }
            assert secret not in response.text

    asyncio.run(scenario())


def test_an_unconfigured_plane_reports_capability_rather_than_a_fake_answer() -> None:
    async def scenario() -> None:
        async with mounted(configured=False) as env:
            response = await env.client.get(f"/v1/review-tasks/{env.task.task_id}")

            assert response.status_code == 503
            assert response.json()["code"] == ServiceErrorCode.CAPABILITY.value

    asyncio.run(scenario())


def test_the_job_task_and_revision_lists_are_mounted() -> None:
    async def scenario() -> None:
        async with mounted() as env:
            job_id = env.harness.job_id
            tasks = await env.client.get(f"/v1/review-jobs/{job_id}/tasks")
            revisions = await env.client.get(f"/v1/review-jobs/{job_id}/revisions")

            assert tasks.status_code == 200 and len(tasks.json()["tasks"]) == 1
            assert revisions.status_code == 200
            listed = [r["reference"]["revision_id"] for r in revisions.json()["revisions"]]
            assert listed == ["r1"]

    asyncio.run(scenario())


def test_an_unknown_job_is_not_found() -> None:
    async def scenario() -> None:
        async with mounted() as env:
            response = await env.client.get(f"/v1/review-jobs/{uuid4()}/tasks")

            assert response.status_code == 404
            assert response.json()["code"] == ServiceErrorCode.NOT_FOUND.value

    asyncio.run(scenario())


def test_human_tasks_require_a_principal_resolver() -> None:
    with pytest.raises(ValueError, match="principal resolver"):
        create_app(human_task_service=HumanTaskService(LocalHumanTaskStore(InMemoryJobStore())))
