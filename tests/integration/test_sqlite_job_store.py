"""Run the unchanged job contract against independent SQLite transactions."""

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from appraisal_review.adapters.local.review_database import SQLiteReviewDatabase
from appraisal_review.adapters.local.sqlite_job_store import SQLiteJobStore, SQLiteResultStore
from appraisal_review.application.job_state import JobEvent
from appraisal_review.application.outbox import OutboxDispatcher
from appraisal_review.application.review_jobs import ReviewJobService
from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.domain.service_contracts import ExecutionStatus, ServiceResult
from appraisal_review.ports.jobs import ConditionFailed
from appraisal_review.testing.job_store_contract import (
    CONTRACT_CHECKS,
    NOW,
    JobStoreContract,
    principal,
    submission,
)
from tests.unit.test_job_failure_injection import published_result


@pytest.mark.parametrize("check", CONTRACT_CHECKS)
def test_sqlite_store_satisfies_job_contract(tmp_path: Path, check: str) -> None:
    store = SQLiteJobStore(SQLiteReviewDatabase(tmp_path / "review.sqlite"))
    asyncio.run(getattr(JobStoreContract(), check)(store))


def test_service_dispatch_result_and_authorized_read_survive_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        path = tmp_path / "review.sqlite"
        db = SQLiteReviewDatabase(path)
        service = ReviewJobService(SQLiteJobStore(db), SQLiteResultStore(db), clock=lambda: NOW)
        outcome = await service.submit(principal(), submission())
        job_id = outcome.acceptance.job.job_id
        run_id = outcome.acceptance.run.run_id
        reopened = SQLiteReviewDatabase(path)
        next_service = ReviewJobService(
            SQLiteJobStore(reopened), SQLiteResultStore(reopened), clock=lambda: NOW + 1
        )
        delivered = []

        async def send(message):
            delivered.append(message)

        dispatcher = OutboxDispatcher(next_service, send)
        assert (await dispatcher.run_once()).sent == 1
        assert (await dispatcher.run_once()).sent == 0
        attempt = await next_service.claim(job_id=job_id, run_id=run_id, owner=uuid4())
        result = published_result(run_id)
        await next_service.publish(attempt, result)
        final_db = SQLiteReviewDatabase(path)
        final = ReviewJobService(
            SQLiteJobStore(final_db), SQLiteResultStore(final_db), clock=lambda: NOW + 2
        )
        assert await final.result(principal(), job_id) == result
        with pytest.raises(ServiceFault):
            await final.result(principal("another-reviewer"), job_id)
        assert len(delivered) == 1

    asyncio.run(scenario())


def test_result_bodies_are_append_only_and_revalidated_after_reopen(tmp_path: Path) -> None:
    async def scenario() -> None:
        path = tmp_path / "review.sqlite"
        store = SQLiteResultStore(SQLiteReviewDatabase(path))
        run_id = uuid4()
        result = published_result(run_id)
        digest = await store.put(run_id=run_id, result_version=1, result=result)
        reopened = SQLiteResultStore(SQLiteReviewDatabase(path))
        assert await reopened.put(run_id=run_id, result_version=1, result=result) == digest
        assert await reopened.get(run_id=run_id, result_version=1) == result
        different = ServiceResult(
            run=result.run, result_version=1, execution_status=ExecutionStatus.QUEUED
        )
        with pytest.raises(ServiceFault):
            await reopened.put(run_id=run_id, result_version=1, result=different)
        with pytest.raises(ServiceFault):
            await reopened.put(run_id=uuid4(), result_version=1, result=result)
        assert await reopened.get(run_id=run_id, result_version=1) == result

    asyncio.run(scenario())


def test_expired_lease_cannot_write_even_before_reclaimer_runs(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = SQLiteJobStore(SQLiteReviewDatabase(tmp_path / "review.sqlite"))
        job, _ = await store.create_job(
            principal(), submission(), job_id=uuid4(), run_id=uuid4(), now=NOW
        )
        attempt = await store.claim(
            job_id=job.job_id,
            run_id=job.current_run.run_id,
            owner=uuid4(),
            lease_seconds=2,
            now=NOW,
        )
        with pytest.raises(ConditionFailed):
            await store.heartbeat(attempt, lease_seconds=2, now=NOW + 2)
        with pytest.raises(ConditionFailed):
            await store.finish(
                attempt, event=JobEvent.NEEDS_HUMAN, open_task_ids=(uuid4(),), now=NOW + 2
            )
        leases = await store.expired_leases(now=NOW + 2, limit=10)
        await store.expire_lease(leases[0], now=NOW + 2)
        renewed = await store.claim(
            job_id=job.job_id,
            run_id=job.current_run.run_id,
            owner=uuid4(),
            lease_seconds=10,
            now=NOW + 3,
        )
        with pytest.raises(ConditionFailed):
            await store.finish(
                attempt, event=JobEvent.NEEDS_HUMAN, open_task_ids=(uuid4(),), now=NOW + 4
            )
        assert renewed.fencing_token > attempt.fencing_token

    asyncio.run(scenario())
