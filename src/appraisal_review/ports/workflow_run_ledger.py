"""Atomic run reservations shared by bounded runners and direct coordinators.

A durable implementation commits acquire before any selector/tool call. An active
reservation excludes every other owner and reserves the entire remaining allowance;
checkpoints never replenish it. Restart must retain active reservations and quarantine
unknown external results, never silently reacquire them. Tokens fence stale writes.
All operations are atomic, including complete(result + budget + reservation state).
"""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from appraisal_review.domain.service_contracts import BoundedWorkflowResult, Budget, RunReference


class WorkflowRunUnavailable(Exception):
    """The exact run is active, quarantined, conflicting, or the owner is stale."""


class WorkflowExternalResultUnknown(Exception):
    """An executor cannot determine whether its external operation took effect."""


@dataclass(frozen=True)
class WorkflowRunReservation:
    """Trusted internal handle; never deserialize an owner token from a client."""

    run: RunReference
    token: UUID


class WorkflowRunLedger(Protocol):
    async def acquire(
        self, run: RunReference, initial_budget: Budget
    ) -> WorkflowRunReservation | BoundedWorkflowResult:
        """CAS reserve an idle run, replay a terminal result, or reject admission.

        Bind the full RunReference to a unique run ID. Initial budget applies only
        on insert. Keep terminal failure history in the immutable cached result.
        """
        ...

    async def assert_active(self, owner: WorkflowRunReservation) -> None:
        """Fence external call admission; reject quarantined or stale owners."""
        ...

    async def remaining(self, owner: WorkflowRunReservation) -> Budget:
        """Read detached authoritative budget, checking exact run and fencing token."""
        ...

    async def checkpoint(self, owner: WorkflowRunReservation, budget: Budget) -> None:
        """Persist monotonically decreasing budget; retain the active reservation."""
        ...

    async def quarantine(self, owner: WorkflowRunReservation) -> None:
        """Prohibit further calls; retain reserved allowance for unknown side effects.

        The current owner may still checkpoint and complete a failure report, but
        cannot release for reuse or complete a successful result.
        """
        ...

    async def release(self, owner: WorkflowRunReservation) -> None:
        """Release a settled direct decision; quarantine remains inadmissible."""
        ...

    async def complete(self, owner: WorkflowRunReservation, result: BoundedWorkflowResult) -> None:
        """Atomically persist exact terminal result/budget and close admission.

        Keep unresolved reservations quarantined even when storing a failure result.
        A result cache must not turn a quarantined attempt into a verified result.
        """
        ...

    async def abandon(self, owner: WorkflowRunReservation) -> None:
        """Fence the owner and quarantine after cancellation or unknown failure.

        Never refund unused-looking allowance: an external call may still be running.
        No automatic timeout/restart takeover is allowed by this contract. Durable
        recovery must reconcile externally before authorizing a distinct new run.
        """
        ...
