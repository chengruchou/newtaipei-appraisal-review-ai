"""Prepare or serve the explicit synthetic workbench on numeric loopback.

Examples: --directory artifacts/workbench --prepare; the same command with --serve
reopens durable state. With neither mode flag, prepare and serve. fixture.json is
private local authentication configuration, never a public evidence report.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import uvicorn

from appraisal_review.adapters.local.synthetic_workbench import (
    SyntheticWorkbench,
    _private_json,
    prepare_workbench,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--fixture", type=Path)
    args = parser.parse_args()
    root = args.directory.absolute()
    target = args.fixture.absolute() if args.fixture else root / "fixture.json"
    if not target.is_relative_to(root) or target.is_symlink():
        parser.error("The private fixture manifest must remain inside the workbench directory")

    async def prepare() -> SyntheticWorkbench:
        workbench = await prepare_workbench(root, port=args.port)
        await workbench.settle()
        _private_json(target, workbench.manifest())
        return workbench

    workbench = asyncio.run(prepare())
    print(f"Synthetic local API ready: http://127.0.0.1:{args.port}", flush=True)
    if args.serve or not args.prepare:
        uvicorn.run(workbench.app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
