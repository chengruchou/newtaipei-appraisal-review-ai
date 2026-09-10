"""Bounded controlled execution with stable failure and no-progress handling."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from hashlib import sha256

from appraisal_review.application.controlled_workflow import ControlledWorkflowCoordinator
from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.domain.document_models import SourceCitation
from appraisal_review.domain.service_contracts import (
    BoundedWorkflowResult,
    Budget,
    DecisionEvent,
    FailureCategory,
    HumanReviewHandoff,
    RunReference,
    ServiceErrorCode,
    ToolOutcome,
    WorkflowSnapshot,
    WorkflowState,
    WorkflowTermination,
)
from appraisal_review.ports.action_selection import ActionSelectionError, SelectorErrorCode
from appraisal_review.ports.controlled_workflow import WorkflowSnapshotProvider
from appraisal_review.ports.workflow_run_ledger import (
    WorkflowRunReservation,
    WorkflowRunUnavailable,
)

Sleep = Callable[[float], Awaitable[None]]


def classify_event_failure(event: DecisionEvent) -> FailureCategory | None:
    """Classify only stable event fields, never provider or exception text."""

    if event.disposition == "executed":
        if event.state_after == WorkflowState.EVIDENCE_NEEDS_REVIEW:
            return FailureCategory.MISSING_EVIDENCE
        if event.state_after == WorkflowState.UNSUPPORTED_CONTEXTS:
            return FailureCategory.UNSUPPORTED_RULE_CONTEXT
        if event.state_after == WorkflowState.REVIEW_FAILED:
            return FailureCategory.DETERMINISTIC_REVIEW_BLOCKER
        return None
    if event.reason_code in {"tool-time-exhausted", "tool-result-unknown"}:
        return FailureCategory.UNRESOLVED_EXECUTION
    if event.reason_code in {"invalid-tool-receipt", "proposal-unauthorized"}:
        return FailureCategory.UNAUTHORIZED_SOURCE
    if event.disposition == "rejected":
        return FailureCategory.PERMANENT_MALFORMED_PROPOSAL
    if (
        event.tool_result
        and event.tool_result.problem
        and event.tool_result.problem.code == ServiceErrorCode.UNAUTHORIZED
    ):
        return FailureCategory.UNAUTHORIZED_SOURCE
    if (
        event.tool_result
        and event.tool_result.problem
        and event.tool_result.problem.code == ServiceErrorCode.EXECUTION
    ):
        return FailureCategory.RETRYABLE_PROVIDER_TOOL
    return FailureCategory.DETERMINISTIC_REVIEW_BLOCKER


def classify_selection_failure(error: ActionSelectionError) -> FailureCategory:
    if error.code in {SelectorErrorCode.TIMEOUT, SelectorErrorCode.IN_FLIGHT}:
        return FailureCategory.UNRESOLVED_EXECUTION
    if error.code in {
        SelectorErrorCode.THROTTLED,
        SelectorErrorCode.PROVIDER_ERROR,
    }:
        return FailureCategory.RETRYABLE_PROVIDER_TOOL
    if error.code == SelectorErrorCode.MISSING_ARGUMENT_SOURCE:
        return FailureCategory.MISSING_EVIDENCE
    if error.code == SelectorErrorCode.NO_ALLOWED_ACTION:
        return FailureCategory.DETERMINISTIC_REVIEW_BLOCKER
    return FailureCategory.PERMANENT_MALFORMED_PROPOSAL


def no_progress_fingerprint(
    event: DecisionEvent,
    snapshot: WorkflowSnapshot,
    category: FailureCategory,
) -> str:
    """Hash exact action inputs, material/source versions and a stable failure code."""

    sources = sorted(
        (
            document.document_id,
            document.version,
            document.content_hash,
            document.purpose,
        )
        for document in snapshot.revision.documents
    )
    canonical = json.dumps(
        {
            "action": event.proposal.action.value,
            "arguments": event.proposal.arguments.model_dump(mode="json"),
            "revision": snapshot.revision.reference.model_dump(mode="json"),
            "sources": sources,
            "category": category.value,
            "failure_code": event.reason_code,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


class BoundedWorkflowRunner:
    """Run controlled decisions until a terminal state or a fail-closed handoff."""

    def __init__(
        self,
        *,
        coordinator: ControlledWorkflowCoordinator,
        snapshots: WorkflowSnapshotProvider,
        retry_backoff_ms: int = 100,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if type(retry_backoff_ms) is not int or retry_backoff_ms < 0:
            raise ValueError("Retry backoff must be a non-negative integer")
        self._coordinator = coordinator
        self._snapshots = snapshots
        self._retry_backoff_ms = retry_backoff_ms
        self._sleep = sleep

    async def run(
        self,
        run: RunReference,
        *,
        evidence: tuple[SourceCitation, ...] = (),
    ) -> BoundedWorkflowResult:
        snapshot = await self._current(run)
        try:
            owner = await self._coordinator.ledger.acquire(run, snapshot.budget)
        except WorkflowRunUnavailable:
            raise ServiceFault(ServiceErrorCode.CONFLICT) from None
        if isinstance(owner, BoundedWorkflowResult):
            return owner
        try:
            result = await self._run(run, evidence=evidence, owner=owner)
            result = BoundedWorkflowResult.model_validate(
                {
                    **result.model_dump(),
                    "selection_failures": await self._coordinator.selection_failures(run),
                }
            )
            await self._coordinator.ledger.complete(owner, result)
            return result
        except BaseException:
            await self._coordinator.ledger.abandon(owner)
            raise

    async def _run(
        self,
        run: RunReference,
        *,
        evidence: tuple[SourceCitation, ...],
        owner: WorkflowRunReservation,
    ) -> BoundedWorkflowResult:
        budget = await self._coordinator.ledger.remaining(owner)
        snapshot = await self._current(run, budget=budget)
        events: list[DecisionEvent] = []
        seen_failures: set[str] = set()
        selection_retry_count = 0
        retry_charge = 0

        previous = await self._coordinator.prior_decisions(run)
        if previous and previous[-1].disposition != "executed" and self._terminal(snapshot):
            return self._handoff_result(
                run,
                [previous[-1]],
                previous[-1].budget_after,
                snapshot,
                WorkflowTermination.PERMANENT_FAILURE,
                classify_event_failure(previous[-1])
                or FailureCategory.DETERMINISTIC_REVIEW_BLOCKER,
                "failed-terminal-transition",
            )

        while True:
            await self._coordinator.ledger.checkpoint(owner, budget)
            terminal = self._terminal(snapshot)
            if terminal is not None:
                return BoundedWorkflowResult(
                    run=run,
                    termination=terminal,
                    events=tuple(events),
                    final_budget=budget,
                )
            if budget.steps_remaining == 0 or budget.time_remaining_ms == 0:
                return self._handoff_result(
                    run,
                    events,
                    budget,
                    snapshot,
                    WorkflowTermination.BUDGET_EXHAUSTED,
                    FailureCategory.DETERMINISTIC_REVIEW_BLOCKER,
                    "workflow-budget-exhausted",
                )
            if retry_charge and budget.retries_remaining == 0:
                return self._handoff_result(
                    run,
                    events,
                    budget,
                    snapshot,
                    WorkflowTermination.BUDGET_EXHAUSTED,
                    FailureCategory.RETRYABLE_PROVIDER_TOOL,
                    "workflow-retries-exhausted",
                )
            try:
                event = await self._coordinator.decide_reserved(
                    owner,
                    evidence=evidence,
                    budget=budget,
                    retry_charge=retry_charge,
                )
            except ActionSelectionError as error:
                category = classify_selection_failure(error)
                budget = (
                    error.event.budget_after
                    if error.event is not None
                    else self._consume_selection_failure(budget, error)
                )
                snapshot = await self._current(run, budget=budget)
                if (
                    category == FailureCategory.RETRYABLE_PROVIDER_TOOL
                    and budget.retries_remaining > 0
                    and budget.model_calls_remaining > 0
                    and budget.time_remaining_ms != 0
                ):
                    selection_retry_count += 1
                    budget = budget.model_copy(
                        update={"retries_remaining": budget.retries_remaining - 1}
                    )
                    budget = await self._backoff(budget, selection_retry_count)
                    continue
                return self._handoff_result(
                    run,
                    events,
                    budget,
                    snapshot,
                    (
                        WorkflowTermination.BUDGET_EXHAUSTED
                        if category == FailureCategory.RETRYABLE_PROVIDER_TOOL
                        else WorkflowTermination.PERMANENT_FAILURE
                    ),
                    category,
                    f"selector-{error.code.value}",
                )

            events.append(event)
            budget = event.budget_after
            snapshot = await self._current(run, budget=budget)
            terminal = self._terminal(snapshot)
            if terminal is not None and event.disposition == "executed":
                return BoundedWorkflowResult(
                    run=run,
                    termination=terminal,
                    events=tuple(events),
                    final_budget=budget,
                )

            event_category = classify_event_failure(event)
            if terminal is not None:
                return self._handoff_result(
                    run,
                    events,
                    budget,
                    snapshot,
                    WorkflowTermination.PERMANENT_FAILURE,
                    event_category or FailureCategory.DETERMINISTIC_REVIEW_BLOCKER,
                    "failed-terminal-transition",
                )
            if event.disposition == "executed":
                # Successful review with business blockers must reach the human action.
                retry_charge = 0
                continue
            if event_category is None:
                retry_charge = 0
                continue
            fingerprint = no_progress_fingerprint(event, snapshot, event_category)
            if fingerprint in seen_failures:
                return self._handoff_result(
                    run,
                    events,
                    budget,
                    snapshot,
                    WorkflowTermination.NO_PROGRESS,
                    event_category,
                    "workflow-no-progress",
                    fingerprint=fingerprint,
                )
            seen_failures.add(fingerprint)
            if event_category != FailureCategory.RETRYABLE_PROVIDER_TOOL:
                return self._handoff_result(
                    run,
                    events,
                    budget,
                    snapshot,
                    WorkflowTermination.PERMANENT_FAILURE,
                    event_category,
                    "workflow-permanent-failure",
                    fingerprint=fingerprint,
                )
            if budget.retries_remaining == 0 or budget.steps_remaining == 0:
                return self._handoff_result(
                    run,
                    events,
                    budget,
                    snapshot,
                    WorkflowTermination.BUDGET_EXHAUSTED,
                    event_category,
                    "workflow-retries-exhausted",
                    fingerprint=fingerprint,
                )
            retry_charge = 1
            budget = await self._backoff(budget, len(seen_failures))
            if budget.time_remaining_ms == 0:
                return self._handoff_result(
                    run,
                    events,
                    budget,
                    snapshot,
                    WorkflowTermination.BUDGET_EXHAUSTED,
                    event_category,
                    "workflow-time-exhausted",
                    fingerprint=fingerprint,
                )

    async def _backoff(self, budget: Budget, failure_count: int) -> Budget:
        delay_ms = self._retry_backoff_ms * (2 ** (failure_count - 1))
        if budget.time_remaining_ms is not None:
            delay_ms = min(delay_ms, budget.time_remaining_ms)
        if delay_ms:
            await self._sleep(delay_ms / 1_000)
        return budget.model_copy(
            update={
                "time_remaining_ms": (
                    None
                    if budget.time_remaining_ms is None
                    else budget.time_remaining_ms - delay_ms
                )
            }
        )

    async def _current(
        self, run: RunReference, *, budget: Budget | None = None
    ) -> WorkflowSnapshot:
        snapshot = WorkflowSnapshot.model_validate_json(
            (await self._snapshots.current(run)).model_dump_json()
        )
        if snapshot.run != run:
            raise ValueError("Snapshot does not belong to the bounded run")
        if budget is None:
            return snapshot
        source = snapshot.budget
        if (
            budget.steps_remaining > source.steps_remaining
            or budget.model_calls_remaining > source.model_calls_remaining
            or budget.retries_remaining > source.retries_remaining
            or (budget.time_remaining_ms is None) != (source.time_remaining_ms is None)
            or (
                budget.time_remaining_ms is not None
                and source.time_remaining_ms is not None
                and budget.time_remaining_ms > source.time_remaining_ms
            )
        ):
            raise ValueError("Snapshot provider cannot increase the bounded budget")
        return snapshot.model_copy(update={"budget": budget})

    @staticmethod
    def _terminal(snapshot: WorkflowSnapshot) -> WorkflowTermination | None:
        if snapshot.state == WorkflowState.VERIFIED:
            return WorkflowTermination.VERIFIED
        if snapshot.state == WorkflowState.WAITING_FOR_HUMAN:
            return WorkflowTermination.WAITING_FOR_HUMAN
        return None

    @staticmethod
    def _consume_selection_failure(budget: Budget, error: ActionSelectionError) -> Budget:
        calls = min(max(error.attempts, 0), budget.model_calls_remaining)
        retries = min(max(error.attempts - 1, 0), budget.retries_remaining)
        elapsed = (
            0
            if budget.time_remaining_ms is None
            else min(max(error.latency_ms, 0), budget.time_remaining_ms)
        )
        return Budget(
            steps_remaining=budget.steps_remaining,
            model_calls_remaining=budget.model_calls_remaining - calls,
            retries_remaining=budget.retries_remaining - retries,
            time_remaining_ms=(
                None if budget.time_remaining_ms is None else budget.time_remaining_ms - elapsed
            ),
        )

    @staticmethod
    def _handoff_result(
        run: RunReference,
        events: list[DecisionEvent],
        budget: Budget,
        snapshot: WorkflowSnapshot,
        termination: WorkflowTermination,
        category: FailureCategory,
        reason_code: str,
        *,
        fingerprint: str | None = None,
    ) -> BoundedWorkflowResult:
        subjects = tuple(
            dict.fromkeys(
                [
                    subject
                    for blocker in snapshot.unresolved_blockers
                    for subject in blocker.affected_subject_ids
                ]
                + [subject for event in events for subject in event.affected_subject_ids]
            )
        )
        citations_by_location: dict[tuple[object, ...], SourceCitation] = {}
        preserved_evidence = [
            citation for blocker in snapshot.unresolved_blockers for citation in blocker.evidence
        ] + [citation for event in events for citation in event.evidence]
        for citation in preserved_evidence:
            key = (
                citation.document_id,
                citation.version,
                citation.content_hash,
                citation.page,
                citation.region_id,
                citation.bbox,
                citation.excerpt,
            )
            citations_by_location.setdefault(key, citation)
        citations = tuple(citations_by_location.values())
        last_outcome: ToolOutcome | None = next(
            (event.tool_result for event in reversed(events) if event.tool_result is not None),
            None,
        )
        handoff = HumanReviewHandoff(
            reason_code=reason_code,
            category=category,
            question=(
                "Review the preserved workflow blockers and decide the next authorized action."
            ),
            revision=snapshot.revision.reference,
            blockers=snapshot.unresolved_blockers,
            affected_subject_ids=subjects,
            evidence=citations,
            last_tool_outcome=last_outcome,
            no_progress_fingerprint=fingerprint,
        )
        return BoundedWorkflowResult(
            run=run,
            termination=termination,
            events=tuple(events),
            final_budget=budget,
            handoff=handoff,
        )
