from __future__ import annotations

import asyncio
from collections.abc import Callable
from uuid import UUID

from appraisal_review.adapters.local.action_selector import DeterministicActionSelector
from appraisal_review.adapters.local.decision_trace import NonDurableInMemoryDecisionTrace
from appraisal_review.adapters.local.synthetic import synthetic_material
from appraisal_review.application.action_policy import ControlledActionPolicy
from appraisal_review.application.bounded_workflow import (
    BoundedWorkflowRunner,
    classify_event_failure,
    classify_selection_failure,
    no_progress_fingerprint,
)
from appraisal_review.application.controlled_workflow import (
    ControlledWorkflowCoordinator,
    RoutedControlledActionExecutor,
)
from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.domain.service_contracts import (
    ActionKind,
    ActionPrerequisite,
    ActionProposal,
    ActorReference,
    Budget,
    ControlledToolReceipt,
    FailureCategory,
    RunReference,
    ServiceErrorCode,
    ServiceProblem,
    ToolOutcome,
    WorkflowBlocker,
    WorkflowSnapshot,
    WorkflowState,
    WorkflowTermination,
)
from appraisal_review.ports.action_selection import (
    ActionSelectionError,
    ActionSelector,
    SelectorErrorCode,
)


class MutableSnapshots:
    def __init__(self, snapshot: WorkflowSnapshot) -> None:
        self.snapshot = snapshot

    async def current(self, run: RunReference) -> WorkflowSnapshot:
        return self.snapshot

    def transition(self, state: WorkflowState) -> None:
        self.snapshot = self.snapshot.model_copy(
            update={"state": state, "state_version": self.snapshot.state_version + 1}
        )


class SequenceTool:
    def __init__(
        self,
        receipts: list[ControlledToolReceipt | Exception],
        *,
        snapshots: MutableSnapshots | None = None,
        next_state: WorkflowState | None = None,
    ) -> None:
        self.receipts = receipts
        self.snapshots = snapshots
        self.next_state = next_state
        self.calls = 0

    async def invoke(self, proposal: ActionProposal) -> ControlledToolReceipt:
        value = self.receipts[self.calls]
        self.calls += 1
        if self.snapshots is not None and self.next_state is not None:
            self.snapshots.transition(self.next_state)
        if isinstance(value, Exception):
            raise value
        return value


class FailingSelector:
    def __init__(self, *errors: ActionSelectionError) -> None:
        self._actor = ActorReference(actor_id="model:failure", kind="model")
        self.errors = errors
        self.calls = 0

    @property
    def actor(self) -> ActorReference:
        return self._actor

    async def select(self, selector_input: object) -> ActionProposal:
        error = self.errors[min(self.calls, len(self.errors) - 1)]
        self.calls += 1
        raise error


def _ids(start: int = 100) -> Callable[[], UUID]:
    current = start

    def next_id() -> UUID:
        nonlocal current
        value = UUID(int=current)
        current += 1
        return value

    return next_id


def _snapshot(
    *,
    state: WorkflowState = WorkflowState.MATERIAL_READY,
    retries: int = 2,
    steps: int = 5,
    time_remaining_ms: int | None = 1_000,
    with_blocker: bool = False,
) -> WorkflowSnapshot:
    material = synthetic_material()
    revision = RevisionSnapshot.capture(material, "bounded-r1").revision
    blocker = WorkflowBlocker(
        blocker_id="road-width-review",
        reason_code="low-confidence-evidence",
        affected_subject_ids=("synthetic.road-width.target",),
        evidence=tuple(material.facts.pairs[0].target_sources),
    )
    prerequisites = (
        ActionPrerequisite.FORMS_PARSED,
        ActionPrerequisite.RULES_APPROVED,
        ActionPrerequisite.CRITICAL_EVIDENCE_AVAILABLE,
        ActionPrerequisite.MATERIAL_COMPLETE,
    )
    if state == WorkflowState.EVIDENCE_NEEDS_REVIEW:
        prerequisites = (ActionPrerequisite.FORMS_PARSED,)
        with_blocker = True
    return WorkflowSnapshot(
        state_version=1,
        run=RunReference(run_id=UUID(int=90), revision=revision.reference),
        revision=revision,
        state=state,
        satisfied_prerequisites=prerequisites,
        unresolved_blockers=(blocker,) if with_blocker else (),
        budget=Budget(
            steps_remaining=steps,
            model_calls_remaining=3,
            retries_remaining=retries,
            time_remaining_ms=time_remaining_ms,
        ),
    )


def _failed(reason: str) -> ControlledToolReceipt:
    return ControlledToolReceipt(
        outcome=ToolOutcome(
            outcome="failed",
            problem=ServiceProblem(code=ServiceErrorCode.EXECUTION),
        ),
        reason_code=reason,
        reviewer_summary="The controlled operation failed.",
    )


def _success(*, task_id: UUID | None = None) -> ControlledToolReceipt:
    return ControlledToolReceipt(
        outcome=ToolOutcome(outcome="succeeded", result_digest="d" * 64),
        reason_code="tool-succeeded",
        reviewer_summary="The controlled operation succeeded.",
        linked_task_id=task_id,
    )


def _runner(
    snapshots: MutableSnapshots,
    tool: SequenceTool,
    *,
    selector: ActionSelector | None = None,
    proposer_kind: str = "system",
    backoff_ms: int = 10,
    sleeps: list[float] | None = None,
) -> BoundedWorkflowRunner:
    async def sleep(delay: float) -> None:
        if sleeps is not None:
            sleeps.append(delay)

    coordinator = ControlledWorkflowCoordinator(
        snapshots=snapshots,
        policy=ControlledActionPolicy(proposer_kind=proposer_kind),  # type: ignore[arg-type]
        selector=selector or DeterministicActionSelector(proposal_id_factory=_ids(110)),
        executor=RoutedControlledActionExecutor({ActionKind.REVIEW: tool, ActionKind.HUMAN: tool}),
        trace=NonDurableInMemoryDecisionTrace(),
        executor_actor=ActorReference(actor_id="bounded-executor", kind="system"),
        event_id_factory=_ids(120),
        monotonic=lambda: 1.0,
    )
    return BoundedWorkflowRunner(
        coordinator=coordinator,
        snapshots=snapshots,
        retry_backoff_ms=backoff_ms,
        sleep=sleep,
    )


def test_retryable_failures_stop_at_retry_limit_with_deterministic_backoff() -> None:
    snapshot = _snapshot(retries=2)
    snapshots = MutableSnapshots(snapshot)
    tool = SequenceTool([_failed("provider-a"), _failed("provider-b"), _failed("provider-c")])
    sleeps: list[float] = []

    result = asyncio.run(_runner(snapshots, tool, sleeps=sleeps).run(snapshot.run))

    assert tool.calls == 3
    assert result.termination == WorkflowTermination.BUDGET_EXHAUSTED
    assert result.final_budget.retries_remaining == 0
    assert result.final_budget.steps_remaining == 2
    assert result.final_budget.time_remaining_ms == 970
    assert sleeps == [0.01, 0.02]


def test_repeated_identical_failure_triggers_no_progress_handoff() -> None:
    snapshot = _snapshot(retries=3, with_blocker=True)
    snapshots = MutableSnapshots(snapshot)
    tool = SequenceTool([RuntimeError("secret"), RuntimeError("secret")])

    result = asyncio.run(_runner(snapshots, tool).run(snapshot.run))

    assert tool.calls == 2
    assert result.termination == WorkflowTermination.NO_PROGRESS
    assert result.handoff is not None
    assert result.handoff.no_progress_fingerprint is not None
    assert result.handoff.blockers == snapshot.unresolved_blockers
    assert result.handoff.affected_subject_ids == ("synthetic.road-width.target",)
    assert result.handoff.evidence == snapshot.unresolved_blockers[0].evidence
    assert result.handoff.last_tool_outcome == result.events[-1].tool_result
    assert "secret" not in result.model_dump_json()


def test_permanent_invalid_receipt_executes_once() -> None:
    snapshot = _snapshot()
    snapshots = MutableSnapshots(snapshot)
    invalid = _success().model_copy(
        update={
            "evidence": (
                snapshot.unresolved_blockers[0].evidence[0]
                if snapshot.unresolved_blockers
                else synthetic_material()
                .facts.pairs[0]
                .target_sources[0]
                .model_copy(update={"document_id": "wrong-document"}),
            )
        }
    )
    tool = SequenceTool([invalid])

    result = asyncio.run(_runner(snapshots, tool).run(snapshot.run))

    assert tool.calls == 1
    assert result.termination == WorkflowTermination.PERMANENT_FAILURE
    assert result.handoff is not None
    assert result.handoff.category == FailureCategory.UNAUTHORIZED_SOURCE


def test_changed_revision_changes_no_progress_fingerprint() -> None:
    snapshot = _snapshot()
    snapshots = MutableSnapshots(snapshot)
    tool = SequenceTool([_failed("provider-a")])
    result = asyncio.run(_runner(snapshots, tool, backoff_ms=0).run(snapshot.run))
    event = result.events[0]
    first = no_progress_fingerprint(event, snapshot, FailureCategory.RETRYABLE_PROVIDER_TOOL)
    changed_reference = snapshot.revision.reference.model_copy(
        update={"revision_id": "bounded-r2", "material_digest": "e" * 64}
    )
    changed_revision = snapshot.revision.model_copy(update={"reference": changed_reference})
    changed = snapshot.model_copy(update={"revision": changed_revision})

    assert no_progress_fingerprint(event, changed, FailureCategory.RETRYABLE_PROVIDER_TOOL) != first


def test_selector_failure_accounts_attempts_without_negative_budget() -> None:
    snapshot = _snapshot(retries=1)
    snapshots = MutableSnapshots(snapshot)
    selector = FailingSelector(
        ActionSelectionError(
            SelectorErrorCode.THROTTLED,
            model_id="failure",
            prompt_version="controlled-action-selector-v1",
            attempts=2,
            latency_ms=15,
        )
    )
    tool = SequenceTool([])

    result = asyncio.run(
        _runner(
            snapshots,
            tool,
            selector=selector,
            proposer_kind="model",
        ).run(snapshot.run)
    )

    assert tool.calls == 0
    assert result.events == ()
    assert result.final_budget.model_calls_remaining == 1
    assert result.final_budget.retries_remaining == 0
    assert result.final_budget.time_remaining_ms == 985
    assert result.handoff is not None
    assert result.handoff.category == FailureCategory.RETRYABLE_PROVIDER_TOOL


def test_retryable_selector_failures_consume_outer_retries_and_stop() -> None:
    snapshot = _snapshot(retries=2)
    snapshots = MutableSnapshots(snapshot)
    selector = FailingSelector(
        *(
            ActionSelectionError(
                SelectorErrorCode.THROTTLED,
                model_id="failure",
                prompt_version="controlled-action-selector-v1",
                attempts=1,
                latency_ms=5,
            )
            for _ in range(3)
        )
    )
    sleeps: list[float] = []

    result = asyncio.run(
        _runner(
            snapshots,
            SequenceTool([]),
            selector=selector,
            proposer_kind="model",
            sleeps=sleeps,
        ).run(snapshot.run)
    )

    assert selector.calls == 3
    assert result.final_budget.model_calls_remaining == 0
    assert result.final_budget.retries_remaining == 0
    assert result.final_budget.time_remaining_ms == 955
    assert sleeps == [0.01, 0.02]
    assert result.termination == WorkflowTermination.BUDGET_EXHAUSTED


def test_initial_terminal_and_exhausted_states_do_not_select_or_execute() -> None:
    verified = _snapshot(state=WorkflowState.VERIFIED)
    verified_snapshots = MutableSnapshots(verified)
    verified_tool = SequenceTool([])
    verified_result = asyncio.run(_runner(verified_snapshots, verified_tool).run(verified.run))
    assert verified_result.termination == WorkflowTermination.VERIFIED
    assert verified_result.events == ()

    exhausted = _snapshot(steps=0, with_blocker=True)
    exhausted_result = asyncio.run(
        _runner(MutableSnapshots(exhausted), SequenceTool([])).run(exhausted.run)
    )
    assert exhausted_result.termination == WorkflowTermination.BUDGET_EXHAUSTED
    assert exhausted_result.handoff is not None
    assert exhausted_result.handoff.blockers == exhausted.unresolved_blockers


def test_backoff_exhausts_time_before_another_tool_attempt() -> None:
    snapshot = _snapshot(retries=2, time_remaining_ms=5)
    snapshots = MutableSnapshots(snapshot)
    tool = SequenceTool([_failed("provider-a")])
    sleeps: list[float] = []

    result = asyncio.run(_runner(snapshots, tool, sleeps=sleeps).run(snapshot.run))

    assert tool.calls == 1
    assert sleeps == [0.005]
    assert result.termination == WorkflowTermination.BUDGET_EXHAUSTED
    assert result.final_budget.time_remaining_ms == 0
    assert result.handoff is not None
    assert result.handoff.reason_code == "workflow-time-exhausted"


def test_stable_failure_classifiers_cover_business_and_selector_categories() -> None:
    snapshot = _snapshot()
    snapshots = MutableSnapshots(snapshot)
    result = asyncio.run(
        _runner(snapshots, SequenceTool([_failed("provider-a")]), backoff_ms=0).run(snapshot.run)
    )
    event = result.events[0]
    assert classify_event_failure(event) == FailureCategory.RETRYABLE_PROVIDER_TOOL
    assert (
        classify_event_failure(
            event.model_copy(
                update={
                    "disposition": "executed",
                    "state_after": WorkflowState.EVIDENCE_NEEDS_REVIEW,
                }
            )
        )
        == FailureCategory.MISSING_EVIDENCE
    )
    assert (
        classify_event_failure(
            event.model_copy(
                update={
                    "disposition": "executed",
                    "state_after": WorkflowState.UNSUPPORTED_CONTEXTS,
                }
            )
        )
        == FailureCategory.UNSUPPORTED_RULE_CONTEXT
    )
    assert (
        classify_event_failure(
            event.model_copy(
                update={
                    "disposition": "executed",
                    "state_after": WorkflowState.REVIEW_FAILED,
                }
            )
        )
        == FailureCategory.DETERMINISTIC_REVIEW_BLOCKER
    )
    assert (
        classify_selection_failure(ActionSelectionError(SelectorErrorCode.MISSING_ARGUMENT_SOURCE))
        == FailureCategory.MISSING_EVIDENCE
    )
    assert (
        classify_selection_failure(ActionSelectionError(SelectorErrorCode.NO_ALLOWED_ACTION))
        == FailureCategory.DETERMINISTIC_REVIEW_BLOCKER
    )
    assert (
        classify_selection_failure(ActionSelectionError(SelectorErrorCode.MALFORMED_OUTPUT))
        == FailureCategory.PERMANENT_MALFORMED_PROPOSAL
    )


def test_needs_review_is_a_business_handoff_not_a_retry() -> None:
    snapshot = _snapshot(state=WorkflowState.EVIDENCE_NEEDS_REVIEW)
    snapshots = MutableSnapshots(snapshot)
    blocker = snapshot.unresolved_blockers[0]
    receipt = _success(task_id=UUID(int=130)).model_copy(
        update={
            "reason_code": blocker.reason_code,
            "affected_subject_ids": blocker.affected_subject_ids,
            "evidence": blocker.evidence,
        }
    )
    tool = SequenceTool([receipt], snapshots=snapshots, next_state=WorkflowState.WAITING_FOR_HUMAN)

    result = asyncio.run(_runner(snapshots, tool).run(snapshot.run))

    assert tool.calls == 1
    assert result.termination == WorkflowTermination.WAITING_FOR_HUMAN
    assert result.handoff is None
    assert result.final_budget.retries_remaining == snapshot.budget.retries_remaining
