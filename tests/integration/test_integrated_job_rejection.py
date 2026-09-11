"""Human rejection projection through the actual conditional database adapter."""

import asyncio
from uuid import uuid4

import pytest

from appraisal_review.application.job_state import JobEvent
from appraisal_review.domain.job_contracts import JobStatus
from appraisal_review.ports.jobs import ConditionFailed
from appraisal_review.testing.job_store_contract import NOW
from tests.integration.test_dynamodb_job_store import claim, create, db, store

__all__ = ["db"]


@pytest.mark.parametrize("remaining", [0, 1])
def test_rejection_projection_is_persistent_and_conditional(db, remaining):
    async def scenario():
        adapter = store(db)
        job = await create(adapter)
        attempt = await claim(adapter, job)
        task = uuid4()
        others = tuple(uuid4() for _ in range(remaining))
        await adapter.finish(
            attempt, event=JobEvent.NEEDS_HUMAN, open_task_ids=(task, *others), now=NOW
        )
        rejected = await adapter.reject_human_task(
            job_id=job.job_id,
            run_id=attempt.run_id,
            task_id=task,
            now=NOW + 1,
        )
        assert rejected.open_task_ids == others
        assert rejected.status == (JobStatus.WAITING_FOR_HUMAN if remaining else JobStatus.FAILED)
        assert await store(db).read_job(job_id=job.job_id) == rejected
        with pytest.raises(ConditionFailed):
            await adapter.reject_human_task(
                job_id=job.job_id,
                run_id=attempt.run_id,
                task_id=task,
                now=NOW + 2,
            )
        assert await store(db).read_job(job_id=job.job_id) == rejected

    asyncio.run(scenario())
