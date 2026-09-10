"""Local pause/continuation preparation; no process recovery or cloud acceptance."""

import asyncio
import json
import runpy
from pathlib import Path
from uuid import uuid4

import jsonschema
import pytest
from pydantic import ValidationError

from appraisal_review.adapters.local.human_tasks import NonDurableInMemoryHumanTaskRepository
from appraisal_review.application.pause_resume import PauseResumeService
from appraisal_review.application.service_guards import Principal, ServiceFault
from appraisal_review.domain.factor_models import NormalizedValue
from appraisal_review.domain.service_contracts import (
    ActorReference,
    ResponseAction,
    TaskKind,
    ValueRevision,
    WorkflowContinuation,
    WorkflowPause,
    WorkflowTermination,
)
from appraisal_review.ports.service import PauseResumeRepository

H = runpy.run_path(str(Path(__file__).with_name("test_controlled_case_integration.py")))


async def prepared(tmp_path):
    snapshot, run, subjects, resolver, repo, service, approval, _ = H["setup"](
        tmp_path, blocked=True
    )
    case, result = await H["execute"](
        snapshot,
        run,
        repo,
        service,
        approval,
        H["bindings"](snapshot, side_kind=TaskKind.CORRECTION),
        model=False,
    )
    principal = await resolver.current_principal()
    old = subjects.public_value(snapshot, "road-target")
    command = H["command"](
        case.tasks[0],
        ResponseAction.CORRECT,
        correction=ValueRevision(
            subject_id="road-target",
            original=old,
            proposed=old.model_copy(
                update={
                    "raw_text": "10 m",
                    "value": NormalizedValue(type="number", value=10, unit="m"),
                }
            ),
        ),
    )
    return snapshot, run, principal, repo, service, result, command


def test_pause_and_admitted_response_link_fresh_pending_run_atomically(tmp_path):
    async def scenario():
        snapshot, run, principal, repo, service, result, command = await prepared(tmp_path)
        port: PauseResumeRepository = repo

        class Resolver:
            async def current_principal(self):
                return principal

        handoff = PauseResumeService(port, Resolver())
        pause = await handoff.pause(result)
        assert await port.pause(principal, result) == pause
        assert pause.result.events == result.events
        assert len(pause.task_ids) == 2
        with pytest.raises(ServiceFault):
            await service.reenter(run)
        first, replay = await asyncio.gather(service.respond(command), service.respond(command))
        assert first == replay
        continuation = await handoff.continuation(first.event_id)
        assert continuation.pause_id == pause.pause_id
        assert continuation.previous_run == run
        assert continuation.next_run == first.next_run
        assert continuation.next_run.run_id != run.run_id
        assert continuation.next_run.attempt_id is None
        assert continuation.next_run.runtime_session_id is None
        assert first.revision.parent == snapshot.revision.reference
        assert await port.read_pause(principal, run) == pause
        assert await handoff.read(run) == pause
        schema = json.loads(
            (Path(__file__).resolve().parents[2] / "schemas/service-v1.json").read_text()
        )
        for record in (pause, continuation):
            jsonschema.validate(
                record.model_dump(mode="json"),
                {**schema, "$ref": f"#/$defs/{type(record).__name__}"},
            )
        assert len(await repo.responses(principal, run.revision.case_id)) == 1
        with pytest.raises(ServiceFault):
            await repo.context(principal, first.next_run)
        recomputed = await service.reenter(first.next_run)
        assert recomputed.status.value == "needs_review"
        assert await service.reenter(first.next_run) == recomputed
        with pytest.raises(ServiceFault):
            await service.reenter(run)
        # A new empty adapter is not restart recovery, even with identical IDs.
        with pytest.raises(ServiceFault):
            await NonDurableInMemoryHumanTaskRepository().read_pause(principal, run)

    asyncio.run(scenario())


def test_response_event_failure_rolls_back_task_revision_and_continuation(tmp_path, monkeypatch):
    async def scenario():
        snapshot, run, principal, repo, service, result, command = await prepared(tmp_path)
        pause = await repo.pause(principal, result)
        original_factory = repo._event_id

        def fail_event():
            raise RuntimeError("synthetic response event failure")

        monkeypatch.setattr(repo, "_event_id", fail_event)
        with pytest.raises(RuntimeError):
            await service.respond(command)
        assert (await repo.get_task(principal, command.task_id)).state == "open"
        assert (await repo.context(principal, run)).snapshot == snapshot
        assert await repo.responses(principal, run.revision.case_id) == ()
        assert await repo.read_pause(principal, run) == pause
        monkeypatch.setattr(repo, "_event_id", original_factory)
        response = await service.respond(command)
        assert (await repo.continuation(principal, response.event_id)).next_run == response.next_run
        with pytest.raises(ServiceFault):
            await service.respond(
                command.model_copy(update={"idempotency_key": "different-replay"})
            )
        assert len(await repo.responses(principal, run.revision.case_id)) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "mutation", ["failed", "missing_parent", "duplicate", "wrong_task", "cycle", "receipt"]
)
def test_invalid_pause_does_not_create_checkpoint(tmp_path, mutation):
    async def scenario():
        _, run, principal, repo, _, result, _ = await prepared(tmp_path)
        events = list(result.events)
        if mutation == "failed":
            result = result.model_copy(update={"termination": WorkflowTermination.VERIFIED})
        elif mutation == "missing_parent":
            events[-1] = events[-1].model_copy(update={"parent_event_ids": (uuid4(),)})
        elif mutation == "duplicate":
            events = [events[0], events[0], events[-1]]
        elif mutation == "cycle":
            events[0] = events[0].model_copy(update={"parent_event_ids": (events[-1].event_id,)})
        elif mutation == "receipt":
            events[-1] = events[-1].model_copy(
                update={
                    "tool_result": events[-1].tool_result.model_copy(
                        update={"result_digest": "f" * 64}
                    )
                }
            )
        else:
            events[-1] = events[-1].model_copy(update={"linked_task_id": uuid4()})
        result = result.model_copy(update={"events": tuple(events)})
        with pytest.raises((ValidationError, ServiceFault)):
            await repo.pause(principal, result)
        with pytest.raises(ServiceFault):
            await repo.read_pause(principal, run)

    asyncio.run(scenario())


def test_response_failure_and_rejection_do_not_enqueue_continuation(tmp_path):
    async def scenario():
        _, run, principal, repo, service, result, command = await prepared(tmp_path)
        pause = await repo.pause(principal, result)
        with pytest.raises(ServiceFault):
            await service.respond(command.model_copy(update={"expected_version": 99}))
        assert await repo.responses(principal, run.revision.case_id) == ()
        assert await repo.read_pause(principal, run) == pause
        rejected = await service.respond(
            command.model_copy(update={"action": ResponseAction.REJECT, "correction": None})
        )
        assert rejected.next_run is None
        with pytest.raises(ServiceFault):
            await repo.continuation(principal, rejected.event_id)

    asyncio.run(scenario())


def test_pause_access_and_continuation_identity_fail_closed(tmp_path):
    async def scenario():
        _, run, principal, repo, service, result, command = await prepared(tmp_path)
        denied = Principal(
            actor=ActorReference(actor_id="unrelated", kind="human"),
            case_ids=frozenset(),
            permissions=principal.permissions,
        )
        with pytest.raises(ServiceFault):
            await repo.pause(denied, result)
        await repo.pause(principal, result)
        with pytest.raises(ServiceFault):
            await repo.read_pause(denied, run)
        accepted = await service.respond(command)
        with pytest.raises(ServiceFault):
            await repo.continuation(denied, accepted.event_id)
        link = await repo.continuation(principal, accepted.event_id)
        for next_run in (run, link.next_run.model_copy(update={"attempt_id": uuid4()})):
            with pytest.raises(ValidationError):
                WorkflowContinuation.model_validate({**link.model_dump(), "next_run": next_run})
        checkpoint = await repo.read_pause(principal, run)
        assert WorkflowPause.model_validate_json(checkpoint.model_dump_json()) == checkpoint

    asyncio.run(scenario())
