"""Private paths, schema compatibility and bounded SQLite lock behavior."""

import asyncio
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest

from appraisal_review.adapters.local.review_database import SQLiteReviewDatabase
from appraisal_review.adapters.local.sqlite_job_store import SQLiteJobStore
from appraisal_review.application.job_state import JobPolicy
from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.domain.service_contracts import ServiceErrorCode
from appraisal_review.testing.job_store_contract import NOW, principal, submission


@dataclass
class Record:
    value: int


def test_database_reopens_typed_rows_and_rolls_back(tmp_path: Path) -> None:
    path = tmp_path / "review.sqlite"
    database = SQLiteReviewDatabase(path)
    with database.transaction(write=True) as tx:
        tx.put("test", "one", Record(1), insert=True)
    with pytest.raises(RuntimeError), database.transaction(write=True) as tx:
        tx.put("test", "one", Record(2))
        raise RuntimeError("rollback")
    with SQLiteReviewDatabase(path).transaction() as tx:
        assert tx.get(Record, "test", "one") == Record(1)
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "unsafe", ["symlink", "hardlink", "public_file", "public_parent", "parent_symlink"]
)
def test_unsafe_paths_fail_closed(tmp_path: Path, unsafe: str) -> None:
    parent = tmp_path / "private"
    parent.mkdir(mode=0o700)
    path = parent / "review.sqlite"
    SQLiteReviewDatabase(path)
    before = path.read_bytes()
    if unsafe == "symlink":
        alias = parent / "alias.sqlite"
        alias.symlink_to(path)
        target = alias
    elif unsafe == "hardlink":
        target = parent / "hardlink.sqlite"
        os.link(path, target)
    elif unsafe == "public_file":
        path.chmod(0o644)
        target = path
    elif unsafe == "public_parent":
        parent.chmod(0o755)
        target = path
    else:
        alias = tmp_path / "alias"
        alias.symlink_to(parent, target_is_directory=True)
        target = alias / "review.sqlite"
    with pytest.raises(ServiceFault) as error:
        SQLiteReviewDatabase(target)
    assert error.value.problem.code == ServiceErrorCode.CAPABILITY
    assert path.read_bytes() == before


@pytest.mark.parametrize("version", [0, 2])
def test_existing_incompatible_database_is_not_migrated(tmp_path, version) -> None:
    path = tmp_path / "review.sqlite"
    SQLiteReviewDatabase(path)
    with sqlite3.connect(path) as connection:
        connection.execute(f"PRAGMA user_version={version}")
    before = path.read_bytes()
    with pytest.raises(ServiceFault):
        SQLiteReviewDatabase(path)
    assert path.read_bytes() == before


def test_corrupt_typed_row_fails_with_sanitized_error(tmp_path) -> None:
    database = SQLiteReviewDatabase(tmp_path / "review.sqlite")
    with database.transaction(write=True) as tx:
        tx.connection.execute(
            "INSERT INTO review_records VALUES (?,?,?,?)",
            ("test", "one", "", b'{"value":"private malformed value"}'),
        )
    with pytest.raises(ServiceFault) as error, database.transaction() as tx:
        tx.get(Record, "test", "one")
    assert error.value.problem.code == ServiceErrorCode.EXECUTION
    assert "private malformed" not in str(error.value)


def test_separate_connections_report_contention_without_partial_write(tmp_path) -> None:
    async def scenario() -> None:
        path = tmp_path / "review.sqlite"
        database = SQLiteReviewDatabase(path, timeout=0.01)
        first = SQLiteJobStore(database)
        second = SQLiteJobStore(SQLiteReviewDatabase(path, timeout=0.01))
        job, _ = await first.create_job(
            principal(), submission(), job_id=uuid4(), run_id=uuid4(), now=NOW
        )
        with database.transaction(write=True), pytest.raises(ServiceFault) as error:
            await second.cancel(job_id=job.job_id, now=NOW + 1)
        assert error.value.problem.code == ServiceErrorCode.CAPABILITY
        assert await first.read_job(job_id=job.job_id) == job
        await second.cancel(job_id=job.job_id, now=NOW + 2)
        assert (await first.read_job(job_id=job.job_id)).status == "cancelled"

    asyncio.run(scenario())


def test_policy_survives_reopen_and_conflicting_policy_is_refused(tmp_path) -> None:
    path = tmp_path / "review.sqlite"
    policy = JobPolicy(max_attempts=2)
    SQLiteJobStore(SQLiteReviewDatabase(path), policy=policy)
    assert SQLiteJobStore(SQLiteReviewDatabase(path)).policy == policy
    with pytest.raises(ServiceFault):
        SQLiteJobStore(SQLiteReviewDatabase(path), policy=JobPolicy(max_attempts=3))


@pytest.mark.parametrize("timeout", [0, -1, 6, float("nan"), float("inf")])
def test_lock_wait_is_bounded(tmp_path, timeout) -> None:
    with pytest.raises(ValueError):
        SQLiteReviewDatabase(tmp_path / "review.sqlite", timeout=timeout)
