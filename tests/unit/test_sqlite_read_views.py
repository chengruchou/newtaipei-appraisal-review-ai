"""Read-only durability regressions using synthetic unit material, not real acceptance."""

import asyncio
import json
from contextlib import closing

import pytest

from appraisal_review.adapters.local.sqlite_review_store import SQLiteReviewStoreError
from appraisal_review.application.human_tasks import HumanTaskService
from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.domain.service_contracts import ServiceErrorCode
from tests.unit.test_human_task_service import caller
from tests.unit.test_sqlite_review_store import NOW, prepared


def test_all_read_views_preserve_committed_database_bytes(tmp_path):
    async def scenario():
        store, snapshot, job_id, task, _ = await prepared(tmp_path / "private" / "state.sqlite")
        before = store.path.read_bytes()
        assert await store.read_job(job_id=job_id)
        assert await store.read_task(task_id=task.task_id)
        assert await store.list_tasks(job_id=job_id)
        assert await store.list_revisions(job_id=job_id)
        assert await store.read_snapshot(revision=snapshot.revision.reference) == snapshot
        assert await store.read_receipt(principal_id=caller().actor.actor_id, key="missing") is None
        await store.pending_dispatches(now=NOW, limit=10)
        await store.expired_leases(now=NOW, limit=10)
        await store.stranded_retryables(stranded_before=NOW, limit=10)
        await store.read_submission(run_id=task.run.run_id)
        await store.read_result_reference(run_id=task.run.run_id, result_version=1)
        assert store.path.read_bytes() == before

    asyncio.run(scenario())


def test_cached_canonical_snapshot_is_detached_and_changed_content_is_revalidated(tmp_path):
    async def scenario():
        store, snapshot, _, _, _ = await prepared(tmp_path / "private" / "state.sqlite")
        first = await store.read_snapshot(revision=snapshot.revision.reference)
        changed = first.material
        changed.facts.pairs[0].pair.target.confidence = 0
        assert (
            await store.read_snapshot(revision=snapshot.revision.reference)
        ).material == snapshot.material
        with closing(store._connect()) as connection:
            raw = json.loads(connection.execute("SELECT payload FROM review_state").fetchone()[0])
            raw["snapshots"][0]["material"]["facts"]["pairs"][0]["pair"]["target"]["confidence"] = 0
            connection.execute("UPDATE review_state SET payload=?", (json.dumps(raw),))
        with pytest.raises(SQLiteReviewStoreError, match="snapshot"):
            await store.read_snapshot(revision=snapshot.revision.reference)

    asyncio.run(scenario())


def test_current_response_authority_failure_rolls_back_every_effect(tmp_path):
    async def scenario():
        store, snapshot, job_id, task, command = await prepared(
            tmp_path / "private" / "state.sqlite"
        )
        before = store.path.read_bytes()
        checked = []

        def expired(accepted, current_task):
            checked.append((accepted.command, current_task))
            raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)

        store.response_authority = expired
        service = HumanTaskService(store, clock=lambda: NOW)
        with pytest.raises(ServiceFault) as failure:
            await service.respond(caller(), task.task_id, command)
        assert failure.value.problem.code == ServiceErrorCode.UNAUTHORIZED
        assert checked == [(command, task)]
        assert (
            await store.read_receipt(
                principal_id=caller().actor.actor_id, key=command.idempotency_key
            )
            is None
        )
        assert (
            await store.read_job(job_id=job_id)
        ).current_run.revision == snapshot.revision.reference
        assert (await store.read_task(task_id=task.task_id)).task.state == "open"
        assert store.path.read_bytes() == before

    asyncio.run(scenario())


def test_committed_result_reads_do_not_rewrite_database(tmp_path):
    from appraisal_review.application.job_state import JobEvent
    from tests.unit.test_sqlite_review_store import reference_for, result_body, running, store_at

    async def scenario():
        store = store_at(tmp_path / "private" / "state.sqlite")
        payload, attempt = await running(store)
        body = result_body(attempt, payload.revision)
        digest = await store.results.put(run_id=attempt.run_id, result_version=1, result=body)
        ref = reference_for(attempt, body, digest)
        await store.finish(attempt, event=JobEvent.PUBLISH_RESULT, now=NOW, result=ref)
        before = store.path.read_bytes()
        assert await store.results.get(run_id=attempt.run_id, result_version=1) == body
        assert await store.results.get_committed(ref) == body
        assert store.path.read_bytes() == before

    asyncio.run(scenario())


def test_warm_payload_cache_does_not_share_mutable_job_task_or_revision_state(tmp_path):
    async def scenario():
        store, snapshot, job_id, task, _ = await prepared(tmp_path / "private" / "state.sqlite")
        first = await store.read_task(task_id=task.task_id)
        # Even bypassing a returned frozen model must not mutate a cached state.
        object.__setattr__(first.task, "version", 999)
        object.__setattr__(first.current_revision, "revision_id", "mutated-detached-read")
        first_snapshot = await store.read_snapshot(revision=snapshot.revision.reference)
        first_snapshot.material.policy.identity.district = "mutated-detached-material"
        assert (await store.read_task(task_id=task.task_id)).task.version == task.version
        assert (
            await store.read_task(task_id=task.task_id)
        ).current_revision == snapshot.revision.reference
        assert (
            await store.read_snapshot(revision=snapshot.revision.reference)
        ).material == snapshot.material
        # A real persisted change must invalidate cached metadata, without a new store.
        with closing(store._connect()) as connection:
            raw = json.loads(connection.execute("SELECT payload FROM review_state").fetchone()[0])
            raw["tasks"][0]["task"]["state"] = "superseded"
            raw["tasks"][0]["task"]["version"] += 1
            connection.execute("UPDATE review_state SET payload=?", (json.dumps(raw),))
        current = await store.read_task(task_id=task.task_id)
        assert current.task.state == "superseded"
        assert current.task.version == task.version + 1
        assert await store.read_job(job_id=job_id)

    asyncio.run(scenario())


def test_cached_payload_still_checks_live_store_policy(tmp_path):
    from appraisal_review.application.job_state import JobPolicy

    async def scenario():
        store, _, job_id, _, _ = await prepared(tmp_path / "private" / "state.sqlite")
        assert await store.read_job(job_id=job_id)
        store.policy = JobPolicy(max_attempts=store.policy.max_attempts + 1)
        with pytest.raises(SQLiteReviewStoreError, match="policy differs"):
            await store.read_job(job_id=job_id)

    asyncio.run(scenario())
