"""Committed responses remain subject to the caller's current authority."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from httpx import ASGITransport, AsyncClient

from appraisal_review.api.app import create_app
from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.domain.service_contracts import ActorReference, Permission, ServiceErrorCode
from tests.unit.test_human_task_api import StubResolver
from tests.unit.test_human_task_service import (
    Harness,
    caller,
    confirming,
    correcting,
    correction_task,
    fact_task,
)


@pytest.mark.parametrize("correction", [False, True], ids=["confirm", "correct"])
@pytest.mark.parametrize(
    "revocation",
    [
        "write_permission",
        "review_permission",
        "all_permissions",
        "case",
        "actor",
        "system",
        "model",
    ],
)
def test_replay_requires_current_authority(correction: bool, revocation: str) -> None:
    async def scenario() -> None:
        harness = Harness()
        owner = caller()
        task = (correction_task if correction else fact_task)(harness.snapshot, harness.run_id)
        await harness.setup((task,), principal=owner)
        command = (correcting if correction else confirming)(harness, task)
        receipt = await harness.service.respond(owner, task.task_id, command)
        job_before = await harness.jobs.read_job(job_id=harness.job_id)
        revisions_before = await harness.tasks.list_revisions(job_id=harness.job_id)
        tasks_before = await harness.tasks.list_tasks(job_id=harness.job_id)

        denied = owner
        if revocation == "write_permission":
            denied = replace(owner, permissions=owner.permissions - {task.required_permission})
        elif revocation == "review_permission":
            denied = replace(owner, permissions=owner.permissions - {Permission.REVIEW})
        elif revocation == "all_permissions":
            denied = replace(owner, permissions=frozenset())
        elif revocation == "case":
            denied = replace(owner, case_ids=frozenset())
        elif revocation == "actor":
            denied = replace(owner, actor=ActorReference(actor_id="another-reviewer", kind="human"))
        elif revocation in {"system", "model"}:
            denied = replace(
                owner,
                actor=ActorReference.model_validate(
                    {"actor_id": owner.actor.actor_id, "kind": revocation}
                ),
            )

        with pytest.raises(ServiceFault) as error:
            await harness.service.respond(denied, task.task_id, command)
        assert error.value.problem.code == (
            ServiceErrorCode.NOT_FOUND
            if revocation in {"case", "actor"}
            else ServiceErrorCode.UNAUTHORIZED
        )
        assert await harness.jobs.read_job(job_id=harness.job_id) == job_before
        assert await harness.tasks.list_revisions(job_id=harness.job_id) == revisions_before
        assert await harness.tasks.list_tasks(job_id=harness.job_id) == tasks_before

        # Restoring authority must recover the original receipt without re-admitting
        # the already consumed task version or scheduling another run.
        assert await harness.service.respond(owner, task.task_id, command) == receipt
        assert await harness.jobs.read_job(job_id=harness.job_id) == job_before
        assert await harness.tasks.list_revisions(job_id=harness.job_id) == revisions_before

    asyncio.run(scenario())


@pytest.mark.parametrize("correction", [False, True], ids=["confirm", "correct"])
def test_http_replay_rechecks_write_permission(correction: bool) -> None:
    async def scenario() -> None:
        harness = Harness()
        owner = caller()
        task = (correction_task if correction else fact_task)(harness.snapshot, harness.run_id)
        await harness.setup((task,), principal=owner)
        command = (correcting if correction else confirming)(harness, task)
        resolver = StubResolver(owner)
        app = create_app(human_task_service=harness.service, principal_resolver=resolver)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://service"
        ) as client:
            path = f"/v1/review-tasks/{task.task_id}/responses"
            payload = command.model_dump(mode="json")
            first = await client.post(path, json=payload)
            assert first.status_code == 200
            resolver.principal = replace(
                owner, permissions=owner.permissions - {task.required_permission}
            )
            denied = await client.post(path, json=payload)
            assert denied.status_code == 403
            assert denied.json() == {
                "schema_version": "service-v1",
                "code": "unauthorized",
                "message": "Service operation could not be completed.",
            }
            resolver.principal = owner
            replay = await client.post(path, json=payload)
            assert replay.status_code == 200 and replay.json() == first.json()
            assert len(await harness.tasks.list_revisions(job_id=harness.job_id)) == 2

    asyncio.run(scenario())
