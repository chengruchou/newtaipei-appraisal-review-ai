"""Direct decisions remain part of bounded results, pause and terminal replay."""

import asyncio
from pathlib import Path
from runpy import run_path
from uuid import UUID, uuid4

import pytest

from appraisal_review.adapters.local.sqlite_workflow_run_ledger import SqliteWorkflowRunLedger
from appraisal_review.application.bounded_workflow import BoundedWorkflowRunner
from appraisal_review.domain.service_contracts import (
    WorkflowPause,
    WorkflowState,
    WorkflowTermination,
)

B = run_path(str(Path(__file__).with_name("test_bounded_workflow.py")))


@pytest.mark.parametrize("terminal", [WorkflowState.VERIFIED, WorkflowState.WAITING_FOR_HUMAN])
def test_direct_decisions_survive_bounded_handoff_and_rebuilt_runner(terminal, tmp_path):
    async def scenario():
        initial = B["_snapshot"]()
        snapshots = B["MutableSnapshots"](initial)
        task_id = UUID(int=500)
        tool = B["SequenceTool"]([B["_success"](), B["_success"](task_id=task_id)])
        runner = B["_runner"](snapshots, tool)
        coordinator = runner._coordinator
        path = tmp_path / "run.sqlite3"
        coordinator.ledger = SqliteWorkflowRunLedger(path)
        first = await coordinator.decide_once(initial.run)
        if terminal == WorkflowState.WAITING_FOR_HUMAN:
            snapshots.snapshot = B["_snapshot"](state=WorkflowState.EVIDENCE_NEEDS_REVIEW)
        tool.snapshots, tool.next_state = snapshots, terminal
        # A new ledger/runner sees the existing budget and all causal predecessors.
        coordinator.ledger = SqliteWorkflowRunLedger(path)
        result = await BoundedWorkflowRunner(coordinator=coordinator, snapshots=snapshots).run(
            initial.run
        )
        assert result.events[0] == first
        assert len(result.events) == 2
        assert result.events[1].parent_event_ids == (first.event_id,)
        assert result.final_budget.steps_remaining == initial.budget.steps_remaining - 2
        if terminal == WorkflowState.WAITING_FOR_HUMAN:
            pause = WorkflowPause(pause_id=uuid4(), result=result, task_ids=(task_id,))
            assert pause.task_ids == (task_id,)
        coordinator.ledger = SqliteWorkflowRunLedger(path)
        replay = await BoundedWorkflowRunner(coordinator=coordinator, snapshots=snapshots).run(
            initial.run
        )
        assert replay == result
        assert tool.calls == 2

    asyncio.run(scenario())


def test_direct_terminal_event_is_not_cached_as_empty_trace():
    async def scenario():
        initial = B["_snapshot"]()
        snapshots = B["MutableSnapshots"](initial)
        tool = B["SequenceTool"](
            [B["_success"]()], snapshots=snapshots, next_state=WorkflowState.VERIFIED
        )
        runner = B["_runner"](snapshots, tool)
        first = await runner._coordinator.decide_once(initial.run)
        result = await runner.run(initial.run)
        assert result.events == (first,)
        assert result.final_budget == first.budget_after
        assert result.termination == WorkflowTermination.VERIFIED
        assert await runner.run(initial.run) == result
        assert tool.calls == 1

    asyncio.run(scenario())


def test_direct_failure_counts_towards_bounded_no_progress():
    async def scenario():
        initial = B["_snapshot"]()
        snapshots = B["MutableSnapshots"](initial)
        tool = B["SequenceTool"]([B["_failed"]("provider-a")] * 3)
        runner = B["_runner"](snapshots, tool)
        first = await runner._coordinator.decide_once(initial.run)
        result = await runner.run(initial.run)
        assert result.events[0] == first
        assert result.termination == WorkflowTermination.NO_PROGRESS
        assert tool.calls == 2
        assert result.final_budget.retries_remaining == initial.budget.retries_remaining - 1

    asyncio.run(scenario())


def test_incomplete_cached_result_does_not_replay_forever():
    from appraisal_review.application.service_guards import ServiceFault
    from appraisal_review.domain.service_contracts import BoundedWorkflowResult

    async def scenario():
        initial = B["_snapshot"]()
        snapshots = B["MutableSnapshots"](initial)
        tool = B["SequenceTool"](
            [B["_success"]()], snapshots=snapshots, next_state=WorkflowState.VERIFIED
        )
        runner = B["_runner"](snapshots, tool)
        coordinator = runner._coordinator
        first = await coordinator.decide_once(initial.run)
        owner = await coordinator.ledger.acquire(initial.run, initial.budget)
        await coordinator.ledger.complete(
            owner,
            BoundedWorkflowResult(
                run=initial.run,
                termination=WorkflowTermination.VERIFIED,
                final_budget=first.budget_after,
            ),
        )
        with pytest.raises(ServiceFault):
            await runner.run(initial.run)
        assert tool.calls == 1

    asyncio.run(scenario())
