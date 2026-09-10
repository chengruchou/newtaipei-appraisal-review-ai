"""Actual subprocess replay, crash reservations and SQLite admission contention."""

import asyncio
import json
import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

from appraisal_review.adapters.local.sqlite_workflow_run_ledger import SqliteWorkflowRunLedger
from appraisal_review.domain.service_contracts import (
    BoundedWorkflowResult,
    ServiceErrorCode,
    WorkflowTermination,
)
from appraisal_review.ports.workflow_run_ledger import WorkflowRunUnavailable

ROOT = Path(__file__).resolve().parents[2]
B = runpy.run_path(str(ROOT / "tests/unit/test_bounded_workflow.py"))

# Each invocation constructs fresh snapshots, coordinator, runner, selector and DB
# connection in a new process. The call marker records actual injected client entry.
RUN_PROCESS = r"""
import asyncio
import os
import runpy
import sys
from pathlib import Path
from uuid import uuid4

from appraisal_review.ports.workflow_run_ledger import WorkflowExternalResultUnknown
from appraisal_review.adapters.aws.action_selector import BedrockActionSelector, ModelSelectorConfig
from appraisal_review.adapters.local.sqlite_workflow_run_ledger import SqliteWorkflowRunLedger
from appraisal_review.application.bounded_workflow import BoundedWorkflowRunner
from appraisal_review.domain.service_contracts import WorkflowSnapshot, WorkflowState

helpers = runpy.run_path("tests/unit/test_workflow_run_authority.py")
database, snapshot_path, marker, mode = map(Path, sys.argv[1:])
mode = str(mode)
snapshot = WorkflowSnapshot.model_validate_json(snapshot_path.read_text())
states = helpers["B"]["MutableSnapshots"](snapshot)

class CrashAfterAcquire(SqliteWorkflowRunLedger):
    async def acquire(self, run, initial_budget):
        result = await super().acquire(run, initial_budget)
        os._exit(23)

class CrashAfterTrace(SqliteWorkflowRunLedger):
    async def checkpoint(self, owner, budget):
        if budget.steps_remaining < snapshot.budget.steps_remaining:
            with marker.open("a") as output:
                output.write("checkpoint-entered\n")
            os._exit(23)
        await super().checkpoint(owner, budget)

class Client:
    def converse(self, **kwargs):
        with marker.open("a") as output:
            output.write("provider-call\n")
        return {"stopReason": "end_turn", "output": {"message": {
            "content": [{"text": "invalid-injected-response"}]}}}

class Tool:
    async def invoke(self, proposal):
        states.transition(WorkflowState.VERIFIED)
        with marker.open("a") as output:
            output.write("tool-side-effect\n")
        if mode == "crash_after_tool":
            os._exit(23)
        if mode == "unknown_result":
            raise WorkflowExternalResultUnknown("private-result-canary")
        return helpers["B"]["_success"](
            task_id=uuid4() if mode == "invalid_receipt" else None
        )

async def main():
    ledger_type = CrashAfterAcquire if mode == "crash_after_reserve" else SqliteWorkflowRunLedger
    if mode == "crash_after_trace":
        ledger_type = CrashAfterTrace
    ledger = ledger_type(database)
    selector = BedrockActionSelector(
        Client(), ModelSelectorConfig(model_id="synthetic-only", attempts=1)
    ) if mode == "permanent_failure" else None
    coordinator = helpers["coordinator"](states, Tool(), selector, ledger=ledger)
    runner = BoundedWorkflowRunner(coordinator=coordinator, snapshots=states)
    result = await runner.run(snapshot.run)
    print(result.model_dump_json())

asyncio.run(main())
"""


def env():
    return {**os.environ, "PYTHONPATH": str(ROOT / "src")}


def invoke(tmp_path, snapshot_path, mode):
    return subprocess.run(
        [
            sys.executable,
            "-c",
            RUN_PROCESS,
            str(tmp_path / "workflow.sqlite3"),
            str(snapshot_path),
            str(tmp_path / "calls.txt"),
            mode,
        ],
        cwd=ROOT,
        env=env(),
        text=True,
        capture_output=True,
        timeout=15,
    )


def source(tmp_path):
    snapshot = B["_snapshot"](retries=0)
    snapshot = snapshot.model_copy(
        update={"budget": snapshot.budget.model_copy(update={"model_calls_remaining": 1})}
    )
    path = tmp_path / "snapshot.json"
    path.write_text(snapshot.model_dump_json())
    return path, snapshot


def test_permanent_failure_replays_exact_result_after_process_restart(tmp_path):
    path, _ = source(tmp_path)
    first = invoke(tmp_path, path, "permanent_failure")
    second = invoke(tmp_path, path, "permanent_failure")
    assert first.returncode == second.returncode == 0, first.stderr + second.stderr
    result = BoundedWorkflowResult.model_validate_json(first.stdout)
    replay = BoundedWorkflowResult.model_validate_json(second.stdout)
    assert result == replay
    assert result.termination == WorkflowTermination.PERMANENT_FAILURE
    assert result.final_budget.model_calls_remaining == 0
    assert len(result.selection_failures) == 1
    assert (tmp_path / "calls.txt").read_text().splitlines() == ["provider-call"]


@pytest.mark.parametrize("mode", ["crash_after_reserve", "crash_after_tool", "crash_after_trace"])
def test_crashed_reservation_never_becomes_available_after_process_restart(tmp_path, mode):
    path, snapshot = source(tmp_path)
    first = invoke(tmp_path, path, mode)
    assert first.returncode == 23, first.stderr
    ledger = SqliteWorkflowRunLedger(tmp_path / "workflow.sqlite3")
    with pytest.raises(WorkflowRunUnavailable):
        asyncio.run(ledger.acquire(snapshot.run, snapshot.budget))
    second = invoke(tmp_path, path, "success")
    assert second.returncode != 0
    assert f"ServiceFault: {ServiceErrorCode.CONFLICT.value}" in second.stderr
    marker = tmp_path / "calls.txt"
    assert (marker.read_text().splitlines() if marker.exists() else []) == {
        "crash_after_reserve": [],
        "crash_after_tool": ["tool-side-effect"],
        "crash_after_trace": ["tool-side-effect", "checkpoint-entered"],
    }[mode]


def test_verified_result_replays_after_process_restart_without_second_side_effect(tmp_path):
    path, _ = source(tmp_path)
    first = invoke(tmp_path, path, "success")
    second = invoke(tmp_path, path, "success")
    assert first.returncode == second.returncode == 0, first.stderr + second.stderr
    assert json.loads(first.stdout) == json.loads(second.stdout)
    assert json.loads(second.stdout)["termination"] == "verified"
    assert (tmp_path / "calls.txt").read_text().splitlines() == ["tool-side-effect"]


ACQUIRE_PROCESS = r"""
import asyncio
import sys
from appraisal_review.adapters.local.sqlite_workflow_run_ledger import SqliteWorkflowRunLedger
from appraisal_review.domain.service_contracts import WorkflowSnapshot
from appraisal_review.ports.workflow_run_ledger import WorkflowRunUnavailable
from pathlib import Path
ledger = SqliteWorkflowRunLedger(sys.argv[1])
snapshot = WorkflowSnapshot.model_validate_json(Path(sys.argv[2]).read_text())
print("ready", flush=True)
sys.stdin.readline()
try:
    asyncio.run(ledger.acquire(snapshot.run, snapshot.budget))
    print("acquired", flush=True)
except WorkflowRunUnavailable:
    print("rejected", flush=True)
"""


def test_two_processes_cannot_acquire_the_same_run(tmp_path):
    path, _ = source(tmp_path)
    database = tmp_path / "workflow.sqlite3"
    SqliteWorkflowRunLedger(database)
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", ACQUIRE_PROCESS, str(database), str(path)],
            cwd=ROOT,
            env=env(),
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for _ in range(2)
    ]
    try:
        for process in processes:
            assert process.stdout.readline().strip() == "ready"
        for process in processes:
            process.stdin.write("go\n")
            process.stdin.flush()
        outputs = [process.communicate(timeout=10) for process in processes]
        assert all(process.returncode == 0 for process in processes), outputs
        assert sorted(stdout.strip() for stdout, _ in outputs) == ["acquired", "rejected"]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)


@pytest.mark.parametrize(
    "mode,reason",
    [
        ("unknown_result", "tool-result-unknown"),
        ("invalid_receipt", "invalid-tool-receipt"),
    ],
)
def test_quarantined_failure_replays_after_restart_even_with_successful_new_tool(
    tmp_path, mode, reason
):
    path, _ = source(tmp_path)
    first = invoke(tmp_path, path, mode)
    second = invoke(tmp_path, path, "success")
    assert first.returncode == second.returncode == 0, first.stderr + second.stderr
    result = BoundedWorkflowResult.model_validate_json(first.stdout)
    assert result == BoundedWorkflowResult.model_validate_json(second.stdout)
    assert result.termination == WorkflowTermination.PERMANENT_FAILURE
    assert result.events[0].reason_code == reason
    assert "private-result-canary" not in result.model_dump_json()
    assert (tmp_path / "calls.txt").read_text().splitlines() == ["tool-side-effect"]
