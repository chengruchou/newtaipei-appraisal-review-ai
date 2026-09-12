"""Real database recovery and concurrency with synthetic case material only."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from appraisal_review.adapters.local.review_database import ReviewTransaction, SQLiteReviewDatabase
from appraisal_review.adapters.local.sqlite_human_task_store import SQLiteHumanTaskStore
from appraisal_review.adapters.local.sqlite_job_store import SQLiteJobStore
from appraisal_review.api.app import create_app
from appraisal_review.application.human_tasks import HumanTaskService
from appraisal_review.application.job_state import JobEvent
from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.application.service_guards import (
    ServiceFault,
    admit_response,
    response_digest,
)
from appraisal_review.domain.job_contracts import JobStatus
from appraisal_review.domain.service_contracts import (
    HumanResponse,
    HumanTask,
    Permission,
    ResponseAction,
    ReviewSubmission,
    RunReference,
    ServiceErrorCode,
)
from appraisal_review.ports.jobs import ConditionFailed
from appraisal_review.testing.job_store_contract import NOW
from tests.unit.test_human_task_api import StubResolver
from tests.unit.test_human_task_service import (
    ACTOR,
    Harness,
    caller,
    confirming,
    correcting,
    correction_task,
    fact_task,
)


async def setup_case(path: Path, *, correction: bool = False, siblings: int = 0):
    harness = Harness()
    harness.jobs = SQLiteJobStore(SQLiteReviewDatabase(path))
    harness.tasks = SQLiteHumanTaskStore(harness.jobs)
    harness.service = HumanTaskService(harness.tasks, clock=lambda: NOW + 2)
    task = (correction_task if correction else fact_task)(harness.snapshot, harness.run_id)
    extra = tuple(fact_task(harness.snapshot, harness.run_id) for _ in range(siblings))
    await harness.jobs.create_job(
        caller(),
        ReviewSubmission(
            revision=harness.snapshot.revision.reference,
            documents=harness.snapshot.revision.documents,
            idempotency_key="initial-job",
        ),
        job_id=harness.job_id,
        run_id=harness.run_id,
        now=NOW,
    )
    attempt = await harness.jobs.claim(
        job_id=harness.job_id, run_id=harness.run_id, owner=uuid4(), lease_seconds=60, now=NOW
    )
    await harness.tasks.finish_with_tasks(
        attempt, snapshot=harness.snapshot, tasks=(task, *extra), now=NOW + 1
    )
    return harness, task


def reopen(path: Path):
    jobs = SQLiteJobStore(SQLiteReviewDatabase(path))
    tasks = SQLiteHumanTaskStore(jobs)
    return jobs, tasks, HumanTaskService(tasks, clock=lambda: NOW + 3)


def rows(database: SQLiteReviewDatabase):
    with database.transaction() as tx:
        return tx.connection.execute(
            "SELECT kind,key,subkey,payload FROM review_records ORDER BY rowid"
        ).fetchall()


def worker_request(path: Path, task: HumanTask, command: HumanResponse, mode: str):
    return json.dumps(
        {
            "database": str(path),
            "task_id": str(task.task_id),
            "command": command.model_dump(mode="json"),
            "mode": mode,
        }
    )


WORKER = [sys.executable, "-m", "tests.integration.sqlite_response_worker"]


@pytest.mark.parametrize("action", ["confirm", "correct", "reject"])
def test_response_and_exact_receipt_survive_reopen(tmp_path: Path, action: str) -> None:
    async def scenario() -> None:
        path = tmp_path / "review.sqlite"
        harness, task = await setup_case(path, correction=action == "correct", siblings=1)
        command = (correcting if action == "correct" else confirming)(harness, task)
        if action == "reject":
            command = command.model_copy(update={"action": ResponseAction.REJECT})
        receipt = await harness.service.respond(caller(), task.task_id, command)
        before = rows(harness.jobs.database)
        jobs, tasks, service = reopen(path)
        assert await service.respond(caller(), task.task_id, command) == receipt
        assert rows(jobs.database) == before
        assert (
            await tasks.read_receipt(principal_id=ACTOR, key=command.idempotency_key)
        ).receipt == receipt
        assert (await tasks.read_task(task_id=task.task_id)).task.state == "answered"
        assert (
            await tasks.read_snapshot(revision=harness.snapshot.revision.reference)
        ).material == harness.snapshot.material
        chain = await tasks.list_revisions(job_id=harness.job_id)
        job = await jobs.read_job(job_id=harness.job_id)
        pending = await jobs.pending_dispatches(now=NOW + 5, limit=10)
        if action == "reject":
            assert len(chain) == 1 and len(pending) == 1
            assert job.current_run.run_id == harness.run_id
            assert job.status == JobStatus.WAITING_FOR_HUMAN
            assert receipt.revision is None and receipt.resumed_run is None
        else:
            assert len(chain) == 2 and len(pending) == 2
            assert chain[1].parent == chain[0].reference
            assert job.current_run == receipt.resumed_run
            assert job.status == JobStatus.QUEUED and not job.open_task_ids
            assert len(receipt.superseded_task_ids) == 1
            if action == "correct":
                assert chain[1].changes[0].corrected_by.actor_id == ACTOR
            assert len([r for r in before if r[0] == "response"]) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "boundary", ["task", "material", "revision_chain", "head", "run", "outbox", "response"]
)
@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
def test_every_response_write_rolls_back(tmp_path: Path, monkeypatch, boundary, failure) -> None:
    async def scenario() -> None:
        path = tmp_path / "review.sqlite"
        harness, task = await setup_case(path, siblings=1)
        before = rows(harness.jobs.database)
        original = ReviewTransaction.put

        def fail_after_write(self, kind, key, value, subkey="", *, insert=False):
            original(self, kind, key, value, subkey, insert=insert)
            if kind == boundary:
                raise failure("injected transaction interruption")

        with monkeypatch.context() as patch:
            patch.setattr(ReviewTransaction, "put", fail_after_write)
            with pytest.raises(failure):
                await harness.service.respond(caller(), task.task_id, confirming(harness, task))
        jobs, tasks, service = reopen(path)
        assert rows(jobs.database) == before
        assert await tasks.read_receipt(principal_id=ACTOR, key="k1") is None
        result = await service.respond(caller(), task.task_id, confirming(harness, task))
        assert result.resumed_run is not None
        assert len(await tasks.list_revisions(job_id=harness.job_id)) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "mode,exit_code,committed",
    [
        ("crash_during_commit", 91, False),
        ("crash_after_commit", 92, True),
    ],
)
def test_process_crash_and_unknown_outcome_recover(tmp_path, mode, exit_code, committed) -> None:
    async def scenario() -> None:
        path = tmp_path / "review.sqlite"
        harness, task = await setup_case(path)
        command = confirming(harness, task)
        process = subprocess.run(
            WORKER,
            input=worker_request(path, task, command, mode),
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
        assert process.returncode == exit_code, process.stderr
        jobs, tasks, service = reopen(path)
        lookup = await tasks.read_receipt(principal_id=ACTOR, key=command.idempotency_key)
        assert (lookup is not None) == committed
        assert len(await tasks.list_revisions(job_id=harness.job_id)) == (2 if committed else 1)
        recovered = await service.respond(caller(), task.task_id, command)
        if lookup is not None:
            assert recovered == lookup.receipt
        assert len(await tasks.list_revisions(job_id=harness.job_id)) == 2
        assert len(await jobs.pending_dispatches(now=NOW + 5, limit=10)) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("same_key", [True, False])
def test_two_processes_commit_only_one_revision(tmp_path: Path, same_key: bool) -> None:
    async def scenario() -> None:
        path = tmp_path / "review.sqlite"
        harness, task = await setup_case(path)
        commands = [
            confirming(harness, task, key="k1"),
            confirming(harness, task, key="k1" if same_key else "k2"),
        ]
        processes = [
            subprocess.Popen(
                WORKER,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in commands
        ]
        try:
            for process, command in zip(processes, commands, strict=True):
                process.stdin.write(worker_request(path, task, command, "commit"))
                process.stdin.close()
                process.stdin = None
            outputs = [process.communicate(timeout=15) for process in processes]
            assert all(process.returncode == 0 for process in processes), outputs
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
        values = [json.loads(stdout) for stdout, _ in outputs]
        if same_key:
            assert values[0] == values[1] and "receipt" in values[0]
        else:
            assert sum("receipt" in value for value in values) == 1
            assert {value["error"] for value in values if "error" in value} == {"version_conflict"}
        _, tasks, _ = reopen(path)
        assert len(await tasks.list_revisions(job_id=harness.job_id)) == 2

    asyncio.run(scenario())


def test_replay_payload_and_current_authority_still_apply(tmp_path: Path) -> None:
    async def scenario() -> None:
        path = tmp_path / "review.sqlite"
        harness, task = await setup_case(path, correction=True)
        command = correcting(harness, task)
        receipt = await harness.service.respond(caller(), task.task_id, command)
        _, tasks, service = reopen(path)
        with pytest.raises(ServiceFault) as error:
            await service.respond(caller(), task.task_id, correcting(harness, task, metres=13))
        assert error.value.problem.code == ServiceErrorCode.CONFLICT
        denied = replace(caller(), permissions=frozenset({Permission.REVIEW}))
        with pytest.raises(ServiceFault) as error:
            await service.respond(denied, task.task_id, command)
        assert error.value.problem.code == ServiceErrorCode.UNAUTHORIZED
        assert await service.respond(caller(), task.task_id, command) == receipt
        assert len(await tasks.list_revisions(job_id=harness.job_id)) == 2

    asyncio.run(scenario())


def test_failed_worker_registration_does_not_leave_waiting_job(tmp_path: Path) -> None:
    async def scenario() -> None:
        harness, task = await setup_case(tmp_path / "review.sqlite")
        receipt = await harness.service.respond(caller(), task.task_id, confirming(harness, task))
        attempt = await harness.jobs.claim(
            job_id=harness.job_id,
            run_id=receipt.resumed_run.run_id,
            owner=uuid4(),
            lease_seconds=60,
            now=NOW + 3,
        )
        before = rows(harness.jobs.database)
        with pytest.raises(ConditionFailed):
            await harness.tasks.finish_with_tasks(
                attempt,
                snapshot=harness.snapshot,
                tasks=(fact_task(harness.snapshot, attempt.run_id),),
                now=NOW + 4,
            )
        assert rows(harness.jobs.database) == before
        current = await harness.tasks.read_snapshot(revision=receipt.revision)
        next_task = fact_task(current, attempt.run_id)
        await harness.tasks.finish_with_tasks(
            attempt, snapshot=current, tasks=(next_task,), now=NOW + 4
        )
        assert (await harness.tasks.read_task(task_id=next_task.task_id)).task == next_task

    asyncio.run(scenario())


def test_revision_identity_and_snapshot_digest_cannot_be_replaced(tmp_path: Path) -> None:
    async def scenario() -> None:
        harness, task = await setup_case(tmp_path / "review.sqlite")
        altered = harness.snapshot.material
        altered.facts.pairs[0].pair.target.raw_text = "different synthetic material"
        changed = RevisionSnapshot.capture(altered, "r1")
        before = rows(harness.jobs.database)
        with pytest.raises(ConditionFailed):
            await harness.tasks.register_snapshot(
                job_id=harness.job_id, principal_id=ACTOR, snapshot=changed, tasks=(task,)
            )
        assert rows(harness.jobs.database) == before
        assert await harness.tasks.read_snapshot(revision=changed.revision.reference) is None

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "invalid",
    [
        "payload_digest",
        "revision",
        "side_digest",
        "version",
        "reject_with_revision",
        "confirm_without_revision",
        "stale_job_run",
    ],
)
def test_store_rechecks_response_before_any_committed_effect(tmp_path, invalid) -> None:
    async def scenario() -> None:
        harness, task = await setup_case(tmp_path / "review.sqlite")
        command = confirming(harness, task)
        accepted = admit_response(
            task, command, caller(), current=harness.snapshot.revision.reference
        )
        record = await harness.tasks.read_task(task_id=task.task_id)
        child = await harness.service._next_revision(record, accepted)
        if invalid == "revision":
            command = command.model_copy(
                update={
                    "revision": command.revision.model_copy(update={"material_digest": "f" * 64})
                }
            )
        elif invalid == "side_digest":
            command = command.model_copy(update={"side_digest": "f" * 64})
        elif invalid == "version":
            command = command.model_copy(update={"expected_version": 9})
        elif invalid == "reject_with_revision":
            command = command.model_copy(update={"action": ResponseAction.REJECT})
        elif invalid == "confirm_without_revision":
            child = None
        elif invalid == "stale_job_run":
            other = harness.snapshot.revise(harness.snapshot.material, "other-revision")
            run = RunReference(run_id=uuid4(), revision=other.revision.reference)
            await harness.jobs.resume_after_human(job_id=harness.job_id, run=run, now=NOW + 2)
            attempt = await harness.jobs.claim(
                job_id=harness.job_id,
                run_id=run.run_id,
                owner=uuid4(),
                lease_seconds=60,
                now=NOW + 3,
            )
            await harness.jobs.finish(
                attempt, event=JobEvent.NEEDS_HUMAN, open_task_ids=(task.task_id,), now=NOW + 4
            )
        accepted = accepted.model_copy(update={"command": command})
        before = rows(harness.jobs.database)
        with pytest.raises((ConditionFailed, ServiceFault)):
            await harness.tasks.commit_response(
                accepted,
                task=task,
                next_revision=child,
                payload_digest="f" * 64
                if invalid == "payload_digest"
                else response_digest(command),
                now=NOW + 5,
            )
        assert rows(harness.jobs.database) == before
        assert (
            await harness.tasks.read_receipt(principal_id=ACTOR, key=command.idempotency_key)
            is None
        )

    asyncio.run(scenario())


def test_only_task_rejection_persists_failed_without_continuation(tmp_path) -> None:
    async def scenario() -> None:
        harness, task = await setup_case(tmp_path / "review.sqlite")
        command = confirming(harness, task).model_copy(update={"action": ResponseAction.REJECT})
        receipt = await harness.service.respond(caller(), task.task_id, command)
        jobs, tasks, service = reopen(tmp_path / "review.sqlite")
        assert receipt.job_status == JobStatus.FAILED and receipt.resumed_run is None
        assert (await jobs.read_job(job_id=harness.job_id)).status == JobStatus.FAILED
        assert len(await tasks.list_revisions(job_id=harness.job_id)) == 1
        assert len(await jobs.pending_dispatches(now=NOW + 3, limit=10)) == 1
        assert await service.respond(caller(), task.task_id, command) == receipt

    asyncio.run(scenario())


def test_http_response_recovery_after_replacing_application_instances(tmp_path: Path) -> None:
    async def scenario() -> None:
        path = tmp_path / "review.sqlite"
        harness, task = await setup_case(path, correction=True)
        command = correcting(harness, task)
        route = f"/v1/review-tasks/{task.task_id}/responses"
        resolver = StubResolver(caller())
        first_app = create_app(human_task_service=harness.service, principal_resolver=resolver)
        async with AsyncClient(
            transport=ASGITransport(app=first_app), base_url="http://service"
        ) as client:
            response = await client.post(route, json=command.model_dump(mode="json"))
            assert response.status_code == 200
            receipt = response.json()
        _, tasks, service = reopen(path)
        next_app = create_app(human_task_service=service, principal_resolver=resolver)
        async with AsyncClient(
            transport=ASGITransport(app=next_app), base_url="http://service"
        ) as client:
            recovered = await client.post(route, json=command.model_dump(mode="json"))
            assert recovered.status_code == 200 and recovered.json() == receipt
            history = await client.get(f"/v1/review-jobs/{harness.job_id}/revisions")
            assert history.status_code == 200 and len(history.json()["revisions"]) == 2
        assert len(await tasks.list_revisions(job_id=harness.job_id)) == 2

    asyncio.run(scenario())


def test_worker_material_must_match_fixed_submission_documents(tmp_path: Path) -> None:
    async def scenario() -> None:
        harness = Harness()
        jobs = SQLiteJobStore(SQLiteReviewDatabase(tmp_path / "review.sqlite"))
        tasks = SQLiteHumanTaskStore(jobs)
        documents = harness.snapshot.revision.documents
        wrong = documents[0].model_copy(update={"version": "unrelated-version"})
        await jobs.create_job(
            caller(),
            ReviewSubmission(
                revision=harness.snapshot.revision.reference,
                documents=(wrong, *documents[1:]),
                idempotency_key="mismatched-sources",
            ),
            job_id=harness.job_id,
            run_id=harness.run_id,
            now=NOW,
        )
        attempt = await jobs.claim(
            job_id=harness.job_id, run_id=harness.run_id, owner=uuid4(), lease_seconds=60, now=NOW
        )
        before = rows(jobs.database)
        with pytest.raises(ConditionFailed):
            await tasks.finish_with_tasks(
                attempt,
                snapshot=harness.snapshot,
                tasks=(fact_task(harness.snapshot, harness.run_id),),
                now=NOW + 1,
            )
        assert rows(jobs.database) == before
        assert await tasks.list_tasks(job_id=harness.job_id) == ()

    asyncio.run(scenario())
