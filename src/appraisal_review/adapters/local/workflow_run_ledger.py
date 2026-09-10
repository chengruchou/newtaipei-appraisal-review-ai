"""Non-durable, thread-safe run authority for one configured local composition."""

from dataclasses import dataclass
from threading import Lock
from uuid import UUID, uuid4

from appraisal_review.domain.service_contracts import (
    BoundedWorkflowResult,
    Budget,
    RunReference,
    WorkflowTermination,
)
from appraisal_review.ports.workflow_run_ledger import (
    WorkflowRunReservation,
    WorkflowRunUnavailable,
)


def _budget(value: Budget) -> Budget:
    return Budget.model_validate_json(value.model_dump_json())


def _decreased(before: Budget, after: Budget) -> None:
    for field in ("steps_remaining", "model_calls_remaining", "retries_remaining"):
        if getattr(after, field) > getattr(before, field):
            raise WorkflowRunUnavailable("Run budget cannot increase")
    if (before.time_remaining_ms is None) != (after.time_remaining_ms is None) or (
        before.time_remaining_ms is not None
        and after.time_remaining_ms is not None
        and after.time_remaining_ms > before.time_remaining_ms
    ):
        raise WorkflowRunUnavailable("Run time allowance cannot increase")


@dataclass
class _RunRecord:
    run: RunReference
    budget: Budget
    owner: UUID | None = None
    quarantined: bool = False
    result: BoundedWorkflowResult | None = None


class NonDurableInMemoryWorkflowRunLedger:
    """Atomic within this shared instance, across threads and event loops only.

    No await occurs while the mutex is held. Reconstructing this object loses state;
    production restart protection requires an injected durable ledger implementation.
    The active owner reserves all remaining budget, including allowance for retries.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._runs: dict[UUID, _RunRecord] = {}

    async def acquire(
        self, run: RunReference, initial_budget: Budget
    ) -> WorkflowRunReservation | BoundedWorkflowResult:
        run = RunReference.model_validate_json(run.model_dump_json())
        initial_budget = _budget(initial_budget)
        with self._lock:
            record = self._runs.get(run.run_id)
            if record is None:
                record = _RunRecord(run, initial_budget)
                self._runs[run.run_id] = record
            if record.run != run:
                raise WorkflowRunUnavailable("Run identity conflict")
            if record.result is not None:
                return BoundedWorkflowResult.model_validate_json(record.result.model_dump_json())
            if record.owner is not None or record.quarantined:
                raise WorkflowRunUnavailable("Run already reserved or quarantined")
            record.owner = uuid4()
            return WorkflowRunReservation(run, record.owner)

    def _owned(self, owner: WorkflowRunReservation) -> _RunRecord:
        record = self._runs.get(owner.run.run_id)
        if (
            record is None
            or record.run != owner.run
            or record.owner != owner.token
            or record.result is not None
        ):
            raise WorkflowRunUnavailable("Stale run owner")
        return record

    async def assert_active(self, owner: WorkflowRunReservation) -> None:
        with self._lock:
            if self._owned(owner).quarantined:
                raise WorkflowRunUnavailable("Run is quarantined")

    async def remaining(self, owner: WorkflowRunReservation) -> Budget:
        with self._lock:
            return _budget(self._owned(owner).budget)

    async def checkpoint(self, owner: WorkflowRunReservation, budget: Budget) -> None:
        budget = _budget(budget)
        with self._lock:
            record = self._owned(owner)
            _decreased(record.budget, budget)
            record.budget = budget

    async def quarantine(self, owner: WorkflowRunReservation) -> None:
        with self._lock:
            self._owned(owner).quarantined = True

    async def release(self, owner: WorkflowRunReservation) -> None:
        with self._lock:
            self._owned(owner).owner = None

    async def complete(self, owner: WorkflowRunReservation, result: BoundedWorkflowResult) -> None:
        result = BoundedWorkflowResult.model_validate_json(result.model_dump_json())
        with self._lock:
            record = self._owned(owner)
            if result.run != record.run or (
                record.quarantined
                and result.termination
                in {WorkflowTermination.VERIFIED, WorkflowTermination.WAITING_FOR_HUMAN}
            ):
                raise WorkflowRunUnavailable("Result cannot complete this reserved run")
            _decreased(record.budget, result.final_budget)
            record.budget = _budget(result.final_budget)
            record.result = result
            record.owner = None

    async def abandon(self, owner: WorkflowRunReservation) -> None:
        with self._lock:
            record = self._owned(owner)
            record.quarantined = True
            record.owner = None
