"""SQLite run reservations, independent of job and human-task transactions."""

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from uuid import uuid4

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


class SqliteWorkflowRunLedger:
    """Durable exact-run authority with non-expiring, fenced owner reservations.

    Use a trusted configured file path. Each method commits one SQLite transaction
    before returning; no external work runs inside a transaction. A crashed owner's
    active reservation remains unavailable. Recovery never treats lease age as proof
    that an external operation did not occur.
    """

    def __init__(self, path: str | Path) -> None:
        if str(path) == ":memory:":
            raise ValueError("The workflow ledger requires a persistent database file")
        self.path = Path(path).resolve()
        with closing(self._connect()) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS workflow_run_ledger_v1 (
                    run_id TEXT PRIMARY KEY,
                    run_json TEXT NOT NULL,
                    budget_json TEXT NOT NULL,
                    owner_token TEXT,
                    reservation_token TEXT,
                    quarantined INTEGER NOT NULL DEFAULT 0 CHECK (quarantined IN (0, 1)),
                    result_json TEXT
                )
            """)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    @staticmethod
    def _same_run(row: sqlite3.Row, run: RunReference) -> None:
        if RunReference.model_validate_json(row["run_json"]) != run:
            raise WorkflowRunUnavailable("Run identity conflict")

    def _owned(self, connection: sqlite3.Connection, owner: WorkflowRunReservation) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM workflow_run_ledger_v1 WHERE run_id = ?", (str(owner.run.run_id),)
        ).fetchone()
        if not isinstance(row, sqlite3.Row):
            raise WorkflowRunUnavailable("Unknown run owner")
        self._same_run(row, owner.run)
        if row["owner_token"] != str(owner.token) or row["result_json"] is not None:
            raise WorkflowRunUnavailable("Stale run owner")
        return row

    @staticmethod
    def _decreased(row: sqlite3.Row, budget: Budget) -> str:
        # Revalidate even instances produced by model_copy/model_construct.
        budget = Budget.model_validate_json(budget.model_dump_json())
        before = Budget.model_validate_json(row["budget_json"])
        for field in ("steps_remaining", "model_calls_remaining", "retries_remaining"):
            if getattr(budget, field) > getattr(before, field):
                raise WorkflowRunUnavailable("Run budget cannot increase")
        if (before.time_remaining_ms is None) != (budget.time_remaining_ms is None) or (
            before.time_remaining_ms is not None
            and budget.time_remaining_ms is not None
            and budget.time_remaining_ms > before.time_remaining_ms
        ):
            raise WorkflowRunUnavailable("Run time allowance cannot increase")
        return budget.model_dump_json()

    async def acquire(
        self, run: RunReference, initial_budget: Budget
    ) -> WorkflowRunReservation | BoundedWorkflowResult:
        run = RunReference.model_validate_json(run.model_dump_json())
        initial_budget = Budget.model_validate_json(initial_budget.model_dump_json())
        with self._transaction() as connection:
            connection.execute(
                """INSERT INTO workflow_run_ledger_v1 (run_id, run_json, budget_json)
                   VALUES (?, ?, ?) ON CONFLICT(run_id) DO NOTHING""",
                (str(run.run_id), run.model_dump_json(), initial_budget.model_dump_json()),
            )
            row = connection.execute(
                "SELECT * FROM workflow_run_ledger_v1 WHERE run_id = ?", (str(run.run_id),)
            ).fetchone()
            self._same_run(row, run)
            if row["result_json"] is not None:
                result = BoundedWorkflowResult.model_validate_json(row["result_json"])
                if result.run != run or (
                    row["quarantined"]
                    and result.termination
                    in {WorkflowTermination.VERIFIED, WorkflowTermination.WAITING_FOR_HUMAN}
                ):
                    raise WorkflowRunUnavailable("Stored result conflicts with run authority")
                return result
            if row["owner_token"] is not None or row["quarantined"]:
                raise WorkflowRunUnavailable("Run already reserved or quarantined")
            token = uuid4()
            connection.execute(
                """UPDATE workflow_run_ledger_v1 SET owner_token = ?, reservation_token = ?
                   WHERE run_id = ?""",
                (str(token), str(token), str(run.run_id)),
            )
            return WorkflowRunReservation(run, token)

    async def assert_active(self, owner: WorkflowRunReservation) -> None:
        with self._transaction() as connection:
            if self._owned(connection, owner)["quarantined"]:
                raise WorkflowRunUnavailable("Run is quarantined")

    async def remaining(self, owner: WorkflowRunReservation) -> Budget:
        with self._transaction() as connection:
            return Budget.model_validate_json(self._owned(connection, owner)["budget_json"])

    async def checkpoint(self, owner: WorkflowRunReservation, budget: Budget) -> None:
        with self._transaction() as connection:
            serialized = self._decreased(self._owned(connection, owner), budget)
            connection.execute(
                "UPDATE workflow_run_ledger_v1 SET budget_json = ? WHERE run_id = ?",
                (serialized, str(owner.run.run_id)),
            )

    async def quarantine(self, owner: WorkflowRunReservation) -> None:
        with self._transaction() as connection:
            self._owned(connection, owner)
            connection.execute(
                "UPDATE workflow_run_ledger_v1 SET quarantined = 1 WHERE run_id = ?",
                (str(owner.run.run_id),),
            )

    async def release(self, owner: WorkflowRunReservation) -> None:
        with self._transaction() as connection:
            self._owned(connection, owner)
            connection.execute(
                "UPDATE workflow_run_ledger_v1 SET owner_token = NULL WHERE run_id = ?",
                (str(owner.run.run_id),),
            )

    async def complete(self, owner: WorkflowRunReservation, result: BoundedWorkflowResult) -> None:
        result = BoundedWorkflowResult.model_validate_json(result.model_dump_json())
        with self._transaction() as connection:
            row = self._owned(connection, owner)
            if result.run != owner.run or (
                row["quarantined"]
                and result.termination
                in {WorkflowTermination.VERIFIED, WorkflowTermination.WAITING_FOR_HUMAN}
            ):
                raise WorkflowRunUnavailable("Result cannot complete this reserved run")
            serialized = self._decreased(row, result.final_budget)
            connection.execute(
                """UPDATE workflow_run_ledger_v1
                   SET budget_json = ?, result_json = ?, owner_token = NULL WHERE run_id = ?""",
                (serialized, result.model_dump_json(), str(owner.run.run_id)),
            )

    async def abandon(self, owner: WorkflowRunReservation) -> None:
        with self._transaction() as connection:
            self._owned(connection, owner)
            connection.execute(
                """UPDATE workflow_run_ledger_v1
                   SET quarantined = 1, owner_token = NULL WHERE run_id = ?""",
                (str(owner.run.run_id),),
            )
