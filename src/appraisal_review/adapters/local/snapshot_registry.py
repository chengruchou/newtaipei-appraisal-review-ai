"""Registered calculation snapshots, one JSON file per (case, revision).

A snapshot enters the registry through an operator action - a preparation script or a
trusted composition call - never through an HTTP body. The export service reads it back
by the exact (case_id, revision_id) pair and verifies the digest the client pinned, so
a snapshot swapped on disk after a page loaded still refuses to export.

Files live under the private workbench root (0700), one directory per case, named by
revision. Re-registering the same revision must carry identical content; silently
replacing a revision's numbers is exactly the mistake this store exists to prevent.
"""

from __future__ import annotations

import os
from pathlib import Path

from appraisal_review.domain.calculation_snapshot import CalculationSnapshot


class SnapshotRegistryError(Exception):
    """Carries a stable code, never file paths or content."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _segment(value: str) -> str:
    if not value or value != value.strip() or "/" in value or "\\" in value or ".." in value:
        raise SnapshotRegistryError("invalid_identifier")
    return value


class RegisteredSnapshots:
    """Directory-backed snapshot provider for the local composition."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)

    def _path(self, case_id: str, revision_id: str) -> Path:
        return self.root / _segment(case_id) / f"{_segment(revision_id)}.json"

    def register(self, snapshot: CalculationSnapshot) -> str:
        """Store one snapshot; identical re-registration is a no-op, drift is refused."""
        snapshot = CalculationSnapshot.model_validate_json(snapshot.model_dump_json())
        reference = snapshot.revision
        path = self._path(reference.case_id, reference.revision_id)
        rendered = snapshot.model_dump_json(indent=2) + "\n"
        if path.exists():
            existing = CalculationSnapshot.model_validate_json(path.read_text())
            if existing.digest() != snapshot.digest():
                raise SnapshotRegistryError("snapshot_conflict")
            return snapshot.digest()
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(rendered)
        return snapshot.digest()

    def read(self, case_id: str, revision_id: str) -> CalculationSnapshot | None:
        try:
            path = self._path(case_id, revision_id)
        except SnapshotRegistryError:
            return None
        if not path.is_file():
            return None
        return CalculationSnapshot.model_validate_json(path.read_text())
