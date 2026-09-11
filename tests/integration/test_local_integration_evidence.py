"""Hermetic collector regressions using real generated SQLite/PDF publications.

These checks exercise observations, not browser or live acceptance attestation.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import uuid4

import pytest

from appraisal_review.adapters.local.synthetic_workbench import prepare_workbench


@pytest.fixture(scope="module")
def collector() -> Iterator[ModuleType]:
    with pytest.MonkeyPatch.context() as patch:
        patch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
        yield importlib.import_module("collect_local_integration_evidence")


@pytest.fixture(scope="module")
def generated_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("collector-fixture").resolve()

    async def prepare() -> None:
        context = await prepare_workbench(root / "core")
        await context.settle()

    asyncio.run(prepare())
    return root


@dataclass
class Observed:
    collector: ModuleType
    root: Path
    db: sqlite3.Connection
    documents: dict[Any, Any]
    snapshots: dict[str, Any]
    jobs: list[dict[str, Any]]
    artifacts: dict[str, Any]

    def collect_jobs(self) -> Any:
        return self.collector.collect_jobs(self.db, self.documents, self.snapshots, self.root)


@pytest.fixture
def observed(collector: ModuleType, generated_root: Path) -> Iterator[Observed]:
    """Each test mutates only a fresh in-memory copy of the shared generated DB."""
    path = generated_root / "core/state/review.sqlite"
    source_paths = sorted((generated_root / "core").glob("*/documents.sqlite"))
    paths = [path, *source_paths]
    before = [collector.digest(p.read_bytes()) for p in paths]
    try:
        with ExitStack() as stack:
            db = collector.snapshot(path, generated_root, stack)
            sources = [collector.snapshot(p, generated_root, stack) for p in source_paths]
            documents, snapshots = collector.source_observations(sources)
            jobs, artifacts = collector.collect_jobs(db, documents, snapshots, generated_root)
            yield Observed(collector, generated_root, db, documents, snapshots, jobs, artifacts)
    finally:
        assert [collector.digest(p.read_bytes()) for p in paths] == before


def test_actual_binding_and_no_captures(observed: Observed) -> None:
    jobs = observed.jobs
    assert len(jobs) == 7 and len(observed.documents) == 14 and len(observed.artifacts) == 2
    assert sum(j["c2_snapshot_observed"] for j in jobs) == 7
    assert sum(j["job_status"] == "failed" for j in jobs) == 0
    assert all(j["c2_snapshot_observed"] for j in jobs if j["artifacts"])
    assert observed.collector.collect_captures(
        None, observed.root, jobs, observed.artifacts, {}, ""
    ) == {"status": "not_observed", "records": []}


def test_duplicate_json_rejected(collector: ModuleType) -> None:
    with pytest.raises(collector.EvidenceError):
        collector.decode('{"x":1,"x":2}')


def test_readonly_db(observed: Observed) -> None:
    with pytest.raises(sqlite3.OperationalError):
        observed.db.execute("DELETE FROM review_state")


def test_corrupt_object_rejected(observed: Observed) -> None:
    observed.db.execute("PRAGMA query_only=OFF")
    observed.db.execute("UPDATE publication_objects SET digest=?", ("0" * 64,))
    with pytest.raises(observed.collector.EvidenceError, match="artifact_content_binding_mismatch"):
        observed.collect_jobs()


def test_corrupt_manifest_rejected(observed: Observed) -> None:
    observed.db.execute("PRAGMA query_only=OFF")
    observed.db.execute("UPDATE publication_manifests SET digest=?", ("0" * 64,))
    with pytest.raises(observed.collector.EvidenceError, match="manifest_run_binding_mismatch"):
        observed.collect_jobs()


def test_source_snapshot_mismatch(observed: Observed) -> None:
    other = next(
        p for p in observed.snapshots.values() if str(p.run_id) != observed.jobs[0]["run_id"]
    )
    observed.snapshots = dict(observed.snapshots)
    observed.snapshots[observed.jobs[0]["run_id"]] = other
    with pytest.raises(observed.collector.EvidenceError, match="run_source_snapshot_mismatch"):
        observed.collect_jobs()


@dataclass
class Capture:
    observed: Observed
    directory: Path
    entry: dict[str, Any]

    def collect(self) -> Any:
        path = self.directory / "index.json"
        path.write_text(
            json.dumps({"schema_version": "local-integration-captures-v1", "records": [self.entry]})
        )
        return self.observed.collector.collect_captures(
            path,
            self.observed.root,
            self.observed.jobs,
            self.observed.artifacts,
            {"head": "0" * 40},
            "0" * 64,
        )


@pytest.fixture
def captured(observed: Observed) -> Capture:
    directory = observed.root / str(uuid4())
    directory.mkdir()
    owner, artifact, _ = next(iter(observed.artifacts.values()))
    raw = observed.db.execute(
        "SELECT body FROM publication_objects WHERE object_key=?", (artifact.key,)
    ).fetchone()[0]
    (directory / "body.pdf").write_bytes(raw)
    entry = {k: owner[k] for k in ("job_id", "case_id", "run_id", "revision_id", "attempt_id")}
    # This is an explicitly unattested test file, not an invented HTTP receipt.
    entry.update(
        kind="publication_pdf",
        body_file="body.pdf",
        body_sha256=observed.collector.digest(raw),
        observed_at=int(time.time()),
        artifact_id=str(artifact.artifact_id),
    )
    return Capture(observed, directory, entry)


def test_capture_bytes_match_but_not_attested(captured: Capture) -> None:
    result = captured.collect()
    assert result["records"][0]["capture_producer_authenticated"] is False
    assert result["status"] == "files_observed_producer_unattested"


def test_foreign_job_capture_rejected(captured: Capture) -> None:
    other = next(j for j in captured.observed.jobs if j["job_id"] != captured.entry["job_id"])
    captured.entry.update(
        {k: other[k] for k in ("job_id", "case_id", "run_id", "revision_id", "attempt_id")}
    )
    with pytest.raises(
        captured.observed.collector.EvidenceError, match="capture_artifact_job_mismatch"
    ):
        captured.collect()


def test_capture_digest_rejected(captured: Capture) -> None:
    captured.entry["body_sha256"] = "0" * 64
    with pytest.raises(
        captured.observed.collector.EvidenceError, match="capture_file_digest_mismatch"
    ):
        captured.collect()


def test_paths_confined(collector: ModuleType, generated_root: Path) -> None:
    with pytest.raises(collector.EvidenceError, match="path_outside_repository_or_symlink"):
        collector.confined(generated_root.parent / "outside", generated_root)
    source = generated_root / "plain.txt"
    source.write_text("synthetic source")
    path = generated_root / "link"
    path.symlink_to(source)
    with pytest.raises(collector.EvidenceError, match="path_outside_repository_or_symlink"):
        collector.read_file(path, generated_root)
