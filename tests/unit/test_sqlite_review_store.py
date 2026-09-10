"""Actual SQLite durability and combined response transaction regressions."""

import asyncio
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from appraisal_review.adapters.local.job_store import InMemoryJobStore
from appraisal_review.adapters.local.sqlite_review_store import (
    SQLiteReviewStore,
    SQLiteReviewStoreError,
)
from appraisal_review.adapters.local.synthetic import synthetic_material
from appraisal_review.application.human_tasks import HumanTaskService
from appraisal_review.application.job_state import JobEvent
from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.domain.job_contracts import JobStatus
from appraisal_review.domain.service_contracts import (
    HumanResponse,
    ResponseAction,
    ReviewSubmission,
)
from appraisal_review.ports.jobs import ConditionFailed
from appraisal_review.testing.job_store_contract import (
    CONTRACT_CHECKS,
    NOW,
    JobStoreContract,
    principal,
    submission,
)
from tests.unit.test_human_task_service import ACTOR, caller, fact_task


def test_store_constructs_private_database(tmp_path: Path) -> None:
    path = tmp_path / "private" / "review.sqlite3"
    SQLiteReviewStore(path)
    assert path.is_file()


def store_at(path: Path) -> SQLiteReviewStore:
    return SQLiteReviewStore(path, clock=lambda: NOW)


def stored_json(path: Path) -> dict:
    with sqlite3.connect(path) as connection:
        return json.loads(connection.execute("SELECT payload FROM review_state").fetchone()[0])


async def prepared(path: Path):
    store = store_at(path)
    snapshot = RevisionSnapshot.capture(synthetic_material(), "r1")
    job_id, run_id = uuid4(), uuid4()
    payload = ReviewSubmission(
        revision=snapshot.revision.reference,
        documents=snapshot.revision.documents,
        idempotency_key="job-key",
    )
    await store.create_job(caller(), payload, job_id=job_id, run_id=run_id, now=NOW)
    task = fact_task(snapshot, run_id)
    attempt = await store.claim(
        job_id=job_id, run_id=run_id, owner=uuid4(), lease_seconds=60, now=NOW
    )
    # Runtime registers before committing NEEDS_HUMAN, while the job is RUNNING.
    await store.register_tasks(job_id=job_id, principal_id=ACTOR, snapshot=snapshot, tasks=(task,))
    await store.finish(attempt, event=JobEvent.NEEDS_HUMAN, now=NOW, open_task_ids=(task.task_id,))
    command = HumanResponse(
        task_id=task.task_id,
        expected_version=1,
        revision=snapshot.revision.reference,
        side_digest=task.side.input_digest,
        idempotency_key="response-key",
        action=ResponseAction.CONFIRM,
    )
    return store, snapshot, job_id, task, command


@pytest.mark.parametrize("check", CONTRACT_CHECKS)
def test_sqlite_runs_existing_job_state_machine_contract(tmp_path: Path, check: str) -> None:
    asyncio.run(getattr(JobStoreContract(), check)(store_at(tmp_path / "private" / "jobs.sqlite3")))


def test_independent_instances_race_claim_and_reopen_after_process_exit(tmp_path: Path) -> None:
    async def scenario():
        path = tmp_path / "private" / "jobs.sqlite3"
        first, second = store_at(path), store_at(path)
        job_id, run_id = uuid4(), uuid4()
        await first.create_job(principal(), submission(), job_id=job_id, run_id=run_id, now=NOW)
        claimed = await asyncio.gather(
            *(
                store.claim(job_id=job_id, run_id=run_id, owner=uuid4(), lease_seconds=60, now=NOW)
                for store in (first, second)
            ),
            return_exceptions=True,
        )
        assert sum(isinstance(item, ConditionFailed) for item in claimed) == 1
        winner = next(item for item in claimed if not isinstance(item, BaseException))
        child = subprocess.run(
            [
                sys.executable,
                "-c",
                "import asyncio,json,sys; from pathlib import Path; from uuid import UUID; "
                "from appraisal_review.adapters.local.sqlite_review_store "
                "import SQLiteReviewStore; "
                "s=SQLiteReviewStore(Path(sys.argv[1])); "
                "r=asyncio.run(s.read_job(job_id=UUID(sys.argv[2]))); "
                "print(json.dumps([r.status.value,str(r.current_run.attempt_id)]))",
                str(path),
                str(job_id),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=20,
        )
        assert json.loads(child.stdout) == ["running", str(winner.attempt_id)]
        assert (await second.read_job(job_id=job_id)).current_run.attempt_id == winner.attempt_id

    asyncio.run(scenario())


def test_response_restarts_replays_and_two_rounds_without_duplicate_revisions(
    tmp_path: Path,
) -> None:
    async def scenario():
        path = tmp_path / "private" / "review.sqlite3"
        first, snapshot, job_id, task, command = await prepared(path)
        service = HumanTaskService(first, clock=lambda: NOW, new_revision_id=lambda: "r2")
        receipt = await service.respond(caller(), task.task_id, command)
        second = store_at(path)
        assert await HumanTaskService(second).respond(caller(), task.task_id, command) == receipt
        r2 = await second.read_snapshot(revision=receipt.revision)
        assert r2 is not None
        run = receipt.resumed_run
        assert run is not None
        next_task = fact_task(r2, run.run_id)
        # New revision/head is already committed and its resumed run is QUEUED.
        for store in (first, second):
            await store.register_tasks(
                job_id=job_id, principal_id=ACTOR, snapshot=r2, tasks=(next_task,)
            )
        assert [r.reference.revision_id for r in await second.list_revisions(job_id=job_id)] == [
            "r1",
            "r2",
        ]
        attempt = await second.claim(
            job_id=job_id, run_id=run.run_id, owner=uuid4(), lease_seconds=60, now=NOW
        )
        await first.finish(
            attempt, event=JobEvent.NEEDS_HUMAN, now=NOW, open_task_ids=(next_task.task_id,)
        )
        new_command = HumanResponse(
            task_id=next_task.task_id,
            expected_version=1,
            revision=r2.revision.reference,
            side_digest=next_task.side.input_digest,
            idempotency_key="second-response",
            action=ResponseAction.CONFIRM,
        )
        last = await HumanTaskService(
            second, clock=lambda: NOW, new_revision_id=lambda: "r3"
        ).respond(
            caller(),
            next_task.task_id,
            new_command,
        )
        assert last.revision.revision_id == "r3"
        fresh = store_at(path)
        history = await fresh.list_revisions(job_id=job_id)
        assert [r.reference.revision_id for r in history] == ["r1", "r2", "r3"]
        assert history[1].parent == history[0].reference
        assert history[2].parent == history[1].reference
        assert len(await fresh.pending_dispatches(now=NOW, limit=20)) == 3
        assert (await fresh.read_job(job_id=job_id)).current_run == last.resumed_run
        changed = command.model_copy(update={"action": ResponseAction.REJECT})
        with pytest.raises(ServiceFault):
            await HumanTaskService(fresh).respond(caller(), task.task_id, changed)
        with pytest.raises(ConditionFailed):
            await fresh.register_tasks(
                job_id=job_id, principal_id=ACTOR, snapshot=snapshot, tasks=(task,)
            )

    asyncio.run(scenario())


def test_cancelled_response_keeps_one_atomic_state(tmp_path: Path) -> None:
    async def scenario():
        path = tmp_path / "private" / "review.sqlite3"
        store, _snapshot, job_id, task, command = await prepared(path)
        second = store_at(path)
        outcomes = await asyncio.gather(
            HumanTaskService(store, clock=lambda: NOW, new_revision_id=lambda: "r2").respond(
                caller(), task.task_id, command
            ),
            second.cancel(job_id=job_id, now=NOW),
            return_exceptions=True,
        )
        job = await second.read_job(job_id=job_id)
        assert job.status == JobStatus.CANCELLED and job.open_task_ids == ()
        history = await second.list_revisions(job_id=job_id)
        receipt = await second.read_receipt(principal_id=ACTOR, key=command.idempotency_key)
        record = await second.read_task(task_id=task.task_id)
        if receipt is None:
            assert len(history) == 1 and record.task.state == "superseded"
            assert isinstance(outcomes[0], (ServiceFault, ConditionFailed))
            assert len(await second.pending_dispatches(now=NOW, limit=20)) == 1
        else:
            assert len(history) == 2 and record.task.state == "answered"
            assert len(await second.pending_dispatches(now=NOW, limit=20)) == 2
            assert receipt.receipt == outcomes[0]

    asyncio.run(scenario())


def test_exception_after_job_resume_rolls_back_job_tasks_revision_outbox_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario():
        path = tmp_path / "private" / "review.sqlite3"
        store, _snapshot, _job_id, task, command = await prepared(path)
        before = stored_json(path)
        resume = InMemoryJobStore.resume_after_human

        async def fail_after_resume(self, **kwargs):
            await resume(self, **kwargs)
            raise asyncio.CancelledError()

        monkeypatch.setattr(InMemoryJobStore, "resume_after_human", fail_after_resume)
        with pytest.raises(asyncio.CancelledError):
            await HumanTaskService(store, clock=lambda: NOW, new_revision_id=lambda: "r2").respond(
                caller(), task.task_id, command
            )
        assert stored_json(path) == before
        assert (await store_at(path).read_task(task_id=task.task_id)).task.state == "open"

    asyncio.run(scenario())


def test_crash_during_sql_write_recovers_previous_state_in_new_process(tmp_path: Path) -> None:
    async def scenario():
        path = tmp_path / "private" / "review.sqlite3"
        _store, _snapshot, _job_id, task, command = await prepared(path)
        before = stored_json(path)
        child = subprocess.run(
            [
                sys.executable,
                "-c",
                "import asyncio,os,sys; from pathlib import Path; from uuid import UUID; "
                "from appraisal_review.adapters.local.sqlite_review_store "
                "import SQLiteReviewStore; "
                "from appraisal_review.application.human_tasks import HumanTaskService; "
                "from appraisal_review.domain.service_contracts import HumanResponse; "
                "from tests.unit.test_human_task_service import caller; "
                "s=SQLiteReviewStore(Path(sys.argv[1])); original=SQLiteReviewStore._save; "
                'exec("def die(conn,state):\\n original(conn,state)\\n'
                ' if state.receipts: os._exit(73)"); '
                "SQLiteReviewStore._save=staticmethod(die); "
                f'service=HumanTaskService(s,clock=lambda:{NOW},new_revision_id=lambda:"r2"); '
                "asyncio.run(service.respond(caller(),UUID(sys.argv[2]),HumanResponse.model_validate_json(sys.argv[3])))",
                str(path),
                str(task.task_id),
                command.model_dump_json(),
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert child.returncode == 73, child.stderr
        fresh = store_at(path)
        assert (await fresh.read_task(task_id=task.task_id)).task.state == "open"
        assert stored_json(path) == before
        receipt = await HumanTaskService(
            fresh, clock=lambda: NOW, new_revision_id=lambda: "r2"
        ).respond(caller(), task.task_id, command)
        assert receipt.revision.revision_id == "r2"

    asyncio.run(scenario())


def test_registration_rejects_foreign_principal_revision_and_run(tmp_path: Path) -> None:
    async def scenario():
        store, snapshot, job_id, task, _command = await prepared(tmp_path / "private" / "r.sqlite3")
        with pytest.raises(ConditionFailed):
            await store.register_tasks(
                job_id=job_id, principal_id="foreign", snapshot=snapshot, tasks=(task,)
            )
        foreign = task.model_copy(update={"run": task.run.model_copy(update={"run_id": uuid4()})})
        with pytest.raises(ConditionFailed):
            await store.register_tasks(
                job_id=job_id, principal_id=ACTOR, snapshot=snapshot, tasks=(foreign,)
            )
        assert len(await store.list_tasks(job_id=job_id)) == 1

    asyncio.run(scenario())


def test_private_directory_file_and_storage_policy_are_enforced(tmp_path: Path) -> None:
    from appraisal_review.application.job_state import JobPolicy

    directory = tmp_path / "public"
    directory.mkdir(mode=0o755)
    with pytest.raises(SQLiteReviewStoreError):
        store_at(directory / "r.sqlite3")
    path = tmp_path / "private" / "r.sqlite3"
    store_at(path)
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(SQLiteReviewStoreError):
        SQLiteReviewStore(path, policy=JobPolicy(max_attempts=7))
    alias = path.with_name("alias.sqlite3")
    alias.symlink_to(path)
    with pytest.raises(SQLiteReviewStoreError):
        store_at(alias)
    alias.unlink()
    alias.hardlink_to(path)
    with pytest.raises(SQLiteReviewStoreError):
        store_at(alias)


def result_body(attempt, revision):
    from appraisal_review.domain.factor_models import WorkflowStatus
    from appraisal_review.domain.service_contracts import (
        ExecutionStatus,
        RunReference,
        ServiceResult,
    )

    return ServiceResult(
        run=RunReference(run_id=attempt.run_id, revision=revision, attempt_id=attempt.attempt_id),
        result_version=attempt.expected_result_version + 1,
        execution_status=ExecutionStatus.SUCCEEDED,
        business_status=WorkflowStatus.NEEDS_REVIEW,
        durable=True,
    )


def reference_for(attempt, result, digest):
    from appraisal_review.ports.jobs import ResultReference

    return ResultReference(
        run_id=attempt.run_id,
        result_version=result.result_version,
        fencing_token=attempt.fencing_token,
        execution_status=result.execution_status,
        business_status=result.business_status,
        artifact_status=result.artifact_status,
        result_digest=digest,
    )


async def running(store):
    job_id, run_id = uuid4(), uuid4()
    payload = submission()
    await store.create_job(principal(), payload, job_id=job_id, run_id=run_id, now=NOW)
    attempt = await store.claim(
        job_id=job_id, run_id=run_id, owner=uuid4(), lease_seconds=60, now=NOW
    )
    return payload, attempt


def test_results_are_durable_digest_bound_and_hidden_until_committed(tmp_path: Path) -> None:
    async def scenario():
        path = tmp_path / "private" / "review.sqlite3"
        first, second = store_at(path), store_at(path)
        payload, attempt = await running(first)
        body = result_body(attempt, payload.revision)
        digest = await first.results.put(run_id=attempt.run_id, result_version=1, result=body)
        ref = reference_for(attempt, body, digest)
        assert await second.results.get(run_id=attempt.run_id, result_version=1) is None
        with pytest.raises(ConditionFailed):
            await second.results.get_committed(ref)
        assert (
            await second.results.put(run_id=attempt.run_id, result_version=1, result=body) == digest
        )
        changed = body.model_copy(update={"durable": False})
        with pytest.raises(ServiceFault):
            await second.results.put(run_id=attempt.run_id, result_version=1, result=changed)
        await first.finish(attempt, event=JobEvent.PUBLISH_RESULT, now=NOW, result=ref)
        assert await second.results.get_committed(ref) == body
        child = subprocess.run(
            [
                sys.executable,
                "-c",
                "import asyncio,sys; from pathlib import Path; from uuid import UUID; "
                "from appraisal_review.adapters.local.sqlite_review_store "
                "import SQLiteReviewStore; "
                "s=SQLiteReviewStore(Path(sys.argv[1])); "
                "r=asyncio.run(s.results.get(run_id=UUID(sys.argv[2]),result_version=1)); "
                "print(r.model_dump_json())",
                str(path),
                str(attempt.run_id),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=20,
        )
        assert json.loads(child.stdout) == json.loads(body.model_dump_json())
        with sqlite3.connect(path) as connection:
            connection.execute("UPDATE review_results SET payload=?", (changed.model_dump_json(),))
        with pytest.raises(SQLiteReviewStoreError):
            await second.results.get_committed(ref)

    asyncio.run(scenario())


def test_old_attempt_candidate_cannot_poison_new_current_attempt(tmp_path: Path) -> None:
    from dataclasses import replace

    async def scenario():
        path = tmp_path / "private" / "review.sqlite3"
        clock = [NOW]
        store = SQLiteReviewStore(path, clock=lambda: clock[0])
        payload, old = await running(store)
        old_body = result_body(old, payload.revision)
        old_digest = await store.results.put(run_id=old.run_id, result_version=1, result=old_body)
        clock[0] += 60
        leases = await store.expired_leases(now=clock[0], limit=10)
        assert len(leases) == 1
        await store.expire_lease(leases[0], now=clock[0])
        new = await store.claim(
            job_id=old.job_id, run_id=old.run_id, owner=uuid4(), lease_seconds=60, now=clock[0]
        )
        assert new.fencing_token == old.fencing_token + 1
        body = result_body(new, payload.revision)
        digest = await store.results.put(run_id=new.run_id, result_version=1, result=body)
        assert digest != old_digest
        ref = reference_for(new, body, digest)
        with pytest.raises(ConditionFailed):
            await store.finish(
                old,
                event=JobEvent.PUBLISH_RESULT,
                now=clock[0],
                result=reference_for(old, old_body, old_digest),
            )
        with pytest.raises(ConditionFailed):
            await store.finish(
                new,
                event=JobEvent.PUBLISH_RESULT,
                now=clock[0],
                result=replace(ref, result_digest=old_digest),
            )
        with pytest.raises(ConditionFailed):
            await store.finish(
                replace(new, expected_result_version=8),
                event=JobEvent.PUBLISH_RESULT,
                now=clock[0],
                result=ref,
            )
        await store.finish(new, event=JobEvent.PUBLISH_RESULT, now=clock[0], result=ref)
        assert await store_at(path).results.get_committed(ref) == body
        assert (await store.read_job(job_id=new.job_id)).result_version == 1

    asyncio.run(scenario())


def test_result_put_uses_trusted_clock_and_cancellation_blocks_publication(tmp_path: Path) -> None:
    async def scenario():
        path = tmp_path / "private" / "review.sqlite3"
        clock = [NOW]
        store = SQLiteReviewStore(path, clock=lambda: clock[0])
        payload, attempt = await running(store)
        body = result_body(attempt, payload.revision)
        clock[0] += 60
        with pytest.raises(ConditionFailed):
            await store.results.put(run_id=attempt.run_id, result_version=1, result=body)
        with pytest.raises(ConditionFailed):
            await store.heartbeat(attempt, lease_seconds=60, now=clock[0])
        clock[0] = NOW
        digest = await store.results.put(run_id=attempt.run_id, result_version=1, result=body)
        await store.cancel(job_id=attempt.job_id, now=NOW)
        with pytest.raises(ConditionFailed):
            await store.finish(
                attempt,
                event=JobEvent.PUBLISH_RESULT,
                now=NOW,
                result=reference_for(attempt, body, digest),
            )
        assert await store.results.get(run_id=attempt.run_id, result_version=1) is None
        await store.finish(attempt, event=JobEvent.CANCEL_ACKNOWLEDGED, now=NOW)
        assert (await store.read_job(job_id=attempt.job_id)).status == JobStatus.CANCELLED

    asyncio.run(scenario())


def test_resumed_submission_uses_new_revision_document_refs(tmp_path: Path) -> None:
    from appraisal_review.application.service_guards import response_digest
    from appraisal_review.domain.service_contracts import AcceptedResponse, ActorReference

    async def scenario():
        path = tmp_path / "private" / "review.sqlite3"
        store, snapshot, job_id, task, command = await prepared(path)
        material = snapshot.material
        material.policy.registry.documents[0].version = "new-version"
        material.policy.identity.version = material.facts.identity.version = "r2"
        next_revision = RevisionSnapshot.capture(material, "r2", parent=snapshot.revision.reference)
        receipt = await store.commit_response(
            AcceptedResponse(command=command, actor=ActorReference(actor_id=ACTOR, kind="human")),
            task=task,
            next_revision=next_revision,
            payload_digest=response_digest(command),
            now=NOW,
        )
        next_submission = await store_at(path).read_submission(run_id=receipt.resumed_run.run_id)
        assert next_submission.revision == next_revision.revision.reference
        assert next_submission.documents == next_revision.revision.documents
        assert next_submission.documents != snapshot.revision.documents
        initial = await store.read_submission(run_id=task.run.run_id)
        assert initial.documents == snapshot.revision.documents
        assert (await store.read_job(job_id=job_id)).current_run == receipt.resumed_run

    asyncio.run(scenario())


def test_two_processes_claim_one_persisted_run(tmp_path: Path) -> None:
    async def scenario():
        path = tmp_path / "private" / "review.sqlite3"
        store = store_at(path)
        job_id, run_id = uuid4(), uuid4()
        await store.create_job(principal(), submission(), job_id=job_id, run_id=run_id, now=NOW)
        program = """
import asyncio,sys
from pathlib import Path
from uuid import UUID,uuid4
from appraisal_review.adapters.local.sqlite_review_store import SQLiteReviewStore
from appraisal_review.ports.jobs import ConditionFailed
store=SQLiteReviewStore(Path(sys.argv[1]))
print("ready",flush=True)
sys.stdin.readline()
try:
    attempt=asyncio.run(store.claim(job_id=UUID(sys.argv[2]),run_id=UUID(sys.argv[3]),
        owner=uuid4(),lease_seconds=60,now=int(sys.argv[4])))
    print("won",flush=True)
except ConditionFailed:
    print("lost",flush=True)
"""
        children = [
            subprocess.Popen(
                [sys.executable, "-c", program, str(path), str(job_id), str(run_id), str(NOW)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(2)
        ]
        try:
            for child in children:
                assert child.stdout.readline().strip() == "ready"
            for child in children:
                child.stdin.write("claim\n")
                child.stdin.flush()
            outputs = [child.communicate(timeout=20) for child in children]
            assert sorted(output[0].strip() for output in outputs) == ["lost", "won"]
            assert all(child.returncode == 0 for child in children), outputs
            assert len(stored_json(path)["attempts"]) == 1
            assert (await store.read_job(job_id=job_id)).status == JobStatus.RUNNING
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=10)

    asyncio.run(scenario())


def test_response_committed_in_child_survives_process_exit_and_replays(tmp_path: Path) -> None:
    async def scenario():
        path = tmp_path / "private" / "review.sqlite3"
        store, _snapshot, job_id, task, command = await prepared(path)
        program = """
import asyncio,sys
from pathlib import Path
from uuid import UUID
from appraisal_review.adapters.local.sqlite_review_store import SQLiteReviewStore
from appraisal_review.application.human_tasks import HumanTaskService
from appraisal_review.domain.service_contracts import HumanResponse
from tests.unit.test_human_task_service import caller
store=SQLiteReviewStore(Path(sys.argv[1]),clock=lambda:int(sys.argv[4]))
service=HumanTaskService(store,clock=lambda:int(sys.argv[4]),new_revision_id=lambda:"r2")
receipt=asyncio.run(service.respond(caller(),UUID(sys.argv[2]),
    HumanResponse.model_validate_json(sys.argv[3])))
print(receipt.model_dump_json())
"""
        child = subprocess.run(
            [
                sys.executable,
                "-c",
                program,
                str(path),
                str(task.task_id),
                command.model_dump_json(),
                str(NOW),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=20,
        )
        replay = await HumanTaskService(store, clock=lambda: NOW).respond(
            caller(), task.task_id, command
        )
        assert json.loads(child.stdout) == json.loads(replay.model_dump_json())
        history = await store.list_revisions(job_id=job_id)
        assert [r.reference.revision_id for r in history] == ["r1", "r2"]
        assert len(await store.pending_dispatches(now=NOW, limit=10)) == 2
        assert (await store.read_job(job_id=job_id)).current_run == replay.resumed_run

    asyncio.run(scenario())
