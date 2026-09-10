"""Cross-component regressions found during the Issue #17 acceptance audit."""

import asyncio
import json
import runpy
import threading
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import RootModel, ValidationError

from appraisal_review.adapters.aws.action_selector import BedrockActionSelector, ModelSelectorConfig
from appraisal_review.adapters.local.action_selector import DeterministicActionSelector
from appraisal_review.adapters.local.decision_trace import NonDurableInMemoryDecisionTrace
from appraisal_review.application.action_policy import ControlledActionPolicy
from appraisal_review.application.bounded_workflow import BoundedWorkflowRunner
from appraisal_review.application.revisions import RevisionSnapshot, source_preparation_revision
from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.domain.review_contracts import content_digest
from appraisal_review.domain.service_contracts import (
    ActionPrerequisite,
    DocumentReference,
    MaterialRevision,
    ServiceErrorCode,
    WorkflowSnapshot,
    WorkflowState,
)

ROOT = Path(__file__).resolve().parents[2]
C = runpy.run_path(str(ROOT / "tests/unit/test_controlled_workflow.py"))
B = runpy.run_path(str(ROOT / "tests/unit/test_bounded_workflow.py"))
S = runpy.run_path(str(ROOT / "tests/unit/test_action_selectors.py"))


def test_source_preparation_revision_does_not_require_invented_rules():
    original = C["_snapshot"]().revision
    digest = content_digest(RootModel[tuple[DocumentReference, ...]](original.documents))
    revision = MaterialRevision.model_validate(
        {
            **original.model_dump(),
            "rules": (),
            "canonicalization": "source-documents-json-v1",
            "reference": original.reference.model_copy(update={"material_digest": digest}),
        }
    )
    assert revision.rules == ()
    with pytest.raises(ValidationError):
        MaterialRevision.model_validate(
            {**revision.model_dump(), "canonicalization": "review-material-json-v1"}
        )


def test_invalid_receipt_cannot_finish_verified():
    async def scenario():
        snapshot = B["_snapshot"]()
        states = B["MutableSnapshots"](snapshot)
        citation = B["synthetic_material"]().facts.pairs[0].target_sources[0]
        receipt = B["_success"]().model_copy(
            update={"evidence": (citation.model_copy(update={"document_id": "unknown"}),)}
        )
        tool = B["SequenceTool"]([receipt], snapshots=states, next_state=WorkflowState.VERIFIED)
        runner = B["_runner"](states, tool)
        result = await runner.run(snapshot.run)
        assert result.events[0].disposition == "failed"
        assert result.termination.value == "permanent_failure"
        assert result.handoff is not None
        replay = await runner.run(snapshot.run)
        assert replay.termination.value == "permanent_failure"
        assert tool.calls == 1

    asyncio.run(scenario())


def test_elapsed_selection_budget_rejects_before_tool():
    async def scenario():
        snapshot = C["_snapshot"]()
        states = C["MutableSnapshots"](snapshot)
        clock = [0.0]
        baseline = DeterministicActionSelector()

        class DelayedSelector:
            actor = baseline.actor

            async def select(self, data):
                proposal = await baseline.select(data)
                clock[0] = 2.0
                return proposal

        tool = C["SpyTool"](C["_receipt"]())
        coordinator = C["_coordinator"](
            states,
            DelayedSelector(),
            tool,
            NonDurableInMemoryDecisionTrace(),
            clock=lambda: clock[0],
        )
        event = await coordinator.decide_once(snapshot.run)
        assert tool.calls == []
        assert event.disposition == "rejected"
        assert event.budget_after.time_remaining_ms == 0

    asyncio.run(scenario())


def test_outer_timeout_never_overlaps_inflight_provider_calls():
    class BlockingClient:
        def __init__(self):
            self.release = threading.Event()
            self.lock = threading.Lock()
            self.calls = self.active = self.maximum = 0

        def converse(self, **kwargs):
            with self.lock:
                self.calls += 1
                self.active += 1
                self.maximum = max(self.maximum, self.active)
            try:
                assert self.release.wait(2)
                raise RuntimeError("synthetic provider failure")
            finally:
                with self.lock:
                    self.active -= 1

    async def scenario():
        client = BlockingClient()
        snapshot = B["_snapshot"](retries=2)
        selector = BedrockActionSelector(
            client,
            ModelSelectorConfig(model_id="synthetic-probe", attempts=1, timeout_seconds=0.01),
        )
        runner = B["_runner"](
            B["MutableSnapshots"](snapshot),
            B["SequenceTool"]([]),
            selector=selector,
            proposer_kind="model",
            backoff_ms=0,
        )
        try:
            result = await runner.run(snapshot.run)
            assert client.calls == client.maximum == 1
            assert result.handoff is not None
        finally:
            client.release.set()

    asyncio.run(scenario())


def test_permanent_tool_denial_is_not_retried():
    async def scenario():
        snapshot = B["_snapshot"]()
        tool = B["SequenceTool"]([ServiceFault(ServiceErrorCode.UNAUTHORIZED)] * 2)
        result = await B["_runner"](B["MutableSnapshots"](snapshot), tool).run(snapshot.run)
        assert tool.calls == 1
        assert result.handoff.category.value == "unauthorized_or_wrong_purpose_source"

    asyncio.run(scenario())


def test_review_blocker_runs_the_human_action_next():
    async def scenario():
        snapshot = B["_snapshot"](with_blocker=True)
        states = B["MutableSnapshots"](snapshot)

        class ReviewThenHuman:
            calls = 0

            async def invoke(self, proposal):
                self.calls += 1
                if self.calls == 1:
                    states.transition(WorkflowState.EVIDENCE_NEEDS_REVIEW)
                    return B["_success"]()
                states.transition(WorkflowState.WAITING_FOR_HUMAN)
                return B["_success"](task_id=B["UUID"](int=999))

        tool = ReviewThenHuman()
        result = await B["_runner"](states, tool).run(snapshot.run)
        assert tool.calls == 2
        assert result.termination.value == "waiting_for_human"
        assert result.events[-1].linked_task_id is not None

    asyncio.run(scenario())


def test_malformed_model_output_retains_a_sanitized_failure_event():
    async def scenario():
        current = S["_input"](WorkflowState.MATERIAL_READY)
        selector = S["_model_selector"](S["FakeClient"](S["_response"]("private invalid JSON")))
        states = C["MutableSnapshots"](current.snapshot)
        trace = NonDurableInMemoryDecisionTrace()
        tool = C["SpyTool"](C["_receipt"]())
        coordinator = C["_coordinator"](states, selector, tool, trace, proposer_kind="model")
        result = await BoundedWorkflowRunner(coordinator=coordinator, snapshots=states).run(
            current.snapshot.run
        )
        events = await trace.read_failures(current.snapshot.run.run_id)
        assert len(events) == 1
        assert "fixture-selector-model" in result.model_dump_json()
        assert "private invalid JSON" not in result.model_dump_json()
        assert tool.calls == []

    asyncio.run(scenario())


def test_model_cannot_invent_a_region_on_an_authorized_page():
    async def scenario():
        current = S["_input"](WorkflowState.CRITERIA_PENDING)
        payload = json.loads(S["_selection"](current))
        payload["arguments"]["region"]["region_id"] = "invented-region"
        selector = S["_model_selector"](S["FakeClient"](S["_response"](json.dumps(payload))))
        try:
            await selector.select(current)
        except S["ActionSelectionError"]:
            return
        raise AssertionError("Invented region was admitted")

    asyncio.run(scenario())


@pytest.mark.parametrize("trusted_registry", [False, True])
def test_source_action_requires_real_registry_before_execution(trusted_registry):
    async def scenario():
        from appraisal_review.application.controlled_workflow import (
            ControlledWorkflowCoordinator,
            RoutedControlledActionExecutor,
        )
        from appraisal_review.domain.service_contracts import ActionKind, ActorReference

        current = S["_input"](WorkflowState.CRITERIA_PENDING)
        current = current.model_copy(
            update={"evidence": tuple(B["synthetic_material"]().policy.rule_sets[0].evidence)}
        )
        selector = S["_model_selector"](S["FakeClient"](S["_response"](S["_selection"](current))))
        states = C["MutableSnapshots"](current.snapshot)
        tool = C["SpyTool"](C["_receipt"]())
        coordinator = ControlledWorkflowCoordinator(
            snapshots=states,
            policy=ControlledActionPolicy(proposer_kind="model"),
            selector=selector,
            executor=RoutedControlledActionExecutor({ActionKind.EXTRACT: tool}),
            trace=NonDurableInMemoryDecisionTrace(),
            executor_actor=ActorReference(actor_id="local-executor", kind="system"),
            source_registry=B["synthetic_material"]().policy.registry if trusted_registry else None,
        )
        event = await coordinator.decide_once(current.snapshot.run, evidence=current.evidence)
        assert len(tool.calls) == int(trusted_registry)
        assert event.disposition == ("executed" if trusted_registry else "rejected")

    asyncio.run(scenario())


@pytest.mark.parametrize("state", [WorkflowState.MATERIAL_READY, WorkflowState.VERIFIED])
def test_source_only_revision_cannot_claim_ready_or_verified(state):
    snapshot = C["_snapshot"](WorkflowState.CRITERIA_PENDING)
    revision = source_preparation_revision(snapshot.revision.documents, "source-r1")
    source = WorkflowSnapshot.model_validate(
        {
            **snapshot.model_dump(),
            "revision": revision,
            "satisfied_prerequisites": (ActionPrerequisite.CRITERIA_DOCUMENT,),
            "run": snapshot.run.model_copy(update={"revision": revision.reference}),
        }
    )
    assert source.revision.rules == ()
    assert ControlledActionPolicy(proposer_kind="system").derive(source).actions
    with pytest.raises(ValidationError):
        WorkflowSnapshot.model_validate({**source.model_dump(), "state": state})
    with pytest.raises(ValidationError):
        MaterialRevision.model_validate(
            {
                **revision.model_dump(),
                "reference": revision.reference.model_copy(update={"material_digest": "f" * 64}),
            }
        )
    child = RevisionSnapshot.capture(
        B["synthetic_material"](), "assembled-r2", parent=revision.reference
    )
    assert child.revision.parent == revision.reference and child.revision.rules


def test_tool_deadline_cannot_return_verified_or_restart_the_run():
    async def scenario():
        snapshot = C["_snapshot"]()
        states = C["MutableSnapshots"](snapshot)
        clock = [0.0]

        class SlowTool:
            calls = 0

            async def invoke(self, proposal):
                self.calls += 1
                clock[0] = 2.0
                states.snapshot = states.snapshot.model_copy(
                    update={"state": WorkflowState.VERIFIED}
                )
                return C["_receipt"]()

        tool = SlowTool()
        coordinator = C["_coordinator"](
            states,
            DeterministicActionSelector(),
            tool,
            NonDurableInMemoryDecisionTrace(),
            clock=lambda: clock[0],
        )
        event = await coordinator.decide_once(snapshot.run)
        assert event.disposition == "failed"
        assert event.reason_code == "tool-time-exhausted"
        with pytest.raises(S["ActionSelectionError"]):
            await coordinator.decide_once(snapshot.run)
        assert tool.calls == 1

    asyncio.run(scenario())


def test_failed_selection_and_next_decision_share_causal_trace():
    async def scenario():
        current = S["_input"](WorkflowState.MATERIAL_READY)
        client = S["FakeClient"](S["_response"]("invalid"))
        selector = S["_model_selector"](client)
        states = C["MutableSnapshots"](current.snapshot)
        trace = NonDurableInMemoryDecisionTrace()
        coordinator = C["_coordinator"](
            states, selector, C["SpyTool"](C["_receipt"]()), trace, proposer_kind="model"
        )
        with pytest.raises(S["ActionSelectionError"]):
            await coordinator.decide_once(current.snapshot.run)
        failure = (await trace.read_failures(current.snapshot.run.run_id))[0]
        good = S["_model_selector"](S["FakeClient"](S["_response"](S["_selection"](current))))
        next_coordinator = C["_coordinator"](
            states,
            good,
            C["SpyTool"](C["_receipt"]()),
            trace,
            proposer_kind="model",
            event_ids=uuid4,
        )
        event = await next_coordinator.decide_once(current.snapshot.run)
        assert event.parent_event_ids == (failure.event_id,)
        with pytest.raises(ValueError):
            await trace.append_failure(failure)
        with pytest.raises(ValueError):
            await trace.append(event.model_copy(update={"event_id": failure.event_id}))
        with pytest.raises(ValueError):
            await trace.append_failure(
                failure.model_copy(update={"event_id": uuid4(), "parent_event_ids": (uuid4(),)})
            )
        assert (await trace.read_failures(current.snapshot.run.run_id))[0] == failure

    asyncio.run(scenario())
