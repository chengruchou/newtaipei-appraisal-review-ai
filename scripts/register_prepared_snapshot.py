"""Bind a prepared calculation snapshot to a workbench's completed job revision.

A snapshot template carries real computed values but is authored before any workbench
exists, so its revision reference cannot match the case revision a fresh workbench
mints. This operator step reads the private fixture manifest, resolves the completed
job's current revision, rebinds the snapshot to it, and registers the result in the
workbench's snapshot directory. Registration refuses to replace a different snapshot
already registered for the same revision.

Values, units, states, origins, traces and gaps are copied unchanged; only the
revision reference is rebound. This never invents or upgrades a single number.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import UUID

from appraisal_review.adapters.local.snapshot_registry import RegisteredSnapshots
from appraisal_review.adapters.local.sqlite_review_store import SQLiteReviewStore
from appraisal_review.domain.calculation_snapshot import CalculationSnapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbench", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    arguments = parser.parse_args()

    manifest_path = arguments.workbench / "fixture.json"
    if not manifest_path.is_file():
        print("No fixture manifest; prepare the workbench first.", file=sys.stderr)
        return 2
    manifest = json.loads(manifest_path.read_text())
    job_id = UUID(manifest["completed_job_id"])

    store = SQLiteReviewStore(arguments.workbench / "state" / "review.sqlite")
    record = asyncio.run(store.read_job(job_id=job_id))
    if record is None or record.current_run is None:
        print("The completed job has no current run to bind against.", file=sys.stderr)
        return 2
    revision = record.current_run.revision

    raw = json.loads(arguments.snapshot.read_text())
    raw["revision"] = json.loads(revision.model_dump_json())
    snapshot = CalculationSnapshot.model_validate(raw)
    digest = RegisteredSnapshots(arguments.workbench / "snapshots").register(snapshot)
    print(
        f"Registered snapshot {digest[:16]} for case {revision.case_id[:8]} "
        f"revision {revision.revision_id[:8]}: "
        f"{len(snapshot.entries)} entries, {len(snapshot.gaps)} gaps."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
