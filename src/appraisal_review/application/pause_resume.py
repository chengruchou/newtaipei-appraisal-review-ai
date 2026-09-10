"""Explicit local handoff seam, not a job scheduler or Runtime lifecycle manager."""

from uuid import UUID

from appraisal_review.domain.service_contracts import (
    BoundedWorkflowResult,
    RunReference,
    WorkflowContinuation,
    WorkflowPause,
)
from appraisal_review.ports.service import PauseResumeRepository, PrincipalResolver


class PauseResumeService:
    """Capture an ended bounded workflow; never wait, dispatch or revive an attempt.

    Supply actual runner results from trusted composition, never request-body state.
    WorkflowTaskService owns reference-workflow responses; its repository records
    continuations in the same response transaction. Durable dispatch and resource
    release are deferred.
    """

    def __init__(self, repository: PauseResumeRepository, principals: PrincipalResolver) -> None:
        self._repository = repository
        self._principals = principals

    async def pause(self, result: BoundedWorkflowResult) -> WorkflowPause:
        return await self._repository.pause(await self._principals.current_principal(), result)

    async def read(self, run: RunReference) -> WorkflowPause:
        return await self._repository.read_pause(await self._principals.current_principal(), run)

    async def continuation(self, response_event_id: UUID) -> WorkflowContinuation:
        return await self._repository.continuation(
            await self._principals.current_principal(), response_event_id
        )
