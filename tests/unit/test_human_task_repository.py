"""Single-process transaction, replay and detached-record guarantees at public ports."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from appraisal_review.adapters.local.human_tasks import NonDurableInMemoryHumanTaskRepository
from appraisal_review.adapters.local.synthetic import synthetic_material
from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.application.service_guards import Principal, ServiceFault
from appraisal_review.domain.case_review import CaseReviewer
from appraisal_review.domain.confidence import confirmation_digest
from appraisal_review.domain.service_contracts import (
    ActorReference,
    AuthorizationRecord,
    FactSideReference,
    HumanResponse,
    HumanResponseResult,
    HumanTask,
    Permission,
    ResponseAction,
    RunReference,
    ServiceErrorCode,
    TaskKind,
)
from appraisal_review.ports.service import HumanTaskTransition


def review(context):
    material = context.snapshot.material
    return CaseReviewer(None).review(material.policy, material.facts, material.policy.registry)


async def bootstrap():
    material = synthetic_material()
    pair = material.facts.pairs[0]
    pair.pair.target.confidence = 0.1
    snapshot = RevisionSnapshot.capture(material, "r1")
    run = RunReference(run_id=uuid4(), revision=snapshot.revision.reference)
    actual = CaseReviewer(None).review(material.policy, material.facts, material.policy.registry)
    finding = next(finding for finding in actual.findings if finding.kind == "factor")
    principal = Principal(
        ActorReference(actor_id="repository-reviewer", kind="human"),
        frozenset({run.revision.case_id}),
        frozenset(Permission),
    )
    task = HumanTask(
        task_id=uuid4(),
        run=run,
        version=1,
        kind=TaskKind.FACT,
        required_permission=Permission.CONFIRM,
        side=FactSideReference(
            context=pair.context,
            factor_id=pair.pair.factor_id,
            side="target",
            input_digest=confirmation_digest(pair, "target"),
        ),
        question="Confirm the target road width against the cited source location.",
        evidence=tuple(pair.target_sources),
        finding_ids=(finding.id,),
        allowed_responses=(ResponseAction.CONFIRM, ResponseAction.REJECT),
        reason_code=finding.kind,
        affected_subject_ids=("road-target",),
    )
    command = HumanResponse(
        task_id=task.task_id,
        expected_version=task.version,
        revision=run.revision,
        side_digest=task.side.input_digest,
        idempotency_key="response-1",
        action=ResponseAction.CONFIRM,
    )
    repository = NonDurableInMemoryHumanTaskRepository()
    await repository.register(snapshot, run, actual)
    return SimpleNamespace(
        repository=repository,
        principal=principal,
        snapshot=snapshot,
        run=run,
        review=actual,
        task=task,
        command=command,
    )


def new_revision(task, context):
    # This trusted pure callback models the application's append operation. The
    # application service separately tests the meaning of each response action.
    snapshot = context.snapshot.revise(context.snapshot.material, "r2")
    return HumanTaskTransition(
        snapshot, RunReference(run_id=uuid4(), revision=snapshot.revision.reference)
    )


def assert_code(error, code):
    assert error.value.problem.code == code


def test_task_creation_deduplicates_exact_requests_without_losing_finding_context():
    async def scenario():
        case = await bootstrap()
        repository, principal, task = case.repository, case.principal, case.task
        created = await repository.create_task(principal, task)
        assert created.finding_ids == task.finding_ids
        assert created.evidence == task.evidence
        assert await repository.create_task(principal, task) == created
        same_cause = task.model_copy(update={"task_id": uuid4()})
        assert await repository.create_task(principal, same_cause) == created
        for candidate in (
            task.model_copy(update={"question": "A different question"}),
            same_cause.model_copy(update={"question": "A different question"}),
        ):
            with pytest.raises(ServiceFault) as error:
                await repository.create_task(principal, candidate)
            assert_code(error, ServiceErrorCode.CONFLICT)
        assert await repository.get_task(principal, task.task_id) == created

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "update",
    [
        {"reason_code": None},
        {"affected_subject_ids": ()},
        {"finding_ids": ("invented-finding",)},
        {"version": 2},
        {"state": "answered"},
    ],
)
def test_new_tasks_require_complete_actual_finding_context(update):
    async def scenario():
        case = await bootstrap()
        invalid = case.task.model_copy(update=update)
        with pytest.raises(ServiceFault) as error:
            await case.repository.create_task(case.principal, invalid)
        assert_code(error, ServiceErrorCode.VALIDATION)
        with pytest.raises(ServiceFault) as absent:
            await case.repository.get_task(case.principal, invalid.task_id)
        assert_code(absent, ServiceErrorCode.NOT_FOUND)
        assert await case.repository.create_task(case.principal, case.task) == case.task

    asyncio.run(scenario())


def test_exact_replay_after_revision_advance_does_not_repeat_transform_or_event():
    async def scenario():
        case = await bootstrap()
        repository, principal = case.repository, case.principal
        await repository.create_task(principal, case.task)
        calls = []

        def transform(task, context):
            calls.append(context.snapshot.revision.reference)
            return new_revision(task, context)

        accepted = await repository.respond(principal, case.command, transform)
        assert accepted.task.state == "answered" and accepted.task.version == 2
        assert accepted.revision.parent == case.snapshot.revision.reference
        replay = await repository.respond(principal, case.command, transform)
        assert replay == accepted
        assert calls == [case.snapshot.revision.reference]
        assert await repository.responses(principal, case.run.revision.case_id) == (accepted,)
        assert (await repository.get_task(principal, case.task.task_id)).state == "answered"

    asyncio.run(scenario())


@pytest.mark.parametrize("revocation", ["permission", "case", "review", "actor"])
def test_exact_replay_rechecks_current_authority(revocation):
    async def scenario():
        case = await bootstrap()
        await case.repository.create_task(case.principal, case.task)
        accepted = await case.repository.respond(case.principal, case.command, new_revision)
        permissions = case.principal.permissions
        cases = case.principal.case_ids
        actor = case.principal.actor
        if revocation == "permission":
            permissions -= {Permission.CONFIRM}
        elif revocation == "review":
            permissions -= {Permission.REVIEW}
        elif revocation == "case":
            cases = frozenset()
        else:
            actor = actor.model_copy(update={"kind": "model"})
        revoked = Principal(actor, cases, permissions)
        with pytest.raises(ServiceFault) as error:
            await case.repository.respond(revoked, case.command, new_revision)
        assert_code(error, ServiceErrorCode.UNAUTHORIZED)
        assert await case.repository.respond(case.principal, case.command, new_revision) == accepted

    asyncio.run(scenario())


@pytest.mark.parametrize("conflict", ["payload", "new-key", "other-human"])
def test_answered_task_cannot_create_second_response(conflict):
    async def scenario():
        case = await bootstrap()
        repository, principal = case.repository, case.principal
        await repository.create_task(principal, case.task)
        accepted = await repository.respond(principal, case.command, new_revision)
        command = case.command
        if conflict == "payload":
            command = command.model_copy(update={"action": ResponseAction.REJECT})
        elif conflict == "new-key":
            command = command.model_copy(update={"idempotency_key": "another-key"})
        else:
            principal = Principal(
                ActorReference(actor_id="second-reviewer", kind="human"),
                principal.case_ids,
                principal.permissions,
            )
        with pytest.raises(ServiceFault) as error:
            await repository.respond(principal, command, new_revision)
        assert_code(error, ServiceErrorCode.CONFLICT)
        assert await repository.responses(case.principal, case.run.revision.case_id) == (accepted,)

    asyncio.run(scenario())


def test_concurrent_responses_append_exactly_one_child_and_one_event():
    async def scenario():
        case = await bootstrap()
        repository, principal = case.repository, case.principal
        await repository.create_task(principal, case.task)
        calls = []

        def transform(task, context):
            calls.append(task.task_id)
            return new_revision(task, context)

        responses = await asyncio.gather(
            repository.respond(principal, case.command, transform),
            repository.respond(
                principal,
                case.command.model_copy(update={"idempotency_key": "concurrent-key"}),
                transform,
            ),
            return_exceptions=True,
        )
        completed = [
            response for response in responses if isinstance(response, HumanResponseResult)
        ]
        rejected = [response for response in responses if isinstance(response, ServiceFault)]
        assert len(completed) == len(rejected) == len(calls) == 1
        assert rejected[0].problem.code == ServiceErrorCode.CONFLICT
        assert await repository.responses(principal, case.run.revision.case_id) == tuple(completed)
        result = await repository.reenter(principal, completed[0].next_run, review)
        assert result.identity.version == completed[0].revision.reference.revision_id

    asyncio.run(scenario())


def test_transform_failure_rolls_back_task_key_revision_event_and_detached_context():
    async def scenario():
        case = await bootstrap()
        repository, principal = case.repository, case.principal
        await repository.create_task(principal, case.task)

        def failing(task, context):
            context.review.findings[0].trace = "must not be saved"
            context.snapshot.material.facts.identity.version = "must-not-be-saved"
            context.snapshot.revise(context.snapshot.material, "r2")
            raise ValueError("synthetic transform failed")

        with pytest.raises(ValueError, match="synthetic transform failed"):
            await repository.respond(principal, case.command, failing)
        assert await repository.get_task(principal, case.task.task_id) == case.task
        context = await repository.context(principal, case.run)
        assert context.snapshot == case.snapshot
        assert context.review == case.review
        assert await repository.responses(principal, case.run.revision.case_id) == ()
        accepted = await repository.respond(principal, case.command, new_revision)
        assert accepted.revision.reference.revision_id == "r2"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "invalid", ["wrong-revision", "reused-run", "attempt", "missing-run", "wrong-parent"]
)
def test_invalid_transition_rolls_back_all_public_state(invalid):
    async def scenario():
        case = await bootstrap()
        repository, principal = case.repository, case.principal
        await repository.create_task(principal, case.task)

        def invalid_transition(task, context):
            change = new_revision(task, context)
            if invalid == "wrong-revision":
                return HumanTaskTransition(
                    change.snapshot, case.run.model_copy(update={"run_id": uuid4()})
                )
            if invalid == "reused-run":
                return HumanTaskTransition(
                    change.snapshot, change.next_run.model_copy(update={"run_id": case.run.run_id})
                )
            if invalid == "attempt":
                return HumanTaskTransition(
                    change.snapshot, change.next_run.model_copy(update={"attempt_id": uuid4()})
                )
            if invalid == "missing-run":
                return HumanTaskTransition(change.snapshot)
            orphan = RevisionSnapshot.capture(change.snapshot.material, "r2")
            return HumanTaskTransition(orphan, change.next_run)

        with pytest.raises(ServiceFault) as error:
            await repository.respond(principal, case.command, invalid_transition)
        assert_code(error, ServiceErrorCode.CONFLICT)
        assert await repository.get_task(principal, case.task.task_id) == case.task
        assert (await repository.context(principal, case.run)).snapshot == case.snapshot
        assert await repository.responses(principal, case.run.revision.case_id) == ()
        accepted = await repository.respond(principal, case.command, new_revision)
        assert accepted.revision.reference.revision_id == "r2"

    asyncio.run(scenario())


def test_pending_reentry_rejects_stale_result_and_keeps_work_available_for_retry():
    async def scenario():
        case = await bootstrap()
        repository, principal = case.repository, case.principal
        await repository.create_task(principal, case.task)
        accepted = await repository.respond(principal, case.command, new_revision)
        run = accepted.next_run
        with pytest.raises(ServiceFault) as pending:
            await repository.context(principal, run)
        assert_code(pending, ServiceErrorCode.CAPABILITY)
        with pytest.raises(ServiceFault) as stale:
            await repository.reenter(principal, run, lambda context: case.review)
        assert_code(stale, ServiceErrorCode.CONFLICT)
        with pytest.raises(ServiceFault) as still_pending:
            await repository.context(principal, run)
        assert_code(still_pending, ServiceErrorCode.CAPABILITY)
        recomputed = await repository.reenter(principal, run, review)
        assert recomputed.identity.version == "r2"
        assert (await repository.context(principal, run)).review == recomputed

        def should_not_repeat(context):
            pytest.fail("Already completed re-entry must return the stored result")

        assert await repository.reenter(principal, run, should_not_repeat) == recomputed
        with pytest.raises(ServiceFault) as old_run:
            await repository.reenter(principal, case.run, review)
        assert_code(old_run, ServiceErrorCode.CONFLICT)

    asyncio.run(scenario())


def test_new_run_on_same_revision_invalidates_old_work_and_supersedes_sibling_tasks():
    async def scenario():
        case = await bootstrap()
        repository, principal = case.repository, case.principal
        approval = case.task.model_copy(
            update={
                "task_id": uuid4(),
                "kind": TaskKind.MATERIAL,
                "required_permission": Permission.APPROVE_MATERIAL,
                "side": None,
                "question": "Approve this exact material after separate fact and rule review?",
                "finding_ids": ("trust",),
                "allowed_responses": (ResponseAction.APPROVE, ResponseAction.REJECT),
                "reason_code": "approval",
                "affected_subject_ids": ("material",),
            }
        )
        await repository.create_task(principal, approval)
        sibling = case.task
        await repository.create_task(principal, sibling)
        command = HumanResponse(
            task_id=approval.task_id,
            expected_version=approval.version,
            revision=approval.run.revision,
            idempotency_key="material-approval-1",
            action=ResponseAction.APPROVE,
        )

        def same_revision(task, context):
            return HumanTaskTransition(
                context.snapshot,
                RunReference(run_id=uuid4(), revision=context.snapshot.revision.reference),
                AuthorizationRecord(
                    authorization_id=uuid4(),
                    purpose="material",
                    revision=context.snapshot.revision,
                    actor=principal.actor,
                ),
            )

        accepted = await repository.respond(principal, command, same_revision)
        assert accepted.revision == case.snapshot.revision
        assert (await repository.get_task(principal, sibling.task_id)).state == "superseded"
        with pytest.raises(ServiceFault) as old_run:
            await repository.context(principal, case.run)
        assert_code(old_run, ServiceErrorCode.CONFLICT)
        await repository.reenter(principal, accepted.next_run, review)
        assert (await repository.context(principal, accepted.next_run)).snapshot == case.snapshot

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "invalid",
    ["no-child", "no-work", "side-authority", "rejection-change", "rejection-authority"],
)
def test_invalid_response_shape_rolls_back_task_key_revision_and_event(invalid):
    async def scenario():
        case = await bootstrap()
        repository, principal = case.repository, case.principal
        await repository.create_task(principal, case.task)
        command = case.command
        if invalid.startswith("rejection"):
            command = command.model_copy(update={"action": ResponseAction.REJECT})

        def malformed(task, context):
            if invalid == "no-child":
                return HumanTaskTransition(
                    context.snapshot,
                    RunReference(run_id=uuid4(), revision=context.snapshot.revision.reference),
                )
            if invalid == "no-work":
                return HumanTaskTransition(context.snapshot)
            change = new_revision(task, context)
            if invalid == "rejection-change":
                return change
            snapshot = context.snapshot if invalid == "rejection-authority" else change.snapshot
            authority = AuthorizationRecord(
                authorization_id=uuid4(),
                purpose="material",
                revision=snapshot.revision,
                actor=principal.actor,
            )
            return HumanTaskTransition(
                snapshot,
                change.next_run if invalid == "side-authority" else None,
                authority,
            )

        with pytest.raises(ValidationError):
            await repository.respond(principal, command, malformed)
        assert await repository.get_task(principal, case.task.task_id) == case.task
        assert (await repository.context(principal, case.run)).snapshot == case.snapshot
        assert await repository.responses(principal, case.run.revision.case_id) == ()
        accepted = await repository.respond(principal, case.command, new_revision)
        assert accepted.revision.reference.revision_id == "r2"

    asyncio.run(scenario())


def test_task_batch_failure_rolls_back_new_tasks_and_keeps_existing_records():
    async def scenario():
        case = await bootstrap()
        repository, principal = case.repository, case.principal
        await repository.create_task(principal, case.task)
        comparable = case.task.model_copy(
            update={
                "task_id": uuid4(),
                "affected_subject_ids": ("road-comparable",),
                "side": case.task.side.model_copy(
                    update={
                        "side": "comparable",
                        "input_digest": confirmation_digest(
                            case.snapshot.material.facts.pairs[0], "comparable"
                        ),
                    }
                ),
            }
        )
        invalid = comparable.model_copy(
            update={"task_id": uuid4(), "finding_ids": ("invented-finding",)}
        )
        with pytest.raises(ServiceFault) as error:
            await repository.create_tasks(principal, (comparable, invalid))
        assert_code(error, ServiceErrorCode.VALIDATION)
        assert await repository.get_task(principal, case.task.task_id) == case.task
        for absent_id in (comparable.task_id, invalid.task_id):
            with pytest.raises(ServiceFault) as absent:
                await repository.get_task(principal, absent_id)
            assert_code(absent, ServiceErrorCode.NOT_FOUND)
        assert await repository.create_tasks(principal, (case.task, comparable)) == (
            case.task,
            comparable,
        )

    asyncio.run(scenario())


def test_historical_revision_read_remains_exact_and_detached_after_advance():
    async def scenario():
        case = await bootstrap()
        repository, principal = case.repository, case.principal
        await repository.create_task(principal, case.task)
        accepted = await repository.respond(principal, case.command, new_revision)
        historical = await repository.read_revision(principal, case.snapshot.revision.reference)
        assert historical == case.snapshot
        historical.material.facts.pairs[0].pair.target.confidence = 1
        historical.revision.rules[0].context.target_id = "outside mutation"
        reread = await repository.read_revision(principal, case.snapshot.revision.reference)
        assert reread.material == case.snapshot.material
        assert reread.revision == case.snapshot.revision
        current = await repository.read_revision(principal, accepted.revision.reference)
        assert current.revision == accepted.revision
        assert current.material.facts.identity.version == "r2"
        with pytest.raises(ServiceFault) as stale_digest:
            await repository.read_revision(
                principal,
                case.snapshot.revision.reference.model_copy(update={"material_digest": "0" * 64}),
            )
        assert_code(stale_digest, ServiceErrorCode.CONFLICT)
        with pytest.raises(ServiceFault) as unknown:
            await repository.read_revision(
                principal,
                case.snapshot.revision.reference.model_copy(update={"revision_id": "unknown"}),
            )
        assert_code(unknown, ServiceErrorCode.NOT_FOUND)

    asyncio.run(scenario())


@pytest.mark.parametrize("missing", ["permission", "case"])
def test_historical_revision_read_requires_current_case_and_review_permission(missing):
    async def scenario():
        case = await bootstrap()
        principal = Principal(
            case.principal.actor,
            frozenset() if missing == "case" else case.principal.case_ids,
            case.principal.permissions - {Permission.REVIEW}
            if missing == "permission"
            else case.principal.permissions,
        )
        with pytest.raises(ServiceFault) as error:
            await case.repository.read_revision(principal, case.snapshot.revision.reference)
        assert_code(error, ServiceErrorCode.UNAUTHORIZED)
        assert (
            await case.repository.read_revision(case.principal, case.snapshot.revision.reference)
            == case.snapshot
        )

    asyncio.run(scenario())


def test_returned_nested_models_are_detached_from_persisted_records():
    async def scenario():
        case = await bootstrap()
        repository, principal = case.repository, case.principal
        created = await repository.create_task(principal, case.task)
        created.side.context.target_id = "outside-mutation"
        created.evidence[0].excerpt = "outside evidence"
        assert await repository.get_task(principal, case.task.task_id) == case.task
        context = await repository.context(principal, case.run)
        context.review.findings[0].trace = "outside review"
        assert (await repository.context(principal, case.run)).review == case.review
        accepted = await repository.respond(principal, case.command, new_revision)
        saved = accepted.model_dump_json()
        accepted.task.side.context.target_id = "outside response"
        accepted.revision.rules[0].context.target_id = "outside revision"
        replay = await repository.respond(principal, case.command, new_revision)
        assert replay.model_dump_json() == saved
        listed = await repository.responses(principal, case.run.revision.case_id)
        listed[0].task.evidence[0].excerpt = "outside listed evidence"
        assert (await repository.responses(principal, case.run.revision.case_id))[0] == replay

    asyncio.run(scenario())


@pytest.mark.parametrize("invalid", ["run", "result", "duplicate"])
def test_bootstrap_rejects_mismatched_revision_result_and_duplicate_case(invalid):
    async def scenario():
        case = await bootstrap()
        repository = NonDurableInMemoryHumanTaskRepository()
        run, result = case.run, case.review.model_copy(deep=True)
        if invalid == "run":
            run = run.model_copy(
                update={"revision": run.revision.model_copy(update={"revision_id": "wrong"})}
            )
        elif invalid == "result":
            result.identity.version = "wrong"
        else:
            repository = case.repository
        with pytest.raises(ServiceFault) as error:
            await repository.register(case.snapshot, run, result)
        assert_code(error, ServiceErrorCode.CONFLICT)
        if invalid != "duplicate":
            await repository.register(case.snapshot, case.run, case.review)
        assert (await repository.context(case.principal, case.run)).snapshot == case.snapshot

    asyncio.run(scenario())
