"""Connected synthetic consumer scenario; no browser, HTTP or live model acceptance."""

import asyncio
from itertools import count
from uuid import UUID

from appraisal_review.adapters.local.action_selector import DeterministicActionSelector
from appraisal_review.adapters.local.controlled_case import LocalControlledCase
from appraisal_review.adapters.local.decision_trace import NonDurableInMemoryDecisionTrace
from appraisal_review.adapters.local.human_tasks import NonDurableInMemoryHumanTaskRepository
from appraisal_review.adapters.local.synthetic import synthetic_material
from appraisal_review.application.action_policy import ControlledActionPolicy
from appraisal_review.application.bounded_workflow import BoundedWorkflowRunner
from appraisal_review.application.controlled_workflow import (
    ControlledWorkflowCoordinator,
    RoutedControlledActionExecutor,
)
from appraisal_review.application.material_corrections import MaterialSubject, MaterialSubjectMap
from appraisal_review.application.pause_resume import PauseResumeService
from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.application.service_guards import Principal
from appraisal_review.application.workflow_tasks import HumanTaskBinding, WorkflowTaskService
from appraisal_review.domain.case_review import CaseReviewer
from appraisal_review.domain.document_models import DocumentModel
from appraisal_review.domain.factor_models import NormalizedValue
from appraisal_review.domain.service_contracts import (
    ActionKind,
    ActorReference,
    Budget,
    HumanResponse,
    Permission,
    ResponseAction,
    RunReference,
    TaskKind,
    ValueRevision,
)
from appraisal_review.ports.action_selection import ActionSelector


class FixturePrincipal:
    """Injected synthetic authority, not browser authentication."""

    async def current_principal(self) -> Principal:
        return Principal(
            actor=ActorReference(actor_id="synthetic-workbench-reviewer", kind="human"),
            case_ids=frozenset({"synthetic-case"}),
            permissions=frozenset(Permission),
        )


async def scenario(
    *,
    selector: ActionSelector | None = None,
    budget: Budget | None = None,
    confidence: float | None = None,
    evaluation: bool = False,
) -> dict[str, DocumentModel]:
    sequence = count(1900000)

    def next_id() -> UUID:
        return UUID(int=next(sequence))

    material = synthetic_material()
    material.policy.rule_sets[0].rules.status = "approved"
    pair = material.facts.pairs[0]
    pair.pair.target.value = NormalizedValue(type="number", value=9, unit="m")
    pair.pair.target.raw_text = "9 m"
    pair.target_reliability.method = "model_proposed"
    if confidence is not None:
        pair.pair.target.confidence = confidence
    snapshot = RevisionSnapshot.capture(material, "workbench-r1")
    run = RunReference(run_id=next_id(), revision=snapshot.revision.reference)
    subjects = MaterialSubjectMap(
        (MaterialSubject("road-target", pair.context, pair.pair.factor_id, "target"),)
    )
    repository = NonDurableInMemoryHumanTaskRepository(event_id_factory=next_id)
    principals = FixturePrincipal()
    service = WorkflowTaskService(
        repository=repository, principals=principals, subjects=subjects, id_factory=next_id
    )
    review = CaseReviewer(None).review(material.policy, material.facts, material.policy.registry)
    bindings = (
        HumanTaskBinding(
            "road-correction",
            TaskKind.CORRECTION,
            tuple(
                f.id
                for f in review.findings
                if f.status != "verified" and f.id != "trust" and f.kind != "calculation"
            ),
            "road-target",
        ),
        HumanTaskBinding("material-approval", TaskKind.MATERIAL, ("trust",)),
    )
    case = LocalControlledCase(
        snapshot=snapshot,
        run=run,
        budget=budget
        or Budget(
            steps_remaining=3, model_calls_remaining=0, retries_remaining=0, time_remaining_ms=5000
        ),
        repository=repository,
        human_tasks=service,
        bindings=bindings,
    )
    coordinator = ControlledWorkflowCoordinator(
        snapshots=case,
        policy=ControlledActionPolicy(
            proposer_kind="model"
            if selector is not None and selector.actor.kind == "model"
            else "system"
        ),
        selector=selector or DeterministicActionSelector(proposal_id_factory=next_id),
        executor=RoutedControlledActionExecutor({ActionKind.REVIEW: case, ActionKind.HUMAN: case}),
        trace=NonDurableInMemoryDecisionTrace(),
        executor_actor=ActorReference(actor_id="synthetic-workbench-executor", kind="system"),
        event_id_factory=next_id,
        monotonic=lambda: 1.0,
        source_registry=material.policy.registry,
    )
    result = await BoundedWorkflowRunner(coordinator=coordinator, snapshots=case).run(run)
    evaluation_records: dict[str, DocumentModel] = {}
    if evaluation:
        evaluation_records = {
            "evaluation-revision": snapshot.revision,
            "evaluation-budget": (await case.current(run)).budget,
            "evaluation-result": result,
        }
        if result.termination.value != "waiting_for_human":
            return evaluation_records
    pauses = PauseResumeService(repository, principals)
    pause = await pauses.pause(result)
    task = case.tasks[0]
    original = subjects.public_value(snapshot, "road-target")
    command = HumanResponse(
        task_id=task.task_id,
        expected_version=task.version,
        revision=run.revision,
        side_digest=task.side.input_digest if task.side else None,
        idempotency_key="workbench-correction-1",
        action=ResponseAction.CORRECT,
        correction=ValueRevision(
            subject_id="road-target",
            original=original,
            proposed=original.model_copy(
                update={
                    "raw_text": "10 m",
                    "value": NormalizedValue(type="number", value=10, unit="m"),
                }
            ),
        ),
    )
    response = await service.respond(command)
    if response.next_run is None or case.result is None:
        raise AssertionError("Actual correction must produce new pending work")
    recomputed = await service.reenter(response.next_run)
    return {
        **evaluation_records,
        "workbench-task": task,
        "workbench-approval-task": case.tasks[1],
        "workbench-original": original,
        "workbench-command": command,
        "workbench-pause": pause,
        "workbench-response": response,
        "workbench-continuation": await pauses.continuation(response.event_id),
        "workbench-before-review": case.result,
        "workbench-after-review": recomputed,
        "workbench-superseded-task": await repository.get_task(
            await principals.current_principal(), case.tasks[1].task_id
        ),
    }


def workbench_fixtures() -> dict[str, DocumentModel]:
    return asyncio.run(scenario())
