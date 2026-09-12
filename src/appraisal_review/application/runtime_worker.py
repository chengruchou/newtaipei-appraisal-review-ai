"""Bounded execution with durable claims, lease heartbeats and fenced publication."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID, uuid4

from appraisal_review.application.outbox import DispatchMessage
from appraisal_review.application.review_jobs import ReviewJobService
from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.domain.job_contracts import JobStatus
from appraisal_review.domain.service_contracts import ServiceErrorCode, ServiceResult
from appraisal_review.ports.jobs import ClaimedAttempt, ConditionFailed, JobRecord
from appraisal_review.ports.model_dispatch import dispatch_async_authority


@dataclass(frozen=True)
class ExecutedReview:
    result: ServiceResult | None = None
    persisted_task_ids: tuple[UUID, ...] = ()

    def __post_init__(self) -> None:
        if (self.result is None) == (not self.persisted_task_ids):
            raise ValueError("Return a result or already persisted human tasks")


class ReviewExecution(Protocol):
    async def execute(self, record: JobRecord, attempt: ClaimedAttempt) -> ExecutedReview:
        """Recheck current principal and C2 snapshot; never derive authority from the queue.

        Human tasks must be durably committed before returning their IDs. An unavailable
        reviewed rule/revision/publication provider raises capability_unavailable.
        """
        ...


class RuntimeWorker:
    def __init__(
        self, service: ReviewJobService, execution: ReviewExecution, *, timeout_seconds: float = 90
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("Positive worker timeout required")
        self.service, self.execution, self.timeout = service, execution, timeout_seconds

    async def process(self, message: DispatchMessage) -> str:
        try:
            attempt = await self.service.claim(
                job_id=message.job_id, run_id=message.run_id, owner=uuid4()
            )
        except ConditionFailed:
            return "superseded"
        task: asyncio.Task[ExecutedReview] | None = None
        try:
            record = await self.service.store.read_job(job_id=message.job_id)
            if record is None or record.current_run.run_id != message.run_id:
                raise ConditionFailed
            if record.cancel_requested:
                await self.service.acknowledge_cancel(attempt)
                return "cancelled"
            # A queue payload contains no caller, source path, rule approval or result.
            loop = asyncio.get_running_loop()
            deadline = loop.time() + self.timeout
            lease_expires_at = attempt.lease_expires_at

            async def require_dispatch() -> None:
                current = await self.service.store.read_job(job_id=record.job_id)
                if (
                    loop.time() >= deadline
                    or self.service.clock() >= lease_expires_at
                    or current is None
                    or current.status != JobStatus.RUNNING
                    or current.cancel_requested
                    or current.current_run != record.current_run
                    or current.principal_id != record.principal_id
                    or current.case_id != record.case_id
                    or current.result_version != attempt.expected_result_version
                    or current.current_run.attempt_id != attempt.attempt_id
                ):
                    raise ConditionFailed("Current uncancelled attempt and lease required")

            async def execute_current() -> ExecutedReview:
                with dispatch_async_authority(require_dispatch):
                    return await self.execution.execute(record, attempt)

            task = asyncio.create_task(execute_current())
            while not task.done():
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise TimeoutError
                done, _ = await asyncio.wait(
                    {task}, timeout=min(self.service.policy.heartbeat_seconds, remaining)
                )
                if asyncio.get_running_loop().time() >= deadline:
                    raise TimeoutError
                state = await self.service.heartbeat(attempt)
                lease_expires_at = state.lease_expires_at
                if state.cancel_requested:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    await self.service.acknowledge_cancel(attempt)
                    return "cancelled"
                if asyncio.get_running_loop().time() >= deadline:
                    raise TimeoutError
                if done:
                    break
            executed = task.result()
            if executed.result is not None:
                if executed.result.run != record.current_run:
                    raise ServiceFault(ServiceErrorCode.VALIDATION)
                result = ServiceResult.model_validate(
                    executed.result.model_dump(warnings="error") | {"durable": True}
                )
                await self.service.publish(attempt, result)
                return "published"
            await self.service.wait_for_human(attempt, executed.persisted_task_ids)
            return "waiting_for_human"
        except ConditionFailed:
            return "superseded"
        except ServiceFault as error:
            permanent = error.problem.code in {
                ServiceErrorCode.UNAUTHORIZED,
                ServiceErrorCode.NOT_FOUND,
                ServiceErrorCode.VALIDATION,
            }
            try:
                failed = await self.service.fail(
                    attempt, code=error.problem.code, retryable=not permanent
                )
            except ConditionFailed:
                return "superseded"
            return self._failure_outcome(failed)
        except Exception:
            try:
                failed = await self.service.fail(
                    attempt, code=ServiceErrorCode.EXECUTION, retryable=True
                )
            except ConditionFailed:
                return "superseded"
            return self._failure_outcome(failed)
        finally:
            if task is not None:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    @staticmethod
    def _failure_outcome(record: JobRecord) -> str:
        if record.status == JobStatus.CANCELLED:
            return "cancelled"
        if record.status == JobStatus.FAILED:
            return "failed"
        return "retry_scheduled"
