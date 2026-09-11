"""The store's own conditional writes, driven directly rather than through the service.

The service already refuses a stale version, a foreign actor and a replayed key, so going
through it can never show whether the store rechecks any of them. It has to: the service's
decision is made from a read that another writer may already have invalidated, which is the
entire reason `commit_response` rechecks instead of trusting what it is handed.

These are the conditions a DynamoDB adapter must express as one TransactWriteItems. When a
second implementation exists they should move into a shared conformance suite the way
testing/job_store_contract.py did; one implementation does not yet justify the abstraction.
"""

from __future__ import annotations

import asyncio
from typing import cast
from uuid import UUID, uuid4

import pytest

from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.application.service_guards import ServiceFault, response_digest
from appraisal_review.domain.confidence import confirm_side
from appraisal_review.domain.job_contracts import JobStatus
from appraisal_review.domain.service_contracts import (
    AcceptedResponse,
    ActorReference,
    HumanResponse,
    HumanTask,
    ResponseAction,
    RevisionReference,
    RunReference,
    ServiceErrorCode,
)
from appraisal_review.ports.jobs import ConditionFailed, JobRecord, JobStore
from appraisal_review.testing.job_store_contract import NOW, digest
from tests.unit.test_human_task_service import ACTOR, Harness, caller, confirming, fact_task


def accepted_by(command: HumanResponse, actor_id: str = ACTOR) -> AcceptedResponse:
    """Build the admission record directly: the store sits below admission, not behind it."""
    return AcceptedResponse(command=command, actor=ActorReference(actor_id=actor_id, kind="human"))


def next_revision(harness: Harness, revision_id: str = "r2") -> RevisionSnapshot:
    """Derive the revision a confirmation commits, the same way the service would."""
    material = harness.snapshot.material
    confirm_side(material.facts.pairs[0], "target", reviewer=ACTOR)
    material.policy.identity.version = material.facts.identity.version = revision_id
    return RevisionSnapshot.capture(
        material, revision_id, parent=harness.snapshot.revision.reference
    )


async def prepared() -> tuple[Harness, HumanTask]:
    harness = Harness()
    task = fact_task(harness.snapshot, harness.run_id)
    await harness.setup((task,), principal=caller())
    return harness, task


async def commit(
    harness: Harness,
    task: HumanTask,
    command: HumanResponse,
    *,
    actor_id: str = ACTOR,
    revision: RevisionSnapshot | None = None,
) -> object:
    return await harness.tasks.commit_response(
        accepted_by(command, actor_id),
        task=task,
        next_revision=revision if revision is not None else next_revision(harness),
        payload_digest=response_digest(command),
        now=NOW,
    )


def test_a_stale_expected_version_is_refused_by_the_store() -> None:
    async def scenario() -> None:
        harness, task = await prepared()
        command = confirming(harness, task).model_copy(update={"expected_version": 2})

        with pytest.raises(ConditionFailed):
            await commit(harness, task, command)

        assert len(await harness.tasks.list_revisions(job_id=harness.job_id)) == 1

    asyncio.run(scenario())


def test_an_answered_task_is_refused_by_the_store() -> None:
    async def scenario() -> None:
        harness, task = await prepared()
        await commit(harness, task, confirming(harness, task, key="first"))

        # The same version is presented again, as a duplicate delivery would.
        with pytest.raises(ConditionFailed):
            await commit(harness, task, confirming(harness, task, key="second"))

        assert len(await harness.tasks.list_revisions(job_id=harness.job_id)) == 2

    asyncio.run(scenario())


def test_an_actor_who_does_not_own_the_task_is_refused_by_the_store() -> None:
    async def scenario() -> None:
        harness, task = await prepared()

        with pytest.raises(ConditionFailed):
            await commit(harness, task, confirming(harness, task), actor_id="someone-else")

        assert len(await harness.tasks.list_revisions(job_id=harness.job_id)) == 1

    asyncio.run(scenario())


def test_a_revision_whose_parent_is_not_the_head_is_refused_by_the_store() -> None:
    async def scenario() -> None:
        harness, task = await prepared()
        detached = RevisionSnapshot.capture(
            harness.snapshot.material,
            "r9",
            parent=RevisionReference(
                case_id=harness.snapshot.revision.reference.case_id,
                revision_id="not-the-head",
                material_digest=digest("f"),
            ),
        )

        with pytest.raises(ConditionFailed):
            await commit(harness, task, confirming(harness, task), revision=detached)

        assert len(await harness.tasks.list_revisions(job_id=harness.job_id)) == 1

    asyncio.run(scenario())


def test_the_store_replays_a_consumed_key_without_committing_again() -> None:
    async def scenario() -> None:
        harness, task = await prepared()
        command = confirming(harness, task, key="same")

        first = await commit(harness, task, command)
        again = await commit(harness, task, command)

        assert first == again
        assert len(await harness.tasks.list_revisions(job_id=harness.job_id)) == 2

    asyncio.run(scenario())


def test_the_store_conflicts_on_one_key_carrying_two_payloads() -> None:
    async def scenario() -> None:
        harness, task = await prepared()
        await commit(harness, task, confirming(harness, task, key="k"))
        changed = confirming(harness, task, key="k").model_copy(
            update={"action": ResponseAction.REJECT}
        )

        with pytest.raises(ServiceFault) as refused:
            await commit(harness, task, changed)

        assert refused.value.problem.code == ServiceErrorCode.CONFLICT

    asyncio.run(scenario())


class RefusingJobs:
    """A job plane that accepts nothing, to show the task write does not survive alone."""

    def __init__(self, inner: JobStore) -> None:
        self.inner = inner

    async def resume_after_human(self, *, job_id: UUID, run: RunReference, now: int) -> JobRecord:
        raise ConditionFailed("The job moved on")

    async def read_job(self, *, job_id: UUID) -> JobRecord | None:
        return await self.inner.read_job(job_id=job_id)


def test_a_refused_resume_leaves_no_task_revision_or_head_change() -> None:
    async def scenario() -> None:
        harness, task = await prepared()
        before_tasks = await harness.tasks.list_tasks(job_id=harness.job_id)
        before_head = before_tasks[0].current_revision
        harness.tasks._jobs = cast(JobStore, RefusingJobs(harness.jobs))

        with pytest.raises(ConditionFailed):
            await commit(harness, task, confirming(harness, task))

        after = await harness.tasks.list_tasks(job_id=harness.job_id)
        assert len(await harness.tasks.list_revisions(job_id=harness.job_id)) == 1
        assert after[0].current_revision == before_head
        # A task marked answered here would be unanswerable forever: its response was
        # never scheduled, so nothing would ever act on it.
        assert after[0].task.state == "open"
        assert after[0].task.version == before_tasks[0].task.version
        assert await harness.tasks.read_receipt(principal_id=ACTOR, key="k1") is None

    asyncio.run(scenario())


def test_a_rejection_reports_the_jobs_reconciled_blocked_status() -> None:
    async def scenario() -> None:
        harness, task = await prepared()
        command = confirming(harness, task).model_copy(update={"action": ResponseAction.REJECT})

        receipt = await harness.tasks.commit_response(
            accepted_by(command),
            task=task,
            next_revision=None,
            payload_digest=response_digest(command),
            now=NOW,
        )

        assert receipt.job_status == JobStatus.FAILED
        job = await harness.jobs.read_job(job_id=harness.job_id)
        assert job is not None and job.open_task_ids == () and job.problem is not None
        assert receipt.revision is None and receipt.resumed_run is None
        assert len(await harness.tasks.list_revisions(job_id=harness.job_id)) == 1

    asyncio.run(scenario())


def test_a_superseded_sibling_is_recorded_on_the_receipt() -> None:
    async def scenario() -> None:
        harness = Harness()
        answered = fact_task(harness.snapshot, harness.run_id)
        sibling = fact_task(harness.snapshot, harness.run_id, task_id=uuid4())
        await harness.setup((answered, sibling), principal=caller())

        receipt = await commit(harness, answered, confirming(harness, answered))

        assert receipt.superseded_task_ids == (sibling.task_id,)  # type: ignore[attr-defined]
        listed = {
            r.task.task_id: r.task.state
            for r in await harness.tasks.list_tasks(job_id=harness.job_id)
        }
        assert listed[answered.task_id] == "answered"
        assert listed[sibling.task_id] == "superseded"

    asyncio.run(scenario())


def test_a_task_that_is_no_longer_open_is_refused_at_its_own_version() -> None:
    """State is checked in its own right, not merely implied by the version counter.

    This adapter bumps the version whenever it changes the state, so the version check
    would mask this one. A store that tracked state separately, or superseded a task
    without touching its counter, would answer a closed question without this condition.
    """

    async def scenario() -> None:
        harness = Harness()
        open_task = fact_task(harness.snapshot, harness.run_id)
        closed = HumanTask.model_validate(
            fact_task(harness.snapshot, harness.run_id, task_id=uuid4()).model_dump(mode="json")
            | {"state": "superseded"}
        )
        await harness.setup((open_task, closed), principal=caller())

        with pytest.raises(ConditionFailed):
            await commit(harness, closed, confirming(harness, closed))

        assert len(await harness.tasks.list_revisions(job_id=harness.job_id)) == 1

    asyncio.run(scenario())
