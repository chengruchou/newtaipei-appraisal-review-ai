"""Outbox dispatch and reconciliation, independent of any queue implementation.

A job and its outbox entry are committed before anything is sent, so the window between
"durably owned" and "handed to the queue" always exists. These passes close it: they are
the only reason a crash after the commit does not lose work.

The queue is injected as a coroutine, so the same logic is exercised by local tests and,
in #29's cloud phase, by an SQS-backed sender. Nothing here decides job status; every
change goes through the store's conditional writes and the shared state machine.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from appraisal_review.application.review_jobs import ReviewJobService
from appraisal_review.domain.job_contracts import JobStatus
from appraisal_review.ports.jobs import ConditionFailed, DispatchRecord


class TransientDispatchError(Exception):
    """The queue refused the message for a reason that may succeed on a later pass."""


@dataclass(frozen=True)
class DispatchMessage:
    """Queue payload: references only.

    A message is read repeatedly by monitoring, dead-letter tooling and redrive
    operators, so it carries no submission payload. The worker re-reads the run under
    strong consistency, which also removes any chance of the message and the store
    disagreeing. The dispatch token identifies the round for observability, never for
    authorization: authority comes only from a conditional claim.
    """

    job_id: UUID
    run_id: UUID
    outbox_seq: int
    dispatch_token: UUID
    enqueued_at: int
    schema_version: Literal["job-dispatch-v1"] = "job-dispatch-v1"


Sender = Callable[[DispatchMessage], Awaitable[None]]


@dataclass(frozen=True)
class DispatchOutcome:
    sent: int = 0
    deferred: int = 0
    superseded: int = 0


@dataclass(frozen=True)
class ReconcileOutcome:
    dispatch: DispatchOutcome
    leases_reclaimed: int = 0
    retries_scheduled: int = 0


class OutboxDispatcher:
    """Hands durably owned work to the queue and records the outcome of each round."""

    def __init__(self, service: ReviewJobService, send: Sender) -> None:
        self.service = service
        self.send = send

    async def run_once(self, *, limit: int = 25) -> DispatchOutcome:
        sent = deferred = superseded = 0
        for record in await self.service.due_dispatches(limit=limit):
            try:
                await self.send(self._message(record))
            except TransientDispatchError:
                # The work stays queued and owned; only its schedule moves. Reporting the
                # job as failed here would discard work the store still holds.
                await self.service.defer_dispatch(record)
                deferred += 1
                continue
            try:
                await self.service.confirm_dispatch(record)
                sent += 1
            except ConditionFailed:
                # A duplicate pass, or a worker that already claimed between the send and
                # this mark. The message is out either way, and a claim is still safe.
                superseded += 1
        return DispatchOutcome(sent=sent, deferred=deferred, superseded=superseded)

    def _message(self, record: DispatchRecord) -> DispatchMessage:
        return DispatchMessage(
            job_id=record.job_id,
            run_id=record.run_id,
            outbox_seq=record.outbox_seq,
            dispatch_token=record.dispatch_token,
            enqueued_at=self.service.clock(),
        )


class JobReconciler:
    """Repairs the states a crash can leave behind, with bounded work per pass."""

    def __init__(self, dispatcher: OutboxDispatcher) -> None:
        self.dispatcher = dispatcher
        self.service = dispatcher.service

    async def run_once(self, *, limit: int = 25) -> ReconcileOutcome:
        # Order matters: reclaim first, so a run whose worker died becomes dispatchable
        # again within the same pass instead of waiting for the next one.
        reclaimed = await self.service.reclaim_expired_leases(limit=limit)
        retries = 0
        for record in reclaimed:
            if record.status is not JobStatus.RETRYABLE_FAILED:
                continue
            if await self.service.recover_retryable(job_id=record.job_id) is not None:
                retries += 1
        dispatch = await self.dispatcher.run_once(limit=limit)
        return ReconcileOutcome(
            dispatch=dispatch, leases_reclaimed=len(reclaimed), retries_scheduled=retries
        )

    async def recover_job(self, *, job_id: UUID) -> bool:
        """Reschedule one job left in retryable_failed by a process that died mid-recovery."""
        return await self.service.recover_retryable(job_id=job_id) is not None
