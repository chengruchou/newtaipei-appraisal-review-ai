"""Injected model and deterministic policies execute the same real local case tools."""

import asyncio
import json
from uuid import uuid4

import pytest

from appraisal_review.adapters.aws.action_selector import BedrockActionSelector, ModelSelectorConfig
from appraisal_review.adapters.local.action_selector import DeterministicActionSelector
from appraisal_review.adapters.local.approval import LocalApprovalStore, current_reviewer
from appraisal_review.adapters.local.controlled_case import LocalControlledCase
from appraisal_review.adapters.local.decision_trace import NonDurableInMemoryDecisionTrace
from appraisal_review.adapters.local.human_tasks import NonDurableInMemoryHumanTaskRepository
from appraisal_review.adapters.local.synthetic import synthetic_material
from appraisal_review.adapters.local.task_principal import LocalReviewerPrincipalResolver
from appraisal_review.application.action_policy import ControlledActionPolicy
from appraisal_review.application.bounded_workflow import BoundedWorkflowRunner
from appraisal_review.application.controlled_workflow import (
    ControlledWorkflowCoordinator,
    RoutedControlledActionExecutor,
)
from appraisal_review.application.material_corrections import MaterialSubject, MaterialSubjectMap
from appraisal_review.application.pause_resume import PauseResumeService
from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.application.workflow_tasks import HumanTaskBinding, WorkflowTaskService
from appraisal_review.domain.case_review import CaseReviewer
from appraisal_review.domain.factor_models import EvaluationStatus, NormalizedValue
from appraisal_review.domain.review_contracts import content_digest
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


class InjectedClient:
    """No provider connection: select only from the actual advertised workflow input."""

    def __init__(self):
        self.calls = []

    def converse(self, **kwargs):
        data = json.loads(kwargs["messages"][0]["content"][0]["text"])
        self.calls.append(data)
        action = data["allowed_actions"]["actions"][0]
        revision = data["snapshot"]["revision"]
        if action["action"] == "deterministic_review":
            arguments = {
                "kind": action["action"],
                "revision": revision["reference"],
                "rules": revision["rules"],
            }
        else:
            blocker = data["snapshot"]["unresolved_blockers"][0]
            arguments = {
                "kind": action["action"],
                "reason_code": blocker["reason_code"],
                "question": "Review the actual located findings.",
                "affected_subject_ids": blocker["affected_subject_ids"],
                "evidence": blocker["evidence"],
            }
        return {
            "stopReason": "end_turn",
            "output": {
                "message": {
                    "content": [
                        {
                            "text": json.dumps(
                                {
                                    "action": action["action"],
                                    "action_id": action["action_id"],
                                    "arguments": arguments,
                                }
                            )
                        }
                    ]
                }
            },
        }


def setup(tmp_path, *, blocked):
    material = synthetic_material()
    material.policy.rule_sets[0].rules.status = "approved"
    reviewer = current_reviewer()
    approval = LocalApprovalStore.initialize(tmp_path / "approval", reviewer)
    receipt = approval.approve(material, expected_digest=content_digest(material))
    pair = material.facts.pairs[0]
    if blocked:
        pair.pair.target.value = NormalizedValue(type="number", value=9, unit="m")
        pair.pair.target.raw_text = "9 m"
        pair.target_reliability.method = "model_proposed"
    snapshot = RevisionSnapshot.capture(material, "controlled-r1")
    run = RunReference(run_id=uuid4(), revision=snapshot.revision.reference)
    subjects = MaterialSubjectMap(
        (MaterialSubject("road-target", pair.context, pair.pair.factor_id, "target"),)
    )
    principal = LocalReviewerPrincipalResolver(
        reviewer, frozenset({run.revision.case_id}), frozenset(Permission)
    )
    repo = NonDurableInMemoryHumanTaskRepository()
    service = WorkflowTaskService(
        repository=repo, principals=principal, subjects=subjects, authorization=approval
    )
    return snapshot, run, subjects, principal, repo, service, approval, receipt


def bindings(snapshot, *, side_kind=None):
    material = HumanTaskBinding("material-approval", TaskKind.MATERIAL, ("trust",))
    if side_kind is None:
        return (material,)
    return (
        HumanTaskBinding(
            "road-review",
            side_kind,
            tuple(
                f.id
                for f in CaseReviewer(None)
                .review(
                    snapshot.material.policy,
                    snapshot.material.facts,
                    snapshot.material.policy.registry,
                )
                .findings
                if f.status != "verified" and f.id != "trust" and f.kind != "calculation"
            ),
            "road-target",
        ),
        material,
    )


async def execute(
    snapshot, run, repo, service, approval, mapping, *, model, reentry=False, direct_steps=0
):
    case = LocalControlledCase(
        snapshot=snapshot,
        run=run,
        budget=Budget(
            steps_remaining=4, model_calls_remaining=4, retries_remaining=1, time_remaining_ms=5000
        ),
        repository=repo,
        human_tasks=service,
        bindings=mapping,
        authorization=approval,
        reentry=reentry,
    )
    client = InjectedClient()
    selector = (
        BedrockActionSelector(
            client, ModelSelectorConfig(model_id="synthetic-integration", attempts=1)
        )
        if model
        else DeterministicActionSelector()
    )
    trace = NonDurableInMemoryDecisionTrace()
    coordinator = ControlledWorkflowCoordinator(
        snapshots=case,
        policy=ControlledActionPolicy(proposer_kind="model" if model else "system"),
        selector=selector,
        executor=RoutedControlledActionExecutor({ActionKind.REVIEW: case, ActionKind.HUMAN: case}),
        trace=trace,
        executor_actor=ActorReference(actor_id="local-executor", kind="system"),
        source_registry=snapshot.material.policy.registry,
    )
    prior = tuple([await coordinator.decide_once(run) for _ in range(direct_steps)])
    result = await BoundedWorkflowRunner(coordinator=coordinator, snapshots=case).run(run)
    assert result.events == await trace.read(run.run_id)
    assert result.events[:direct_steps] == prior
    assert len({event.event_id for event in result.events}) == len(result.events)
    replay = await BoundedWorkflowRunner(coordinator=coordinator, snapshots=case).run(run)
    assert replay == result
    assert await trace.read(run.run_id) == result.events
    if result.events[0].tool_result.result_digest is None:
        raise AssertionError(
            str([(f.id, f.kind, f.trace) for f in case.result.findings if f.status != "verified"])
        )
    assert result.events[0].tool_result.result_digest == content_digest(case.result)
    assert result.selection_failures == ()
    assert len(client.calls) == (len(result.events) if model else 0)
    return case, result


@pytest.mark.parametrize("model", [False, True])
@pytest.mark.parametrize("blocked,direct_steps", [(False, 1), (True, 1), (True, 2)])
def test_direct_to_bounded_preserves_history_and_terminal_replay(
    tmp_path, model, blocked, direct_steps
):
    async def scenario():
        snapshot, run, _, principals, repo, service, approval, _ = setup(tmp_path, blocked=blocked)
        case, result = await execute(
            snapshot,
            run,
            repo,
            service,
            approval,
            bindings(snapshot, side_kind=TaskKind.CORRECTION) if blocked else (),
            model=model,
            direct_steps=direct_steps,
        )
        assert [event.executed_action for event in result.events] == (
            [ActionKind.REVIEW, ActionKind.HUMAN] if blocked else [ActionKind.REVIEW]
        )
        assert result.final_budget == result.events[-1].budget_after
        if blocked:
            assert result.termination.value == "waiting_for_human"
            assert result.events[1].parent_event_ids == (result.events[0].event_id,)
            handoff = PauseResumeService(repo, principals)
            pause = await handoff.pause(result)
            assert pause.result.events == result.events
            assert set(pause.task_ids) == {task.task_id for task in case.tasks}
            assert len(pause.task_ids) == 2
            assert await handoff.pause(result) == pause
        else:
            assert result.termination.value == "verified"

    asyncio.run(scenario())


def command(task, action, *, correction=None):
    return HumanResponse(
        task_id=task.task_id,
        expected_version=task.version,
        revision=task.run.revision,
        side_digest=task.side.input_digest if task.side else None,
        result_digest=task.result_digest,
        idempotency_key=f"command-{uuid4().hex}",
        action=action,
        correction=correction,
    )


@pytest.mark.parametrize("model", [False, True])
@pytest.mark.parametrize("blocked", [False, True])
def test_selectors_execute_real_review_or_human_task_branches(tmp_path, model, blocked):
    async def scenario():
        snapshot, run, _, principal, repo, service, approval, _ = setup(tmp_path, blocked=blocked)
        case, result = await execute(
            snapshot,
            run,
            repo,
            service,
            approval,
            bindings(snapshot, side_kind=TaskKind.CORRECTION) if blocked else (),
            model=model,
        )
        if blocked:
            assert result.termination.value == "waiting_for_human"
            assert [event.executed_action for event in result.events] == [
                ActionKind.REVIEW,
                ActionKind.HUMAN,
            ]
            assert result.events[-1].parent_event_ids == (result.events[0].event_id,)
            assert len(case.tasks) == 2
            for task in case.tasks:
                assert (
                    await repo.get_task(await principal.current_principal(), task.task_id) == task
                )
            assert result.events[-1].linked_task_id == case.tasks[0].task_id
            assert case.result.status != EvaluationStatus.VERIFIED
        else:
            assert result.termination.value == "verified"
            assert case.result.status == EvaluationStatus.VERIFIED
            assert case.result.comparisons[0].summary.total_adjustment_percent == 5
            assert case.tasks == ()

    asyncio.run(scenario())


def test_model_handoff_correction_and_fresh_runs_recompute_through_real_core(tmp_path):
    async def scenario():
        original, run, subjects, principals, repo, service, approval, old_receipt = setup(
            tmp_path, blocked=True
        )
        principal = await principals.current_principal()
        case, _ = await execute(
            original,
            run,
            repo,
            service,
            approval,
            bindings(original, side_kind=TaskKind.CORRECTION),
            model=True,
        )
        old = subjects.public_value(original, "road-target")
        proposed = old.model_copy(
            update={"raw_text": "10 m", "value": NormalizedValue(type="number", value=10, unit="m")}
        )
        correction = command(
            case.tasks[0],
            ResponseAction.CORRECT,
            correction=ValueRevision(subject_id="road-target", original=old, proposed=proposed),
        )
        accepted = await service.respond(correction)
        assert await service.respond(correction) == accepted
        assert accepted.revision.parent == original.revision.reference
        changed = await repo.read_revision(principal, accepted.revision.reference)
        assert not approval.permits(changed.material)
        assert accepted.revision.reference.material_digest != old_receipt.material_digest
        corrected_case, corrected_run = await execute(
            changed,
            accepted.next_run,
            repo,
            service,
            approval,
            bindings(changed, side_kind=TaskKind.FACT),
            model=True,
            reentry=True,
        )
        assert corrected_run.termination.value == "waiting_for_human"
        confirmed = await service.respond(command(corrected_case.tasks[0], ResponseAction.CONFIRM))
        confirmed_snapshot = await repo.read_revision(principal, confirmed.revision.reference)
        await service.reenter(confirmed.next_run)
        material_task = await service.create_task(
            confirmed.next_run, kind=TaskKind.MATERIAL, finding_ids=("trust",)
        )
        assert not approval.permits(confirmed_snapshot.material)
        approval.approve(
            confirmed_snapshot.material, expected_digest=content_digest(confirmed_snapshot.material)
        )
        authorized = await service.respond(command(material_task, ResponseAction.APPROVE))
        final_case, final_run = await execute(
            confirmed_snapshot,
            authorized.next_run,
            repo,
            service,
            approval,
            (),
            model=True,
            reentry=True,
        )
        assert final_run.termination.value == "verified"
        assert final_case.result.comparisons[0].summary.total_adjustment_percent == 5
        assert await repo.read_revision(principal, original.revision.reference) == original
        assert confirmed_snapshot.material.facts.pairs[0].pair.target.confidence == old.confidence
        responses = await repo.responses(principal, original.revision.reference.case_id)
        assert len(responses) == 3
        assert [event.next_run.run_id for event in responses] == [
            accepted.next_run.run_id,
            confirmed.next_run.run_id,
            authorized.next_run.run_id,
        ]

    asyncio.run(scenario())
