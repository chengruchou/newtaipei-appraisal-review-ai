"""Explicit prepared-case composition; real review and local human-task operations."""

from pydantic import RootModel

from appraisal_review.adapters.local.human_tasks import NonDurableInMemoryHumanTaskRepository
from appraisal_review.application.human_tasks import HumanTaskBinding, HumanTaskService
from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.domain.case_review import CaseReviewer
from appraisal_review.domain.factor_models import CaseReviewResult, EvaluationStatus
from appraisal_review.domain.review_contracts import content_digest
from appraisal_review.domain.service_contracts import (
    ActionKind,
    ActionPrerequisite,
    ActionProposal,
    Budget,
    ControlledToolReceipt,
    DeterministicReviewArguments,
    HumanReviewHandoff,
    HumanTask,
    RequestHumanReviewArguments,
    RunReference,
    ServiceErrorCode,
    ToolOutcome,
    WorkflowBlocker,
    WorkflowSnapshot,
    WorkflowState,
)
from appraisal_review.ports.approval import ReviewAuthorization


class LocalControlledCase:
    """One prepared immutable run, explicitly injected as snapshot provider and tool.

    This does not parse raw documents, sign material, write PDFs or mount routes.
    Source extraction uses separately injected tools and registry validation. A
    response's next_run is a new instance with reentry=True, not a revived attempt.
    """

    def __init__(
        self,
        *,
        snapshot: RevisionSnapshot,
        run: RunReference,
        budget: Budget,
        repository: NonDurableInMemoryHumanTaskRepository,
        human_tasks: HumanTaskService,
        bindings: tuple[HumanTaskBinding, ...],
        authorization: ReviewAuthorization | None = None,
        reentry: bool = False,
    ) -> None:
        if snapshot.revision.reference != run.revision:
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        if any(scoped.rules.status != "approved" for scoped in snapshot.material.policy.rule_sets):
            raise ServiceFault(ServiceErrorCode.CAPABILITY)
        material = snapshot.material
        if not material.facts.pairs or any(
            observation.value is None
            or not citations
            or any(not material.policy.registry.resolves(citation) for citation in citations)
            for pair in material.facts.pairs
            for observation, citations in (
                (pair.pair.target, pair.target_sources),
                (pair.pair.comparable, pair.comparable_sources),
            )
        ):
            raise ServiceFault(ServiceErrorCode.CAPABILITY)
        self._snapshot = snapshot
        self._run = run
        self._repository = repository
        self._human_tasks = human_tasks
        self._bindings = bindings
        self._authorization = authorization
        self._reentry = reentry
        self._result: CaseReviewResult | None = None
        self._handoff: HumanReviewHandoff | None = None
        self._tasks: tuple[HumanTask, ...] = ()
        self._state = WorkflowSnapshot(
            state_version=1,
            run=run,
            revision=snapshot.revision,
            state=WorkflowState.MATERIAL_READY,
            satisfied_prerequisites=(
                ActionPrerequisite.FORMS_PARSED,
                ActionPrerequisite.RULES_APPROVED,
                ActionPrerequisite.CRITICAL_EVIDENCE_AVAILABLE,
                ActionPrerequisite.MATERIAL_COMPLETE,
            ),
            budget=budget,
        )

    @property
    def tasks(self) -> tuple[HumanTask, ...]:
        return tuple(HumanTask.model_validate_json(task.model_dump_json()) for task in self._tasks)

    @property
    def result(self) -> CaseReviewResult | None:
        return (
            None
            if self._result is None
            else CaseReviewResult.model_validate_json(self._result.model_dump_json())
        )

    async def current(self, run: RunReference) -> WorkflowSnapshot:
        if run != self._run:
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        return WorkflowSnapshot.model_validate_json(self._state.model_dump_json())

    async def invoke(self, proposal: ActionProposal) -> ControlledToolReceipt:
        if proposal.run != self._run:
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        if proposal.action == ActionKind.REVIEW and (
            not isinstance(proposal.arguments, DeterministicReviewArguments)
            or proposal.arguments.revision != self._run.revision
            or proposal.arguments.rules != self._snapshot.revision.rules
        ):
            raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
        if proposal.action == ActionKind.HUMAN and (
            not isinstance(proposal.arguments, RequestHumanReviewArguments)
            or self._handoff is None
            or proposal.arguments.reason_code != self._handoff.reason_code
            or proposal.arguments.affected_subject_ids != self._handoff.affected_subject_ids
            or proposal.arguments.evidence != self._handoff.evidence
        ):
            raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
        if (
            proposal.action == ActionKind.REVIEW
            and self._state.state == WorkflowState.MATERIAL_READY
        ):
            material = self._snapshot.material
            if self._result is None:
                if self._reentry:
                    self._result = await self._human_tasks.reenter(self._run)
                else:
                    result = CaseReviewer(self._authorization).review(
                        material.policy, material.facts, material.policy.registry
                    )
                    await self._repository.register(self._snapshot, self._run, result)
                    self._result = result
            if self._result.status == EvaluationStatus.VERIFIED:
                state = WorkflowState.VERIFIED
                blockers: tuple[WorkflowBlocker, ...] = ()
            else:
                self._handoff = await self._human_tasks.handoff_for_run(self._run, self._bindings)
                state = WorkflowState.EVIDENCE_NEEDS_REVIEW
                blockers = (
                    WorkflowBlocker(
                        blocker_id="all-review-findings",
                        reason_code=self._handoff.reason_code,
                        affected_subject_ids=self._handoff.affected_subject_ids,
                        evidence=self._handoff.evidence,
                    ),
                )
            self._state = self._state.model_copy(
                update={
                    "state": state,
                    "state_version": self._state.state_version + 1,
                    "unresolved_blockers": blockers,
                }
            )
            return ControlledToolReceipt(
                outcome=ToolOutcome(
                    outcome="succeeded", result_digest=content_digest(self._result)
                ),
                reason_code="deterministic-review-result",
                reviewer_summary="The existing case reviewer produced this exact result.",
                affected_subject_ids=()
                if self._handoff is None
                else self._handoff.affected_subject_ids,
            )
        if (
            proposal.action == ActionKind.HUMAN
            and self._state.state == WorkflowState.EVIDENCE_NEEDS_REVIEW
            and self._handoff is not None
        ):
            self._tasks = await self._human_tasks.create_from_handoff(
                self._run, self._handoff, self._bindings
            )
            self._state = self._state.model_copy(
                update={
                    "state": WorkflowState.WAITING_FOR_HUMAN,
                    "state_version": self._state.state_version + 1,
                }
            )
            return ControlledToolReceipt(
                outcome=ToolOutcome(
                    outcome="succeeded",
                    result_digest=content_digest(RootModel[tuple[HumanTask, ...]](self._tasks)),
                ),
                reason_code="human-task-batch-created",
                reviewer_summary="All findings were retained in the authorized local task batch.",
                affected_subject_ids=self._handoff.affected_subject_ids,
                evidence=self._handoff.evidence,
                linked_task_id=self._tasks[0].task_id,
            )
        raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
