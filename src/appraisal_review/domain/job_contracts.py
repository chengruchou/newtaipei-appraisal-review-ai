"""Job control-plane contracts, added to the service-v1 bundle without changing v1 models.

A job is the durable delegation a caller polls. It spans one or more runs, because a
human revision produces a new immutable run by definition. Frozen v1 models forbid
extra fields, so job status is expressed by new models rather than by widening
ExecutionStatus. See docs/service-contracts.md.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from appraisal_review.domain.service_contracts import (
    ExecutionStatus,
    OpaqueID,
    RunReference,
    ServiceModel,
    ServiceProblem,
)


class JobStatus(StrEnum):
    """Durable control-plane status; never interchangeable with wire ExecutionStatus."""

    QUEUED = "queued"
    DISPATCHED = "dispatched"
    RUNNING = "running"
    WAITING_FOR_HUMAN = "waiting_for_human"
    RETRYABLE_FAILED = "retryable_failed"
    FAILED = "failed"
    SUCCEEDED = "succeeded"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = frozenset({JobStatus.FAILED, JobStatus.SUCCEEDED, JobStatus.CANCELLED})

# Waiting for a reviewer is neither progress nor failure, so it releases the lease but
# stays non-terminal. Retry policy must never treat it as an infrastructure error.
LEASE_HOLDING_STATUSES = frozenset({JobStatus.RUNNING})

# Only a running attempt defers its cancellation, and every event that ends or reclaims
# that attempt resolves the request. A pending cancel is therefore observable while the
# job runs and afterwards only on a terminal job: no other status may carry one forward
# into new work. See job_state._cancel_preempted for the transitions that guarantee it.
CANCEL_PENDING_STATUSES = frozenset({JobStatus.RUNNING}) | TERMINAL_STATUSES

WIRE_STATUS: Mapping[JobStatus, ExecutionStatus] = {
    JobStatus.QUEUED: ExecutionStatus.QUEUED,
    JobStatus.DISPATCHED: ExecutionStatus.QUEUED,
    JobStatus.RUNNING: ExecutionStatus.RUNNING,
    # An attempt that produced findings and open tasks ran successfully; the case did not
    # pass. Reporting RUNNING would also be rejected by ServiceResult's own validator.
    JobStatus.WAITING_FOR_HUMAN: ExecutionStatus.SUCCEEDED,
    # Still in progress from the caller's view; an exhausted retry becomes FAILED instead.
    JobStatus.RETRYABLE_FAILED: ExecutionStatus.RUNNING,
    JobStatus.FAILED: ExecutionStatus.FAILED,
    JobStatus.SUCCEEDED: ExecutionStatus.SUCCEEDED,
    # v1 has no cancelled value; the lossless status stays in JobStatusView.job_status.
    JobStatus.CANCELLED: ExecutionStatus.FAILED,
}


def wire_status(status: JobStatus) -> ExecutionStatus:
    """Project the eight durable statuses onto the four frozen wire values."""
    return WIRE_STATUS[status]


class JobReference(ServiceModel):
    """External job identity. Never carries a queue name, table name or worker identity."""

    case_id: OpaqueID
    job_id: UUID


class JobStatusView(ServiceModel):
    """Authorized status projection: no storage URI, lease owner or dispatch token."""

    job: JobReference
    job_status: JobStatus
    current_run: RunReference | None = None
    attempt_count: int = Field(default=0, ge=0, strict=True)
    result_version: int = Field(default=0, ge=0, strict=True)
    # A cancel a running attempt has not yet acknowledged. Without it the status view
    # cannot distinguish a job that is running from one that is running under notice,
    # and the principal who cancelled sees no trace of the decision until it lands.
    cancel_requested: bool = False
    open_task_ids: tuple[UUID, ...] = ()
    problem: ServiceProblem | None = None

    @model_validator(mode="after")
    def honest_status(self) -> JobStatusView:
        if (self.job_status in {JobStatus.FAILED, JobStatus.CANCELLED}) != (
            self.problem is not None
        ):
            raise ValueError("Terminal failure and cancellation carry a sanitized problem")
        if self.job_status == JobStatus.SUCCEEDED and self.result_version < 1:
            raise ValueError("A succeeded job must reference a committed result version")
        if self.job_status == JobStatus.WAITING_FOR_HUMAN and not self.open_task_ids:
            raise ValueError("Waiting for human requires at least one open task")
        if self.cancel_requested and self.job_status not in CANCEL_PENDING_STATUSES:
            raise ValueError("A pending cancel cannot survive into work that was not cancelled")
        if len(set(self.open_task_ids)) != len(self.open_task_ids):
            raise ValueError("Duplicate open task reference")
        if self.current_run is not None and self.current_run.revision.case_id != self.job.case_id:
            raise ValueError("The current run must belong to the job's case")
        return self


class JobAcceptance(ServiceModel):
    """202 body: acceptance of durable responsibility, never a result or a claim of work.

    job_status is pinned so this body can never advertise progress that has not happened.
    An idempotent replay of an already-known submission returns JobStatusView instead.
    """

    job: JobReference
    run: RunReference
    job_status: Literal[JobStatus.QUEUED] = JobStatus.QUEUED

    @model_validator(mode="after")
    def same_case(self) -> JobAcceptance:
        if self.run.revision.case_id != self.job.case_id:
            raise ValueError("The accepted run must belong to the job's case")
        if self.run.attempt_id is not None or self.run.runtime_session_id is not None:
            raise ValueError("Acceptance precedes any attempt or Runtime session")
        return self
