"""Local human review uses real material, reviewer receipts and deterministic recomputation."""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from appraisal_review.adapters.local.approval import LocalApprovalStore, current_reviewer
from appraisal_review.adapters.local.human_tasks import NonDurableInMemoryHumanTaskRepository
from appraisal_review.adapters.local.synthetic import synthetic_material
from appraisal_review.adapters.local.task_principal import LocalReviewerPrincipalResolver
from appraisal_review.application.human_tasks import HumanTaskBinding, HumanTaskService
from appraisal_review.application.material_corrections import MaterialSubject, MaterialSubjectMap
from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.application.service_guards import Principal, ServiceFault
from appraisal_review.domain.case_review import CaseReviewer
from appraisal_review.domain.factor_models import EvaluationStatus, NormalizedValue
from appraisal_review.domain.review_contracts import content_digest
from appraisal_review.domain.service_contracts import (
    ActorReference,
    FailureCategory,
    HumanResponse,
    HumanReviewHandoff,
    HumanTask,
    Permission,
    ResponseAction,
    RunReference,
    TaskKind,
    ValueRevision,
    WorkflowBlocker,
)


@dataclass
class Resolver:
    principal: Principal

    async def current_principal(self) -> Principal:
        return self.principal


def _mapping(snapshot: RevisionSnapshot) -> MaterialSubjectMap:
    pair = snapshot.material.facts.pairs[0]
    return MaterialSubjectMap(
        tuple(
            MaterialSubject(f"road-{side}", pair.context, pair.pair.factor_id, side)
            for side in ("target", "comparable")
        )
    )


def _command(task: HumanTask, action: ResponseAction, *, correction=None, key=None):
    return HumanResponse(
        task_id=task.task_id,
        expected_version=task.version,
        revision=task.run.revision,
        side_digest=task.side.input_digest if task.side else None,
        result_digest=task.result_digest,
        idempotency_key=key or f"response-{uuid4().hex}",
        action=action,
        correction=correction,
    )


async def _setup(tmp_path: Path, *, both_sides=False, missing=False):
    material = synthetic_material()
    material.policy.rule_sets[0].rules.status = "approved"
    reviewer = current_reviewer()
    approval = LocalApprovalStore.initialize(tmp_path / "task-approval", reviewer)
    old_receipt = approval.approve(material, expected_digest=content_digest(material))
    material.facts.pairs[0].target_reliability.method = "model_proposed"
    material.facts.pairs[0].pair.target.value.value = 9.0
    material.facts.pairs[0].pair.target.raw_text = "9 m"
    if both_sides:
        material.facts.pairs[0].comparable_reliability.method = "model_proposed"
    if missing:
        material.facts.pairs[0].pair.target.value = None
        material.facts.pairs[0].pair.target.evidence = []
        material.facts.pairs[0].target_sources = []
    snapshot = RevisionSnapshot.capture(material, "human-r1")
    run = RunReference(run_id=uuid4(), revision=snapshot.revision.reference)
    result = CaseReviewer(approval).review(
        material.policy, material.facts, material.policy.registry
    )
    repo = NonDurableInMemoryHumanTaskRepository()
    await repo.register(snapshot, run, result)
    principal = await LocalReviewerPrincipalResolver(
        reviewer, frozenset({run.revision.case_id}), frozenset(Permission)
    ).current_principal()
    resolver = Resolver(principal)
    subjects = _mapping(snapshot)
    service = HumanTaskService(
        repository=repo, principals=resolver, subjects=subjects, authorization=approval
    )
    finding = next(
        f for f in result.findings if f.kind == "evidence_reliability" and f.factor_id is not None
    )
    return snapshot, run, repo, resolver, subjects, service, approval, old_receipt, finding


def test_correction_confirmation_material_approval_and_reentry_use_real_core(tmp_path):
    async def scenario():
        original, run, repo, resolver, subjects, service, approval, receipt, finding = await _setup(
            tmp_path
        )
        task = await service.create_task(
            run, kind=TaskKind.CORRECTION, finding_ids=(finding.id,), subject_id="road-target"
        )
        assert task.reason_code == finding.kind
        assert task.evidence and task.affected_subject_ids == ("road-target",)
        old_value = subjects.public_value(original, "road-target")
        assert old_value.value.value == 9
        proposed = old_value.model_copy(
            update={"raw_text": "10 m", "value": NormalizedValue(type="number", value=10, unit="m")}
        )
        command = _command(
            task,
            ResponseAction.CORRECT,
            correction=ValueRevision(
                subject_id="road-target", original=old_value, proposed=proposed
            ),
        )
        corrected = await service.respond(command)
        assert corrected.next_run is not None and corrected.authorization is None
        assert corrected.revision.parent == original.revision.reference
        assert corrected.revision.changes[0].original == old_value
        assert corrected.revision.changes[0].corrected_by == resolver.principal.actor
        assert corrected.revision.changes[0].corrected.value.value == 10
        assert corrected.revision.reference.material_digest != receipt.material_digest
        assert await repo.read_revision(resolver.principal, original.revision.reference) == original
        assert await service.respond(command) == corrected
        with pytest.raises(ServiceFault, match="capability_unavailable"):
            await repo.context(resolver.principal, corrected.next_run)
        recomputed = await service.reenter(corrected.next_run)
        assert recomputed.status == EvaluationStatus.NEEDS_REVIEW
        changed_context = await repo.context(resolver.principal, corrected.next_run)
        assert not approval.permits(changed_context.snapshot.material)
        assert original.material.facts.pairs[0].pair.target.raw_text == old_value.raw_text
        fact = next(f for f in recomputed.findings if f.kind == "evidence_reliability")
        confirmation = await service.create_task(
            corrected.next_run, kind=TaskKind.FACT, finding_ids=(fact.id,), subject_id="road-target"
        )
        confirmed = await service.respond(_command(confirmation, ResponseAction.CONFIRM))
        assert confirmed.next_run is not None
        await service.reenter(confirmed.next_run)
        material_task = await service.create_task(
            confirmed.next_run, kind=TaskKind.MATERIAL, finding_ids=("trust",)
        )
        material_command = _command(material_task, ResponseAction.APPROVE)
        with pytest.raises(ServiceFault, match="capability_unavailable"):
            await service.respond(material_command)
        context = await repo.context(resolver.principal, confirmed.next_run)
        assert (
            context.snapshot.material.facts.pairs[0].pair.target.confidence == old_value.confidence
        )
        approval.approve(
            context.snapshot.material, expected_digest=content_digest(context.snapshot.material)
        )
        accepted = await service.respond(material_command)
        assert accepted.authorization.purpose == "material" and accepted.next_run is not None
        result = await service.reenter(accepted.next_run)
        assert result.status == EvaluationStatus.VERIFIED
        assert result.comparisons[0].summary.total_adjustment_percent == 5
        assert await service.reenter(accepted.next_run) == result
        publication = await service.create_task(
            accepted.next_run, kind=TaskKind.PUBLICATION, finding_ids=("trust",)
        )
        published = await service.respond(
            _command(publication, ResponseAction.AUTHORIZE_PUBLICATION)
        )
        assert published.authorization.purpose == "publication"
        assert published.authorization.result_digest == content_digest(result)
        assert published.next_run is None
        assert len(await repo.responses(resolver.principal, run.revision.case_id)) == 4

    asyncio.run(scenario())


def test_sequential_confirmations_reach_separate_material_approval(tmp_path):
    async def scenario():
        _, run, repo, resolver, _, service, approval, _, finding = await _setup(
            tmp_path, both_sides=True
        )
        for side in ("target", "comparable"):
            task = await service.create_task(
                run, kind=TaskKind.FACT, finding_ids=(finding.id,), subject_id=f"road-{side}"
            )
            response = await service.respond(_command(task, ResponseAction.CONFIRM))
            run = response.next_run
            await service.reenter(run)
        context = await repo.context(resolver.principal, run)
        pair = context.snapshot.material.facts.pairs[0]
        assert (
            pair.target_reliability.method
            == pair.comparable_reliability.method
            == "reviewer_confirmed"
        )
        assert len(context.confirmed_sides) == 2
        approval.approve(
            context.snapshot.material, expected_digest=content_digest(context.snapshot.material)
        )
        assert approval.permits(context.snapshot.material)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "field", ["expected_version", "side_digest", "result_digest", "revision", "task_id"]
)
def test_wrong_task_version_side_result_and_revision_fail_closed(tmp_path, field):
    async def scenario():
        _, run, repo, resolver, _, service, _, _, finding = await _setup(tmp_path)
        task = await service.create_task(
            run, kind=TaskKind.FACT, finding_ids=(finding.id,), subject_id="road-target"
        )
        command = _command(task, ResponseAction.CONFIRM)
        wrong = {
            "expected_version": 2,
            "side_digest": "a" * 64,
            "result_digest": "b" * 64,
            "revision": run.revision.model_copy(update={"revision_id": "stale"}),
            "task_id": UUID(int=0),
        }
        with pytest.raises(ServiceFault):
            await service.respond(command.model_copy(update={field: wrong[field]}))
        assert (await repo.get_task(resolver.principal, task.task_id)).state == "open"
        assert await repo.responses(resolver.principal, run.revision.case_id) == ()

    asyncio.run(scenario())


@pytest.mark.parametrize("actor_kind", ["model", "system"])
def test_nonhuman_response_never_changes_material(tmp_path, actor_kind):
    async def scenario():
        _, run, repo, resolver, _, service, _, _, finding = await _setup(tmp_path)
        task = await service.create_task(
            run, kind=TaskKind.FACT, finding_ids=(finding.id,), subject_id="road-target"
        )
        human = resolver.principal
        resolver.principal = Principal(
            ActorReference(actor_id="untrusted", kind=actor_kind), human.case_ids, human.permissions
        )
        with pytest.raises(ServiceFault, match="unauthorized"):
            await service.respond(_command(task, ResponseAction.CONFIRM))
        assert (await repo.get_task(human, task.task_id)).state == "open"

    asyncio.run(scenario())


def test_supply_missing_evidence_requires_its_explicit_task(tmp_path):
    async def scenario():
        snapshot, run, repo, resolver, subjects, service, _, _, finding = await _setup(
            tmp_path, missing=True
        )
        task = await service.create_task(
            run, kind=TaskKind.EVIDENCE, finding_ids=(finding.id,), subject_id="road-target"
        )
        original = subjects.public_value(snapshot, "road-target")
        reference = snapshot.material.facts.pairs[0].comparable_sources[0]
        proposed = original.model_copy(
            update={
                "state": "present",
                "value": NormalizedValue(type="number", value=10, unit="m"),
                "unit": "m",
                "raw_text": "10 m",
                "evidence": (reference,),
            }
        )
        correction = ValueRevision(subject_id="road-target", original=original, proposed=proposed)
        with pytest.raises(ServiceFault, match="unauthorized"):
            await service.respond(_command(task, ResponseAction.CORRECT, correction=correction))
        result = await service.respond(
            _command(task, ResponseAction.SUPPLY_EVIDENCE, correction=correction)
        )
        await service.reenter(result.next_run)
        context = await repo.context(resolver.principal, result.next_run)
        assert context.snapshot.material.facts.pairs[0].pair.target.value.value == 10
        assert (
            context.snapshot.material.facts.pairs[0].target_reliability.method == "model_proposed"
        )

    asyncio.run(scenario())


def test_rule_approval_records_only_rule_authority_and_preserves_changes(tmp_path):
    async def scenario():
        snapshot, _, _, resolver, subjects, _, approval, _, _ = await _setup(tmp_path)
        material = snapshot.material
        material.policy.rule_sets[0].rules.status = "candidate"
        snapshot = RevisionSnapshot.capture(material, "rules-r1")
        run = RunReference(run_id=uuid4(), revision=snapshot.revision.reference)
        review = CaseReviewer(approval).review(
            material.policy, material.facts, material.policy.registry
        )
        repo = NonDurableInMemoryHumanTaskRepository()
        await repo.register(snapshot, run, review)
        service = HumanTaskService(
            repository=repo, principals=resolver, subjects=subjects, authorization=approval
        )
        task = await service.create_task(run, kind=TaskKind.RULES, finding_ids=("trust",))
        response = await service.respond(_command(task, ResponseAction.APPROVE))
        assert response.authorization.purpose == "rules"
        result = await service.reenter(response.next_run)
        assert result.status == EvaluationStatus.NEEDS_REVIEW
        context = await repo.context(resolver.principal, response.next_run)
        assert context.snapshot.material.policy.rule_sets[0].rules.status == "approved"
        assert not approval.permits(context.snapshot.material)

    asyncio.run(scenario())


def test_reject_keeps_blockers_and_does_not_enqueue_or_approve(tmp_path):
    async def scenario():
        snapshot, run, repo, resolver, _, service, _, _, finding = await _setup(tmp_path)
        task = await service.create_task(
            run, kind=TaskKind.FACT, finding_ids=(finding.id,), subject_id="road-target"
        )
        result = await service.respond(_command(task, ResponseAction.REJECT))
        assert result.revision == snapshot.revision
        assert result.authorization is None and result.next_run is None
        assert (
            await repo.context(resolver.principal, run)
        ).review.status == EvaluationStatus.NEEDS_REVIEW

    asyncio.run(scenario())


def test_handoff_creates_every_bound_task_and_rejects_incomplete_mapping(tmp_path):
    async def scenario():
        _, run, repo, resolver, _, service, _, _, finding = await _setup(tmp_path)
        handoff = HumanReviewHandoff(
            reason_code="workflow-budget-exhausted",
            category=FailureCategory.MISSING_EVIDENCE,
            question="Inspect unresolved evidence before continuing.",
            revision=run.revision,
            blockers=(
                WorkflowBlocker(
                    blocker_id="road-blocker",
                    reason_code=finding.kind,
                    affected_subject_ids=("road-target",),
                    evidence=tuple(finding.evidence),
                ),
                WorkflowBlocker(
                    blocker_id="material-blocker",
                    reason_code="approval",
                    affected_subject_ids=("material",),
                ),
            ),
        )
        binding = HumanTaskBinding(
            "road-blocker", TaskKind.CORRECTION, (finding.id,), "road-target"
        )
        with pytest.raises(ServiceFault, match="capability_unavailable"):
            await service.create_from_handoff(run, handoff, (binding,))
        tasks = await service.create_from_handoff(
            run,
            handoff,
            (binding, HumanTaskBinding("material-blocker", TaskKind.MATERIAL, ("trust",))),
        )
        assert len(tasks) == 2
        assert tasks[0].finding_ids == (finding.id,)
        assert tasks[1].finding_ids == ("trust",)
        assert await repo.get_task(resolver.principal, tasks[0].task_id) == tasks[0]
        again = await service.create_from_handoff(
            run,
            handoff,
            (binding, HumanTaskBinding("material-blocker", TaskKind.MATERIAL, ("trust",))),
        )
        assert again == tasks
        changed = handoff.model_copy(
            update={"revision": run.revision.model_copy(update={"revision_id": "stale"})}
        )
        with pytest.raises(ServiceFault, match="version_conflict"):
            await service.create_from_handoff(run, changed, (binding,))
        wrong = handoff.model_copy(
            update={
                "blockers": (
                    handoff.blockers[0].model_copy(update={"reason_code": "invented"}),
                    handoff.blockers[1],
                )
            }
        )
        with pytest.raises(ServiceFault, match="version_conflict"):
            await service.create_from_handoff(
                run,
                wrong,
                (binding, HumanTaskBinding("material-blocker", TaskKind.MATERIAL, ("trust",))),
            )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "case",
    [
        "unknown-finding",
        "verified-finding",
        "missing-subject",
        "unknown-subject",
        "wrong-purpose",
        "unverified-publication",
        "rule-already-approved",
    ],
)
def test_task_creation_rejects_unbound_or_premature_requests(tmp_path, case):
    async def scenario():
        _, run, _, _, _, service, _, _, finding = await _setup(tmp_path)
        options = dict(kind=TaskKind.FACT, finding_ids=(finding.id,), subject_id="road-target")
        overrides = {
            "unknown-finding": dict(finding_ids=("invented",)),
            "verified-finding": dict(finding_ids=("sources",)),
            "missing-subject": dict(subject_id=None),
            "unknown-subject": dict(subject_id="unregistered"),
            "wrong-purpose": dict(kind=TaskKind.MATERIAL, subject_id=None),
            "unverified-publication": dict(kind=TaskKind.PUBLICATION, subject_id=None),
            "rule-already-approved": dict(
                kind=TaskKind.RULES, subject_id=None, finding_ids=("trust",)
            ),
        }
        options.update(overrides[case])
        with pytest.raises(ServiceFault):
            await service.create_task(run, **options)

    asyncio.run(scenario())


def test_posix_principal_cannot_claim_another_local_reviewer():
    reviewer = current_reviewer()
    resolver = LocalReviewerPrincipalResolver(
        reviewer.model_copy(update={"uid": reviewer.uid + 1}),
        frozenset({"synthetic-case"}),
        frozenset(Permission),
    )
    with pytest.raises(ServiceFault, match="unauthorized"):
        asyncio.run(resolver.current_principal())
