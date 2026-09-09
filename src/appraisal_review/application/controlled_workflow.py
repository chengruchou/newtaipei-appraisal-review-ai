"""Single-decision execution with fresh admission and bounded resource ownership."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping
from typing import Literal
from uuid import UUID, uuid4

from appraisal_review.application.service_guards import ServiceFault, admit_action
from appraisal_review.domain.document_models import SourceCitation, SourceRegistry
from appraisal_review.domain.review_contracts import content_digest
from appraisal_review.domain.service_contracts import (
    ActionKind,
    ActionProposal,
    ActorReference,
    Budget,
    BudgetConsumption,
    ControlledToolReceipt,
    DecisionEvent,
    ExtractPageArguments,
    InspectReferenceArguments,
    RunReference,
    SelectionFailureEvent,
    SelectorInput,
    ServiceErrorCode,
    ServiceProblem,
    ToolOutcome,
    WorkflowSnapshot,
)
from appraisal_review.ports.action_selection import (
    ActionSelectionError,
    ActionSelector,
    SelectorErrorCode,
)
from appraisal_review.ports.controlled_workflow import (
    AllowedActionPolicy,
    ControlledActionExecutor,
    ControlledActionTool,
    DecisionTrace,
    WorkflowSnapshotProvider,
)


class RoutedControlledActionExecutor:
    """Dispatch an admitted proposal to exactly one explicitly injected tool."""

    def __init__(self, tools: Mapping[ActionKind, ControlledActionTool]) -> None:
        if not tools or any(not isinstance(kind, ActionKind) for kind in tools):
            raise ValueError("Controlled tools require explicit typed action routes")
        self._tools = dict(tools)

    async def execute(self, proposal: ActionProposal) -> ControlledToolReceipt:
        tool = self._tools.get(proposal.action)
        if tool is None:
            raise ValueError("No controlled tool is configured for the admitted action")
        return await tool.invoke(proposal)


class ControlledWorkflowCoordinator:
    """Select, re-admit, execute once and append the decision when it occurs."""

    def __init__(
        self,
        *,
        snapshots: WorkflowSnapshotProvider,
        policy: AllowedActionPolicy,
        selector: ActionSelector,
        executor: ControlledActionExecutor,
        trace: DecisionTrace,
        executor_actor: ActorReference,
        event_id_factory: Callable[[], UUID] = uuid4,
        monotonic: Callable[[], float] = time.monotonic,
        source_registry: SourceRegistry | None = None,
    ) -> None:
        if executor_actor.kind != "system":
            raise ValueError("A controlled executor requires a trusted system actor")
        self._snapshots = snapshots
        self._policy = policy
        self._selector = selector
        self._executor = executor
        self._trace = trace
        self._executor_actor = executor_actor
        self._event_id_factory = event_id_factory
        self._monotonic = monotonic
        self._registry = (
            SourceRegistry.model_validate_json(source_registry.model_dump_json())
            if source_registry is not None
            else None
        )
        self._uncertain_runs: set[UUID] = set()

    async def selection_failures(self, run: RunReference) -> tuple[SelectionFailureEvent, ...]:
        return await self._trace.read_failures(run.run_id)

    async def decide_once(
        self,
        run: RunReference,
        *,
        evidence: tuple[SourceCitation, ...] = (),
        budget: Budget | None = None,
        retry_charge: int = 0,
    ) -> DecisionEvent:
        """Execute at most one tool; selection failures without a proposal propagate."""

        if type(retry_charge) is not int or retry_charge < 0:
            raise ValueError("Retry charge must be a non-negative integer")
        started = self._monotonic()
        initial = await self._current(run, budget=budget)
        parents = self._frontier(
            (*await self._trace.read(run.run_id), *await self._trace.read_failures(run.run_id))
        )
        allowed = self._policy.derive(initial)
        selector_input = SelectorInput(
            snapshot=initial,
            allowed_actions=allowed,
            evidence=evidence,
            budget=initial.budget,
        )
        try:
            if run.run_id in self._uncertain_runs:
                raise ActionSelectionError(SelectorErrorCode.IN_FLIGHT)
            remaining = self._remaining(initial.budget, started)
            if remaining is not None and remaining <= 0:
                raise ActionSelectionError(SelectorErrorCode.TIMEOUT)
            proposal = await asyncio.wait_for(
                self._selector.select(selector_input), timeout=remaining
            )
        except TimeoutError:
            error = ActionSelectionError(
                SelectorErrorCode.TIMEOUT, attempts_known=self._selector.actor.kind == "system"
            )
            self._uncertain_runs.add(run.run_id)
            await self._record_selection_failure(
                error, initial, allowed.policy_version, parents, started
            )
            raise error from None
        except ActionSelectionError as error:
            if error.code in {SelectorErrorCode.TIMEOUT, SelectorErrorCode.IN_FLIGHT}:
                self._uncertain_runs.add(run.run_id)
            await self._record_selection_failure(
                error, initial, allowed.policy_version, parents, started
            )
            raise
        execution_snapshot = await self._current(run, budget=initial.budget)
        execution_allowed = self._policy.derive(execution_snapshot)
        try:
            remaining = self._remaining(initial.budget, started)
            if remaining is not None and remaining <= 0:
                raise ServiceFault(ServiceErrorCode.CAPABILITY)
            admit_action(
                proposal,
                proposer=self._selector.actor,
                executor=self._executor_actor,
                snapshot=execution_snapshot,
                allowed=execution_allowed,
            )
            if isinstance(
                proposal.arguments, (ExtractPageArguments, InspectReferenceArguments)
            ) and not self._source_is_current(proposal, evidence):
                raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
        except ServiceFault as error:
            event = self._rejection(
                proposal,
                initial,
                parents=parents,
                started=started,
                code=error.problem.code,
            )
            await self._trace.append(event)
            return event

        admitted = next(
            action
            for action in execution_allowed.actions
            if action.action_id == proposal.action_id and action.action == proposal.action
        )
        provider_retries = (
            proposal.attempt_count - 1
            if proposal.proposer.kind == "model" and proposal.attempt_count is not None
            else 0
        )
        actual_retries = admitted.cost.retries + provider_retries + retry_charge
        if initial.budget.retries_remaining < actual_retries:
            event = self._rejection(
                proposal,
                initial,
                parents=parents,
                started=started,
                code=ServiceErrorCode.CAPABILITY,
            )
            await self._trace.append(event)
            return event
        try:
            remaining = self._remaining(initial.budget, started)
            if remaining is not None and remaining <= 0:
                raise TimeoutError
            receipt = await asyncio.wait_for(self._executor.execute(proposal), timeout=remaining)
            receipt = ControlledToolReceipt.model_validate_json(receipt.model_dump_json())
            remaining = self._remaining(initial.budget, started)
            if remaining is not None and remaining <= 0:
                raise TimeoutError
        except TimeoutError:
            self._uncertain_runs.add(run.run_id)
            receipt = ControlledToolReceipt(
                outcome=ToolOutcome(
                    outcome="failed", problem=ServiceProblem(code=ServiceErrorCode.CAPABILITY)
                ),
                reason_code="tool-time-exhausted",
                reviewer_summary="The tool exceeded its deadline; automatic retry is prohibited.",
            )
        except ServiceFault as error:
            receipt = ControlledToolReceipt(
                outcome=ToolOutcome(outcome="failed", problem=error.problem),
                reason_code=f"tool-{error.problem.code.value}",
                reviewer_summary="The tool rejected the operation at its trusted boundary.",
            )
        except Exception:
            receipt = ControlledToolReceipt(
                outcome=ToolOutcome(
                    outcome="failed",
                    problem=ServiceProblem(code=ServiceErrorCode.EXECUTION),
                ),
                reason_code="tool-execution-failed",
                reviewer_summary="The admitted tool failed without an accepted result.",
            )
        result_snapshot = await self._current(run)
        if not self._receipt_evidence_is_current(receipt, result_snapshot):
            receipt = ControlledToolReceipt(
                outcome=ToolOutcome(
                    outcome="failed",
                    problem=ServiceProblem(code=ServiceErrorCode.EXECUTION),
                ),
                reason_code="invalid-tool-receipt",
                reviewer_summary="The admitted tool returned an invalid result receipt.",
            )
        model_calls = proposal.attempt_count if proposal.proposer.kind == "model" else 0
        budget_after, consumed = self._consume(
            initial.budget,
            steps=admitted.cost.steps,
            model_calls=model_calls or 0,
            retries=actual_retries,
            started=started,
        )
        disposition: Literal["executed", "failed"] = (
            "executed" if receipt.outcome.outcome == "succeeded" else "failed"
        )
        event = DecisionEvent(
            event_id=self._event_id_factory(),
            parent_event_ids=parents,
            proposal=proposal,
            executor=self._executor_actor,
            policy_version=proposal.policy_version,
            state_before=initial.state,
            state_after=result_snapshot.state,
            disposition=disposition,
            executed_action=proposal.action,
            checked_prerequisites=admitted.prerequisites,
            reason_code=receipt.reason_code,
            reviewer_summary=receipt.reviewer_summary,
            affected_subject_ids=receipt.affected_subject_ids,
            evidence=receipt.evidence,
            tool_result=receipt.outcome,
            remaining_blockers=tuple(
                blocker.blocker_id for blocker in result_snapshot.unresolved_blockers
            ),
            linked_task_id=receipt.linked_task_id,
            linked_response_key=receipt.linked_response_key,
            budget_before=initial.budget,
            budget_after=budget_after,
            budget_consumed=consumed,
        )
        await self._trace.append(event)
        return event

    async def prior_decisions(self, run: RunReference) -> tuple[DecisionEvent, ...]:
        """Expose detached local history for terminal-state replay validation."""
        return await self._trace.read(run.run_id)

    async def _current(
        self, run: RunReference, *, budget: Budget | None = None
    ) -> WorkflowSnapshot:
        snapshot = await self._snapshots.current(run)
        current = WorkflowSnapshot.model_validate_json(snapshot.model_dump_json())
        if current.run != run:
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        if budget is not None:
            source = current.budget
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
                raise ServiceFault(ServiceErrorCode.CAPABILITY)
            current = WorkflowSnapshot.model_validate({**current.model_dump(), "budget": budget})
        return current

    def _rejection(
        self,
        proposal: ActionProposal,
        snapshot: WorkflowSnapshot,
        *,
        parents: tuple[UUID, ...],
        started: float,
        code: ServiceErrorCode,
    ) -> DecisionEvent:
        model_calls = proposal.attempt_count if proposal.proposer.kind == "model" else 0
        provider_retries = max((model_calls or 0) - 1, 0)
        budget_after, consumed = self._consume(
            snapshot.budget,
            steps=0,
            model_calls=min(model_calls or 0, snapshot.budget.model_calls_remaining),
            retries=min(provider_retries, snapshot.budget.retries_remaining),
            started=started,
        )
        return DecisionEvent(
            event_id=self._event_id_factory(),
            parent_event_ids=parents,
            proposal=proposal,
            policy_version=proposal.policy_version,
            state_before=snapshot.state,
            state_after=snapshot.state,
            disposition="rejected",
            reason_code=f"proposal-{code.value}",
            reviewer_summary="The proposal failed execution-time admission.",
            remaining_blockers=tuple(
                blocker.blocker_id for blocker in snapshot.unresolved_blockers
            ),
            budget_before=snapshot.budget,
            budget_after=budget_after,
            budget_consumed=consumed,
        )

    def _consume(
        self,
        budget: Budget,
        *,
        steps: int,
        model_calls: int,
        retries: int,
        started: float,
        minimum_elapsed_ms: int = 0,
    ) -> tuple[Budget, BudgetConsumption]:
        elapsed = max(minimum_elapsed_ms, 0, round((self._monotonic() - started) * 1_000))
        charged_elapsed = (
            None if budget.time_remaining_ms is None else min(elapsed, budget.time_remaining_ms)
        )
        after = Budget(
            steps_remaining=budget.steps_remaining - steps,
            model_calls_remaining=budget.model_calls_remaining - model_calls,
            retries_remaining=budget.retries_remaining - retries,
            time_remaining_ms=(
                None
                if budget.time_remaining_ms is None
                else budget.time_remaining_ms - (charged_elapsed or 0)
            ),
        )
        return after, BudgetConsumption(
            steps=steps,
            model_calls=model_calls,
            retries=retries,
            elapsed_ms=charged_elapsed,
        )

    @staticmethod
    def _frontier(events: tuple[DecisionEvent | SelectionFailureEvent, ...]) -> tuple[UUID, ...]:
        referenced = {parent for event in events for parent in event.parent_event_ids}
        return tuple(event.event_id for event in events if event.event_id not in referenced)

    def _receipt_evidence_is_current(
        self, receipt: ControlledToolReceipt, snapshot: WorkflowSnapshot
    ) -> bool:
        documents = {
            (document.document_id, document.version, document.content_hash)
            for document in snapshot.revision.documents
        }
        return all(
            (citation.document_id, citation.version, citation.content_hash) in documents
            and (self._registry is None or self._registry.resolves(citation))
            for citation in receipt.evidence
        )

    def _remaining(self, budget: Budget, started: float) -> float | None:
        if budget.time_remaining_ms is None:
            return None
        return max(0.0, budget.time_remaining_ms / 1000 - (self._monotonic() - started))

    def _source_is_current(
        self, proposal: ActionProposal, evidence: tuple[SourceCitation, ...]
    ) -> bool:
        arguments = proposal.arguments
        if self._registry is None or not isinstance(
            arguments, (ExtractPageArguments, InspectReferenceArguments)
        ):
            return False
        document = next(
            (
                item
                for item in self._registry.documents
                if item.document_id == arguments.document.document_id
            ),
            None,
        )
        if (
            document is None
            or (document.version, document.content_hash, document.role)
            != (
                arguments.document.version,
                arguments.document.content_hash,
                arguments.document.purpose,
            )
            or arguments.page > len(document.pages)
        ):
            return False
        if not any(
            ref.document_id == document.document_id
            and ref.page == arguments.page
            and self._registry.resolves(ref)
            for ref in evidence
        ):
            return False
        if isinstance(arguments, ExtractPageArguments) and arguments.region is not None:
            return arguments.region in evidence and self._registry.resolves(arguments.region)
        if isinstance(arguments, InspectReferenceArguments) and arguments.section_id is not None:
            return any(
                ref.document_id == document.document_id
                and ref.page == arguments.page
                and ref.region_id == arguments.section_id
                and self._registry.resolves(ref)
                for ref in evidence
            )
        return True

    async def _record_selection_failure(
        self,
        error: ActionSelectionError,
        snapshot: WorkflowSnapshot,
        policy_version: str,
        parents: tuple[UUID, ...],
        started: float,
    ) -> None:
        # An uncooperative adapter timeout has unknown call usage: reserve the remaining
        # model allowance, explicitly without claiming those calls were observed.
        calls = error.attempts if error.attempts_known else snapshot.budget.model_calls_remaining
        calls = min(max(calls, 0), snapshot.budget.model_calls_remaining)
        after, consumed = self._consume(
            snapshot.budget,
            steps=0,
            model_calls=calls,
            retries=min(max(error.attempts - 1, 0), snapshot.budget.retries_remaining),
            started=started,
            minimum_elapsed_ms=error.latency_ms,
        )
        event = SelectionFailureEvent(
            event_id=self._event_id_factory(),
            parent_event_ids=parents,
            run=snapshot.run,
            snapshot_digest=content_digest(snapshot),
            policy_version=policy_version,
            actor=self._selector.actor,
            error_code=error.code.value,
            model_id=error.model_id,
            prompt_version=error.prompt_version,
            attempt_count=error.attempts if error.attempts_known else None,
            input_tokens=error.input_tokens,
            output_tokens=error.output_tokens,
            latency_ms=max(error.latency_ms, consumed.elapsed_ms or 0),
            budget_before=snapshot.budget,
            budget_after=after,
            budget_consumed=consumed,
            remaining_blockers=tuple(item.blocker_id for item in snapshot.unresolved_blockers),
        )
        await self._trace.append_failure(event)
        error.event = event
