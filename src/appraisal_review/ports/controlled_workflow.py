"""Provider-neutral execution and decision-trace boundaries."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from appraisal_review.domain.service_contracts import (
    ActionProposal,
    AllowedActionSet,
    ControlledToolReceipt,
    DecisionEvent,
    RunReference,
    SelectionFailureEvent,
    WorkflowSnapshot,
)


class WorkflowSnapshotProvider(Protocol):
    async def current(self, run: RunReference) -> WorkflowSnapshot:
        """Capture current trusted state; never accept workflow state from a request body."""
        ...


class AllowedActionPolicy(Protocol):
    def derive(self, snapshot: WorkflowSnapshot) -> AllowedActionSet: ...


class ControlledActionExecutor(Protocol):
    async def execute(self, proposal: ActionProposal) -> ControlledToolReceipt:
        """Invoke exactly the proposal's typed action through trusted adapters."""
        ...


class ControlledActionTool(Protocol):
    async def invoke(self, proposal: ActionProposal) -> ControlledToolReceipt:
        """Adapt one existing parser/extractor/reviewer/task operation."""
        ...


class DecisionTrace(Protocol):
    async def append_failure(self, event: SelectionFailureEvent) -> None: ...

    async def read_failures(self, run_id: UUID) -> tuple[SelectionFailureEvent, ...]: ...

    async def append(self, event: DecisionEvent) -> None:
        """Append one event as it occurs; durable implementations require Issue #9 CAS."""
        ...

    async def read(self, run_id: UUID) -> tuple[DecisionEvent, ...]:
        """Read detached events for one run in local append order."""
        ...
