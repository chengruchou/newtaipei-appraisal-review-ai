"""What answering a task actually commits, and what it refuses to commit.

The interesting cases here are the ones where a plausible implementation would quietly do
the wrong thing: a confirmation routed through `revise` would erase itself, a correction
that kept its old confirmations would carry human authority across a value change, and a
stale task answered after someone else moved the revision would overwrite newer material.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest

from appraisal_review.adapters.local.human_task_store import LocalHumanTaskStore
from appraisal_review.adapters.local.job_store import InMemoryJobStore
from appraisal_review.adapters.local.synthetic import synthetic_material
from appraisal_review.application.human_tasks import HumanTaskService, side_subject_id
from appraisal_review.application.job_state import JobEvent
from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.application.service_guards import Principal, ServiceFault
from appraisal_review.domain.confidence import confirm_side, confirmation_digest
from appraisal_review.domain.job_contracts import JobStatus
from appraisal_review.domain.review_contracts import ComparisonContext
from appraisal_review.domain.service_contracts import (
    ActorReference,
    FactSideReference,
    HumanResponse,
    HumanTask,
    Permission,
    PublicValue,
    ResponseAction,
    ServiceErrorCode,
    TaskKind,
    ValueRevision,
)
from appraisal_review.ports.human_tasks import HumanTaskStore
from appraisal_review.testing.job_store_contract import NOW, submission

CASE = "synthetic-case"
ACTOR = "reviewer-one"


def caller(
    actor_id: str = ACTOR,
    *,
    case_id: str = CASE,
    permissions: frozenset[Permission] | None = None,
) -> Principal:
    return Principal(
        actor=ActorReference(actor_id=actor_id, kind="human"),
        case_ids=frozenset({case_id}),
        permissions=permissions
        or frozenset({Permission.REVIEW, Permission.CONFIRM, Permission.CORRECT}),
    )


def fact_task(
    snapshot: RevisionSnapshot, run_id: UUID, *, task_id: UUID | None = None
) -> HumanTask:
    pair = snapshot.material.facts.pairs[0]
    return HumanTask(
        task_id=task_id or uuid4(),
        run={"run_id": run_id, "revision": snapshot.revision.reference},
        version=1,
        kind=TaskKind.FACT,
        required_permission=Permission.CONFIRM,
        side=FactSideReference(
            context=pair.context,
            factor_id=pair.pair.factor_id,
            side="target",
            input_digest=confirmation_digest(pair, "target"),
        ),
        question="Is the target road width as extracted?",
        finding_ids=("synthetic.road_width",),
        allowed_responses=(ResponseAction.CONFIRM, ResponseAction.REJECT),
    )


def correction_task(
    snapshot: RevisionSnapshot, run_id: UUID, *, task_id: UUID | None = None
) -> HumanTask:
    pair = snapshot.material.facts.pairs[0]
    return HumanTask(
        task_id=task_id or uuid4(),
        run={"run_id": run_id, "revision": snapshot.revision.reference},
        version=1,
        kind=TaskKind.CORRECTION,
        required_permission=Permission.CORRECT,
        side=FactSideReference(
            context=pair.context,
            factor_id=pair.pair.factor_id,
            side="target",
            input_digest=confirmation_digest(pair, "target"),
        ),
        question="Correct the target road width.",
        finding_ids=("synthetic.road_width",),
        allowed_responses=(ResponseAction.CORRECT, ResponseAction.REJECT),
    )


class Harness:
    def __init__(self) -> None:
        self.jobs = InMemoryJobStore()
        self.tasks = LocalHumanTaskStore(self.jobs)
        self.service = HumanTaskService(
            self.tasks, clock=lambda: NOW, new_revision_id=self._revision_id
        )
        self.snapshot = RevisionSnapshot.capture(synthetic_material(), "r1")
        self.job_id = uuid4()
        self.run_id = uuid4()
        self._minted = 0

    def _revision_id(self) -> str:
        self._minted += 1
        return f"r{self._minted + 1}"

    async def setup(self, tasks: tuple[HumanTask, ...], *, principal: Principal) -> None:
        """Bring a job to waiting_for_human the way a finished attempt would have."""
        record, _ = await self.jobs.create_job(
            principal,
            submission(case_id=CASE, revision_id="r1", key="job-key"),
            job_id=self.job_id,
            run_id=self.run_id,
            now=NOW,
        )
        attempt = await self.jobs.claim(
            job_id=self.job_id, run_id=self.run_id, owner=uuid4(), lease_seconds=60, now=NOW
        )
        await self.jobs.finish(
            attempt,
            event=JobEvent.NEEDS_HUMAN,
            now=NOW,
            open_task_ids=tuple(task.task_id for task in tasks),
        )
        self.tasks.seed(
            job_id=self.job_id,
            principal_id=principal.actor.actor_id,
            snapshot=self.snapshot,
            tasks=tasks,
        )
        assert record.case_id == CASE


def confirming(harness: Harness, task: HumanTask, *, key: str = "k1") -> HumanResponse:
    return HumanResponse(
        task_id=task.task_id,
        expected_version=task.version,
        revision=harness.snapshot.revision.reference,
        side_digest=task.side.input_digest if task.side else None,
        idempotency_key=key,
        action=ResponseAction.CONFIRM,
    )


def correcting(
    harness: Harness, task: HumanTask, *, key: str = "k1", metres: float = 12.0
) -> HumanResponse:
    assert task.side is not None
    return HumanResponse(
        task_id=task.task_id,
        expected_version=task.version,
        revision=harness.snapshot.revision.reference,
        side_digest=task.side.input_digest,
        idempotency_key=key,
        action=ResponseAction.CORRECT,
        correction=ValueRevision(
            subject_id=side_subject_id(task.side),
            original=PublicValue(state="blank", raw_text="unused by the server"),
            proposed=PublicValue(
                state="present",
                value={"type": "number", "value": metres, "unit": "m"},
                raw_text=f"corrected to {metres} m",
                unit="m",
                confidence=0.5,
            ),
        ),
    )


def test_local_store_satisfies_the_port() -> None:
    store: HumanTaskStore = LocalHumanTaskStore(InMemoryJobStore())
    assert store is not None


def test_confirmation_commits_a_revision_that_keeps_the_confirmation() -> None:
    async def scenario() -> None:
        harness = Harness()
        principal = caller()
        task = fact_task(harness.snapshot, harness.run_id)
        await harness.setup((task,), principal=principal)

        receipt = await harness.service.respond(principal, task.task_id, confirming(harness, task))

        assert receipt.revision is not None and receipt.resumed_run is not None
        assert receipt.resumed_run.revision == receipt.revision
        assert receipt.job_status == JobStatus.QUEUED
        stored = await harness.tasks.read_snapshot(revision=receipt.revision)
        assert stored is not None
        reliability = stored.material.facts.pairs[0].target_reliability
        # Routing a confirmation through `revise` would have cleared exactly this.
        assert reliability.method == "reviewer_confirmed"
        assert reliability.confirmation is not None
        assert reliability.confirmation.reviewer == ACTOR
        # The observed value itself is untouched: confirming is an assertion about the
        # material, not a change to it.
        assert stored.material.facts.pairs[0].pair.target.value.value == 10.0

    asyncio.run(scenario())


def test_a_correction_clears_confirmations_and_never_raises_confidence() -> None:
    async def scenario() -> None:
        harness = Harness()
        principal = caller()
        material = synthetic_material()
        # The comparable side arrives already confirmed, so we can watch it be cleared.
        confirm_side(material.facts.pairs[0], "comparable", reviewer="someone-earlier")
        harness.snapshot = RevisionSnapshot.capture(material, "r1")
        task = correction_task(harness.snapshot, harness.run_id)
        await harness.setup((task,), principal=principal)

        receipt = await harness.service.respond(
            principal, task.task_id, correcting(harness, task, metres=12.0)
        )

        assert receipt.revision is not None
        stored = await harness.tasks.read_snapshot(revision=receipt.revision)
        assert stored is not None
        pair = stored.material.facts.pairs[0]
        assert pair.pair.target.value.value == 12.0
        assert pair.pair.target.confidence <= 0.5
        assert pair.comparable_reliability.confirmation is None
        assert pair.target_reliability.confirmation is None
        chain = await harness.tasks.list_revisions(job_id=harness.job_id)
        change = chain[-1].changes[0]
        assert change.corrected_by is not None and change.corrected_by.actor_id == ACTOR
        assert change.original.value.value == 10.0

    asyncio.run(scenario())


def test_a_rejection_records_an_answer_and_commits_no_revision() -> None:
    async def scenario() -> None:
        harness = Harness()
        principal = caller()
        task = fact_task(harness.snapshot, harness.run_id)
        await harness.setup((task,), principal=principal)
        command = confirming(harness, task).model_copy(update={"action": ResponseAction.REJECT})

        receipt = await harness.service.respond(principal, task.task_id, command)

        assert receipt.revision is None and receipt.resumed_run is None
        assert receipt.job_status == JobStatus.FAILED
        job = await harness.jobs.read_job(job_id=harness.job_id)
        assert job is not None and job.open_task_ids == () and job.problem is not None
        assert len(await harness.tasks.list_revisions(job_id=harness.job_id)) == 1

    asyncio.run(scenario())


def test_two_reviewers_racing_one_task_produce_one_transition() -> None:
    async def scenario() -> None:
        harness = Harness()
        principal = caller()
        task = fact_task(harness.snapshot, harness.run_id)
        await harness.setup((task,), principal=principal)

        first = confirming(harness, task, key="race-a")
        second = confirming(harness, task, key="race-b")
        outcomes = await asyncio.gather(
            harness.service.respond(principal, task.task_id, first),
            harness.service.respond(principal, task.task_id, second),
            return_exceptions=True,
        )

        committed = [o for o in outcomes if not isinstance(o, BaseException)]
        refused = [o for o in outcomes if isinstance(o, ServiceFault)]
        assert len(committed) == 1 and len(refused) == 1
        assert refused[0].problem.code == ServiceErrorCode.CONFLICT
        # Exactly one new revision exists, so the loser wrote nothing at all.
        assert len(await harness.tasks.list_revisions(job_id=harness.job_id)) == 2

    asyncio.run(scenario())


def test_a_stale_task_cannot_overwrite_newer_material() -> None:
    async def scenario() -> None:
        harness = Harness()
        principal = caller()
        answered = fact_task(harness.snapshot, harness.run_id)
        stale = correction_task(harness.snapshot, harness.run_id)
        await harness.setup((answered, stale), principal=principal)

        first = await harness.service.respond(
            principal, answered.task_id, confirming(harness, answered, key="first")
        )
        assert stale.task_id in first.superseded_task_ids

        with pytest.raises(ServiceFault) as refused:
            await harness.service.respond(
                principal, stale.task_id, correcting(harness, stale, key="second")
            )

        assert refused.value.problem.code == ServiceErrorCode.CONFLICT
        assert len(await harness.tasks.list_revisions(job_id=harness.job_id)) == 2

    asyncio.run(scenario())


def test_an_exact_replay_returns_the_first_receipt_and_commits_once() -> None:
    async def scenario() -> None:
        harness = Harness()
        principal = caller()
        task = fact_task(harness.snapshot, harness.run_id)
        await harness.setup((task,), principal=principal)
        command = confirming(harness, task, key="same")

        first = await harness.service.respond(principal, task.task_id, command)
        again = await harness.service.respond(principal, task.task_id, command)

        assert first == again
        assert len(await harness.tasks.list_revisions(job_id=harness.job_id)) == 2

    asyncio.run(scenario())


def test_the_same_key_with_a_different_payload_conflicts() -> None:
    async def scenario() -> None:
        harness = Harness()
        principal = caller()
        task = correction_task(harness.snapshot, harness.run_id)
        await harness.setup((task,), principal=principal)

        await harness.service.respond(
            principal, task.task_id, correcting(harness, task, key="k", metres=12.0)
        )
        with pytest.raises(ServiceFault) as refused:
            await harness.service.respond(
                principal, task.task_id, correcting(harness, task, key="k", metres=99.0)
            )

        assert refused.value.problem.code == ServiceErrorCode.CONFLICT

    asyncio.run(scenario())


def test_another_principals_task_is_reported_absent_not_forbidden() -> None:
    async def scenario() -> None:
        harness = Harness()
        owner = caller()
        task = fact_task(harness.snapshot, harness.run_id)
        await harness.setup((task,), principal=owner)
        intruder = caller("reviewer-two")

        for call in (
            harness.service.read_task(intruder, task.task_id),
            harness.service.list_tasks(intruder, harness.job_id),
            harness.service.list_revisions(intruder, harness.job_id),
        ):
            with pytest.raises(ServiceFault) as refused:
                await call
            # NOT_FOUND, never UNAUTHORIZED: the latter would confirm the job exists.
            assert refused.value.problem.code == ServiceErrorCode.NOT_FOUND

    asyncio.run(scenario())


def test_reading_is_allowed_without_the_permission_that_answering_needs() -> None:
    async def scenario() -> None:
        harness = Harness()
        reader = caller(permissions=frozenset({Permission.REVIEW}))
        task = fact_task(harness.snapshot, harness.run_id)
        await harness.setup((task,), principal=reader)

        view = await harness.service.read_task(reader, task.task_id)
        assert view.task.task_id == task.task_id
        # The server names the subject, so a browser never re-derives a canonical key.
        assert view.subject_id == side_subject_id(task.side)
        with pytest.raises(ServiceFault) as refused:
            await harness.service.respond(reader, task.task_id, confirming(harness, task))

        assert refused.value.problem.code == ServiceErrorCode.UNAUTHORIZED

    asyncio.run(scenario())


def test_a_correction_naming_another_subject_is_rejected() -> None:
    async def scenario() -> None:
        harness = Harness()
        principal = caller()
        task = correction_task(harness.snapshot, harness.run_id)
        await harness.setup((task,), principal=principal)
        command = correcting(harness, task)
        assert command.correction is not None
        wrong = command.model_copy(
            update={
                "correction": replace_subject(command.correction, "some-other-cell"),
            }
        )

        with pytest.raises(ServiceFault) as refused:
            await harness.service.respond(principal, task.task_id, wrong)

        assert refused.value.problem.code == ServiceErrorCode.VALIDATION
        assert len(await harness.tasks.list_revisions(job_id=harness.job_id)) == 1

    asyncio.run(scenario())


def replace_subject(change: ValueRevision, subject_id: str) -> ValueRevision:
    return ValueRevision.model_validate(change.model_dump(mode="json") | {"subject_id": subject_id})


def test_a_body_naming_another_task_is_not_answered() -> None:
    async def scenario() -> None:
        harness = Harness()
        principal = caller()
        task = fact_task(harness.snapshot, harness.run_id)
        other = correction_task(harness.snapshot, harness.run_id)
        await harness.setup((task, other), principal=principal)

        with pytest.raises(ServiceFault) as refused:
            await harness.service.respond(principal, other.task_id, confirming(harness, task))

        assert refused.value.problem.code == ServiceErrorCode.NOT_FOUND
        assert len(await harness.tasks.list_revisions(job_id=harness.job_id)) == 1

    asyncio.run(scenario())


def test_the_revision_list_is_a_linked_chain_in_order() -> None:
    async def scenario() -> None:
        harness = Harness()
        principal = caller()
        task = fact_task(harness.snapshot, harness.run_id)
        await harness.setup((task,), principal=principal)
        await harness.service.respond(principal, task.task_id, confirming(harness, task))

        view = await harness.service.list_revisions(principal, harness.job_id)

        assert [r.reference.revision_id for r in view.revisions] == ["r1", "r2"]
        assert view.revisions[1].parent == view.revisions[0].reference

    asyncio.run(scenario())


def test_the_published_subject_name_uses_the_servers_own_canonical_form() -> None:
    """A browser must not re-derive this name, and this pins why.

    `ComparisonContext.key()` is `json.dumps(...)`, which escapes non-ASCII by default.
    JavaScript's `JSON.stringify` does not, so a client deriving the same name for a case
    identified in Chinese would compute a different string and have every correction
    rejected. Every real case in this project is identified in Chinese.
    """
    side = FactSideReference(
        context=ComparisonContext(scope="regional", target_id="板橋-A", comparable_id="三重-B"),
        factor_id="golden.road_width",
        side="target",
        input_digest="a" * 64,
    )

    name = side_subject_id(side)

    assert name == '["regional","\\u677f\\u6a4b-A","\\u4e09\\u91cd-B"]:golden.road_width:target'
    assert "板橋" not in name
