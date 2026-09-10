"""One conformance suite for every JobStore implementation.

Issue #29 requires that local in-memory contract tests and opt-in cloud tests exercise
the same state machine. Two suites written separately would drift, so the checks live
here, import no test framework, and are driven by thin wrappers under tests/ and
cloud_tests/. Every check builds its own synthetic identities; none carries case data.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

from appraisal_review.application.job_state import JobEvent
from appraisal_review.application.service_guards import Principal, ServiceFault
from appraisal_review.domain.factor_models import WorkflowStatus
from appraisal_review.domain.job_contracts import JobStatus
from appraisal_review.domain.service_contracts import (
    ActorReference,
    DocumentReference,
    ExecutionStatus,
    Permission,
    ReviewSubmission,
    RevisionReference,
    RunReference,
    ServiceErrorCode,
    ServiceProblem,
)
from appraisal_review.ports.jobs import ConditionFailed, JobStore, ResultReference

NOW = 1_700_000_000


def digest(seed: str) -> str:
    """A deterministic synthetic digest; never a hash of real material."""
    return (seed * 64)[:64]


def principal(actor_id: str = "contract-reviewer", case_id: str = "case-contract") -> Principal:
    return Principal(
        actor=ActorReference(actor_id=actor_id, kind="human"),
        case_ids=frozenset({case_id}),
        permissions=frozenset({Permission.REVIEW}),
    )


def submission(
    *,
    case_id: str = "case-contract",
    key: str = "contract-key-1",
    revision_id: str = "case-contract-r1",
    material: str = "a",
    document_version: str = "1",
) -> ReviewSubmission:
    return ReviewSubmission(
        revision=RevisionReference(
            case_id=case_id, revision_id=revision_id, material_digest=digest(material)
        ),
        documents=(
            DocumentReference(
                case_id=case_id,
                document_id="doc-criteria",
                version=document_version,
                content_hash=digest("b"),
                purpose="criteria",
            ),
            DocumentReference(
                case_id=case_id,
                document_id="doc-forms",
                version=document_version,
                content_hash=digest("c"),
                purpose="forms",
            ),
        ),
        idempotency_key=key,
    )


def result_reference(run_id: UUID, *, version: int, token: int) -> ResultReference:
    return ResultReference(
        run_id=run_id,
        result_version=version,
        fencing_token=token,
        execution_status=ExecutionStatus.SUCCEEDED,
        business_status=WorkflowStatus.NEEDS_REVIEW,
        artifact_status="not_requested",
        result_digest=digest("d"),
        finding_count=3,
    )


class JobStoreContract:
    """Checks every implementation must satisfy. Each takes a store with no prior state."""

    async def check_submission_persists_a_job_and_an_outbox_entry(self, store: JobStore) -> None:
        caller = principal()
        record, created = await store.create_job(
            caller, submission(), job_id=uuid4(), run_id=uuid4(), now=NOW
        )
        assert created is True
        assert record.status is JobStatus.QUEUED
        assert record.result_version == 0
        # Persisting the job without its outbox entry would lose the work on a crash.
        pending = await store.pending_dispatches(now=NOW, limit=10)
        assert [entry.job_id for entry in pending] == [record.job_id]
        assert pending[0].run_id == record.current_run.run_id

    async def check_exact_replay_returns_the_stored_job(self, store: JobStore) -> None:
        caller = principal()
        first, created = await store.create_job(
            caller, submission(), job_id=uuid4(), run_id=uuid4(), now=NOW
        )
        second, replayed = await store.create_job(
            caller, submission(), job_id=uuid4(), run_id=uuid4(), now=NOW + 1
        )
        assert created is True and replayed is False
        assert second.job_id == first.job_id
        assert second.current_run.run_id == first.current_run.run_id
        # A replay must not enqueue a second unit of work.
        assert len(await store.pending_dispatches(now=NOW + 1, limit=10)) == 1

    async def check_same_key_with_a_different_payload_conflicts(self, store: JobStore) -> None:
        caller = principal()
        await store.create_job(caller, submission(), job_id=uuid4(), run_id=uuid4(), now=NOW)
        changed = submission(material="e")
        try:
            await store.create_job(caller, changed, job_id=uuid4(), run_id=uuid4(), now=NOW + 1)
        except ServiceFault as fault:
            assert fault.problem.code is ServiceErrorCode.CONFLICT
        else:
            raise AssertionError("A reused key with a changed payload must conflict")

    async def check_the_key_order_of_documents_does_not_change_identity(
        self, store: JobStore
    ) -> None:
        caller = principal()
        original = submission()
        reordered = original.model_copy(update={"documents": tuple(reversed(original.documents))})
        first, _ = await store.create_job(caller, original, job_id=uuid4(), run_id=uuid4(), now=NOW)
        second, created = await store.create_job(
            caller, reordered, job_id=uuid4(), run_id=uuid4(), now=NOW + 1
        )
        assert created is False
        assert second.job_id == first.job_id

    async def check_principals_have_separate_key_namespaces(self, store: JobStore) -> None:
        first, _ = await store.create_job(
            principal("reviewer-one"), submission(), job_id=uuid4(), run_id=uuid4(), now=NOW
        )
        second, created = await store.create_job(
            principal("reviewer-two"), submission(), job_id=uuid4(), run_id=uuid4(), now=NOW
        )
        assert created is True
        assert second.job_id != first.job_id
        assert second.principal_id == "reviewer-two"

    async def check_concurrent_submissions_create_exactly_one_job(self, store: JobStore) -> None:
        caller = principal()

        async def submit() -> tuple[UUID, bool]:
            record, created = await store.create_job(
                caller, submission(), job_id=uuid4(), run_id=uuid4(), now=NOW
            )
            return record.job_id, created

        outcomes = await asyncio.gather(*(submit() for _ in range(20)))
        # Uniqueness must come from a conditional write on the record's own key. An
        # eventually consistent index would let several of these observe an unused key.
        assert sum(1 for _, created in outcomes if created) == 1
        assert len({job_id for job_id, _ in outcomes}) == 1

    async def check_concurrent_claims_produce_one_attempt(self, store: JobStore) -> None:
        record, _ = await store.create_job(
            principal(), submission(), job_id=uuid4(), run_id=uuid4(), now=NOW
        )
        run_id = record.current_run.run_id

        async def claim() -> object:
            try:
                return await store.claim(
                    job_id=record.job_id,
                    run_id=run_id,
                    owner=uuid4(),
                    lease_seconds=120,
                    now=NOW,
                )
            except ConditionFailed as failure:
                return failure

        outcomes = await asyncio.gather(*(claim() for _ in range(8)))
        winners = [item for item in outcomes if not isinstance(item, ConditionFailed)]
        assert len(winners) == 1
        assert all(isinstance(item, ConditionFailed) for item in outcomes if item not in winners)

    async def check_duplicate_delivery_does_not_start_a_second_attempt(
        self, store: JobStore
    ) -> None:
        record, _ = await store.create_job(
            principal(), submission(), job_id=uuid4(), run_id=uuid4(), now=NOW
        )
        run_id = record.current_run.run_id
        first = await store.claim(
            job_id=record.job_id, run_id=run_id, owner=uuid4(), lease_seconds=120, now=NOW
        )
        try:
            await store.claim(
                job_id=record.job_id, run_id=run_id, owner=uuid4(), lease_seconds=120, now=NOW + 5
            )
        except ConditionFailed:
            pass
        else:
            raise AssertionError("A redelivered message must not start a second attempt")
        assert first.fencing_token == 1

    async def check_fencing_tokens_increase_on_every_takeover(self, store: JobStore) -> None:
        record, _ = await store.create_job(
            principal(), submission(), job_id=uuid4(), run_id=uuid4(), now=NOW
        )
        run_id = record.current_run.run_id
        tokens = []
        for index in range(3):
            moment = NOW + index * 200
            attempt = await store.claim(
                job_id=record.job_id,
                run_id=run_id,
                owner=uuid4(),
                lease_seconds=120,
                now=moment,
            )
            tokens.append(attempt.fencing_token)
            expired = await store.expired_leases(now=moment + 121, limit=5)
            if expired:
                await store.expire_lease(expired[0], now=moment + 121)
        assert tokens == sorted(set(tokens)) == [1, 2, 3]

    async def check_a_taken_over_attempt_cannot_extend_its_lease(self, store: JobStore) -> None:
        record, _ = await store.create_job(
            principal(), submission(), job_id=uuid4(), run_id=uuid4(), now=NOW
        )
        run_id = record.current_run.run_id
        stale = await store.claim(
            job_id=record.job_id, run_id=run_id, owner=uuid4(), lease_seconds=120, now=NOW
        )
        expired = await store.expired_leases(now=NOW + 121, limit=5)
        await store.expire_lease(expired[0], now=NOW + 121)
        await store.claim(
            job_id=record.job_id, run_id=run_id, owner=uuid4(), lease_seconds=120, now=NOW + 122
        )
        try:
            await store.heartbeat(stale, lease_seconds=120, now=NOW + 123)
        except ConditionFailed:
            return
        raise AssertionError("A superseded attempt must not extend a lease it lost")

    async def check_a_stale_attempt_cannot_publish_over_a_newer_result(
        self, store: JobStore
    ) -> None:
        record, _ = await store.create_job(
            principal(), submission(), job_id=uuid4(), run_id=uuid4(), now=NOW
        )
        run_id = record.current_run.run_id
        stale = await store.claim(
            job_id=record.job_id, run_id=run_id, owner=uuid4(), lease_seconds=120, now=NOW
        )
        expired = await store.expired_leases(now=NOW + 121, limit=5)
        await store.expire_lease(expired[0], now=NOW + 121)
        current = await store.claim(
            job_id=record.job_id, run_id=run_id, owner=uuid4(), lease_seconds=120, now=NOW + 122
        )
        await store.finish(
            current,
            event=JobEvent.PUBLISH_RESULT,
            now=NOW + 130,
            result=result_reference(run_id, version=1, token=current.fencing_token),
        )
        try:
            await store.finish(
                stale,
                event=JobEvent.PUBLISH_RESULT,
                now=NOW + 131,
                result=result_reference(run_id, version=1, token=stale.fencing_token),
            )
        except ConditionFailed:
            pass
        else:
            raise AssertionError("A fenced-out attempt must not publish")
        committed = await store.read_result_reference(run_id=run_id, result_version=1)
        assert committed is not None and committed.fencing_token == current.fencing_token

    async def check_waiting_for_human_releases_the_lease(self, store: JobStore) -> None:
        record, _ = await store.create_job(
            principal(), submission(), job_id=uuid4(), run_id=uuid4(), now=NOW
        )
        attempt = await store.claim(
            job_id=record.job_id,
            run_id=record.current_run.run_id,
            owner=uuid4(),
            lease_seconds=120,
            now=NOW,
        )
        waiting = await store.finish(
            attempt, event=JobEvent.NEEDS_HUMAN, now=NOW + 10, open_task_ids=(uuid4(),)
        )
        assert waiting.status is JobStatus.WAITING_FOR_HUMAN
        assert waiting.attempt_count == 0
        assert waiting.problem is None
        # No lease is held, so no execution resource stays open for the reviewer and the
        # reconciler has nothing to reclaim.
        assert await store.expired_leases(now=NOW + 10_000, limit=5) == ()

    async def check_a_human_response_starts_a_new_run(self, store: JobStore) -> None:
        record, _ = await store.create_job(
            principal(), submission(), job_id=uuid4(), run_id=uuid4(), now=NOW
        )
        attempt = await store.claim(
            job_id=record.job_id,
            run_id=record.current_run.run_id,
            owner=uuid4(),
            lease_seconds=120,
            now=NOW,
        )
        await store.finish(
            attempt, event=JobEvent.NEEDS_HUMAN, now=NOW + 10, open_task_ids=(uuid4(),)
        )
        resumed = await store.resume_after_human(
            job_id=record.job_id,
            run=RunReference(
                run_id=uuid4(),
                revision=RevisionReference(
                    case_id="case-contract",
                    revision_id="case-contract-r2",
                    material_digest=digest("f"),
                ),
            ),
            now=NOW + 20,
        )
        assert resumed.status is JobStatus.QUEUED
        assert resumed.current_run.run_id != record.current_run.run_id
        assert resumed.open_task_ids == ()
        assert len(await store.pending_dispatches(now=NOW + 20, limit=10)) == 2

    async def check_a_response_cannot_revive_the_finished_run(self, store: JobStore) -> None:
        record, _ = await store.create_job(
            principal(), submission(), job_id=uuid4(), run_id=uuid4(), now=NOW
        )
        attempt = await store.claim(
            job_id=record.job_id,
            run_id=record.current_run.run_id,
            owner=uuid4(),
            lease_seconds=120,
            now=NOW,
        )
        await store.finish(
            attempt, event=JobEvent.NEEDS_HUMAN, now=NOW + 10, open_task_ids=(uuid4(),)
        )
        try:
            await store.resume_after_human(
                job_id=record.job_id, run=record.current_run, now=NOW + 20
            )
        except ConditionFailed:
            return
        raise AssertionError("Resuming must require a new run for a new revision")

    async def check_a_confirmed_dispatch_leaves_the_pending_set(self, store: JobStore) -> None:
        record, _ = await store.create_job(
            principal(), submission(), job_id=uuid4(), run_id=uuid4(), now=NOW
        )
        pending = await store.pending_dispatches(now=NOW, limit=10)
        updated = await store.mark_dispatched(pending[0], now=NOW + 1)
        assert updated.status is JobStatus.DISPATCHED
        assert await store.pending_dispatches(now=NOW + 1, limit=10) == ()
        assert record.job_id == updated.job_id

    async def check_a_dispatch_round_is_confirmed_only_once(self, store: JobStore) -> None:
        await store.create_job(principal(), submission(), job_id=uuid4(), run_id=uuid4(), now=NOW)
        pending = await store.pending_dispatches(now=NOW, limit=10)
        await store.mark_dispatched(pending[0], now=NOW + 1)
        try:
            await store.mark_dispatched(pending[0], now=NOW + 2)
        except ConditionFailed:
            return
        raise AssertionError("A stale dispatch round must not be confirmed twice")

    async def check_a_failed_send_moves_only_the_schedule(self, store: JobStore) -> None:
        record, _ = await store.create_job(
            principal(), submission(), job_id=uuid4(), run_id=uuid4(), now=NOW
        )
        pending = await store.pending_dispatches(now=NOW, limit=10)
        await store.reschedule_dispatch(pending[0], available_at=NOW + 60)
        after = await store.read_job(job_id=record.job_id)
        assert after is not None and after.status is JobStatus.QUEUED
        # The work is still durably owned; it is simply not due yet.
        assert await store.pending_dispatches(now=NOW + 1, limit=10) == ()
        due = await store.pending_dispatches(now=NOW + 60, limit=10)
        assert len(due) == 1 and due[0].dispatch_attempts == 1

    async def check_a_retry_enqueues_new_work_without_a_new_job(self, store: JobStore) -> None:
        record, _ = await store.create_job(
            principal(), submission(), job_id=uuid4(), run_id=uuid4(), now=NOW
        )
        attempt = await store.claim(
            job_id=record.job_id,
            run_id=record.current_run.run_id,
            owner=uuid4(),
            lease_seconds=120,
            now=NOW,
        )
        failed = await store.finish(
            attempt,
            event=JobEvent.RETRYABLE_ERROR,
            now=NOW + 5,
            problem=ServiceProblem(code=ServiceErrorCode.EXECUTION),
        )
        assert failed.status is JobStatus.RETRYABLE_FAILED
        assert failed.attempt_count == 1
        retried = await store.schedule_retry(
            job_id=record.job_id, available_at=NOW + 65, now=NOW + 5
        )
        assert retried.status is JobStatus.QUEUED
        assert retried.problem is None
        assert retried.current_run.run_id == record.current_run.run_id
        assert len(await store.pending_dispatches(now=NOW + 65, limit=10)) == 2

    async def check_cancellation_of_a_running_attempt_is_cooperative(self, store: JobStore) -> None:
        record, _ = await store.create_job(
            principal(), submission(), job_id=uuid4(), run_id=uuid4(), now=NOW
        )
        attempt = await store.claim(
            job_id=record.job_id,
            run_id=record.current_run.run_id,
            owner=uuid4(),
            lease_seconds=120,
            now=NOW,
        )
        flagged = await store.cancel(job_id=record.job_id, now=NOW + 1)
        assert flagged.status is JobStatus.RUNNING
        assert flagged.cancel_requested is True
        state = await store.heartbeat(attempt, lease_seconds=120, now=NOW + 2)
        assert state.cancel_requested is True

    async def check_a_terminal_job_refuses_further_work(self, store: JobStore) -> None:
        record, _ = await store.create_job(
            principal(), submission(), job_id=uuid4(), run_id=uuid4(), now=NOW
        )
        cancelled = await store.cancel(job_id=record.job_id, now=NOW + 1)
        assert cancelled.status is JobStatus.CANCELLED
        assert cancelled.problem is not None
        try:
            await store.claim(
                job_id=record.job_id,
                run_id=record.current_run.run_id,
                owner=uuid4(),
                lease_seconds=120,
                now=NOW + 2,
            )
        except ConditionFailed:
            return
        raise AssertionError("A cancelled job must not be claimable")

    async def check_results_are_append_only(self, store: JobStore) -> None:
        record, _ = await store.create_job(
            principal(), submission(), job_id=uuid4(), run_id=uuid4(), now=NOW
        )
        run_id = record.current_run.run_id
        attempt = await store.claim(
            job_id=record.job_id, run_id=run_id, owner=uuid4(), lease_seconds=120, now=NOW
        )
        published = await store.finish(
            attempt,
            event=JobEvent.PUBLISH_RESULT,
            now=NOW + 10,
            result=result_reference(run_id, version=1, token=attempt.fencing_token),
        )
        assert published.status is JobStatus.SUCCEEDED
        assert published.result_version == 1
        try:
            await store.finish(
                attempt,
                event=JobEvent.PUBLISH_RESULT,
                now=NOW + 11,
                result=result_reference(run_id, version=1, token=attempt.fencing_token),
            )
        except ConditionFailed:
            return
        raise AssertionError("A committed result version must not be rewritten")

    async def check_an_expired_lease_does_not_consume_an_attempt(self, store: JobStore) -> None:
        record, _ = await store.create_job(
            principal(), submission(), job_id=uuid4(), run_id=uuid4(), now=NOW
        )
        # Dispatch first, so the outbox is empty and the reclaim is the only thing that
        # can put this run back in it.
        await store.mark_dispatched((await store.pending_dispatches(now=NOW, limit=5))[0], now=NOW)
        assert await store.pending_dispatches(now=NOW, limit=5) == ()
        await store.claim(
            job_id=record.job_id,
            run_id=record.current_run.run_id,
            owner=uuid4(),
            lease_seconds=120,
            now=NOW,
        )
        assert await store.expired_leases(now=NOW + 60, limit=5) == ()
        expired = await store.expired_leases(now=NOW + 121, limit=5)
        assert len(expired) == 1
        reclaimed = await store.expire_lease(expired[0], now=NOW + 121)
        assert reclaimed.status is JobStatus.QUEUED
        assert reclaimed.attempt_count == 0
        assert reclaimed.lease_takeover_count == 1
        # The previous round was already marked sent, so recovery needs its own entry
        # rather than a redelivery of a message the dead worker may have consumed.
        recovered = await store.pending_dispatches(now=NOW + 121, limit=10)
        assert len(recovered) == 1
        assert recovered[0].run_id == record.current_run.run_id

    async def check_a_reclaimed_lease_cannot_be_reclaimed_again(self, store: JobStore) -> None:
        record, _ = await store.create_job(
            principal(), submission(), job_id=uuid4(), run_id=uuid4(), now=NOW
        )
        await store.claim(
            job_id=record.job_id,
            run_id=record.current_run.run_id,
            owner=uuid4(),
            lease_seconds=120,
            now=NOW,
        )
        expired = await store.expired_leases(now=NOW + 121, limit=5)
        await store.expire_lease(expired[0], now=NOW + 121)
        try:
            await store.expire_lease(expired[0], now=NOW + 122)
        except ConditionFailed:
            return
        raise AssertionError("A stale reconciler candidate must not reclaim twice")

    async def check_an_unknown_job_reads_as_absent(self, store: JobStore) -> None:
        assert await store.read_job(job_id=uuid4()) is None


CONTRACT_CHECKS: tuple[str, ...] = tuple(
    sorted(name for name in vars(JobStoreContract) if name.startswith("check_"))
)
