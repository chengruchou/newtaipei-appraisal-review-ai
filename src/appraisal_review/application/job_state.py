"""The single authority for durable job status changes. No I/O, clock or randomness.

Both the in-memory adapter and the future DynamoDB adapter route every status change
through next_state, so local contract tests and opt-in cloud tests exercise one state
machine rather than two implementations that drift.

Lease expiry is deliberately not decided here: a pure function that reads a clock cannot
be exhaustively tested. The store decides expiry with a conditional write and reports it
back as the LEASE_EXPIRED event.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.domain.job_contracts import TERMINAL_STATUSES, JobStatus
from appraisal_review.domain.service_contracts import ServiceErrorCode


class JobEvent(StrEnum):
    SUBMIT = "submit"
    DISPATCH_SUCCEEDED = "dispatch_succeeded"
    DISPATCH_FAILED = "dispatch_failed"
    CLAIM = "claim"
    HEARTBEAT = "heartbeat"
    LEASE_EXPIRED = "lease_expired"
    NEEDS_HUMAN = "needs_human"
    PUBLISH_RESULT = "publish_result"
    RETRYABLE_ERROR = "retryable_error"
    PERMANENT_ERROR = "permanent_error"
    SCHEDULE_RETRY = "schedule_retry"
    HUMAN_RESPONSE_COMMITTED = "human_response_committed"
    CANCEL = "cancel"
    CANCEL_ACKNOWLEDGED = "cancel_acknowledged"


# Events a worker may raise only while it still holds the lease it claimed. The store
# applies an owner/fencing-token condition for these and for nothing else.
LEASE_BOUND_EVENTS = frozenset(
    {
        JobEvent.HEARTBEAT,
        JobEvent.NEEDS_HUMAN,
        JobEvent.PUBLISH_RESULT,
        JobEvent.RETRYABLE_ERROR,
        JobEvent.PERMANENT_ERROR,
        JobEvent.CANCEL_ACKNOWLEDGED,
    }
)

CLAIMABLE_STATUSES = frozenset({JobStatus.QUEUED, JobStatus.DISPATCHED, JobStatus.RETRYABLE_FAILED})

CANCELLABLE_STATUSES = frozenset(
    {
        JobStatus.QUEUED,
        JobStatus.DISPATCHED,
        JobStatus.WAITING_FOR_HUMAN,
        JobStatus.RETRYABLE_FAILED,
    }
)


@dataclass(frozen=True)
class JobPolicy:
    """Bounded execution policy. Values come from configuration, never from a payload."""

    lease_seconds: int = 120
    heartbeat_seconds: int = 30
    visibility_timeout_seconds: int = 180
    max_attempts: int = 5
    max_lease_takeovers: int = 3
    backoff_base_seconds: int = 30
    # SQS DelaySeconds caps at 900; longer backoff must come from the outbox schedule.
    backoff_cap_seconds: int = 900

    def __post_init__(self) -> None:
        if min(self.lease_seconds, self.heartbeat_seconds, self.backoff_base_seconds) < 1:
            raise ValueError("Job policy durations must be positive")
        if min(self.max_attempts, self.max_lease_takeovers) < 1:
            raise ValueError("Job policy limits must allow at least one attempt")
        if self.heartbeat_seconds * 3 > self.lease_seconds:
            raise ValueError("Heartbeat interval must fit three times inside the lease")
        # A message that becomes visible while its lease is still valid only wastes a
        # claim; a lease that expires first strands the job until the reconciler runs.
        if self.visibility_timeout_seconds <= self.lease_seconds:
            raise ValueError("Visibility timeout must outlast the lease")
        if self.backoff_cap_seconds < self.backoff_base_seconds:
            raise ValueError("Backoff cap must not be below the base delay")

    def backoff_seconds(self, attempt: int) -> int:
        """Exponential backoff for a one-based attempt number, without jitter.

        The caller adds jitter; keeping this deterministic makes the bound testable.
        """
        if attempt < 1:
            raise ValueError("Backoff requires a one-based attempt number")
        # Bound the shift so a corrupted attempt counter cannot build a huge integer.
        shift = min(attempt - 1, 32)
        return min(self.backoff_base_seconds << shift, self.backoff_cap_seconds)


@dataclass(frozen=True)
class JobFacts:
    """Everything a transition needs. The caller reads it under strong consistency."""

    status: JobStatus
    attempt_count: int = 0
    lease_takeover_count: int = 0
    cancel_requested: bool = False
    has_open_tasks: bool = False


@dataclass(frozen=True)
class Transition:
    """The decided outcome. Deltas are applied atomically by the store, not by callers."""

    status: JobStatus
    attempt_delta: int = 0
    takeover_delta: int = 0
    release_lease: bool = False
    enqueue_outbox: bool = False
    request_cancel: bool = False
    requires_lease: bool = False


def _conflict() -> ServiceFault:
    return ServiceFault(ServiceErrorCode.CONFLICT)


def initial_transition() -> Transition:
    """T1: a submission creates the job, its first run and its first outbox entry."""
    return Transition(status=JobStatus.QUEUED, enqueue_outbox=True)


def next_state(facts: JobFacts, event: JobEvent, *, policy: JobPolicy) -> Transition:
    """Decide one status change, or raise ServiceFault(CONFLICT) for an illegal one.

    Terminal statuses absorb nothing: they reject every event so that a late worker
    cannot reopen or overwrite a finished job.
    """
    if facts.attempt_count < 0 or facts.lease_takeover_count < 0:
        raise ValueError("Job counters cannot be negative")
    if facts.status in TERMINAL_STATUSES:
        raise _conflict()
    # A job that already exists can never be submitted again; replay is resolved by the
    # idempotency record before this function is reached.
    if event == JobEvent.SUBMIT:
        raise _conflict()

    requires_lease = event in LEASE_BOUND_EVENTS
    if requires_lease and facts.status != JobStatus.RUNNING:
        raise _conflict()

    if event == JobEvent.DISPATCH_SUCCEEDED:
        if facts.status != JobStatus.QUEUED:
            raise _conflict()
        return Transition(status=JobStatus.DISPATCHED)

    if event == JobEvent.DISPATCH_FAILED:
        # T3: only the outbox schedule moves; the job stays queued and keeps its work.
        if facts.status != JobStatus.QUEUED:
            raise _conflict()
        return Transition(status=JobStatus.QUEUED)

    if event == JobEvent.CLAIM:
        if facts.status not in CLAIMABLE_STATUSES:
            raise _conflict()
        return Transition(status=JobStatus.RUNNING)

    if event == JobEvent.HEARTBEAT:
        return Transition(status=JobStatus.RUNNING, requires_lease=True)

    if event == JobEvent.LEASE_EXPIRED:
        if facts.status != JobStatus.RUNNING:
            raise _conflict()
        # A dead worker is an infrastructure event, so it must not consume a business
        # attempt: one Runtime restart would otherwise push every live job to the DLQ.
        # Takeovers still need their own ceiling, or a crash loop retries forever.
        exhausted = facts.lease_takeover_count + 1 >= policy.max_lease_takeovers
        # Reclaiming returns the job to queued with a fresh outbox entry rather than to
        # dispatched. The round that carried this run was already marked sent, so leaving
        # the job dispatched would make recovery depend entirely on the queue redelivering
        # a message that a crashed worker may have consumed or deleted. A duplicate
        # delivery is harmless — the second claim simply loses — but a lost one is not.
        return Transition(
            status=JobStatus.FAILED if exhausted else JobStatus.QUEUED,
            takeover_delta=1,
            release_lease=True,
            enqueue_outbox=not exhausted,
        )

    if event == JobEvent.NEEDS_HUMAN:
        if not facts.has_open_tasks:
            raise _conflict()
        return Transition(
            status=JobStatus.WAITING_FOR_HUMAN, release_lease=True, requires_lease=True
        )

    if event == JobEvent.PUBLISH_RESULT:
        return Transition(status=JobStatus.SUCCEEDED, release_lease=True, requires_lease=True)

    if event == JobEvent.RETRYABLE_ERROR:
        exhausted = facts.attempt_count + 1 >= policy.max_attempts
        return Transition(
            status=JobStatus.FAILED if exhausted else JobStatus.RETRYABLE_FAILED,
            attempt_delta=1,
            release_lease=True,
            requires_lease=True,
        )

    if event == JobEvent.PERMANENT_ERROR:
        return Transition(status=JobStatus.FAILED, release_lease=True, requires_lease=True)

    if event == JobEvent.SCHEDULE_RETRY:
        if facts.status != JobStatus.RETRYABLE_FAILED:
            raise _conflict()
        return Transition(status=JobStatus.QUEUED, enqueue_outbox=True)

    if event == JobEvent.HUMAN_RESPONSE_COMMITTED:
        # T12: a response does not revive the finished attempt. The caller writes a new
        # revision, a new run and a new outbox entry in the same transaction.
        if facts.status != JobStatus.WAITING_FOR_HUMAN:
            raise _conflict()
        return Transition(status=JobStatus.QUEUED, enqueue_outbox=True)

    if event == JobEvent.CANCEL:
        if facts.status == JobStatus.RUNNING:
            # A running attempt is asked to stop, never killed mid-write; it observes the
            # flag on its next heartbeat and finishes cooperatively.
            return Transition(status=JobStatus.RUNNING, request_cancel=True)
        if facts.status not in CANCELLABLE_STATUSES:
            raise _conflict()
        return Transition(status=JobStatus.CANCELLED, release_lease=True)

    if event == JobEvent.CANCEL_ACKNOWLEDGED:
        if not facts.cancel_requested:
            raise _conflict()
        return Transition(status=JobStatus.CANCELLED, release_lease=True, requires_lease=True)

    raise _conflict()
