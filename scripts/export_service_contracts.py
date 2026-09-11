"""Export the service-v1 schema and deterministic synthetic consumer fixtures."""

from __future__ import annotations

import asyncio
import json
import runpy
from pathlib import Path
from uuid import UUID

from pydantic.json_schema import models_json_schema

from appraisal_review.adapters.local.action_selector import DeterministicActionSelector
from appraisal_review.adapters.local.decision_trace import NonDurableInMemoryDecisionTrace
from appraisal_review.adapters.local.golden_cases import GoldenAuthorization, golden_fixtures
from appraisal_review.adapters.local.service import LocalServiceConfiguration
from appraisal_review.adapters.local.synthetic import synthetic_material
from appraisal_review.application.action_policy import ControlledActionPolicy
from appraisal_review.application.bounded_workflow import BoundedWorkflowRunner
from appraisal_review.application.controlled_workflow import (
    ControlledWorkflowCoordinator,
    RoutedControlledActionExecutor,
)
from appraisal_review.application.human_tasks import task_view
from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.domain.case_review import CaseReviewer
from appraisal_review.domain.confidence import confirm_side, confirmation_digest
from appraisal_review.domain.document_models import DocumentModel
from appraisal_review.domain.factor_models import CaseReviewResult, ReviewMaterial, WorkflowStatus
from appraisal_review.domain.job_contracts import (
    JobAcceptance,
    JobReference,
    JobStatus,
    JobStatusView,
)
from appraisal_review.domain.review_contracts import ReviewFinding, content_digest
from appraisal_review.domain.service_contracts import (
    AcceptedResponse,
    ActionCost,
    ActionKind,
    ActionPrerequisite,
    ActionProposal,
    ActorReference,
    AllowedAction,
    AllowedActionSet,
    ArtifactManifest,
    AuthorizationRecord,
    BoundedWorkflowResult,
    Budget,
    BudgetConsumption,
    ControlledToolReceipt,
    DecisionEvent,
    DeterministicReviewArguments,
    DocumentReference,
    ExecutionStatus,
    ExtractPageArguments,
    FactSideReference,
    HumanResponse,
    HumanResponseResult,
    HumanReviewHandoff,
    HumanTask,
    InspectReferenceArguments,
    MaterialRevision,
    Permission,
    PublicValue,
    RequestHumanReviewArguments,
    ResponseAction,
    ReviewSubmission,
    RevisionReference,
    RuleReference,
    RunReference,
    SelectionFailureEvent,
    SelectorInput,
    ServiceErrorCode,
    ServiceProblem,
    ServiceResult,
    ServiceVerification,
    TaskKind,
    ToolOutcome,
    ValueRevision,
    VerificationDiagnostic,
    WorkflowBlocker,
    WorkflowContinuation,
    WorkflowPause,
    WorkflowSnapshot,
    WorkflowState,
)
from appraisal_review.domain.task_contracts import (
    ResponseReceipt,
    RevisionListView,
    TaskListView,
    TaskSubjectView,
    TaskView,
)

MODELS = (
    CaseReviewResult,
    WorkflowPause,
    WorkflowContinuation,
    DocumentReference,
    RevisionReference,
    RuleReference,
    ActorReference,
    PublicValue,
    ValueRevision,
    MaterialRevision,
    RunReference,
    FactSideReference,
    HumanTask,
    HumanResponse,
    HumanResponseResult,
    AcceptedResponse,
    AuthorizationRecord,
    WorkflowBlocker,
    WorkflowSnapshot,
    ActionCost,
    AllowedAction,
    AllowedActionSet,
    SelectorInput,
    ExtractPageArguments,
    InspectReferenceArguments,
    DeterministicReviewArguments,
    RequestHumanReviewArguments,
    ActionProposal,
    Budget,
    BudgetConsumption,
    ServiceProblem,
    ToolOutcome,
    DecisionEvent,
    SelectionFailureEvent,
    ControlledToolReceipt,
    HumanReviewHandoff,
    BoundedWorkflowResult,
    ArtifactManifest,
    ServiceResult,
    ServiceVerification,
    VerificationDiagnostic,
    ReviewSubmission,
    JobReference,
    JobStatusView,
    JobAcceptance,
    TaskView,
    TaskListView,
    TaskSubjectView,
    RevisionListView,
    ResponseReceipt,
)


class _FixtureSnapshots:
    def __init__(self, snapshot: WorkflowSnapshot) -> None:
        self.snapshot = snapshot

    async def current(self, run: RunReference) -> WorkflowSnapshot:
        return self.snapshot


class _FixtureReviewTool:
    def __init__(self, snapshots: _FixtureSnapshots) -> None:
        self.snapshots = snapshots

    async def invoke(self, proposal: ActionProposal) -> ControlledToolReceipt:
        material = synthetic_material()
        result = CaseReviewer(_FixtureAuthorization(content_digest(material))).review(
            material.policy, material.facts, material.policy.registry
        )
        state = (
            WorkflowState.VERIFIED
            if result.status.value == "verified"
            else WorkflowState.REVIEW_FAILED
        )
        self.snapshots.snapshot = self.snapshots.snapshot.model_copy(
            update={"state": state, "state_version": 2}
        )
        return ControlledToolReceipt(
            outcome=ToolOutcome(outcome="succeeded", result_digest=content_digest(result)),
            reason_code="deterministic-review-succeeded",
            reviewer_summary="The deterministic review returned a validated result.",
        )


class _FixtureAuthorization:
    """Exact synthetic fixture authority only; not authentication or a signed approval."""

    def __init__(self, digest: str) -> None:
        self.digest = digest

    def permits(self, material: ReviewMaterial) -> bool:
        return content_digest(material) == self.digest


async def _executed_workflow(revision: MaterialRevision) -> BoundedWorkflowResult:
    run = RunReference(run_id=UUID(int=6), revision=revision.reference)
    snapshot = WorkflowSnapshot(
        state_version=1,
        run=run,
        revision=revision,
        state=WorkflowState.MATERIAL_READY,
        satisfied_prerequisites=(
            ActionPrerequisite.FORMS_PARSED,
            ActionPrerequisite.RULES_APPROVED,
            ActionPrerequisite.CRITICAL_EVIDENCE_AVAILABLE,
            ActionPrerequisite.MATERIAL_COMPLETE,
        ),
        budget=Budget(
            steps_remaining=2,
            model_calls_remaining=0,
            retries_remaining=0,
            time_remaining_ms=5_000,
        ),
    )
    snapshots = _FixtureSnapshots(snapshot)
    coordinator = ControlledWorkflowCoordinator(
        snapshots=snapshots,
        policy=ControlledActionPolicy(proposer_kind="system"),
        selector=DeterministicActionSelector(proposal_id_factory=lambda: UUID(int=7)),
        executor=RoutedControlledActionExecutor({ActionKind.REVIEW: _FixtureReviewTool(snapshots)}),
        trace=NonDurableInMemoryDecisionTrace(),
        executor_actor=ActorReference(actor_id="fixture-controlled-executor", kind="system"),
        event_id_factory=lambda: UUID(int=8),
        monotonic=lambda: 1.0,
    )
    return await BoundedWorkflowRunner(
        coordinator=coordinator,
        snapshots=snapshots,
        retry_backoff_ms=0,
    ).run(run)


def fixtures() -> dict[str, DocumentModel]:
    material = synthetic_material()
    revision = RevisionSnapshot.capture(material, "fixture-r1").revision
    # The revision a confirmation commits: same observations, one recorded human assertion.
    answered = synthetic_material()
    confirm_side(answered.facts.pairs[0], "target", reviewer="fixture-reviewer")
    answered.policy.identity.version = answered.facts.identity.version = "fixture-r2"
    confirmed = RevisionSnapshot.capture(answered, "fixture-r2", parent=revision.reference).revision
    run = RunReference(run_id=UUID(int=1), revision=revision.reference)
    finding = ReviewFinding(
        id="fixture-confirmation",
        kind="confirmation",
        status="needs_review",
        evidence=material.facts.pairs[0].target_sources,
        trace="Synthetic observation requires explicit human confirmation.",
    )
    originating_material = next(
        fixture.material
        for fixture in golden_fixtures()
        if fixture.case_key == "missing-observation"
    )
    originating_review = CaseReviewer(GoldenAuthorization(originating_material)).review(
        originating_material.policy,
        originating_material.facts,
        originating_material.policy.registry,
    )
    originating_revision = RevisionSnapshot.capture(originating_material, "originating-fixture-r1")
    budget = Budget(
        steps_remaining=2,
        model_calls_remaining=1,
        retries_remaining=0,
        time_remaining_ms=5_000,
    )
    snapshot = WorkflowSnapshot(
        state_version=1,
        run=run,
        revision=revision,
        state=WorkflowState.EVIDENCE_NEEDS_REVIEW,
        satisfied_prerequisites=(
            ActionPrerequisite.CRITERIA_DOCUMENT,
            ActionPrerequisite.FORMS_DOCUMENT,
        ),
        unresolved_blockers=(
            WorkflowBlocker(
                blocker_id="fixture-confirmation",
                reason_code="low_confidence_observation",
                affected_subject_ids=("synthetic.road_width.target",),
                evidence=tuple(finding.evidence),
            ),
        ),
        budget=budget,
    )
    policy_version = "controlled-action-policy-v1"
    snapshot_digest = content_digest(snapshot)
    allowed_action = AllowedAction(
        action_id="request-human-review",
        action=ActionKind.HUMAN,
        permitted_states=(WorkflowState.EVIDENCE_NEEDS_REVIEW,),
        revision=revision.reference,
        rules=revision.rules,
        proposer_kinds=("model",),
        prerequisites=(
            ActionPrerequisite.CRITERIA_DOCUMENT,
            ActionPrerequisite.FORMS_DOCUMENT,
        ),
        cost=ActionCost(model_calls=1),
    )
    allowed_actions = AllowedActionSet(
        policy_version=policy_version,
        snapshot_digest=snapshot_digest,
        revision=revision.reference,
        documents=revision.documents,
        rules=revision.rules,
        actions=(allowed_action,),
    )
    task = HumanTask(
        task_id=UUID(int=2),
        run=run,
        version=1,
        kind=TaskKind.FACT,
        required_permission=Permission.CONFIRM,
        question="Confirm this synthetic observation.",
        side=FactSideReference(
            context=material.facts.pairs[0].context,
            factor_id="synthetic.road_width",
            side="target",
            input_digest=confirmation_digest(material.facts.pairs[0], "target"),
        ),
        evidence=tuple(finding.evidence),
        finding_ids=(finding.id,),
        allowed_responses=(ResponseAction.CONFIRM, ResponseAction.REJECT),
    )
    response = HumanResponse(
        task_id=task.task_id,
        expected_version=1,
        revision=revision.reference,
        side_digest=task.side.input_digest,
        idempotency_key="fixture-confirm-1",
        action=ResponseAction.CONFIRM,
    )
    proposal = ActionProposal(
        proposal_id=UUID(int=3),
        run=run,
        action_id=allowed_action.action_id,
        action=ActionKind.HUMAN,
        policy_version=policy_version,
        snapshot_digest=snapshot_digest,
        proposer=ActorReference(actor_id="fixture-policy", kind="model"),
        model_id="fixture-model",
        prompt_version="controlled-action-prompt-v1",
        input_tokens=250,
        output_tokens=80,
        latency_ms=125,
        attempt_count=1,
        arguments=RequestHumanReviewArguments(
            reason_code="low_confidence_observation",
            question="Request trusted review of the synthetic observation.",
            affected_subject_ids=("synthetic.road_width.target",),
            evidence=tuple(finding.evidence),
        ),
        proposer_rationale="The advertised blocker requires a human decision.",
    )
    needs_review = ServiceResult(
        run=run,
        result_version=1,
        execution_status=ExecutionStatus.SUCCEEDED,
        business_status=WorkflowStatus.NEEDS_REVIEW,
        findings=(finding,),
        verification=ServiceVerification(
            status="needs_review",
            critical_errors=(
                VerificationDiagnostic(
                    code="verification_blocker",
                    message=(
                        "Verification could not pass; inspect review findings "
                        "or request human review."
                    ),
                ),
            ),
        ),
    )
    job = JobReference(case_id=revision.reference.case_id, job_id=UUID(int=6))
    artifact = ArtifactManifest(
        artifact_id=UUID(int=4),
        content_hash="a" * 64,
        context=material.facts.pairs[0].context,
        field_ids=("road-rate",),
        page_count=1,
        template_hash="b" * 64,
        field_map_hash="c" * 64,
    )
    bounded_result = asyncio.run(_executed_workflow(revision))
    executed_decision = bounded_result.events[0]
    return {
        **runpy.run_path(str(Path(__file__).with_name("workbench_fixture.py")))[
            "workbench_fixtures"
        ](),
        **runpy.run_path(str(Path(__file__).with_name("human_task_fixture.py")))[
            "human_task_fixtures"
        ](),
        "revision": revision,
        "workflow-snapshot": snapshot,
        "allowed-actions": allowed_actions,
        "selector-input": SelectorInput(
            snapshot=snapshot,
            allowed_actions=allowed_actions,
            evidence=tuple(finding.evidence),
            budget=budget,
        ),
        "task": task,
        "response": response,
        "proposal": proposal,
        "decision-rejected": DecisionEvent(
            event_id=UUID(int=5),
            proposal=proposal,
            policy_version=policy_version,
            state_before=WorkflowState.EVIDENCE_NEEDS_REVIEW,
            state_after=WorkflowState.EVIDENCE_NEEDS_REVIEW,
            disposition="rejected",
            reason_code="proposal_rejected",
            reviewer_summary="The proposal was rejected before tool execution.",
            affected_subject_ids=("synthetic.road_width.target",),
            evidence=tuple(finding.evidence),
            remaining_blockers=(finding.id,),
            budget_before=budget,
            budget_after=budget.model_copy(update={"model_calls_remaining": 0}),
            budget_consumed=BudgetConsumption(steps=0, model_calls=1, retries=0, elapsed_ms=0),
        ),
        "decision-executed": executed_decision,
        "bounded-result": bounded_result,
        "result-needs-review": needs_review,
        "result-originating-fields": ServiceResult(
            run=RunReference(run_id=UUID(int=9), revision=originating_revision.revision.reference),
            result_version=1,
            execution_status=ExecutionStatus.SUCCEEDED,
            business_status=WorkflowStatus.NEEDS_REVIEW,
            findings=tuple(originating_review.findings),
        ),
        "result-source-binding-failed": ServiceResult(
            run=run,
            result_version=1,
            execution_status=ExecutionStatus.SUCCEEDED,
            business_status=WorkflowStatus.FAILED,
            verification=ServiceVerification(
                status="failed",
                critical_errors=(
                    VerificationDiagnostic(
                        code="source_binding",
                        message="Requested documents must match the configured review sources.",
                    ),
                ),
            ),
        ),
        "result-written": ServiceResult(
            run=run,
            result_version=1,
            execution_status=ExecutionStatus.SUCCEEDED,
            business_status=WorkflowStatus.COMPLETED,
            artifact_status="written",
            artifacts=(artifact,),
            verification=ServiceVerification(status="verified"),
        ),
        "job-acceptance": JobAcceptance(job=job, run=run),
        "job-status-queued": JobStatusView(job=job, job_status=JobStatus.QUEUED, current_run=run),
        "job-status-waiting": JobStatusView(
            job=job,
            job_status=JobStatus.WAITING_FOR_HUMAN,
            current_run=run,
            open_task_ids=(task.task_id,),
        ),
        # A running attempt that has been asked to stop. Without cancel_requested a
        # consumer cannot tell this apart from an ordinary running job, so the principal
        # who cancelled sees no trace of the decision until it lands.
        "job-status-cancelling": JobStatusView(
            job=job,
            job_status=JobStatus.RUNNING,
            current_run=run,
            attempt_count=1,
            cancel_requested=True,
        ),
        "job-status-failed": JobStatusView(
            job=job,
            job_status=JobStatus.FAILED,
            current_run=run,
            attempt_count=5,
            problem=ServiceProblem(code=ServiceErrorCode.EXECUTION),
        ),
        "submission": ReviewSubmission(
            revision=revision.reference,
            documents=revision.documents,
            idempotency_key="fixture-submit-1",
        ),
        "task-list": TaskListView(job=job, tasks=(task_view(task),)),
        "task-subject": TaskSubjectView(
            task_id=task.task_id,
            revision=revision.reference,
            subject_id=task_view(task).subject_id or "",
            observation=PublicValue(
                state="present",
                value=material.facts.pairs[0].pair.target.value,
                raw_text=material.facts.pairs[0].pair.target.raw_text or "",
                unit="m",
                confidence=material.facts.pairs[0].pair.target.confidence,
                evidence=tuple(material.facts.pairs[0].target_sources),
            ),
            required_type="number",
            required_unit="m",
            unit_required=True,
        ),
        "revision-list": RevisionListView(job=job, revisions=(revision,)),
        "response-receipt": ResponseReceipt(
            task_id=task.task_id,
            consumed_version=1,
            action=ResponseAction.CONFIRM,
            job=job,
            job_status=JobStatus.QUEUED,
            revision=confirmed.reference,
            resumed_run=RunReference(run_id=UUID(int=7), revision=confirmed.reference),
        ),
        "problem": ServiceProblem(code=ServiceErrorCode.CONFLICT),
        "value": ValueRevision(
            subject_id="road-rate",
            original=PublicValue(
                state="blank", raw_text="", confidence=0, evidence=tuple(finding.evidence)
            ),
            proposed=PublicValue(
                state="present",
                value="5.00",
                raw_text="Synthetic proposed value",
                confidence=0,
                evidence=tuple(finding.evidence),
            ),
        ),
    }


def export(root: Path) -> None:
    _, schema = models_json_schema([(m, "serialization") for m in MODELS], title="Service v1")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    directory = root / "schemas"
    directory.mkdir(exist_ok=True)
    (directory / "service-v1.json").write_text(json.dumps(schema, indent=2) + "\n")
    (directory / "local-service-v1.json").write_text(
        json.dumps(LocalServiceConfiguration.model_json_schema(mode="serialization"), indent=2)
        + "\n"
    )
    material = synthetic_material()
    local = LocalServiceConfiguration.model_validate_json(
        json.dumps(
            {
                "schema_version": "local-service-v1",
                "inputs": {
                    "identity": material.policy.identity.model_dump(mode="json"),
                    "documents": [
                        {
                            "path": f"/absolute/operator/{d.role}.pdf",
                            "document_id": d.document_id,
                            "version": d.version,
                            "role": d.role,
                            "expected_hash": "0" * 64,
                        }
                        for d in material.policy.registry.documents
                    ],
                },
                "material_path": "/absolute/operator/material.json",
                "expected_material_digest": "0" * 64,
                "revision_id": "operator-r1",
                "approval_store": "/absolute/operator/approval",
                "writer": None,
            }
        )
    )
    (root / "examples").mkdir(exist_ok=True)
    (root / "examples/local-service-v1.json").write_text(local.model_dump_json(indent=2) + "\n")
    examples = root / "examples" / "service-v1"
    examples.mkdir(parents=True, exist_ok=True)
    index = {}
    for name, model in fixtures().items():
        (examples / f"{name}.json").write_text(model.model_dump_json(indent=2) + "\n")
        index[f"{name}.json"] = type(model).__name__
    (examples / "index.json").write_text(json.dumps(index, indent=2) + "\n")


if __name__ == "__main__":
    export(Path(__file__).resolve().parents[1])
