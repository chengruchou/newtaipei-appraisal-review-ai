"""Loopback-only synthetic HTTP fixture for committed gateway-failure recovery.

Uses the component's real service and stores with its existing test-data builder.
Never deploy this fixture or use private case data with the fault-injection proxy.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import uvicorn

from appraisal_review.api.app import create_app
from tests.unit.test_human_task_api import StubResolver
from tests.unit.test_human_task_service import Harness, caller, correction_task


async def run(manifest: Path, port: int) -> None:
    harness = Harness()
    task = correction_task(harness.snapshot, harness.run_id)
    await harness.setup((task,), principal=caller())
    app = create_app(human_task_service=harness.service, principal_resolver=StubResolver(caller()))
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(
            {
                "api_base_url": f"http://127.0.0.1:{port}",
                "session_token": "synthetic-browser-session",
                "job_id": str(harness.job_id),
                "tasks": {"lost_response": str(task.task_id)},
            }
        ),
        encoding="utf-8",
    )
    await uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port)).serve()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8769)
    args = parser.parse_args()
    asyncio.run(run(args.manifest, args.port))
