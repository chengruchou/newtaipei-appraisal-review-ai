"""Host-local process coordination; never a coordinator across AWS machines."""

import sqlite3
from contextlib import closing
from pathlib import Path

from appraisal_review.ports.model_dispatch import DispatchDenied


class SqliteModelDispatchStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path).resolve()
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS model_dispatch (scope TEXT PRIMARY KEY, owner TEXT)"
            )

    def _connect(self) -> sqlite3.Connection:
        # Bounded contention is polled by the dispatcher with deadline checks.
        return sqlite3.connect(self.path, timeout=0.05)

    def try_acquire(self, scope: str, owner: str) -> bool:
        with closing(self._connect()) as connection, connection:
            connection.execute("INSERT OR IGNORE INTO model_dispatch VALUES (?, NULL)", (scope,))
            changed = connection.execute(
                "UPDATE model_dispatch SET owner = ? WHERE scope = ? AND owner IS NULL",
                (owner, scope),
            )
            return changed.rowcount == 1

    def release(self, scope: str, owner: str) -> None:
        with closing(self._connect()) as connection, connection:
            changed = connection.execute(
                "UPDATE model_dispatch SET owner = NULL WHERE scope = ? AND owner = ?",
                (scope, owner),
            )
            if changed.rowcount != 1:
                raise DispatchDenied("dispatch_owner_fenced")
