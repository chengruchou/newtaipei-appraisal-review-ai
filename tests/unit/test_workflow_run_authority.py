"""Review regressions for executed receipts and shared run admission."""

import asyncio
import runpy
from pathlib import Path
from uuid import uuid4

import pytest

from appraisal_review.adapters.aws.action_selector import BedrockActionSelector, ModelSelectorConfig
from appraisal_review.adapters.local.action_selector import DeterministicActionSelector
from appraisal_review.adapters.local.controlled_case import LocalControlledCase
from appraisal_review.adapters.local.decision_trace import NonDurableInMemoryDecisionTrace
from appraisal_review.application.action_policy import ControlledActionPolicy
from appraisal_review.application.bounded_workflow import BoundedWorkflowRunner
from appraisal_review.application.controlled_workflow import (
    ControlledWorkflowCoordinator,
    RoutedControlledActionExecutor,
)
from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.domain.service_contracts import (
    ActionKind,
    ActorReference,
    Budget,
    ServiceErrorCode,
    WorkflowState,
    WorkflowTermination,
)

ROOT = Path(__file__).resolve().parents[2]
B = runpy.run_path(str(ROOT / "tests/unit/test_bounded_workflow.py"))
INTEGRATION = runpy.run_path(str(ROOT / "tests/unit/test_controlled_case_integration.py"))


def coordinator(states, tool, selector=None, *, ledger=None):
    options = {} if ledger is None else {"ledger": ledger}
    return ControlledWorkflowCoordinator(
        snapshots=states,
        policy=ControlledActionPolicy(proposer_kind="model" if selector else "system"),
        selector=selector or DeterministicActionSelector(),
        executor=RoutedControlledActionExecutor({ActionKind.REVIEW: tool, ActionKind.HUMAN: tool}),
        trace=NonDurableInMemoryDecisionTrace(),
        executor_actor=ActorReference(actor_id="test-executor", kind="system"),
        **options,
    )


@pytest.mark.parametrize("human,wrong_state", [(False, False), (True, False), (True, True)])
def test_action_receipt_failure_is_traced_and_never_replayed_as_verified(human, wrong_state):
    async def scenario():
        snapshot = B["_snapshot"](
            state=WorkflowState.EVIDENCE_NEEDS_REVIEW if human else WorkflowState.MATERIAL_READY
        )
        states = B["MutableSnapshots"](snapshot)
        receipt = B["_success"](task_id=uuid4() if not human or wrong_state else None)
        receipt = receipt.model_copy(update={"reviewer_summary": "private-receipt-canary"})
        tool = B["SequenceTool"](
            [receipt],
            snapshots=states,
            next_state=WorkflowState.VERIFIED
            if not human or wrong_state
            else WorkflowState.WAITING_FOR_HUMAN,
        )
        authority = coordinator(states, tool)
        runner = BoundedWorkflowRunner(coordinator=authority, snapshots=states)
        result = await runner.run(snapshot.run)
        assert result.termination == WorkflowTermination.PERMANENT_FAILURE
        assert len(result.events) == 1
        assert result.events[0].disposition == "failed"
        assert result.events[0].reason_code == "invalid-tool-receipt"
        assert "private-receipt-canary" not in result.model_dump_json()
        assert await authority.prior_decisions(snapshot.run) == result.events
        replay = await BoundedWorkflowRunner(coordinator=authority, snapshots=states).run(
            snapshot.run
        )
        assert replay == result
        assert tool.calls == 1

    asyncio.run(scenario())


def test_permanent_failure_cannot_refresh_actual_local_case_budget(tmp_path):
    async def scenario():
        snapshot, run, _, _, repo, service, approval, _ = INTEGRATION["setup"](
            tmp_path, blocked=False
        )
        case = LocalControlledCase(
            snapshot=snapshot,
            run=run,
            budget=Budget(steps_remaining=2, model_calls_remaining=1, retries_remaining=0),
            repository=repo,
            human_tasks=service,
            bindings=INTEGRATION["bindings"](snapshot),
            authorization=approval,
        )

        class MalformedClient:
            calls = 0

            def converse(self, **kwargs):
                self.calls += 1
                return {
                    "stopReason": "end_turn",
                    "output": {"message": {"content": [{"text": "private-invalid-response"}]}},
                }

        client = MalformedClient()
        selector = BedrockActionSelector(
            client, ModelSelectorConfig(model_id="synthetic-only", attempts=1)
        )
        authority = coordinator(case, case, selector)
        runner = BoundedWorkflowRunner(coordinator=authority, snapshots=case)
        result = await runner.run(run)
        assert result.termination == WorkflowTermination.PERMANENT_FAILURE
        assert result.final_budget.model_calls_remaining == 0
        assert await runner.run(run) == result
        assert await BoundedWorkflowRunner(coordinator=authority, snapshots=case).run(run) == result
        assert client.calls == 1
        assert len(await authority.selection_failures(run)) == 1
        assert "private-invalid-response" not in result.model_dump_json()

    asyncio.run(scenario())


def test_concurrent_new_runner_is_rejected_before_second_tool():
    async def scenario():
        snapshot = B["_snapshot"]()
        states = B["MutableSnapshots"](snapshot)
        entered, release = asyncio.Event(), asyncio.Event()

        class BlockingTool:
            calls = 0

            async def invoke(self, proposal):
                self.calls += 1
                entered.set()
                await release.wait()
                states.transition(WorkflowState.VERIFIED)
                return B["_success"]()

        tool = BlockingTool()
        authority = coordinator(states, tool)
        first = asyncio.create_task(
            BoundedWorkflowRunner(coordinator=authority, snapshots=states).run(snapshot.run)
        )
        await entered.wait()
        try:
            with pytest.raises(ServiceFault) as error:
                await BoundedWorkflowRunner(coordinator=authority, snapshots=states).run(
                    snapshot.run
                )
            assert error.value.problem.code == ServiceErrorCode.CONFLICT
        finally:
            release.set()
            await first
        assert tool.calls == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("direct", [False, True])
def test_shared_ledger_excludes_another_coordinator_and_cancellation_quarantines(direct):
    from appraisal_review.adapters.local.workflow_run_ledger import (
        NonDurableInMemoryWorkflowRunLedger,
    )
    from appraisal_review.ports.action_selection import ActionSelectionError, SelectorErrorCode

    async def scenario():
        snapshot = B["_snapshot"]()
        states = B["MutableSnapshots"](snapshot)
        ledger = NonDurableInMemoryWorkflowRunLedger()
        entered = asyncio.Event()

        class UnfinishedTool:
            calls = 0

            async def invoke(self, proposal):
                self.calls += 1
                states.transition(WorkflowState.VERIFIED)
                entered.set()
                await asyncio.Event().wait()

        tool = UnfinishedTool()
        first = coordinator(states, tool, ledger=ledger)
        other = coordinator(states, tool, ledger=ledger)
        task = asyncio.create_task(
            first.decide_once(snapshot.run)
            if direct
            else BoundedWorkflowRunner(coordinator=first, snapshots=states).run(snapshot.run)
        )
        await entered.wait()
        try:
            with pytest.raises(ServiceFault):
                await BoundedWorkflowRunner(coordinator=other, snapshots=states).run(snapshot.run)
            with pytest.raises(ActionSelectionError) as error:
                await other.decide_once(snapshot.run)
            assert error.value.code == SelectorErrorCode.IN_FLIGHT
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        # VERIFIED is an uncertain side effect, not a safe replay after cancellation.
        with pytest.raises(ServiceFault):
            await BoundedWorkflowRunner(coordinator=other, snapshots=states).run(snapshot.run)
        assert tool.calls == 1

    asyncio.run(scenario())


def test_completed_run_replays_across_coordinators_with_injected_shared_ledger():
    from appraisal_review.adapters.local.workflow_run_ledger import (
        NonDurableInMemoryWorkflowRunLedger,
    )

    async def scenario():
        snapshot = B["_snapshot"]()
        states = B["MutableSnapshots"](snapshot)
        ledger = NonDurableInMemoryWorkflowRunLedger()
        tool = B["SequenceTool"](
            [B["_success"]()], snapshots=states, next_state=WorkflowState.VERIFIED
        )
        first = await BoundedWorkflowRunner(
            coordinator=coordinator(states, tool, ledger=ledger), snapshots=states
        ).run(snapshot.run)
        other = await BoundedWorkflowRunner(
            coordinator=coordinator(states, tool, ledger=ledger), snapshots=states
        ).run(snapshot.run)
        assert other == first
        assert other is not first
        assert tool.calls == 1

    asyncio.run(scenario())


def test_trace_persistence_failure_quarantines_executed_tool():
    async def scenario():
        snapshot = B["_snapshot"]()
        states = B["MutableSnapshots"](snapshot)
        tool = B["SequenceTool"](
            [B["_success"]()], snapshots=states, next_state=WorkflowState.VERIFIED
        )

        class UnavailableTrace(NonDurableInMemoryDecisionTrace):
            async def append(self, event):
                raise OSError("synthetic trace write failure")

        authority = ControlledWorkflowCoordinator(
            snapshots=states,
            policy=ControlledActionPolicy(proposer_kind="system"),
            selector=DeterministicActionSelector(),
            executor=RoutedControlledActionExecutor({ActionKind.REVIEW: tool}),
            trace=UnavailableTrace(),
            executor_actor=ActorReference(actor_id="test-executor", kind="system"),
        )
        with pytest.raises(OSError):
            await BoundedWorkflowRunner(coordinator=authority, snapshots=states).run(snapshot.run)
        with pytest.raises(ServiceFault):
            await BoundedWorkflowRunner(coordinator=authority, snapshots=states).run(snapshot.run)
        assert tool.calls == 1

    asyncio.run(scenario())


def test_explicit_unknown_external_result_is_traced_and_never_retried():
    from appraisal_review.domain.service_contracts import FailureCategory
    from appraisal_review.ports.workflow_run_ledger import WorkflowExternalResultUnknown

    async def scenario():
        snapshot = B["_snapshot"]()
        states = B["MutableSnapshots"](snapshot)
        tool = B["SequenceTool"](
            [WorkflowExternalResultUnknown("private-external-canary")],
            snapshots=states,
            next_state=WorkflowState.VERIFIED,
        )
        authority = coordinator(states, tool)
        result = await BoundedWorkflowRunner(coordinator=authority, snapshots=states).run(
            snapshot.run
        )
        assert result.termination == WorkflowTermination.PERMANENT_FAILURE
        assert result.handoff.category == FailureCategory.UNRESOLVED_EXECUTION
        assert result.events[0].reason_code == "tool-result-unknown"
        assert "private-external-canary" not in result.model_dump_json()
        assert (
            await BoundedWorkflowRunner(coordinator=authority, snapshots=states).run(snapshot.run)
            == result
        )
        assert tool.calls == 1

    asyncio.run(scenario())
