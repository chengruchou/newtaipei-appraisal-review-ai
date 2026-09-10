"""Regressions at the committed task, job and revision boundary."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.domain.job_contracts import JobStatus
from appraisal_review.domain.service_contracts import ResponseAction, ServiceErrorCode
from appraisal_review.ports.jobs import ConditionFailed
from appraisal_review.testing.job_store_contract import NOW, submission
from tests.unit.test_human_task_api import mounted
from tests.unit.test_human_task_service import (
    ACTOR,
    Harness,
    caller,
    confirming,
    correcting,
    correction_task,
    fact_task,
)


def test_corrected_ledger_contains_only_the_applied_observation() -> None:
    async def scenario() -> None:
        h = Harness()
        task = correction_task(h.snapshot, h.run_id)
        await h.setup((task,), principal=caller())
        command = correcting(h, task)
        assert command.correction is not None and command.correction.proposed is not None
        original_pair = h.snapshot.material.facts.pairs[0]
        proposed = command.correction.proposed.model_copy(
            update={
                "confidence": 1.0,
                "evidence": (
                    original_pair.target_sources[0].model_copy(
                        update={"excerpt": "invented citation"}
                    ),
                ),
            }
        )
        command = command.model_copy(
            update={"correction": command.correction.model_copy(update={"proposed": proposed})}
        )
        receipt = await h.service.respond(caller(), task.task_id, command)
        assert receipt.revision is not None
        stored = await h.tasks.read_snapshot(revision=receipt.revision)
        assert stored is not None
        pair = stored.material.facts.pairs[0]
        change = stored.revision.changes[0]
        assert change.corrected is not None
        assert change.proposed == proposed
        assert change.corrected.confidence == pair.pair.target.confidence
        assert change.corrected.evidence == tuple(pair.target_sources)
        assert change.corrected.value == pair.pair.target.value
        assert pair.pair.target.confidence == original_pair.pair.target.confidence

    asyncio.run(scenario())


@pytest.mark.parametrize("remaining", [0, 1])
def test_rejection_removes_answered_task_from_job_projection(remaining: int) -> None:
    async def scenario() -> None:
        h = Harness()
        task = fact_task(h.snapshot, h.run_id)
        siblings = tuple(fact_task(h.snapshot, h.run_id) for _ in range(remaining))
        await h.setup((task, *siblings), principal=caller())
        command = confirming(h, task).model_copy(update={"action": ResponseAction.REJECT})
        receipt = await h.service.respond(caller(), task.task_id, command)
        job = await h.jobs.read_job(job_id=h.job_id)
        assert job is not None
        assert job.open_task_ids == tuple(t.task_id for t in siblings)
        assert job.status == (JobStatus.WAITING_FOR_HUMAN if remaining else JobStatus.FAILED)
        assert receipt.job_status == job.status
        assert receipt.revision is None and receipt.resumed_run is None
        assert (await h.tasks.read_task(task_id=task.task_id)).task.state == "answered"

    asyncio.run(scenario())


def test_empty_tasks_are_authorized_by_the_owned_job() -> None:
    async def scenario() -> None:
        h = Harness()
        await h.jobs.create_job(
            caller(),
            submission(case_id="synthetic-case", revision_id="r1"),
            job_id=h.job_id,
            run_id=h.run_id,
            now=NOW,
        )
        h.tasks.seed(job_id=h.job_id, principal_id=ACTOR, snapshot=h.snapshot, tasks=())
        assert (await h.service.list_tasks(caller(), h.job_id)).tasks == ()
        assert len((await h.service.list_revisions(caller(), h.job_id)).revisions) == 1
        for principal, job_id in ((caller("foreign"), h.job_id), (caller(), uuid4())):
            with pytest.raises(ServiceFault) as error:
                await h.service.list_tasks(principal, job_id)
            assert error.value.problem.code == ServiceErrorCode.NOT_FOUND

    asyncio.run(scenario())


def test_followup_registration_is_idempotent_and_rejects_stale_head() -> None:
    async def scenario() -> None:
        h = Harness()
        task = fact_task(h.snapshot, h.run_id)
        await h.setup((task,), principal=caller())
        receipt = await h.service.respond(caller(), task.task_id, confirming(h, task))
        assert receipt.revision is not None and receipt.resumed_run is not None
        snapshot = await h.tasks.read_snapshot(revision=receipt.revision)
        assert snapshot is not None
        next_task = fact_task(snapshot, receipt.resumed_run.run_id)
        for _ in range(2):
            h.tasks.seed(job_id=h.job_id, principal_id=ACTOR, snapshot=snapshot, tasks=(next_task,))
        history = await h.service.list_revisions(caller(), h.job_id)
        assert [r.reference.revision_id for r in history.revisions] == ["r1", "r2"]
        with pytest.raises(ConditionFailed):
            h.tasks.seed(job_id=h.job_id, principal_id=ACTOR, snapshot=h.snapshot, tasks=(task,))
        changed = snapshot.material
        changed.facts.pairs[0].pair.target.raw_text = "different same-version material"
        conflict = RevisionSnapshot.capture(changed, "r2", parent=h.snapshot.revision.reference)
        with pytest.raises(ConditionFailed):
            h.tasks.seed(job_id=h.job_id, principal_id=ACTOR, snapshot=conflict, tasks=())

    asyncio.run(scenario())


def test_cancelled_response_rolls_back_all_local_projections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        h = Harness()
        task = fact_task(h.snapshot, h.run_id)
        await h.setup((task,), principal=caller())
        reached = asyncio.Event()
        release = asyncio.Event()
        original_resume = h.jobs.resume_after_human

        async def wait_before_resume(**kwargs):
            reached.set()
            await release.wait()
            return await original_resume(**kwargs)

        monkeypatch.setattr(h.jobs, "resume_after_human", wait_before_resume)
        pending = asyncio.create_task(
            h.service.respond(caller(), task.task_id, confirming(h, task))
        )
        await reached.wait()
        assert h.tasks._lock.locked()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        stored = await h.tasks.read_task(task_id=task.task_id)
        assert stored is not None and stored.task.state == "open"
        assert len(await h.tasks.list_revisions(job_id=h.job_id)) == 1
        assert await h.tasks.read_receipt(principal_id=ACTOR, key="k1") is None
        job = await h.jobs.read_job(job_id=h.job_id)
        assert job is not None and job.status == JobStatus.WAITING_FOR_HUMAN

    asyncio.run(scenario())


@pytest.mark.parametrize("unit,value", [("m", 12.0), ("%", 0.0)])
def test_subject_metadata_and_applied_correction_keep_authoritative_units(
    unit: str, value: float
) -> None:
    async def scenario() -> None:
        h = Harness()
        material = h.snapshot.material
        material.facts.pairs[0].pair.target.value.unit = unit
        material.facts.pairs[0].pair.target.confidence = 0
        h.snapshot = RevisionSnapshot.capture(material, "r1")
        task = correction_task(h.snapshot, h.run_id)
        await h.setup((task,), principal=caller())
        view = await h.service.read_subject(caller(), task.task_id)
        assert view.required_type == "number" and view.required_unit == unit
        assert view.unit_required and view.observation.confidence == 0
        command = correcting(h, task, metres=value)
        data = command.model_dump(mode="json")
        data["correction"]["proposed"]["unit"] = unit
        data["correction"]["proposed"]["value"]["unit"] = unit
        receipt = await h.service.respond(
            caller(), task.task_id, type(command).model_validate(data)
        )
        assert receipt.revision is not None
        snapshot = await h.tasks.read_snapshot(revision=receipt.revision)
        assert snapshot is not None
        observation = snapshot.material.facts.pairs[0].pair.target
        assert observation.value.value == value and observation.value.unit == unit
        assert observation.confidence == 0

    asyncio.run(scenario())


def test_subject_endpoint_is_opt_in_and_exactly_matches_openapi() -> None:
    async def scenario() -> None:
        async with mounted() as env:
            response = await env.client.get(f"/v1/review-tasks/{env.task.task_id}/subject")
            assert response.status_code == 200
            body = response.json()
            assert body["required_unit"] == "m" and body["required_type"] == "number"
            assert body["revision"] == env.task.run.revision.model_dump(mode="json")
            legacy = (await env.client.get(f"/v1/review-tasks/{env.task.task_id}")).json()
            assert set(legacy) == {"schema_version", "task", "subject_id"}
            schema = (await env.client.get("/openapi.json")).json()
            from jsonschema import Draft202012Validator

            model = schema["components"]["schemas"]["TaskSubjectView"]
            Draft202012Validator(model | {"components": schema["components"]}).validate(body)
        async with mounted(viewer=caller("foreign")) as env:
            response = await env.client.get(f"/v1/review-tasks/{env.task.task_id}/subject")
            assert response.status_code == 404

    asyncio.run(scenario())


@pytest.mark.parametrize("unit", [None, "%"])
def test_correction_cannot_erase_or_replace_authoritative_unit(unit: str | None) -> None:
    async def scenario() -> None:
        h = Harness()
        task = correction_task(h.snapshot, h.run_id)
        await h.setup((task,), principal=caller())
        command = correcting(h, task)
        body = command.model_dump(mode="json")
        body["correction"]["proposed"]["unit"] = unit
        body["correction"]["proposed"]["value"]["unit"] = unit
        with pytest.raises(ServiceFault) as error:
            await h.service.respond(caller(), task.task_id, type(command).model_validate(body))
        assert error.value.problem.code == ServiceErrorCode.VALIDATION
        assert len(await h.tasks.list_revisions(job_id=h.job_id)) == 1

    asyncio.run(scenario())
