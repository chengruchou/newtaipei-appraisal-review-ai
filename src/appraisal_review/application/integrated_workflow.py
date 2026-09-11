"""Controlled prepared-revision execution through the canonical human-task API.

This adapter has no response endpoint and does not sign approvals. Runtime owns
claim/heartbeat/publication, the canonical HumanTaskService owns responses, and
injected persistence owns fenced task registration and immutable Controller outputs.
Raw sanitized-document admission/extraction precedes stored RevisionSnapshot creation.
The Controller reparses current sources and independently verifies that exact material.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol
from uuid import uuid5

from pydantic import RootModel

from appraisal_review.adapters.local.service import public_verification
from appraisal_review.application.controller import ReviewAgentController
from appraisal_review.application.document_review import MaterialProvider
from appraisal_review.application.human_tasks import HumanTaskService
from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.application.runtime_sources import CurrentPrincipalLookup
from appraisal_review.application.runtime_worker import ExecutedReview
from appraisal_review.application.service_guards import Principal, ServiceFault, response_digest
from appraisal_review.domain.confidence import confirmation_digest
from appraisal_review.domain.factor_models import AgentReviewRequest, AgentReviewRun, WorkflowStatus
from appraisal_review.domain.review_contracts import ComparisonContext, content_digest
from appraisal_review.domain.service_contracts import (
    ActorReference,
    Budget,
    ExecutionStatus,
    FactSideReference,
    HumanResponseResult,
    HumanTask,
    Permission,
    ResponseAction,
    RunReference,
    ServiceErrorCode,
    ServiceResult,
    TaskKind,
)
from appraisal_review.domain.task_contracts import ResponseReceipt
from appraisal_review.ports.human_tasks import HumanTaskStore
from appraisal_review.ports.jobs import ClaimedAttempt, JobRecord
from appraisal_review.ports.model_dispatch import dispatch_async_authority

if TYPE_CHECKING:
    from appraisal_review.domain.service_contracts import (
        ActionProposal,
        ControlledToolReceipt,
        WorkflowSnapshot,
    )
    from appraisal_review.ports.action_selection import ActionSelector
    from appraisal_review.ports.controlled_workflow import DecisionTrace
    from appraisal_review.ports.workflow_run_ledger import WorkflowRunLedger


class WorkflowReviewStore(Protocol):
    async def put(self, run: RunReference, review: AgentReviewRun) -> None:
        """Durably insert an immutable exact-run result, or accept identical replay.

        A different result for the same run must fail. Commit before returning;
        uncertain acknowledgment must not be interpreted as absence on retry.
        This record is internal evidence, not a publication or approval receipt.
        """
        ...

    async def read(self, run: RunReference) -> AgentReviewRun | None: ...


class WorkflowExecutionGuard(Protocol):
    async def require_current(
        self,
        principal: Principal,
        record: JobRecord,
        attempt: ClaimedAttempt,
        snapshot: RevisionSnapshot,
    ) -> None:
        """Check current source authorization and attempt lease/owner/fencing.

        Resumed runs must have their own authorized C2 snapshot; a parent's source
        approval is insufficient. Failure must propagate before any tool side effect.
        """
        ...


class FencedTaskRegistration(Protocol):
    async def register(
        self,
        record: JobRecord,
        attempt: ClaimedAttempt,
        snapshot: RevisionSnapshot,
        tasks: tuple[HumanTask, ...],
    ) -> None:
        """Atomically register tasks after rechecking current attempt/lease/fence.

        Persist the exact revision and batch idempotently in the same canonical store
        read by HumanTaskService. Do not accept a stale worker's earlier guard read as
        transaction authority. Runtime transitions the job to waiting after return.
        """
        ...


@dataclass(frozen=True)
class ControllerInvocation:
    """Trusted configured parser/writer/authorization and confined request.

    The adapter creates a fresh Controller using these tools, replacing material
    providers with the exact stored revision so a factory cannot reuse old facts.
    """

    controller: ReviewAgentController
    request: AgentReviewRequest


ControllerFactory = Callable[
    [Principal, RunReference, RevisionSnapshot], Awaitable[ControllerInvocation]
]
ResultProjection = Callable[[JobRecord, ClaimedAttempt, AgentReviewRun], Awaitable[ServiceResult]]


@dataclass(frozen=True)
class IntegratedTaskBinding:
    """Trusted configuration mapping actual finding IDs to one editable observation."""

    binding_id: str
    kind: Literal[TaskKind.FACT, TaskKind.CORRECTION]
    context: ComparisonContext
    factor_id: str
    side: Literal["target", "comparable"]
    finding_ids: tuple[str, ...]
    question: str


def _case_result(run: RunReference, review: AgentReviewRun) -> AgentReviewRun:
    review = AgentReviewRun.model_validate_json(review.model_dump_json())
    if (
        review.case_id != run.revision.case_id
        or review.case_review is None
        or review.verification is None
        or review.case_review.identity.case_id != run.revision.case_id
    ):
        raise ServiceFault(ServiceErrorCode.VALIDATION)
    if review.status in {WorkflowStatus.VERIFIED, WorkflowStatus.COMPLETED} and (
        not review.verification.can_complete or review.case_review.status.value != "verified"
    ):
        raise ServiceFault(ServiceErrorCode.VALIDATION)
    return review


class IntegratedWorkflowExecution:
    """Runtime ReviewExecution; model proposes, trusted tools execute, API responds.

    Every dependency is configured at the composition root. No local task responder,
    model fallback, auto-approval, durable-mode fallback or caller-selected path exists.
    """

    def __init__(
        self,
        *,
        human_tasks: HumanTaskService,
        principals: CurrentPrincipalLookup,
        guard: WorkflowExecutionGuard,
        registration: FencedTaskRegistration,
        controllers: ControllerFactory,
        reviews: WorkflowReviewStore,
        selector: ActionSelector,
        ledger: WorkflowRunLedger,
        trace: DecisionTrace,
        bindings: tuple[IntegratedTaskBinding, ...],
        budget: Budget,
        project_result: ResultProjection | None = None,
    ) -> None:
        if selector.actor.kind != "model":
            raise ValueError("Integrated workflow requires a configured model selector")
        if len({binding.binding_id for binding in bindings}) != len(bindings):
            raise ValueError("Workflow task bindings must be unique")
        if any(binding.kind not in {TaskKind.FACT, TaskKind.CORRECTION} for binding in bindings):
            raise ValueError("Only canonical material response purposes are supported")
        self.human_tasks, self.principals, self.guard = human_tasks, principals, guard
        self.registration, self.controllers, self.reviews = registration, controllers, reviews
        self.selector, self.ledger, self.trace = selector, ledger, trace
        self.bindings, self.budget = bindings, Budget.model_validate_json(budget.model_dump_json())
        self.project_result = project_result

    async def _current(
        self, record: JobRecord, attempt: ClaimedAttempt, snapshot: RevisionSnapshot | None = None
    ) -> tuple[Principal, RevisionSnapshot]:
        principal = await self.principals.read(record.principal_id, record.case_id)
        if principal.actor.actor_id != record.principal_id:
            raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
        principal.require(record.case_id, Permission.REVIEW)
        actual = await self.human_tasks.store.read_job(job_id=record.job_id)
        if (
            actual is None
            or actual.current_run != record.current_run
            or actual.principal_id != record.principal_id
            or actual.case_id != record.case_id
            or actual.cancel_requested
            or actual.status.value != "running"
            or attempt.job_id != record.job_id
            or attempt.run_id != record.current_run.run_id
            or attempt.attempt_id != record.current_run.attempt_id
            or attempt.expected_result_version != actual.result_version
        ):
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        stored = await self.human_tasks.store.read_snapshot(revision=record.current_run.revision)
        if stored is None or stored.revision.reference != record.current_run.revision:
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        checked = RevisionSnapshot.capture(
            stored.material,
            stored.revision.reference.revision_id,
            parent=stored.revision.parent,
            changes=stored.revision.changes,
        )
        if checked.revision != stored.revision or (snapshot is not None and checked != snapshot):
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        await self.guard.require_current(principal, actual, attempt, checked)
        return principal, checked

    async def execute(self, record: JobRecord, attempt: ClaimedAttempt) -> ExecutedReview:
        # These imports require the reviewed controlled-workflow head/schema union.
        from appraisal_review.application.action_policy import ControlledActionPolicy
        from appraisal_review.application.bounded_workflow import BoundedWorkflowRunner
        from appraisal_review.application.controlled_workflow import (
            ControlledWorkflowCoordinator,
            RoutedControlledActionExecutor,
        )
        from appraisal_review.domain.service_contracts import ActionKind, WorkflowTermination

        _, snapshot = await self._current(record, attempt)
        session = _WorkflowSession(self, record, attempt, snapshot)
        coordinator = ControlledWorkflowCoordinator(
            snapshots=session,
            policy=ControlledActionPolicy(proposer_kind="model"),
            selector=self.selector,
            executor=RoutedControlledActionExecutor(
                {ActionKind.REVIEW: session, ActionKind.HUMAN: session}
            ),
            trace=self.trace,
            ledger=self.ledger,
            executor_actor=ActorReference(actor_id="integrated-workflow-executor", kind="system"),
            source_registry=snapshot.material.policy.registry,
        )
        with dispatch_async_authority(lambda: self._current(record, attempt, snapshot)):
            bounded = await BoundedWorkflowRunner(coordinator=coordinator, snapshots=session).run(
                record.current_run
            )
        await self._current(record, attempt, snapshot)
        if bounded.termination == WorkflowTermination.WAITING_FOR_HUMAN:
            # Read canonical persisted tasks even after coordinator reconstruction.
            view = await self.human_tasks.list_tasks(
                (await self._current(record, attempt, snapshot))[0], record.job_id
            )
            tasks = tuple(
                item.task
                for item in view.tasks
                if (item.task.run == record.current_run and item.task.state == "open")
            )
            if (
                not tasks
                or not bounded.events
                or bounded.events[-1].linked_task_id not in {t.task_id for t in tasks}
                or bounded.events[-1].tool_result is None
                or bounded.events[-1].tool_result.result_digest
                != content_digest(RootModel[tuple[HumanTask, ...]](tasks))
            ):
                raise ServiceFault(ServiceErrorCode.CONFLICT)
            return ExecutedReview(persisted_task_ids=tuple(task.task_id for task in tasks))
        if bounded.termination != WorkflowTermination.VERIFIED:
            raise ServiceFault(ServiceErrorCode.CAPABILITY)
        review = await self.reviews.read(record.current_run)
        if review is None:
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        review = _case_result(record.current_run, review)
        if (
            not bounded.events
            or bounded.events[-1].tool_result is None
            or bounded.events[-1].tool_result.result_digest != content_digest(review)
        ):
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        if self.project_result is not None:
            result = await self.project_result(record, attempt, review)
        else:
            if review.artifact_status == "written":
                raise ServiceFault(ServiceErrorCode.CAPABILITY)
            result = ServiceResult(
                run=record.current_run,
                result_version=attempt.expected_result_version + 1,
                execution_status=ExecutionStatus.SUCCEEDED,
                business_status=review.status,
                artifact_status=review.artifact_status,
                findings=tuple(review.case_review.findings) if review.case_review else (),
                verification=public_verification(review.verification),
            )
        result = ServiceResult.model_validate_json(result.model_dump_json())
        if (
            result.run != record.current_run
            or result.result_version != attempt.expected_result_version + 1
            or result.business_status != review.status
            or result.artifact_status != review.artifact_status
            or result.findings != tuple(review.case_review.findings if review.case_review else ())
            or result.verification != public_verification(review.verification)
        ):
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        await self._current(record, attempt, snapshot)
        return ExecutedReview(result=result)


class _WorkflowSession:
    def __init__(
        self,
        execution: IntegratedWorkflowExecution,
        record: JobRecord,
        attempt: ClaimedAttempt,
        snapshot: RevisionSnapshot,
    ) -> None:
        from appraisal_review.domain.service_contracts import (
            ActionPrerequisite,
            WorkflowSnapshot,
            WorkflowState,
        )

        material = snapshot.material
        if not material.policy.rule_sets or any(
            scoped.rules.status != "approved" for scoped in material.policy.rule_sets
        ):
            raise ServiceFault(ServiceErrorCode.CAPABILITY)
        if not material.facts.pairs or any(
            getattr(pair.pair, side).value is None
            or not getattr(pair, f"{side}_sources")
            or any(
                not material.policy.registry.resolves(ref)
                for ref in getattr(pair, f"{side}_sources")
            )
            for pair in material.facts.pairs
            for side in ("target", "comparable")
        ):
            raise ServiceFault(ServiceErrorCode.CAPABILITY)
        self.execution, self.record, self.attempt, self.snapshot = (
            execution,
            record,
            attempt,
            snapshot,
        )
        self.tasks: tuple[HumanTask, ...] = ()
        self.state = WorkflowSnapshot(
            state_version=1,
            run=record.current_run,
            revision=snapshot.revision,
            state=WorkflowState.MATERIAL_READY,
            budget=execution.budget,
            satisfied_prerequisites=(
                ActionPrerequisite.FORMS_PARSED,
                ActionPrerequisite.RULES_APPROVED,
                ActionPrerequisite.CRITICAL_EVIDENCE_AVAILABLE,
                ActionPrerequisite.MATERIAL_COMPLETE,
            ),
        )

    async def current(self, run: RunReference) -> WorkflowSnapshot:
        if run != self.record.current_run:
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        await self.execution._current(self.record, self.attempt, self.snapshot)
        return type(self.state).model_validate_json(self.state.model_dump_json())

    def _tasks(self, review: AgentReviewRun) -> tuple[HumanTask, ...]:
        assert review.case_review is not None
        blocked = {
            finding.id: finding
            for finding in review.case_review.findings
            if finding.status != "verified"
        }
        tasks = []
        confirmed_bindings: dict[tuple[str, str], set[str]] = {}
        confirmed_findings: set[str] = set()
        material = self.snapshot.material
        for binding in self.execution.bindings:
            finding_ids = tuple(identity for identity in binding.finding_ids if identity in blocked)
            if not finding_ids:
                continue
            if any(
                blocked[identity].kind == "approval"
                or blocked[identity].context != binding.context
                or (
                    blocked[identity].factor_id is not None
                    and blocked[identity].factor_id != binding.factor_id
                )
                for identity in finding_ids
            ):
                raise ServiceFault(ServiceErrorCode.CAPABILITY)
            pairs = [
                pair
                for pair in material.facts.pairs
                if (pair.context == binding.context and pair.pair.factor_id == binding.factor_id)
            ]
            if len(pairs) != 1:
                raise ServiceFault(ServiceErrorCode.CONFLICT)
            pair = pairs[0]
            reliability = getattr(pair, f"{binding.side}_reliability")
            confirmation = reliability.confirmation
            if (
                binding.kind == TaskKind.FACT
                and reliability.method == "reviewer_confirmed"
                and confirmation is not None
                and confirmation.input_digest == confirmation_digest(pair, binding.side)
                and not reliability.unresolved
                and reliability.selection != "ambiguous"
                and reliability.confidence_kind != "unknown"
                and reliability.provenance != "unknown"
            ):
                # One factor finding can cover both sides. The remaining side still
                # needs review; an exact prior response must not be asked again.
                confirmed_bindings.setdefault(
                    (binding.context.key(), binding.factor_id), set()
                ).add(binding.side)
                confirmed_findings.update(finding_ids)
                continue
            side = FactSideReference(
                context=binding.context,
                factor_id=binding.factor_id,
                side=binding.side,
                input_digest=confirmation_digest(pair, binding.side),
            )
            task = HumanTask(
                task_id=uuid5(self.record.current_run.run_id, binding.binding_id),
                run=self.record.current_run,
                version=1,
                kind=binding.kind,
                required_permission=Permission.CONFIRM
                if binding.kind == TaskKind.FACT
                else Permission.CORRECT,
                side=side,
                question=binding.question,
                evidence=tuple(getattr(pair, f"{binding.side}_sources")),
                finding_ids=finding_ids,
                allowed_responses=(
                    ResponseAction.CONFIRM
                    if binding.kind == TaskKind.FACT
                    else ResponseAction.CORRECT,
                    ResponseAction.REJECT,
                ),
                reason_code="controller-review-findings",
                affected_subject_ids=(binding.binding_id,),
            )
            tasks.append(task)
        covered = {identity for task in tasks for identity in task.finding_ids}
        mapped_contexts = {task.side.context.key() for task in tasks if task.side is not None}
        # Confirmation is a side assertion, not whole-material authorization. The
        # Controller retains these blockers until its independent final authority
        # permits the complete revision. Defer only known, bound approval symptoms;
        # never mark them verified or suppress them in the persisted review.
        approval_pending = (
            "trust" in blocked
            and blocked["trust"].kind == "approval"
            and blocked["trust"].status == "needs_review"
        )
        confirmed_pairs = {
            key for key, sides in confirmed_bindings.items() if sides == {"target", "comparable"}
        }
        confirmed_contexts = {
            entry.context.key()
            for entry in material.policy.inventory.contexts
            if all((entry.context.key(), factor) in confirmed_pairs for factor in entry.factor_ids)
        }
        deferred: set[str] = set()
        if approval_pending:
            for identity in confirmed_findings:
                finding = blocked[identity]
                if finding.context is None or finding.status != "needs_review":
                    continue
                key = finding.context.key()
                if (key, finding.factor_id) not in confirmed_pairs:
                    continue
                if (
                    finding.kind == "evidence_reliability"
                    and identity == f"{key}/factor/{finding.factor_id}"
                ) or (
                    finding.kind == "observed_unresolved"
                    and finding.trace == "Current source and exact-material authority required"
                    and any(
                        identity == f"observed/{slot.id}"
                        and slot.context == finding.context
                        and slot.factor_id == finding.factor_id
                        for slot in material.policy.inventory.slots
                    )
                ):
                    deferred.add(identity)
        calculation_contexts = mapped_contexts | (confirmed_contexts if approval_pending else set())
        if not tasks or any(
            identity not in covered | deferred
            and not (identity == "trust" and finding.kind == "approval")
            and not (
                finding.kind == "calculation"
                and finding.context is not None
                and identity == finding.context.key()
                and finding.context.key() in calculation_contexts
            )
            for identity, finding in blocked.items()
        ):
            raise ServiceFault(ServiceErrorCode.CAPABILITY)
        return tuple(tasks)

    async def invoke(self, proposal: ActionProposal) -> ControlledToolReceipt:
        from appraisal_review.domain.service_contracts import (
            ActionKind,
            ControlledToolReceipt,
            DeterministicReviewArguments,
            RequestHumanReviewArguments,
            ToolOutcome,
            WorkflowBlocker,
            WorkflowState,
        )

        principal, _ = await self.execution._current(self.record, self.attempt, self.snapshot)
        if proposal.run != self.record.current_run:
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        if proposal.action == ActionKind.REVIEW:
            if (
                self.state.state != WorkflowState.MATERIAL_READY
                or not isinstance(proposal.arguments, DeterministicReviewArguments)
                or proposal.arguments.revision != self.snapshot.revision.reference
                or proposal.arguments.rules != self.snapshot.revision.rules
            ):
                raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
            review = await self.execution.reviews.read(proposal.run)
            if review is None:
                from appraisal_review.ports.workflow_run_ledger import WorkflowExternalResultUnknown

                try:
                    invocation = await self.execution.controllers(
                        principal, proposal.run, self.snapshot
                    )
                    request = AgentReviewRequest.model_validate_json(
                        invocation.request.model_dump_json()
                    )
                    if request.case_id != proposal.run.revision.case_id:
                        raise ServiceFault(ServiceErrorCode.CONFLICT)
                    configured = invocation.controller
                    provider = MaterialProvider(self.snapshot.material)
                    controller = ReviewAgentController(
                        parser=configured.parser,
                        fact_extractor=provider,
                        rule_provider=provider,
                        verifier=configured.verifier,
                        pdf_writer=configured.pdf_writer,
                        audit_logger=configured.audit_logger,
                        minimum_confidence=configured.minimum_confidence,
                        authorization=configured.authorization,
                    )
                    review = _case_result(proposal.run, await controller.review(request))
                    await self.execution._current(self.record, self.attempt, self.snapshot)
                    await self.execution.reviews.put(proposal.run, review)
                    stored = await self.execution.reviews.read(proposal.run)
                    if stored != review:
                        raise ServiceFault(ServiceErrorCode.CONFLICT)
                except Exception:
                    # Parsing, writing or persistence may already have taken effect.
                    raise WorkflowExternalResultUnknown() from None
            review = _case_result(proposal.run, review)
            if (
                review.case_review is None
                or review.case_review.identity != self.snapshot.material.policy.identity
            ):
                raise ServiceFault(ServiceErrorCode.CONFLICT)
            blockers: tuple[WorkflowBlocker, ...] = ()
            state = WorkflowState.VERIFIED
            if review.status not in {WorkflowStatus.VERIFIED, WorkflowStatus.COMPLETED}:
                self.tasks = self._tasks(review)
                evidence = tuple(
                    {
                        ref.model_dump_json(): ref for task in self.tasks for ref in task.evidence
                    }.values()
                )
                blockers = (
                    WorkflowBlocker(
                        blocker_id="controller-findings",
                        reason_code="controller-review-findings",
                        affected_subject_ids=tuple(
                            binding for task in self.tasks for binding in task.affected_subject_ids
                        ),
                        evidence=evidence,
                    ),
                )
                state = WorkflowState.EVIDENCE_NEEDS_REVIEW
            self.state = self.state.model_copy(
                update={
                    "state": state,
                    "state_version": self.state.state_version + 1,
                    "unresolved_blockers": blockers,
                }
            )
            return ControlledToolReceipt(
                outcome=ToolOutcome(outcome="succeeded", result_digest=content_digest(review)),
                reason_code="controller-review-result",
                reviewer_summary="The Controller independently verified the stored revision.",
            )
        if proposal.action == ActionKind.HUMAN:
            arguments = proposal.arguments
            if (
                self.state.state != WorkflowState.EVIDENCE_NEEDS_REVIEW
                or not self.tasks
                or not isinstance(arguments, RequestHumanReviewArguments)
            ):
                raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
            blocker = self.state.unresolved_blockers[0]
            if (
                arguments.reason_code != blocker.reason_code
                or arguments.affected_subject_ids != blocker.affected_subject_ids
                or arguments.evidence != blocker.evidence
            ):
                raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
            await self.execution.registration.register(
                self.record, self.attempt, self.snapshot, self.tasks
            )
            principal, _ = await self.execution._current(self.record, self.attempt, self.snapshot)
            persisted = await self.execution.human_tasks.list_tasks(principal, self.record.job_id)
            actual = {view.task.task_id: view.task for view in persisted.tasks}
            if any(actual.get(task.task_id) != task for task in self.tasks):
                raise ServiceFault(ServiceErrorCode.CONFLICT)
            self.state = self.state.model_copy(
                update={
                    "state": WorkflowState.WAITING_FOR_HUMAN,
                    "state_version": self.state.state_version + 1,
                }
            )
            return ControlledToolReceipt(
                outcome=ToolOutcome(
                    outcome="succeeded",
                    result_digest=content_digest(RootModel[tuple[HumanTask, ...]](self.tasks)),
                ),
                reason_code="canonical-human-tasks-persisted",
                reviewer_summary="Exact revision tasks were persisted for the response service.",
                evidence=blocker.evidence,
                affected_subject_ids=blocker.affected_subject_ids,
                linked_task_id=self.tasks[0].task_id,
            )
        raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)


class CanonicalResponseReceiptAdapter:
    """Read-only reconciliation of an internal event against a canonical commit.

    HumanResponseResult remains an internal workflow event document, identified by
    its event_id and retaining its accepted response and optional authorization.
    It does not establish canonical job scheduling. Never serialize it as an HTTP
    ResponseReceipt, or discard its authorization while inventing a receipt.

    Conversion requires an independently committed canonical receipt plus its
    exact stored task, revision and current job/run. Unsupported authority events,
    absent commits and later job transitions fail closed; callers retain the
    original internal event document. This adapter never responds, schedules,
    creates approvals or writes either store. Historical API receipt replay stays
    solely with HumanTaskService, including after subsequent job transitions.
    """

    def __init__(self, store: HumanTaskStore) -> None:
        self.store = store

    async def convert(self, principal: Principal, event: HumanResponseResult) -> ResponseReceipt:
        event = HumanResponseResult.model_validate_json(event.model_dump_json())
        command = event.accepted.command
        principal.require(event.task.run.revision.case_id, Permission.REVIEW)
        if event.accepted.actor != principal.actor:
            raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
        if event.authorization is not None or event.task.kind not in {
            TaskKind.FACT,
            TaskKind.CORRECTION,
            TaskKind.EVIDENCE,
        }:
            raise ServiceFault(ServiceErrorCode.CAPABILITY)
        stored = await self.store.read_receipt(
            principal_id=principal.actor.actor_id, key=command.idempotency_key
        )
        if stored is None:
            raise ServiceFault(ServiceErrorCode.CAPABILITY)
        if (
            stored.principal_id != principal.actor.actor_id
            or stored.key != command.idempotency_key
            or stored.payload_digest != response_digest(command)
        ):
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        receipt = ResponseReceipt.model_validate_json(stored.receipt.model_dump_json())
        task = await self.store.read_task(task_id=event.task.task_id)
        job = await self.store.read_job(job_id=receipt.job.job_id)
        snapshot = await self.store.read_snapshot(revision=event.revision.reference)
        if (
            task is None
            or job is None
            or snapshot is None
            or task.task != event.task
            or task.job_id != job.job_id
            or task.principal_id != principal.actor.actor_id
            or task.case_id != job.case_id
            or job.principal_id != principal.actor.actor_id
            or receipt.job.case_id != job.case_id
            or receipt.task_id != event.task.task_id
            or receipt.consumed_version != command.expected_version
            or receipt.action != command.action
            or receipt.resumed_run != event.next_run
            or snapshot.revision != event.revision
            or task.current_revision != event.revision.reference
            or job.cancel_requested
            or job.status != receipt.job_status
            or job.current_run
            != (
                event.next_run
                or RunReference(run_id=event.task.run.run_id, revision=event.task.run.revision)
            )
            or receipt.revision
            != (None if command.action == ResponseAction.REJECT else event.revision.reference)
        ):
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        for task_id in receipt.superseded_task_ids:
            sibling = await self.store.read_task(task_id=task_id)
            if (
                sibling is None
                or sibling.job_id != job.job_id
                or sibling.principal_id != principal.actor.actor_id
                or sibling.task.state != "superseded"
                or sibling.task.run.revision != event.task.run.revision
            ):
                raise ServiceFault(ServiceErrorCode.CONFLICT)
        return receipt
