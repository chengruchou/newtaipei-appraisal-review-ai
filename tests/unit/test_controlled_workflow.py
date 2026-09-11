from __future__ import annotations

import asyncio
from collections.abc import Callable
from uuid import UUID

import pytest

from appraisal_review.adapters.local.action_selector import DeterministicActionSelector
from appraisal_review.adapters.local.decision_trace import NonDurableInMemoryDecisionTrace
from appraisal_review.adapters.local.synthetic import synthetic_material
from appraisal_review.application.action_policy import ControlledActionPolicy
from appraisal_review.application.controlled_workflow import (
    ControlledWorkflowCoordinator,
    RoutedControlledActionExecutor,
)
from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.domain.service_contracts import (
    ActionKind,
    ActionPrerequisite,
    ActionProposal,
    ActorReference,
    Budget,
    ControlledToolReceipt,
    DecisionEvent,
    DeterministicReviewArguments,
    RunReference,
    SelectorInput,
    ServiceErrorCode,
    ServiceProblem,
    ToolOutcome,
    WorkflowBlocker,
    WorkflowSnapshot,
    WorkflowState,
)
from appraisal_review.ports.action_selection import ActionSelector


class MutableSnapshots:
    def __init__(self, snapshot: WorkflowSnapshot) -> None:
        self.snapshot = snapshot
        self.calls = 0

    async def current(self, run: RunReference) -> WorkflowSnapshot:
        self.calls += 1
        return self.snapshot

    def transition(self, state: WorkflowState) -> None:
        self.snapshot = self.snapshot.model_copy(
            update={"state": state, "state_version": self.snapshot.state_version + 1}
        )


class SequenceSnapshots:
    def __init__(self, *snapshots: WorkflowSnapshot) -> None:
        self.snapshots = list(snapshots)
        self.calls = 0

    async def current(self, run: RunReference) -> WorkflowSnapshot:
        self.calls += 1
        return self.snapshots.pop(0)


class SpyTool:
    def __init__(
        self,
        receipt: ControlledToolReceipt | Exception,
        *,
        snapshots: MutableSnapshots | None = None,
        next_state: WorkflowState | None = None,
        order: list[str] | None = None,
        downstream_spies: dict[str, int] | None = None,
    ) -> None:
        self.receipt = receipt
        self.snapshots = snapshots
        self.next_state = next_state
        self.order = order
        self.downstream_spies = downstream_spies
        self.calls: list[ActionProposal] = []

    async def invoke(self, proposal: ActionProposal) -> ControlledToolReceipt:
        self.calls.append(proposal)
        if self.order is not None:
            self.order.append("tool")
        if self.downstream_spies is not None:
            for name in self.downstream_spies:
                self.downstream_spies[name] += 1
        if self.snapshots is not None and self.next_state is not None:
            self.snapshots.transition(self.next_state)
        if isinstance(self.receipt, Exception):
            raise self.receipt
        return self.receipt


class OrderedTrace(NonDurableInMemoryDecisionTrace):
    def __init__(self, order: list[str]) -> None:
        super().__init__()
        self.order = order

    async def append(self, event: DecisionEvent) -> None:
        self.order.append("trace")
        await super().append(event)


class CountingModelSelector:
    def __init__(self, *, attempts: int = 2) -> None:
        self._actor = ActorReference(actor_id="model:workflow-fixture", kind="model")
        self.attempts = attempts
        self.calls = 0

    @property
    def actor(self) -> ActorReference:
        return self._actor

    async def select(self, selector_input: SelectorInput) -> ActionProposal:
        self.calls += 1
        action = selector_input.allowed_actions.actions[0]
        return ActionProposal(
            proposal_id=UUID(int=70),
            run=selector_input.snapshot.run,
            action_id=action.action_id,
            action=action.action,
            policy_version=selector_input.allowed_actions.policy_version,
            snapshot_digest=selector_input.allowed_actions.snapshot_digest,
            proposer=self.actor,
            model_id="workflow-fixture",
            prompt_version="controlled-action-selector-v1",
            input_tokens=20,
            output_tokens=10,
            latency_ms=4,
            attempt_count=self.attempts,
            arguments=DeterministicReviewArguments(
                revision=selector_input.snapshot.revision.reference,
                rules=selector_input.snapshot.revision.rules,
            ),
            proposer_rationale="Untrusted model rationale.",
        )


def _snapshot(state: WorkflowState = WorkflowState.MATERIAL_READY) -> WorkflowSnapshot:
    material = synthetic_material()
    revision = RevisionSnapshot.capture(material, "workflow-r1").revision
    prerequisites: tuple[ActionPrerequisite, ...] = (
        ActionPrerequisite.FORMS_PARSED,
        ActionPrerequisite.RULES_APPROVED,
        ActionPrerequisite.CRITICAL_EVIDENCE_AVAILABLE,
        ActionPrerequisite.MATERIAL_COMPLETE,
    )
    blockers: tuple[WorkflowBlocker, ...] = ()
    if state == WorkflowState.EVIDENCE_NEEDS_REVIEW:
        prerequisites = (ActionPrerequisite.FORMS_PARSED,)
        blockers = (
            WorkflowBlocker(
                blocker_id="workflow-blocker",
                reason_code="low-confidence-evidence",
                affected_subject_ids=("synthetic.road-width.target",),
                evidence=tuple(material.facts.pairs[0].target_sources),
            ),
        )
    return WorkflowSnapshot(
        state_version=1,
        run=RunReference(run_id=UUID(int=60), revision=revision.reference),
        revision=revision,
        state=state,
        satisfied_prerequisites=prerequisites,
        unresolved_blockers=blockers,
        budget=Budget(
            steps_remaining=4,
            model_calls_remaining=3,
            retries_remaining=1,
            time_remaining_ms=1_000,
        ),
    )


def _ids(*values: int) -> Callable[[], UUID]:
    remaining = iter(UUID(int=value) for value in values)
    return lambda: next(remaining)


def _clock(*values: float) -> Callable[[], float]:
    remaining = iter(values)
    last = values[0]

    def read() -> float:
        nonlocal last
        last = next(remaining, last)
        return last

    return read


def _receipt(*, task_id: UUID | None = None) -> ControlledToolReceipt:
    return ControlledToolReceipt(
        outcome=ToolOutcome(outcome="succeeded", result_digest="a" * 64),
        reason_code="tool-succeeded",
        reviewer_summary="The admitted tool returned a validated result.",
        linked_task_id=task_id,
    )


def _coordinator(
    snapshots: MutableSnapshots | SequenceSnapshots,
    selector: ActionSelector,
    tool: SpyTool,
    trace: NonDurableInMemoryDecisionTrace,
    *,
    proposer_kind: str = "system",
    event_ids: Callable[[], UUID] | None = None,
    clock: Callable[[], float] | None = None,
) -> ControlledWorkflowCoordinator:
    return ControlledWorkflowCoordinator(
        snapshots=snapshots,
        policy=ControlledActionPolicy(proposer_kind=proposer_kind),  # type: ignore[arg-type]
        selector=selector,
        executor=RoutedControlledActionExecutor({ActionKind.REVIEW: tool, ActionKind.HUMAN: tool}),
        trace=trace,
        executor_actor=ActorReference(actor_id="controlled-executor", kind="system"),
        event_id_factory=event_ids or _ids(61, 62, 63),
        monotonic=clock or (lambda: 1.0),
    )


def test_admitted_action_executes_once_and_appends_actual_result() -> None:
    snapshot = _snapshot()
    snapshots = MutableSnapshots(snapshot)
    order: list[str] = []
    tool = SpyTool(_receipt(), snapshots=snapshots, next_state=WorkflowState.VERIFIED, order=order)
    trace = OrderedTrace(order)
    coordinator = _coordinator(
        snapshots,
        DeterministicActionSelector(proposal_id_factory=_ids(65)),
        tool,
        trace,
        event_ids=_ids(66),
        clock=_clock(1.0, 1.007),
    )

    event = asyncio.run(coordinator.decide_once(snapshot.run))

    assert order == ["tool", "trace"]
    assert len(tool.calls) == 1
    assert snapshots.calls == 3
    assert event.disposition == "executed"
    assert event.tool_result == _receipt().outcome
    assert event.state_before == WorkflowState.MATERIAL_READY
    assert event.state_after == WorkflowState.VERIFIED
    assert event.budget_consumed.model_calls == 0
    assert event.budget_consumed.steps == 1
    assert event.budget_consumed.elapsed_ms == 7
    assert event.budget_after.steps_remaining == 3
    assert asyncio.run(trace.read(snapshot.run.run_id)) == (event,)


def test_fresh_state_drift_rejects_with_zero_tool_calls() -> None:
    initial = _snapshot()
    changed = initial.model_copy(update={"state_version": 2})
    snapshots = SequenceSnapshots(initial, changed)
    downstream = {name: 0 for name in ("parser", "extractor", "reviewer", "approval", "writer")}
    tool = SpyTool(_receipt(), downstream_spies=downstream)
    trace = NonDurableInMemoryDecisionTrace()
    coordinator = _coordinator(
        snapshots,
        DeterministicActionSelector(proposal_id_factory=_ids(67)),
        tool,
        trace,
        event_ids=_ids(68),
        clock=_clock(1.0, 1.003),
    )

    event = asyncio.run(coordinator.decide_once(initial.run))

    assert event.disposition == "rejected"
    assert event.executor is None
    assert event.tool_result is None
    assert event.executed_action is None
    assert event.state_after == event.state_before
    assert event.budget_consumed.steps == 0
    assert tool.calls == []
    assert downstream == {name: 0 for name in downstream}
    assert snapshots.calls == 2
    assert asyncio.run(trace.read(initial.run.run_id)) == (event,)


def test_executor_exception_is_sanitized_and_recorded_as_actual_failure() -> None:
    snapshot = _snapshot()
    snapshots = MutableSnapshots(snapshot)
    tool = SpyTool(RuntimeError("private path /tmp/secret.pdf and credential"))
    trace = NonDurableInMemoryDecisionTrace()
    event = asyncio.run(
        _coordinator(
            snapshots,
            DeterministicActionSelector(proposal_id_factory=_ids(69)),
            tool,
            trace,
            event_ids=_ids(71),
        ).decide_once(snapshot.run)
    )

    assert len(tool.calls) == 1
    assert event.disposition == "failed"
    assert event.tool_result is not None
    assert event.tool_result.problem == ServiceProblem(code=ServiceErrorCode.EXECUTION)
    assert "secret" not in event.model_dump_json()
    assert event.executed_action == ActionKind.REVIEW


def test_model_call_metadata_and_actual_attempts_are_recorded_only_when_called() -> None:
    snapshot = _snapshot()
    snapshots = MutableSnapshots(snapshot)
    selector = CountingModelSelector(attempts=2)
    tool = SpyTool(_receipt(), snapshots=snapshots, next_state=WorkflowState.VERIFIED)
    trace = NonDurableInMemoryDecisionTrace()
    event = asyncio.run(
        _coordinator(
            snapshots,
            selector,
            tool,
            trace,
            proposer_kind="model",
            event_ids=_ids(72),
        ).decide_once(snapshot.run)
    )

    assert selector.calls == 1
    assert event.proposal.model_id == "workflow-fixture"
    assert event.proposal.prompt_version == "controlled-action-selector-v1"
    assert event.budget_consumed.model_calls == 2
    assert event.budget_consumed.retries == 1
    assert event.budget_after.model_calls_remaining == 1
    assert event.budget_after.retries_remaining == 0

    system_snapshot = _snapshot()
    system_tool = SpyTool(_receipt())
    system_event = asyncio.run(
        _coordinator(
            MutableSnapshots(system_snapshot),
            DeterministicActionSelector(proposal_id_factory=_ids(73)),
            system_tool,
            NonDurableInMemoryDecisionTrace(),
            event_ids=_ids(74),
        ).decide_once(system_snapshot.run)
    )
    assert system_event.proposal.model_id is system_event.proposal.prompt_version is None
    assert system_event.budget_consumed.model_calls == 0


def test_over_budget_selector_telemetry_rejects_without_negative_budget() -> None:
    snapshot = _snapshot()
    snapshots = MutableSnapshots(snapshot)
    selector = CountingModelSelector(attempts=4)
    tool = SpyTool(_receipt())

    event = asyncio.run(
        _coordinator(
            snapshots,
            selector,
            tool,
            NonDurableInMemoryDecisionTrace(),
            proposer_kind="model",
            event_ids=_ids(741),
        ).decide_once(snapshot.run)
    )

    assert tool.calls == []
    assert event.disposition == "rejected"
    assert event.budget_after.model_calls_remaining == 0
    assert event.budget_after.retries_remaining == 0
    assert event.budget_consumed.model_calls == 3
    assert event.budget_consumed.retries == 1


def test_human_tool_records_task_and_waiting_transition() -> None:
    snapshot = _snapshot(WorkflowState.EVIDENCE_NEEDS_REVIEW)
    snapshots = MutableSnapshots(snapshot)
    task_id = UUID(int=75)
    blocker = snapshot.unresolved_blockers[0]
    receipt = ControlledToolReceipt(
        outcome=ToolOutcome(outcome="succeeded", result_digest="b" * 64),
        reason_code=blocker.reason_code,
        reviewer_summary="A version-bound human task was created.",
        affected_subject_ids=blocker.affected_subject_ids,
        evidence=blocker.evidence,
        linked_task_id=task_id,
    )
    tool = SpyTool(
        receipt,
        snapshots=snapshots,
        next_state=WorkflowState.WAITING_FOR_HUMAN,
    )
    event = asyncio.run(
        _coordinator(
            snapshots,
            DeterministicActionSelector(proposal_id_factory=_ids(76)),
            tool,
            NonDurableInMemoryDecisionTrace(),
            event_ids=_ids(77),
        ).decide_once(snapshot.run)
    )

    assert event.disposition == "executed"
    assert event.linked_task_id == task_id
    assert event.state_after == WorkflowState.WAITING_FOR_HUMAN
    assert event.affected_subject_ids == blocker.affected_subject_ids
    assert event.evidence == blocker.evidence


def test_actual_causal_frontier_is_recorded_across_decisions() -> None:
    snapshot = _snapshot()
    snapshots = MutableSnapshots(snapshot)
    tool = SpyTool(_receipt(), snapshots=snapshots, next_state=WorkflowState.MATERIAL_READY)
    trace = NonDurableInMemoryDecisionTrace()
    coordinator = _coordinator(
        snapshots,
        DeterministicActionSelector(proposal_id_factory=_ids(78, 79)),
        tool,
        trace,
        event_ids=_ids(80, 81),
    )

    first = asyncio.run(coordinator.decide_once(snapshot.run))
    second = asyncio.run(coordinator.decide_once(snapshot.run))
    assert first.parent_event_ids == ()
    assert second.parent_event_ids == (first.event_id,)
    assert asyncio.run(trace.read(snapshot.run.run_id)) == (first, second)


def test_invalid_tool_receipt_becomes_sanitized_failure() -> None:
    snapshot = _snapshot()
    snapshots = MutableSnapshots(snapshot)
    receipt = _receipt().model_copy(
        update={"outcome": ToolOutcome.model_construct(outcome="succeeded")}
    )
    tool = SpyTool(receipt)
    event = asyncio.run(
        _coordinator(
            snapshots,
            DeterministicActionSelector(proposal_id_factory=_ids(82)),
            tool,
            NonDurableInMemoryDecisionTrace(),
            event_ids=_ids(83),
        ).decide_once(snapshot.run)
    )
    assert event.disposition == "failed"
    assert event.reason_code == "tool-execution-failed"
    assert event.tool_result is not None and event.tool_result.problem is not None


def test_unbound_tool_evidence_becomes_sanitized_failure() -> None:
    snapshot = _snapshot(WorkflowState.EVIDENCE_NEEDS_REVIEW)
    citation = (
        snapshot.unresolved_blockers[0]
        .evidence[0]
        .model_copy(update={"document_id": "unbound-document"})
    )
    receipt = ControlledToolReceipt(
        outcome=ToolOutcome(outcome="succeeded", result_digest="c" * 64),
        reason_code="forged-evidence",
        reviewer_summary="This receipt must not be accepted.",
        evidence=(citation,),
        linked_task_id=UUID(int=87),
    )
    snapshots = MutableSnapshots(snapshot)
    tool = SpyTool(receipt, snapshots=snapshots, next_state=WorkflowState.WAITING_FOR_HUMAN)
    event = asyncio.run(
        _coordinator(
            snapshots,
            DeterministicActionSelector(proposal_id_factory=_ids(88)),
            tool,
            NonDurableInMemoryDecisionTrace(),
            event_ids=_ids(89),
        ).decide_once(snapshot.run)
    )
    assert event.disposition == "failed"
    assert event.reason_code == "invalid-tool-receipt"
    assert event.evidence == ()


def test_coordinator_rejects_non_system_executor_and_wrong_snapshot_run() -> None:
    snapshot = _snapshot()
    snapshots = MutableSnapshots(snapshot)
    selector = DeterministicActionSelector()
    tool = SpyTool(_receipt())
    trace = NonDurableInMemoryDecisionTrace()
    with pytest.raises(ValueError, match="trusted system"):
        ControlledWorkflowCoordinator(
            snapshots=snapshots,
            policy=ControlledActionPolicy(proposer_kind="system"),
            selector=selector,
            executor=RoutedControlledActionExecutor({ActionKind.REVIEW: tool}),
            trace=trace,
            executor_actor=ActorReference(actor_id="model", kind="model"),
        )
    wrong = snapshot.model_copy(
        update={"run": snapshot.run.model_copy(update={"run_id": UUID(int=999)})}
    )
    with pytest.raises(ServiceFault, match="version_conflict"):
        asyncio.run(
            _coordinator(
                MutableSnapshots(wrong),
                selector,
                tool,
                trace,
            ).decide_once(snapshot.run)
        )


def test_trace_rejects_duplicate_ids_and_unknown_parents_and_returns_detached_events() -> None:
    snapshot = _snapshot()
    snapshots = MutableSnapshots(snapshot)
    trace = NonDurableInMemoryDecisionTrace()
    event = asyncio.run(
        _coordinator(
            snapshots,
            DeterministicActionSelector(proposal_id_factory=_ids(84)),
            SpyTool(_receipt()),
            trace,
            event_ids=_ids(85),
        ).decide_once(snapshot.run)
    )
    with pytest.raises(ValueError, match="already exists"):
        asyncio.run(trace.append(event))
    unknown_parent = event.model_copy(
        update={"event_id": UUID(int=86), "parent_event_ids": (UUID(int=999),)}
    )
    with pytest.raises(ValueError, match="parents"):
        asyncio.run(trace.append(unknown_parent))
    first_read = asyncio.run(trace.read(snapshot.run.run_id))
    second_read = asyncio.run(trace.read(snapshot.run.run_id))
    assert first_read == second_read == (event,)
    assert first_read[0] is not second_read[0]


def test_router_requires_typed_routes_and_never_falls_back() -> None:
    with pytest.raises(ValueError):
        RoutedControlledActionExecutor({})
    router = RoutedControlledActionExecutor({ActionKind.HUMAN: SpyTool(_receipt())})
    selector_input = SelectorInput(
        snapshot=_snapshot(),
        allowed_actions=ControlledActionPolicy(proposer_kind="system").derive(_snapshot()),
        budget=_snapshot().budget,
    )
    proposal = asyncio.run(DeterministicActionSelector().select(selector_input))
    with pytest.raises(ValueError, match="No controlled tool"):
        asyncio.run(router.execute(proposal))
