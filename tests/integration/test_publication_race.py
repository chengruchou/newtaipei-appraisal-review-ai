"""Cross-process race: a formal export's final commit must re-read the authority.

The worker runs in a REAL second process against the same SQLite file. A file
barrier orders the schedule precisely: the worker performs its last pre-save
revision read (still V1), then this test process commits the V2 correction to
the authoritative review state, then the worker enters save(). The barrier sits
BETWEEN the worker's last read and save() - never inside the BEGIN IMMEDIATE
transaction - so nothing deadlocks and only the in-transaction re-check can
catch the concurrent revision change.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from appraisal_review.adapters.local.approval_store import SQLiteApprovalStore
from appraisal_review.adapters.local.export_store import SQLiteExportStore
from appraisal_review.adapters.local.sqlite_review_store import SQLiteReviewStore
from appraisal_review.application.exports import ExportAssets, ExportService
from appraisal_review.application.report_approvals import ReportApprovalService
from appraisal_review.application.report_readiness import ReadinessPolicy
from appraisal_review.application.review_jobs import ReviewJobService
from appraisal_review.domain.official_export import ExportRequest
from appraisal_review.domain.report_approval import ApprovalDecisionCommand, SubmitReportApproval
from appraisal_review.domain.service_contracts import (
    Permission,
    RunReference,
    ServiceErrorCode,
)
from appraisal_review.testing.job_store_contract import principal, submission
from tests.unit.test_report_approvals import (
    REQUIRED,
    TABLES,
    FakeFilled,
    Snapshots,
    entry,
    mapping,
    snapshot_for,
)

SRC = Path(__file__).resolve().parents[2] / "src"

WORKER = '''
"""Export worker process: last revision read happens BEFORE the barrier."""
import asyncio, json, sys, time
from pathlib import Path
from uuid import UUID

from appraisal_review.adapters.local.approval_store import SQLiteApprovalStore
from appraisal_review.adapters.local.export_store import SQLiteExportStore
from appraisal_review.adapters.local.sqlite_review_store import SQLiteReviewStore
from appraisal_review.application.exports import ExportAssets, ExportService
from appraisal_review.application.review_jobs import ReviewJobService
from appraisal_review.domain.calculation_snapshot import CalculationSnapshot
from appraisal_review.domain.official_table_mapping import TableMapping

base = Path(sys.argv[1])
job_id = UUID(sys.argv[2])
sys.path.insert(0, str(Path(sys.argv[3])))
from race_shared import build_assets, make_filler  # noqa: E402

store = SQLiteReviewStore(base / "p" / "review.sqlite3")
snapshot = CalculationSnapshot.model_validate_json((base / "snapshot.json").read_text())


class SnapshotReader:
    def read(self, case_id, revision_id):
        ref = snapshot.revision
        if (case_id, revision_id) != (ref.case_id, ref.revision_id):
            return None
        return CalculationSnapshot.model_validate_json(snapshot.model_dump_json())


calls = {"n": 0}


def current_revision(job):
    record = asyncio.run(store.read_job(job_id=job))
    value = None if record is None or record.current_run is None else record.current_run.revision
    calls["n"] += 1
    if calls["n"] == 2:
        # This was the worker's LAST pre-save revision read; hold here while the
        # other process commits V2, then proceed straight into save().
        (base / "worker_last_read").write_text(value.revision_id if value else "none")
        deadline = time.time() + 30
        while not (base / "v2_committed").exists():
            if time.time() > deadline:
                raise SystemExit(3)
            time.sleep(0.02)
    return value


service = ExportService(
    jobs=ReviewJobService(store, store.results, policy=store.policy),
    store=SQLiteExportStore(store),
    assets=build_assets(base),
    filler=make_filler(),
    converter=None,
    snapshots=SnapshotReader(),
    approvals=SQLiteApprovalStore(store),
    current_revision=current_revision,
)
asyncio.run(service.run_pending())
'''

SHARED = """
import hashlib, json
from pathlib import Path
from appraisal_review.application.exports import ExportAssets
from appraisal_review.domain.official_table_mapping import TableMapping

TABLES = ("table_3", "table_4", "table_5")


def _mapping(table, digest):
    return TableMapping.model_validate(
        {
            "table": table,
            "template_name": f"{table}.xlsx",
            "template_digest": digest,
            "sheet_name": "visible",
            "bindings": [
                {
                    "cell": "C5",
                    "source": f"{table}.case.example",
                    "label": "example",
                    "value_kind": "decimal",
                    "precision": 2,
                }
            ],
        }
    )


def build_assets(base):
    templates, mappings = {}, {}
    for table in TABLES:
        path = base / f"{table}.xlsx"
        templates[table] = path
        mappings[table] = _mapping(table, hashlib.sha256(path.read_bytes()).hexdigest())
    return ExportAssets.load(
        bundle_id="official-test", version="v1", templates=templates, mappings=mappings
    )


class Filled:
    def __init__(self, content, sheet_name, written_cells):
        self.content, self.sheet_name, self.written_cells = content, sheet_name, written_cells


def make_filler():
    def filler(template, table_mapping, snap):
        body = json.dumps(
            {
                "table": table_mapping.table,
                "values": {
                    key: str(item.value)
                    for key, item in sorted(snap.entries.items())
                    if key.startswith(table_mapping.table)
                },
            },
            sort_keys=True,
        ).encode()
        return Filled(b"PK\\x03\\x04" + body, table_mapping.sheet_name, ("C5",))

    return filler
"""


def _wait_for(path: Path, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while not path.exists():
        if time.time() > deadline:
            raise AssertionError(f"barrier {path.name} never arrived")
        time.sleep(0.02)


def _commit_v2(db_path: Path, job_id: Any, revision_id: str) -> None:
    """The concurrent correction: a durable BEGIN IMMEDIATE commit on the same file."""
    connection = sqlite3.connect(db_path, timeout=15.0, isolation_level=None)
    try:
        connection.execute("BEGIN IMMEDIATE")
        payload = connection.execute(
            "SELECT payload FROM review_state WHERE singleton=1"
        ).fetchone()[0]
        state = json.loads(payload)
        job = state["jobs"][str(job_id)]
        for run in state["runs"]:
            if run["job_id"] == str(job_id) and run["value"]["run_id"] == job["current_run_id"]:
                run["value"]["revision"]["revision_id"] = revision_id
        connection.execute(
            "UPDATE review_state SET payload=? WHERE singleton=1", (json.dumps(state),)
        )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


@dataclass
class Scene:
    base: Path
    store: SQLiteReviewStore
    person: Any
    job_id: Any
    run: RunReference
    export_store: SQLiteExportStore
    operation: Any


@pytest.fixture
def scene(tmp_path: Path) -> Scene:
    base = tmp_path
    store = SQLiteReviewStore(base / "p" / "review.sqlite3")
    person = replace(principal(), permissions=frozenset({Permission.REVIEW, Permission.PUBLISH}))
    job_id, run_id = uuid4(), uuid4()
    asyncio.run(store.create_job(person, submission(), job_id=job_id, run_id=run_id, now=1000))
    run = asyncio.run(store.read_job(job_id=job_id)).current_run
    templates, mappings = {}, {}
    for table in TABLES:
        path = base / f"{table}.xlsx"
        path.write_bytes(b"PK\x03\x04-template-" + table.encode())
        templates[table] = path
        mappings[table] = mapping(table, hashlib.sha256(path.read_bytes()).hexdigest())
    assets = ExportAssets.load(
        bundle_id="official-test", version="v1", templates=templates, mappings=mappings
    )
    snapshot = snapshot_for(run, {key: entry() for key in REQUIRED})
    (base / "snapshot.json").write_text(snapshot.model_dump_json())
    policy_path = base / "policy.json"
    policy_path.write_text(
        json.dumps(
            {
                "policy_version": "formal-readiness-test-v1",
                "required": list(REQUIRED),
                "justified_absence_states": ["not_applicable", "confirmed_zero"],
            }
        )
    )

    def filler(template: bytes, table_mapping: Any, snap: Any) -> FakeFilled:
        body = json.dumps(
            {
                "table": table_mapping.table,
                "values": {
                    key: str(item.value)
                    for key, item in sorted(snap.entries.items())
                    if key.startswith(table_mapping.table)
                },
            },
            sort_keys=True,
        ).encode()
        return FakeFilled(b"PK\x03\x04" + body, table_mapping.sheet_name, ("C5",))

    jobs = ReviewJobService(store, store.results, policy=store.policy)
    approval_store = SQLiteApprovalStore(store)
    approvals = ReportApprovalService(
        jobs=jobs,
        store=approval_store,
        assets=assets,
        filler=filler,
        snapshots=Snapshots(snapshot),
        policy=ReadinessPolicy.load(policy_path),
        clock=lambda: 5000,
    )
    export_store = SQLiteExportStore(store)
    exports = ExportService(
        jobs=jobs,
        store=export_store,
        assets=assets,
        filler=filler,
        converter=None,
        snapshots=Snapshots(snapshot),
        approvals=approval_store,
        current_revision=lambda job: run.revision,
    )
    approval = asyncio.run(
        approvals.submit(
            person,
            job_id,
            SubmitReportApproval(
                idempotency_key="a1",
                run=RunReference(run_id=run.run_id, revision=run.revision),
                calculation_snapshot_digest=snapshot.digest(),
                template_bundle=assets.bundle,
            ),
        )
    )
    asyncio.run(
        approvals.decide(
            person,
            job_id,
            approval.approval_id,
            ApprovalDecisionCommand(idempotency_key="d1", decision="approve"),
        )
    )
    operation = asyncio.run(
        exports.submit(
            person,
            job_id,
            ExportRequest(
                idempotency_key="f1",
                run=RunReference(run_id=run.run_id, revision=run.revision),
                calculation_snapshot_digest=snapshot.digest(),
                template_bundle=assets.bundle,
                requested_mode="formal",
                export_format="xlsx",
            ),
        )
    )
    (base / "race_shared.py").write_text(SHARED)
    (base / "worker.py").write_text(WORKER)
    return Scene(base, store, person, job_id, run, export_store, operation)


def _run_worker(scene: Scene) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [
            sys.executable,
            str(scene.base / "worker.py"),
            str(scene.base),
            str(scene.job_id),
            str(scene.base),
        ],
        env={"PYTHONPATH": str(SRC), "PATH": "/usr/bin:/bin"},
        cwd=str(scene.base),
    )


class TestPublicationRace:
    def test_v2_committed_between_last_read_and_save_fails_closed(self, scene: Scene) -> None:
        worker = _run_worker(scene)
        try:
            _wait_for(scene.base / "worker_last_read")
            # Worker's last read answered V1; commit the V2 correction now,
            # from this (different) process, durably, then release the worker.
            observed = (scene.base / "worker_last_read").read_text()
            assert observed == scene.run.revision.revision_id
            _commit_v2(scene.base / "p" / "review.sqlite3", scene.job_id, str(uuid4()))
            (scene.base / "v2_committed").write_text("yes")
            assert worker.wait(timeout=60) == 0
        finally:
            if worker.poll() is None:
                worker.kill()
        done = scene.export_store.read(scene.job_id, scene.operation.export_id)
        assert done is not None
        assert done.status == "failed", "never succeeded/formal at V1 after a V2 correction"
        assert done.artifacts == ()
        assert done.problem is not None
        assert done.problem.code in {ServiceErrorCode.CONFLICT, ServiceErrorCode.UNAUTHORIZED}

    def test_unchanged_revision_worker_still_publishes(self, scene: Scene) -> None:
        worker = _run_worker(scene)
        try:
            _wait_for(scene.base / "worker_last_read")
            # No correction lands; the same schedule must still publish normally.
            (scene.base / "v2_committed").write_text("go")
            assert worker.wait(timeout=60) == 0
        finally:
            if worker.poll() is None:
                worker.kill()
        done = scene.export_store.read(scene.job_id, scene.operation.export_id)
        assert done is not None and done.status == "succeeded"
