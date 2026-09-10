"""Failure injection for the durable job plane.

Each test forces one specific failure that #29 names, and asserts what the system does
about it. The queue is a fake so the failures are exact and repeatable; the same passes
run against SQS in the cloud phase.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest

from appraisal_review.adapters.local.job_store import InMemoryJobStore, InMemoryResultStore
from appraisal_review.application.job_state import JobEvent, JobPolicy
from appraisal_review.application.outbox import (
    DispatchMessage,
    JobReconciler,
    OutboxDispatcher,
    TransientDispatchError,
)
from appraisal_review.application.review_jobs import ReviewJobService
from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.domain.factor_models import WorkflowStatus
from appraisal_review.domain.job_contracts import JobStatus
from appraisal_review.domain.service_contracts import (
    ExecutionStatus,
    RunReference,
    ServiceErrorCode,
    ServiceProblem,
    ServiceResult,
)
from appraisal_review.ports.jobs import ClaimedAttempt, ConditionFailed
from appraisal_review.testing.job_store_contract import NOW, principal, submission


@dataclass
class FakeQueue:
    """A queue that can fail sends, redeliver messages and lose deletions."""

    send_failures: int = 0
    messages: list[DispatchMessage] = field(default_factory=list)
    delivered: list[DispatchMessage] = field(default_factory=list)

    async def send(self, message: DispatchMessage) -> None:
        if self.send_failures > 0:
            self.send_failures -= 1
            raise TransientDispatchError("Queue refused the message")
        self.messages.append(message)

    def receive(self, *, redeliver: bool = False) -> DispatchMessage:
        message = self.messages[0] if redeliver else self.messages.pop(0)
        self.delivered.append(message)
        return message


@dataclass
class World:
    store: InMemoryJobStore
    results: InMemoryResultStore
    service: ReviewJobService
    queue: FakeQueue
    dispatcher: OutboxDispatcher
    reconciler: JobReconciler
    clock: list[int]


def build(*, send_failures: int = 0) -> World:
    clock = [NOW]
    store = InMemoryJobStore()
    results = InMemoryResultStore()
    service = ReviewJobService(
        store,
        results,
        policy=JobPolicy(),
        clock=lambda: clock[0],
        jitter=lambda bound: 0,
    )
    queue = FakeQueue(send_failures=send_failures)
    dispatcher = OutboxDispatcher(service, queue.send)
    return World(store, results, service, queue, dispatcher, JobReconciler(dispatcher), clock)


def submit(world: World, *, key: str = "contract-key-1") -> tuple[UUID, UUID]:
    outcome = asyncio.run(world.service.submit(principal(), submission(key=key)))
    assert outcome.acceptance is not None
    return outcome.acceptance.job.job_id, outcome.acceptance.run.run_id


def status(world: World, job_id: UUID) -> JobStatus:
    record = asyncio.run(world.store.read_job(job_id=job_id))
    assert record is not None
    return record.status


def claim(world: World, job_id: UUID, run_id: UUID) -> ClaimedAttempt:
    return asyncio.run(world.service.claim(job_id=job_id, run_id=run_id, owner=uuid4()))


def published_result(run_id: UUID, *, version: int = 1) -> ServiceResult:
    return ServiceResult(
        run=RunReference(run_id=run_id, revision=submission().revision),
        result_version=version,
        execution_status=ExecutionStatus.SUCCEEDED,
        business_status=WorkflowStatus.NEEDS_REVIEW,
    )


class TestDispatchGap:
    def test_a_crash_after_commit_but_before_send_loses_no_work(self) -> None:
        world = build()
        job_id, _ = submit(world)
        # The process dies here: the job and its outbox entry are committed, nothing sent.
        assert status(world, job_id) is JobStatus.QUEUED
        assert world.queue.messages == []
        outcome = asyncio.run(world.reconciler.run_once())
        assert outcome.dispatch.sent == 1
        assert status(world, job_id) is JobStatus.DISPATCHED
        assert len(world.queue.messages) == 1

    def test_a_failed_send_backs_off_without_failing_the_job(self) -> None:
        world = build(send_failures=1)
        job_id, _ = submit(world)
        first = asyncio.run(world.dispatcher.run_once())
        assert first.deferred == 1 and first.sent == 0
        # Still queued and still owned; only the schedule moved.
        assert status(world, job_id) is JobStatus.QUEUED
        assert asyncio.run(world.dispatcher.run_once()).sent == 0
        world.clock[0] = NOW + world.service.policy.backoff_seconds(1)
        assert asyncio.run(world.dispatcher.run_once()).sent == 1
        assert status(world, job_id) is JobStatus.DISPATCHED

    def test_repeated_send_failures_lengthen_the_delay(self) -> None:
        world = build(send_failures=3)
        submit(world)
        delays = []
        for attempt in range(1, 4):
            asyncio.run(world.dispatcher.run_once())
            delays.append(world.service.policy.backoff_seconds(attempt))
            world.clock[0] += delays[-1]
        assert delays == sorted(delays) and delays[0] < delays[-1]
        assert asyncio.run(world.dispatcher.run_once()).sent == 1

    def test_a_duplicate_dispatch_pass_does_not_enqueue_twice(self) -> None:
        world = build()
        submit(world)
        due = asyncio.run(world.service.due_dispatches())
        asyncio.run(world.service.confirm_dispatch(due[0]))
        # A second pass over a stale candidate list must not resend or re-mark.
        assert asyncio.run(world.dispatcher.run_once()).sent == 0
        assert world.queue.messages == []

    def test_one_failed_round_does_not_abandon_the_rest_of_the_pass(self) -> None:
        world = build()
        first_id, first_run = submit(world)
        second_id, _ = submit(world, key="contract-key-2")
        # Two rounds for the first job, which is the normal state after a reclaim, a
        # retry or a resume: the reclaim adds a round while the original is still due.
        claim(world, first_id, first_run)
        world.clock[0] += world.service.policy.lease_seconds + 1
        asyncio.run(world.service.reclaim_expired_leases())
        assert len(asyncio.run(world.service.due_dispatches())) == 3

        sent: list[DispatchMessage] = []

        async def send_then_fail(message: DispatchMessage) -> None:
            if sent:
                raise TransientDispatchError("The queue refused a later round")
            sent.append(message)

        world.dispatcher.send = send_then_fail
        outcome = asyncio.run(world.dispatcher.run_once())
        # The failure arrives after another round moved the job out of queued. Refusing
        # it there raised a fault through the dispatcher, so the two remaining rounds
        # were never backed off and the pass abandoned its own reclaim work.
        assert outcome.sent == 1 and outcome.deferred == 2
        assert status(world, first_id) is JobStatus.DISPATCHED
        assert status(world, second_id) is JobStatus.QUEUED
        later = world.clock[0] + world.service.policy.backoff_seconds(1)
        assert len(asyncio.run(world.store.pending_dispatches(now=later, limit=10))) == 2

    def test_a_round_for_a_finished_job_is_abandoned_rather_than_retried(self) -> None:
        world = build(send_failures=1)
        job_id, _ = submit(world)
        asyncio.run(world.service.cancel(principal(), job_id))
        assert status(world, job_id) is JobStatus.CANCELLED
        outcome = asyncio.run(world.dispatcher.run_once())
        # Nothing may claim this job, so backing the round off would keep a cancelled
        # job in the dispatch rotation for as long as the outbox entry lives.
        assert outcome.abandoned == 1 and outcome.deferred == 0
        world.clock[0] += 6_000
        assert asyncio.run(world.service.due_dispatches()) == ()

    def test_a_claim_between_send_and_mark_does_not_lose_the_send(self) -> None:
        world = build()
        job_id, run_id = submit(world)
        due = asyncio.run(world.service.due_dispatches())
        claim(world, job_id, run_id)
        # The mark must still succeed, or the outbox entry would be redispatched forever.
        record = asyncio.run(world.service.confirm_dispatch(due[0]))
        assert record.status is JobStatus.RUNNING
        assert asyncio.run(world.service.due_dispatches()) == ()


class TestDuplicateDelivery:
    def test_a_redelivered_message_cannot_start_a_second_run(self) -> None:
        world = build()
        job_id, run_id = submit(world)
        asyncio.run(world.dispatcher.run_once())
        world.queue.receive(redeliver=True)
        first = claim(world, job_id, run_id)
        world.queue.receive(redeliver=True)
        with pytest.raises(ConditionFailed):
            claim(world, job_id, run_id)
        assert first.fencing_token == 1

    def test_a_worker_that_dies_after_publishing_does_not_rerun(self) -> None:
        world = build()
        job_id, run_id = submit(world)
        asyncio.run(world.dispatcher.run_once())
        attempt = claim(world, job_id, run_id)
        asyncio.run(world.service.publish(attempt, published_result(run_id)))
        # The delete never happened, so the message comes back. The job is terminal, so
        # the redelivery finds nothing to claim and the message can simply be dropped.
        world.queue.receive(redeliver=True)
        assert status(world, job_id) is JobStatus.SUCCEEDED
        with pytest.raises(ConditionFailed):
            claim(world, job_id, run_id)


class TestLeaseRecovery:
    def test_a_dead_worker_is_taken_over_without_consuming_an_attempt(self) -> None:
        world = build()
        job_id, run_id = submit(world)
        stale = claim(world, job_id, run_id)
        world.clock[0] = NOW + world.service.policy.lease_seconds + 1
        outcome = asyncio.run(world.reconciler.run_once())
        assert outcome.leases_reclaimed == 1
        record = asyncio.run(world.store.read_job(job_id=job_id))
        assert record is not None
        assert record.attempt_count == 0 and record.lease_takeover_count == 1
        successor = claim(world, job_id, run_id)
        assert successor.fencing_token == stale.fencing_token + 1

    def test_recovery_does_not_depend_on_the_queue_redelivering(self) -> None:
        world = build()
        job_id, run_id = submit(world)
        asyncio.run(world.dispatcher.run_once())
        world.queue.receive()  # The worker took the message off the queue, then died.
        claim(world, job_id, run_id)
        world.queue.messages.clear()  # And the message went with it.
        world.clock[0] = NOW + world.service.policy.lease_seconds + 1
        outcome = asyncio.run(world.reconciler.run_once())
        # Reclaiming re-enqueues, so a lost message cannot strand a job that is still
        # durably owned. A duplicate delivery would only lose a claim race.
        assert outcome.leases_reclaimed == 1
        assert outcome.dispatch.sent == 1
        assert len(world.queue.messages) == 1
        assert status(world, job_id) is JobStatus.DISPATCHED
        assert claim(world, job_id, run_id).fencing_token == 2

    def test_a_fenced_out_worker_cannot_publish_after_takeover(self) -> None:
        world = build()
        job_id, run_id = submit(world)
        stale = claim(world, job_id, run_id)
        world.clock[0] = NOW + world.service.policy.lease_seconds + 1
        asyncio.run(world.reconciler.run_once())
        successor = claim(world, job_id, run_id)
        asyncio.run(world.service.publish(successor, published_result(run_id)))
        with pytest.raises(ConditionFailed):
            asyncio.run(world.service.publish(stale, published_result(run_id)))
        reference = asyncio.run(world.store.read_result_reference(run_id=run_id, result_version=1))
        assert reference is not None
        assert reference.fencing_token == successor.fencing_token

    def test_a_live_lease_is_never_reclaimed(self) -> None:
        world = build()
        job_id, run_id = submit(world)
        attempt = claim(world, job_id, run_id)
        world.clock[0] = NOW + world.service.policy.lease_seconds - 1
        assert asyncio.run(world.reconciler.run_once()).leases_reclaimed == 0
        assert asyncio.run(world.service.heartbeat(attempt)).cancel_requested is False

    def test_heartbeats_keep_a_long_review_alive(self) -> None:
        world = build()
        job_id, run_id = submit(world)
        attempt = claim(world, job_id, run_id)
        for step in range(1, 8):
            world.clock[0] = NOW + step * world.service.policy.heartbeat_seconds
            asyncio.run(world.service.heartbeat(attempt))
            assert asyncio.run(world.reconciler.run_once()).leases_reclaimed == 0
        assert status(world, job_id) is JobStatus.RUNNING

    def test_endless_takeovers_are_bounded(self) -> None:
        world = build()
        job_id, run_id = submit(world)
        for round_index in range(world.service.policy.max_lease_takeovers):
            world.clock[0] = NOW + round_index * 1_000
            try:
                claim(world, job_id, run_id)
            except ConditionFailed:
                break
            world.clock[0] += world.service.policy.lease_seconds + 1
            asyncio.run(world.reconciler.run_once())
        assert status(world, job_id) is JobStatus.FAILED


class TestCancellationAuthority:
    """A recorded cancel is a human decision; no recovery path may discard it.

    Each of these leaves a cancel pending and then kills the attempt in a different way.
    Before, every one of them resumed the job and published a result, and the principal
    who cancelled was told the job succeeded.
    """

    def test_a_cancel_survives_the_death_of_the_worker_it_was_sent_to(self) -> None:
        world = build()
        job_id, run_id = submit(world)
        claim(world, job_id, run_id)
        view = asyncio.run(world.service.cancel(principal(), job_id))
        assert view.job_status is JobStatus.RUNNING
        # The pending decision is observable rather than hidden behind "running".
        assert view.cancel_requested is True
        world.clock[0] += world.service.policy.lease_seconds + 1
        assert asyncio.run(world.reconciler.run_once()).leases_reclaimed == 1
        assert status(world, job_id) is JobStatus.CANCELLED
        with pytest.raises(ConditionFailed):
            claim(world, job_id, run_id)

    def test_a_cancel_is_not_laundered_through_a_retryable_failure(self) -> None:
        world = build()
        job_id, run_id = submit(world)
        attempt = claim(world, job_id, run_id)
        asyncio.run(world.service.cancel(principal(), job_id))
        record = asyncio.run(
            world.service.fail(attempt, code=ServiceErrorCode.EXECUTION, retryable=True)
        )
        # A retry is scheduled through the outbox, where nothing re-checks the flag.
        assert record.status is JobStatus.CANCELLED
        assert record.attempt_count == 0
        world.clock[0] += 10_000
        asyncio.run(world.reconciler.run_once())
        assert status(world, job_id) is JobStatus.CANCELLED

    def test_a_cancel_is_not_laundered_through_a_reviewer(self) -> None:
        world = build()
        job_id, run_id = submit(world)
        attempt = claim(world, job_id, run_id)
        asyncio.run(world.service.cancel(principal(), job_id))
        record = asyncio.run(world.service.wait_for_human(attempt, (uuid4(),)))
        # Parking the job would launder the cancel: a committed response resumes it on a
        # new run, and the reviewer is shown work that was already called off.
        assert record.status is JobStatus.CANCELLED
        assert record.open_task_ids == ()
        revision = submission().revision.model_copy(update={"revision_id": "case-contract-r2"})
        with pytest.raises(ServiceFault):
            asyncio.run(
                world.service.resume(
                    job_id=job_id, run=RunReference(run_id=uuid4(), revision=revision)
                )
            )

    def test_a_cancel_that_loses_the_race_to_a_publication_still_reports_honestly(self) -> None:
        world = build()
        job_id, run_id = submit(world)
        attempt = claim(world, job_id, run_id)
        asyncio.run(world.service.cancel(principal(), job_id))
        # Cancellation is cooperative: an attempt that had already finished its work may
        # publish. That outcome is legitimate, and the flag stays visible on the view.
        record = asyncio.run(world.service.publish(attempt, published_result(run_id)))
        assert record.status is JobStatus.SUCCEEDED
        view = asyncio.run(world.service.status(principal(), job_id))
        assert view.job_status is JobStatus.SUCCEEDED
        assert view.cancel_requested is True


class TestErrorClassification:
    def test_retryable_failures_reschedule_until_the_ceiling(self) -> None:
        world = build()
        job_id, run_id = submit(world)
        for attempt_number in range(1, world.service.policy.max_attempts):
            attempt = claim(world, job_id, run_id)
            record = asyncio.run(
                world.service.fail(attempt, code=ServiceErrorCode.EXECUTION, retryable=True)
            )
            assert record.attempt_count == attempt_number
            assert record.status is JobStatus.QUEUED
            world.clock[0] += world.service.policy.backoff_seconds(attempt_number)
        final = claim(world, job_id, run_id)
        exhausted = asyncio.run(
            world.service.fail(final, code=ServiceErrorCode.EXECUTION, retryable=True)
        )
        # Exhausting attempts is where a poison message becomes a dead letter.
        assert exhausted.status is JobStatus.FAILED
        assert exhausted.problem is not None

    def test_a_permanent_failure_is_never_retried(self) -> None:
        world = build()
        job_id, run_id = submit(world)
        attempt = claim(world, job_id, run_id)
        record = asyncio.run(
            world.service.fail(attempt, code=ServiceErrorCode.VALIDATION, retryable=False)
        )
        assert record.status is JobStatus.FAILED
        assert record.attempt_count == 0
        assert asyncio.run(world.reconciler.run_once()).retries_scheduled == 0

    @pytest.mark.parametrize(
        ("code", "retryable"),
        [
            (ServiceErrorCode.VALIDATION, True),
            (ServiceErrorCode.UNAUTHORIZED, True),
            (ServiceErrorCode.NOT_FOUND, True),
            (ServiceErrorCode.EXECUTION, False),
            (ServiceErrorCode.CAPABILITY, False),
        ],
    )
    def test_a_misclassified_failure_is_refused(
        self, code: ServiceErrorCode, retryable: bool
    ) -> None:
        world = build()
        job_id, run_id = submit(world)
        attempt = claim(world, job_id, run_id)
        # There is no third category: a payload error can never be retried, and an
        # infrastructure error must never be recorded as permanent.
        with pytest.raises(ValueError, match="classification"):
            asyncio.run(world.service.fail(attempt, code=code, retryable=retryable))

    def test_a_job_stuck_in_retryable_failed_is_recovered(self) -> None:
        world = build()
        job_id, run_id = submit(world)
        attempt = claim(world, job_id, run_id)
        # Simulate a crash between finishing the attempt and scheduling the retry.
        record = asyncio.run(
            world.store.finish(
                attempt,
                event=JobEvent.RETRYABLE_ERROR,
                now=world.clock[0],
                problem=ServiceProblem(code=ServiceErrorCode.EXECUTION),
            )
        )
        assert record.status is JobStatus.RETRYABLE_FAILED
        assert asyncio.run(world.reconciler.recover_job(job_id=job_id)) is True
        assert status(world, job_id) is JobStatus.QUEUED

    def test_a_stranded_retryable_job_is_recovered_without_an_operator(self) -> None:
        world = build()
        job_id, run_id = submit(world)
        attempt = claim(world, job_id, run_id)
        # The same crash between finishing the attempt and scheduling the retry. This
        # job holds no lease and no outbox entry, so it is invisible to both other
        # passes and used to wait for an operator who already knew its id.
        asyncio.run(
            world.store.finish(
                attempt,
                event=JobEvent.RETRYABLE_ERROR,
                now=world.clock[0],
                problem=ServiceProblem(code=ServiceErrorCode.EXECUTION),
            )
        )
        assert asyncio.run(world.store.expired_leases(now=world.clock[0], limit=5)) == ()
        # Not yet: the worker may still be alive and about to schedule its own retry.
        assert asyncio.run(world.reconciler.run_once()).retries_scheduled == 0
        assert status(world, job_id) is JobStatus.RETRYABLE_FAILED
        world.clock[0] += world.service.policy.lease_seconds + 1
        assert asyncio.run(world.reconciler.run_once()).retries_scheduled == 1
        assert status(world, job_id) is JobStatus.QUEUED

    def test_a_reconciler_retry_does_not_double_schedule_the_workers_own(self) -> None:
        world = build()
        job_id, run_id = submit(world)
        attempt = claim(world, job_id, run_id)
        # fail() schedules the retry itself, so the scan must find nothing to repair and
        # must not raise when it loses the race for a job it read a moment earlier.
        record = asyncio.run(
            world.service.fail(attempt, code=ServiceErrorCode.EXECUTION, retryable=True)
        )
        assert record.status is JobStatus.QUEUED
        world.clock[0] += 10_000
        assert asyncio.run(world.reconciler.run_once()).retries_scheduled == 0
        assert asyncio.run(world.service.recover_retryable(job_id=job_id)) is None


class TestPublicationIntegrity:
    def test_a_body_written_without_a_commit_is_never_referenced(self) -> None:
        world = build()
        job_id, run_id = submit(world)
        attempt = claim(world, job_id, run_id)
        asyncio.run(
            world.results.put(run_id=run_id, result_version=1, result=published_result(run_id))
        )
        # The commit never happened, so the orphan body has no reference pointing at it.
        assert asyncio.run(world.store.read_result_reference(run_id=run_id, result_version=1)) is (
            None
        )
        assert status(world, job_id) is JobStatus.RUNNING
        assert attempt.expected_result_version == 0

    def test_publishing_an_unexpected_version_conflicts(self) -> None:
        world = build()
        job_id, run_id = submit(world)
        attempt = claim(world, job_id, run_id)
        with pytest.raises(ServiceFault):
            asyncio.run(world.service.publish(attempt, published_result(run_id, version=2)))

    def test_a_result_body_cannot_be_swapped_under_a_committed_digest(self) -> None:
        world = build()
        job_id, run_id = submit(world)
        attempt = claim(world, job_id, run_id)
        asyncio.run(world.service.publish(attempt, published_result(run_id)))
        different = published_result(run_id).model_copy(
            update={"business_status": WorkflowStatus.FAILED}
        )
        with pytest.raises(ServiceFault):
            asyncio.run(world.results.put(run_id=run_id, result_version=1, result=different))
