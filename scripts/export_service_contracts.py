"""Export the service-v1 schema and deterministic synthetic consumer fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from pydantic.json_schema import models_json_schema

from appraisal_review.adapters.local.service import LocalServiceConfiguration
from appraisal_review.adapters.local.synthetic import synthetic_material
from appraisal_review.application.human_tasks import task_view
from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.domain.confidence import confirm_side, confirmation_digest
from appraisal_review.domain.factor_models import WorkflowStatus
from appraisal_review.domain.job_contracts import (
    JobAcceptance,
    JobReference,
    JobStatus,
    JobStatusView,
)
from appraisal_review.domain.review_contracts import ReviewFinding
from appraisal_review.domain.service_contracts import (
    AcceptedResponse,
    ActionKind,
    ActionProposal,
    ActorReference,
    AllowedAction,
    ArtifactManifest,
    AuthorizationRecord,
    Budget,
    DecisionEvent,
    DocumentReference,
    ExecutionStatus,
    FactSideReference,
    HumanResponse,
    HumanTask,
    MaterialRevision,
    Permission,
    PublicValue,
    ResponseAction,
    ReviewSubmission,
    RevisionReference,
    RuleReference,
    RunReference,
    ServiceErrorCode,
    ServiceModel,
    ServiceProblem,
    ServiceResult,
    ServiceVerification,
    TaskKind,
    ToolOutcome,
    ValueRevision,
    VerificationDiagnostic,
)
from appraisal_review.domain.task_contracts import (
    ResponseReceipt,
    RevisionListView,
    TaskListView,
    TaskSubjectView,
    TaskView,
)

MODELS = (
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
    AcceptedResponse,
    AuthorizationRecord,
    AllowedAction,
    ActionProposal,
    Budget,
    ServiceProblem,
    ToolOutcome,
    DecisionEvent,
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


def fixtures() -> dict[str, ServiceModel]:
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
        action=ActionKind.HUMAN,
        proposer=ActorReference(actor_id="fixture-policy", kind="model"),
        evidence=tuple(finding.evidence),
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
    return {
        "revision": revision,
        "task": task,
        "response": response,
        "proposal": proposal,
        "decision-rejected": DecisionEvent(
            event_id=UUID(int=5),
            proposal=proposal,
            policy_version="fixture-policy-v1",
            disposition="rejected",
            reason_code="budget_exhausted",
            remaining_blockers=(finding.id,),
            budget=Budget(steps_remaining=0, model_calls_remaining=0, retries_remaining=0),
        ),
        "result-needs-review": needs_review,
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
