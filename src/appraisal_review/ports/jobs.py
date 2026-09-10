"""Durable job store primitives. Every method is one conditional write with one meaning.

The port is deliberately fine-grained. A coarse "save the job" method cannot express the
compare-and-swap conditions that make duplicate dispatch, stale publication and lease
takeover safe, and an in-memory adapter that cannot express them cannot faithfully stand
in for DynamoDB in the shared contract suite.

Full results never enter this store: a ServiceResult carries findings with source
excerpts, which exceeds a DynamoDB item and breaches the job-payload privacy boundary.
The body goes to ResultStore under an attempt-scoped key; only the reference is
committed here, and only under the publishing attempt's fencing token.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from appraisal_review.application.job_state import JobEvent
from appraisal_review.application.service_guards import Principal
from appraisal_review.domain.document_models import Digest
from appraisal_review.domain.factor_models import ArtifactStatus, WorkflowStatus
from appraisal_review.domain.job_contracts import JobStatus
from appraisal_review.domain.service_contracts import (
    ExecutionStatus,
    ReviewSubmission,
    RunReference,
    ServiceProblem,
    ServiceResult,
)


class ConditionFailed(Exception):
    """The stored state no longer matches the caller's expectation.

    This is never a retry signal. It means another writer moved first, so the caller
    either stops or re-reads and decides again. Retrying it as if it were throttling is
    the usual way durable state gets corrupted.
    """


@dataclass(frozen=True)
class JobRecord:
    """Authorized projection of the control plane. Carries no URI, queue or lease owner."""

    job_id: UUID
    case_id: str
    principal_id: str
    status: JobStatus
    current_run: RunReference
    attempt_count: int = 0
    lease_takeover_count: int = 0
    result_version: int = 0
    cancel_requested: bool = False
    open_task_ids: tuple[UUID, ...] = ()
    problem: ServiceProblem | None = None


@dataclass(frozen=True)
class ClaimedAttempt:
    """Proof of execution authority for exactly one attempt of exactly one run."""

    job_id: UUID
    run_id: UUID
    attempt_id: UUID
    owner: UUID
    fencing_token: int
    expected_result_version: int
    lease_expires_at: int


@dataclass(frozen=True)
class HeartbeatState:
    lease_expires_at: int
    cancel_requested: bool


@dataclass(frozen=True)
class DispatchRecord:
    """One outbox entry: work that is durably owned but not yet handed to the queue."""

    job_id: UUID
    run_id: UUID
    outbox_seq: int
    dispatch_token: UUID
    available_at: int
    dispatch_attempts: int = 0


@dataclass(frozen=True)
class ExpiredLease:
    job_id: UUID
    run_id: UUID
    fencing_token: int
    lease_expires_at: int


@dataclass(frozen=True)
class ResultReference:
    """What the control plane keeps about a result: identity, status, digest, counts."""

    run_id: UUID
    result_version: int
    fencing_token: int
    execution_status: ExecutionStatus
    business_status: WorkflowStatus | None
    artifact_status: ArtifactStatus
    result_digest: Digest
    artifact_ids: tuple[UUID, ...] = ()
    finding_count: int = 0


class ResultStore(Protocol):
    """Attempt-scoped result bodies. Written before commit, referenced only after it."""

    async def put(self, *, run_id: UUID, result_version: int, result: ServiceResult) -> Digest:
        """Write the body idempotently and return its digest; an orphan body is never read."""
        ...

    async def get(self, *, run_id: UUID, result_version: int) -> ServiceResult | None:
        """Return a committed body. The caller has already checked authorization."""
        ...


class JobStore(Protocol):
    async def reject_human_task(
        self, *, job_id: UUID, run_id: UUID, task_id: UUID, now: int
    ) -> JobRecord:
        """Remove one current open task; fail the job if no unresolved tasks remain.

        A rejection never resumes a run or claims successful completion. The human
        response adapter must enlist this projection in its atomic response boundary.
        """
        ...

    async def create_job(
        self,
        principal: Principal,
        submission: ReviewSubmission,
        *,
        job_id: UUID,
        run_id: UUID,
        now: int,
    ) -> tuple[JobRecord, bool]:
        """Atomically write job, run, first outbox entry and the idempotency record.

        Uniqueness comes from a conditional write on the idempotency record's own primary
        key, never from a secondary index: an eventually consistent index lets two
        concurrent submissions both observe an unused key.

        Returns the record and whether this call created it. Replay with the same
        principal, key and canonical payload returns the stored job; a changed payload
        raises ServiceFault(CONFLICT).
        """
        ...

    async def read_job(self, *, job_id: UUID) -> JobRecord | None:
        """Strongly consistent read. Authorization is the caller's decision, not the store's."""
        ...

    async def read_result_reference(
        self, *, run_id: UUID, result_version: int
    ) -> ResultReference | None:
        """Return the committed reference, so a body can be checked against it before use."""
        ...

    async def claim(
        self, *, job_id: UUID, run_id: UUID, owner: UUID, lease_seconds: int, now: int
    ) -> ClaimedAttempt:
        """Conditionally take the lease and increment the fencing token in one write.

        The token is incremented by the store, never computed by the caller. ConditionFailed
        means a valid lease is held elsewhere, or the run already moved on; the caller stops
        without treating the message as failed work.
        """
        ...

    async def heartbeat(
        self, attempt: ClaimedAttempt, *, lease_seconds: int, now: int
    ) -> HeartbeatState:
        """Extend the lease only while owner and fencing token still match.

        ConditionFailed means this attempt has been taken over and must stop writing.
        The returned cancel flag lets a worker stop cooperatively instead of being killed.
        """
        ...

    async def finish(
        self,
        attempt: ClaimedAttempt,
        *,
        event: JobEvent,
        now: int,
        problem: ServiceProblem | None = None,
        open_task_ids: tuple[UUID, ...] = (),
        result: ResultReference | None = None,
        available_at: int | None = None,
    ) -> JobRecord:
        """Apply one lease-bound transition and release the lease in the same write.

        Accepts PUBLISH_RESULT, NEEDS_HUMAN, RETRYABLE_ERROR, PERMANENT_ERROR and
        CANCEL_ACKNOWLEDGED. Publication additionally requires the attempt's fencing token
        and the expected result version, so a resumed old attempt cannot overwrite a newer
        result.
        """
        ...

    async def expire_lease(self, lease: ExpiredLease, *, now: int) -> JobRecord:
        """Reclaim a lease whose deadline has passed, without consuming a business attempt."""
        ...

    async def cancel(self, *, job_id: UUID, now: int) -> JobRecord:
        """Cancel a non-terminal job, or flag a running attempt to stop cooperatively."""
        ...

    async def schedule_retry(self, *, job_id: UUID, available_at: int, now: int) -> JobRecord:
        """Return a retryable failure to the queue with a new outbox entry and a delay.

        Raises ConditionFailed if the job has left retryable_failed, because the failing
        worker and the reconciler both schedule retries and either may arrive first.
        """
        ...

    async def resume_after_human(self, *, job_id: UUID, run: RunReference, now: int) -> JobRecord:
        """Attach a new run for a committed revision and enqueue it, in one write.

        The human-task owner calls this inside its own response transaction so a stored
        revision can never exist without scheduled work. The old run keeps its history and
        its lease stays released; fencing tokens restart within the new run.
        """
        ...

    async def mark_dispatched(self, record: DispatchRecord, *, now: int) -> JobRecord:
        """Record that the queue accepted this exact dispatch, leaving the index sparse."""
        ...

    async def reschedule_dispatch(self, record: DispatchRecord, *, available_at: int) -> bool:
        """A send failed: move only the outbox schedule, never the job status.

        The work is still durably owned, so the job must not be reported as failed. This
        must succeed whatever non-terminal status the job now holds: a later round may
        already have moved it to dispatched, or a worker may already have claimed it.

        Returns False when the job is terminal and the round was abandoned instead, so a
        cancelled or failed job is not rescheduled against workers that cannot claim it.
        """
        ...

    async def pending_dispatches(self, *, now: int, limit: int) -> tuple[DispatchRecord, ...]:
        """Eventually consistent candidates only; re-read and write conditionally to act."""
        ...

    async def expired_leases(self, *, now: int, limit: int) -> tuple[ExpiredLease, ...]:
        """Same contract: candidates only, never an authorization to take over."""
        ...

    async def stranded_retryables(
        self, *, stranded_before: int, limit: int
    ) -> tuple[JobRecord, ...]:
        """Jobs still in retryable_failed since before the cutoff, oldest first.

        A crash between finishing an attempt and scheduling its retry leaves a job with
        no lease and no outbox entry, so neither of the other two scans can see it. The
        caller sets the cutoff far enough back that a live worker's own retry wins first;
        candidates only, like the other scans.
        """
        ...
