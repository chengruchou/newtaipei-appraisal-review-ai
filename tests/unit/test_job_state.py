"""Exhaustive transition tests. Anything outside the declared table must be refused.

The matrix is enumerated rather than hand-written per case, so a new status or event
cannot be added without deciding its behaviour against every counterpart.
"""

from __future__ import annotations

import pytest

from appraisal_review.application.job_state import (
    JobEvent,
    JobFacts,
    JobPolicy,
    initial_transition,
    next_state,
)
from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.domain.job_contracts import TERMINAL_STATUSES, JobStatus, wire_status
from appraisal_review.domain.service_contracts import ExecutionStatus, ServiceErrorCode

POLICY = JobPolicy()

# The declared table for a job with no attempts, no takeovers, no open task and no
# pending cancellation. Fact-dependent transitions are asserted separately below.
BASELINE_TABLE: dict[tuple[JobStatus, JobEvent], JobStatus] = {
    (JobStatus.QUEUED, JobEvent.DISPATCH_SUCCEEDED): JobStatus.DISPATCHED,
    (JobStatus.QUEUED, JobEvent.DISPATCH_FAILED): JobStatus.QUEUED,
    (JobStatus.QUEUED, JobEvent.CLAIM): JobStatus.RUNNING,
    (JobStatus.QUEUED, JobEvent.CANCEL): JobStatus.CANCELLED,
    (JobStatus.DISPATCHED, JobEvent.CLAIM): JobStatus.RUNNING,
    (JobStatus.DISPATCHED, JobEvent.CANCEL): JobStatus.CANCELLED,
    (JobStatus.RUNNING, JobEvent.HEARTBEAT): JobStatus.RUNNING,
    (JobStatus.RUNNING, JobEvent.LEASE_EXPIRED): JobStatus.QUEUED,
    (JobStatus.RUNNING, JobEvent.PUBLISH_RESULT): JobStatus.SUCCEEDED,
    (JobStatus.RUNNING, JobEvent.RETRYABLE_ERROR): JobStatus.RETRYABLE_FAILED,
    (JobStatus.RUNNING, JobEvent.PERMANENT_ERROR): JobStatus.FAILED,
    (JobStatus.RUNNING, JobEvent.CANCEL): JobStatus.RUNNING,
    (JobStatus.WAITING_FOR_HUMAN, JobEvent.HUMAN_RESPONSE_COMMITTED): JobStatus.QUEUED,
    (JobStatus.WAITING_FOR_HUMAN, JobEvent.CANCEL): JobStatus.CANCELLED,
    (JobStatus.RETRYABLE_FAILED, JobEvent.CLAIM): JobStatus.RUNNING,
    (JobStatus.RETRYABLE_FAILED, JobEvent.SCHEDULE_RETRY): JobStatus.QUEUED,
    (JobStatus.RETRYABLE_FAILED, JobEvent.CANCEL): JobStatus.CANCELLED,
}

MATRIX = [(status, event) for status in JobStatus for event in JobEvent]


def test_matrix_covers_every_status_and_event() -> None:
    assert len(MATRIX) == len(JobStatus) * len(JobEvent) == 8 * 14


@pytest.mark.parametrize(("status", "event"), MATRIX)
def test_baseline_transition_matches_the_declared_table(status: JobStatus, event: JobEvent) -> None:
    facts = JobFacts(status=status)
    expected = BASELINE_TABLE.get((status, event))
    if expected is None:
        with pytest.raises(ServiceFault) as error:
            next_state(facts, event, policy=POLICY)
        assert error.value.problem.code is ServiceErrorCode.CONFLICT
        return
    assert next_state(facts, event, policy=POLICY).status is expected


@pytest.mark.parametrize("status", sorted(TERMINAL_STATUSES))
@pytest.mark.parametrize("event", list(JobEvent))
def test_terminal_statuses_refuse_every_event(status: JobStatus, event: JobEvent) -> None:
    # A late worker must not be able to reopen or overwrite a finished job.
    with pytest.raises(ServiceFault):
        next_state(JobFacts(status=status), event, policy=POLICY)


@pytest.mark.parametrize("status", list(JobStatus))
def test_an_existing_job_can_never_be_submitted_again(status: JobStatus) -> None:
    with pytest.raises(ServiceFault):
        next_state(JobFacts(status=status), JobEvent.SUBMIT, policy=POLICY)


def test_submission_creates_a_queued_job_with_an_outbox_entry() -> None:
    transition = initial_transition()
    assert transition.status is JobStatus.QUEUED
    assert transition.enqueue_outbox is True


def test_needs_human_requires_an_open_task() -> None:
    running = JobFacts(status=JobStatus.RUNNING)
    with pytest.raises(ServiceFault):
        next_state(running, JobEvent.NEEDS_HUMAN, policy=POLICY)
    transition = next_state(
        JobFacts(status=JobStatus.RUNNING, has_open_tasks=True),
        JobEvent.NEEDS_HUMAN,
        policy=POLICY,
    )
    assert transition.status is JobStatus.WAITING_FOR_HUMAN
    assert transition.release_lease is True
    # Waiting for a reviewer is neither a retry nor an attempt.
    assert transition.attempt_delta == 0
    assert transition.enqueue_outbox is False


@pytest.mark.parametrize("event", [JobEvent.RETRYABLE_ERROR, JobEvent.SCHEDULE_RETRY])
def test_waiting_for_human_is_never_treated_as_a_failed_attempt(event: JobEvent) -> None:
    facts = JobFacts(status=JobStatus.WAITING_FOR_HUMAN, has_open_tasks=True)
    with pytest.raises(ServiceFault):
        next_state(facts, event, policy=POLICY)


def test_cancellation_of_a_running_attempt_only_raises_a_flag() -> None:
    transition = next_state(JobFacts(status=JobStatus.RUNNING), JobEvent.CANCEL, policy=POLICY)
    assert transition.status is JobStatus.RUNNING
    assert transition.request_cancel is True
    assert transition.release_lease is False


def test_cancel_acknowledgement_requires_a_pending_request() -> None:
    with pytest.raises(ServiceFault):
        next_state(JobFacts(status=JobStatus.RUNNING), JobEvent.CANCEL_ACKNOWLEDGED, policy=POLICY)
    transition = next_state(
        JobFacts(status=JobStatus.RUNNING, cancel_requested=True),
        JobEvent.CANCEL_ACKNOWLEDGED,
        policy=POLICY,
    )
    assert transition.status is JobStatus.CANCELLED
    assert transition.release_lease is True


@pytest.mark.parametrize(
    ("attempt_count", "expected"),
    [(0, JobStatus.RETRYABLE_FAILED), (3, JobStatus.RETRYABLE_FAILED), (4, JobStatus.FAILED)],
)
def test_retryable_errors_become_terminal_at_the_attempt_ceiling(
    attempt_count: int, expected: JobStatus
) -> None:
    facts = JobFacts(status=JobStatus.RUNNING, attempt_count=attempt_count)
    transition = next_state(facts, JobEvent.RETRYABLE_ERROR, policy=POLICY)
    assert POLICY.max_attempts == 5
    assert transition.status is expected
    assert transition.attempt_delta == 1
    assert transition.release_lease is True


@pytest.mark.parametrize(
    ("takeovers", "expected"),
    [(0, JobStatus.QUEUED), (1, JobStatus.QUEUED), (2, JobStatus.FAILED)],
)
def test_lease_takeovers_have_their_own_independent_ceiling(
    takeovers: int, expected: JobStatus
) -> None:
    facts = JobFacts(status=JobStatus.RUNNING, lease_takeover_count=takeovers)
    transition = next_state(facts, JobEvent.LEASE_EXPIRED, policy=POLICY)
    assert POLICY.max_lease_takeovers == 3
    assert transition.status is expected
    assert transition.takeover_delta == 1
    # A dead worker is infrastructure, not a failed business attempt; counting it would
    # let one restart wave push every live job to the dead-letter queue.
    assert transition.attempt_delta == 0
    # Recovery must not depend on the queue redelivering a message a dead worker may
    # have consumed, so a surviving job is re-enqueued rather than left dispatched.
    assert transition.enqueue_outbox is (expected is JobStatus.QUEUED)


def test_a_takeover_ceiling_below_the_attempt_ceiling_still_fails_the_job() -> None:
    facts = JobFacts(status=JobStatus.RUNNING, lease_takeover_count=5, attempt_count=0)
    assert next_state(facts, JobEvent.LEASE_EXPIRED, policy=POLICY).status is JobStatus.FAILED


def test_lease_bound_events_are_rejected_outside_a_running_attempt() -> None:
    for event in (
        JobEvent.HEARTBEAT,
        JobEvent.PUBLISH_RESULT,
        JobEvent.RETRYABLE_ERROR,
        JobEvent.PERMANENT_ERROR,
    ):
        for status in (JobStatus.QUEUED, JobStatus.DISPATCHED, JobStatus.RETRYABLE_FAILED):
            with pytest.raises(ServiceFault):
                next_state(JobFacts(status=status), event, policy=POLICY)


@pytest.mark.parametrize(
    "event",
    [JobEvent.HEARTBEAT, JobEvent.PUBLISH_RESULT, JobEvent.NEEDS_HUMAN, JobEvent.PERMANENT_ERROR],
)
def test_lease_bound_events_are_marked_for_a_fencing_condition(event: JobEvent) -> None:
    facts = JobFacts(status=JobStatus.RUNNING, has_open_tasks=True)
    assert next_state(facts, event, policy=POLICY).requires_lease is True


def test_lease_expiry_is_not_lease_bound_because_the_reconciler_raises_it() -> None:
    facts = JobFacts(status=JobStatus.RUNNING)
    assert next_state(facts, JobEvent.LEASE_EXPIRED, policy=POLICY).requires_lease is False


def test_negative_counters_are_rejected_as_corrupt_rather_than_clamped() -> None:
    with pytest.raises(ValueError, match="negative"):
        next_state(
            JobFacts(status=JobStatus.RUNNING, attempt_count=-1),
            JobEvent.HEARTBEAT,
            policy=POLICY,
        )


class TestJobPolicy:
    def test_heartbeat_must_fit_three_times_inside_the_lease(self) -> None:
        with pytest.raises(ValueError, match="three times"):
            JobPolicy(lease_seconds=60, heartbeat_seconds=30)

    def test_visibility_timeout_must_outlast_the_lease(self) -> None:
        # The safe direction: a message reappearing early only wastes a claim, while a
        # lease expiring first strands the job until the reconciler notices.
        with pytest.raises(ValueError, match="Visibility timeout"):
            JobPolicy(lease_seconds=180, heartbeat_seconds=30, visibility_timeout_seconds=180)

    def test_defaults_satisfy_their_own_invariants(self) -> None:
        policy = JobPolicy()
        assert policy.visibility_timeout_seconds > policy.lease_seconds
        assert policy.heartbeat_seconds * 3 <= policy.lease_seconds

    def test_backoff_grows_and_stops_at_the_queue_delay_ceiling(self) -> None:
        policy = JobPolicy()
        delays = [policy.backoff_seconds(attempt) for attempt in range(1, 9)]
        assert delays[0] == 30
        assert delays == sorted(delays)
        # SQS DelaySeconds caps at 900; anything longer must come from the outbox schedule.
        assert max(delays) == policy.backoff_cap_seconds == 900

    def test_backoff_requires_a_one_based_attempt(self) -> None:
        with pytest.raises(ValueError, match="one-based"):
            JobPolicy().backoff_seconds(0)


class TestWireProjection:
    @pytest.mark.parametrize("status", list(JobStatus))
    def test_every_durable_status_projects_onto_a_frozen_wire_value(
        self, status: JobStatus
    ) -> None:
        assert wire_status(status) in set(ExecutionStatus)

    def test_waiting_for_human_reports_a_successful_execution(self) -> None:
        # The attempt ran and produced findings; the case did not pass. ServiceResult's
        # own validator also refuses findings on an active execution.
        assert wire_status(JobStatus.WAITING_FOR_HUMAN) is ExecutionStatus.SUCCEEDED

    def test_a_retryable_failure_is_not_reported_as_failure(self) -> None:
        assert wire_status(JobStatus.RETRYABLE_FAILED) is ExecutionStatus.RUNNING

    def test_dispatched_is_indistinguishable_from_queued_on_the_wire(self) -> None:
        assert wire_status(JobStatus.DISPATCHED) is wire_status(JobStatus.QUEUED)
