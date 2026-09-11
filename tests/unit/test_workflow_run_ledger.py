"""Shared reservation contract for memory and SQLite; subprocess tests are separate."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4

import pytest

from appraisal_review.adapters.local.sqlite_workflow_run_ledger import SqliteWorkflowRunLedger
from appraisal_review.adapters.local.workflow_run_ledger import NonDurableInMemoryWorkflowRunLedger
from appraisal_review.domain.service_contracts import (
    BoundedWorkflowResult,
    Budget,
    RevisionReference,
    RunReference,
    WorkflowTermination,
)
from appraisal_review.ports.workflow_run_ledger import (
    WorkflowRunReservation,
    WorkflowRunUnavailable,
)


@pytest.fixture(params=["memory", "sqlite"])
def ledger(request, tmp_path):
    if request.param == "sqlite":
        return SqliteWorkflowRunLedger(tmp_path / "workflow.sqlite3")
    return NonDurableInMemoryWorkflowRunLedger()


def run_reference():
    return RunReference(
        run_id=uuid4(),
        revision=RevisionReference(case_id="synthetic", revision_id="r1", material_digest="a" * 64),
    )


def budget():
    return Budget(steps_remaining=3, model_calls_remaining=1, retries_remaining=0)


def test_atomic_admission_across_real_threads_and_event_loops(ledger):
    run = run_reference()
    gate = threading.Barrier(8)

    def attempt():
        gate.wait(timeout=5)
        try:
            return asyncio.run(ledger.acquire(run, budget()))
        except WorkflowRunUnavailable:
            return None

    with ThreadPoolExecutor(max_workers=8) as executor:
        owners = list(executor.map(lambda _: attempt(), range(8)))
    assert sum(isinstance(owner, WorkflowRunReservation) for owner in owners) == 1
    assert owners.count(None) == 7


def test_release_retains_budget_and_fences_previous_owner(ledger):
    async def scenario():
        run = run_reference()
        owner = await ledger.acquire(run, budget())
        spent = budget().model_copy(update={"model_calls_remaining": 0})
        await ledger.checkpoint(owner, spent)
        await ledger.release(owner)
        replacement = await ledger.acquire(run, budget())
        assert await ledger.remaining(replacement) == spent
        assert replacement.token != owner.token
        with pytest.raises(WorkflowRunUnavailable):
            await ledger.checkpoint(replacement, budget())
        with pytest.raises(WorkflowRunUnavailable):
            await ledger.checkpoint(owner, spent)
        with pytest.raises(WorkflowRunUnavailable):
            await ledger.release(owner)
        with pytest.raises(WorkflowRunUnavailable):
            await ledger.checkpoint(replace(replacement, token=uuid4()), spent)
        assert await ledger.remaining(replacement) == spent

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["quarantine", "abandon"])
def test_unknown_result_keeps_reservation_unavailable_and_prevents_success(operation, ledger):
    async def scenario():
        run = run_reference()
        owner = await ledger.acquire(run, budget())
        await getattr(ledger, operation)(owner)
        with pytest.raises(WorkflowRunUnavailable):
            await ledger.complete(
                owner,
                BoundedWorkflowResult(
                    run=run, termination=WorkflowTermination.VERIFIED, final_budget=budget()
                ),
            )
        if operation == "quarantine":
            assert await ledger.remaining(owner) == budget()
            await ledger.release(owner)
        with pytest.raises(WorkflowRunUnavailable):
            await ledger.acquire(run, budget())

    asyncio.run(scenario())


def test_exact_run_binding_applies_before_terminal_replay(ledger):
    async def scenario():
        run = run_reference()
        owner = await ledger.acquire(run, budget())
        result = BoundedWorkflowResult(
            run=run, termination=WorkflowTermination.VERIFIED, final_budget=budget()
        )
        await ledger.complete(owner, result)
        assert await ledger.acquire(run, budget()) == result
        foreign = run.model_copy(
            update={"revision": run.revision.model_copy(update={"case_id": "different-case"})}
        )
        with pytest.raises(WorkflowRunUnavailable):
            await ledger.acquire(foreign, budget())
        with pytest.raises(WorkflowRunUnavailable):
            await ledger.complete(owner, result)

    asyncio.run(scenario())
