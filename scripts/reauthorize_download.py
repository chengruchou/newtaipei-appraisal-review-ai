"""Renew a lapsed download window for output this case already committed.

A download grant is deliberately short lived, so a reviewer who checks a draft, goes to a
meeting and comes back finds the link refused. That is a closed window, not a withdrawn
authority, and re-running a case to fetch identical bytes would be worse than the problem.

This is an operator command, not an HTTP route: an endpoint able to refresh arbitrary
download grants is exactly the general-purpose admin surface the service contract refuses.
It regenerates nothing, calls no model, issues no synthetic approval and cannot lengthen
the configured window. A revoked grant stays revoked.

    python scripts/reauthorize_download.py --directory artifacts/core-demo
    python scripts/reauthorize_download.py --directory artifacts/core-demo --case <case-id>
"""

from __future__ import annotations

import argparse
import asyncio
import time
from datetime import UTC, datetime
from pathlib import Path

from appraisal_review.adapters.local.synthetic_workbench import prepare_workbench
from appraisal_review.domain.artifact_publication import PublicationError

EXIT_REFUSED = 2


def _report(case_id: str, expires_at: int) -> None:
    remaining = expires_at - int(time.time())
    when = datetime.fromtimestamp(expires_at, tz=UTC).astimezone().isoformat(timespec="seconds")
    print(f"  {case_id}: renewed until {when} ({remaining}s remaining)")


async def _run(root: Path, port: int, cases: list[str] | None) -> int:
    workbench = await prepare_workbench(root, port=port)
    known = dict(workbench.case_ids)
    if cases:
        unknown = [case for case in cases if case not in known.values()]
        if unknown:
            print(f"Unknown case for this workbench: {', '.join(unknown)}")
            return EXIT_REFUSED
        targets = {name: case for name, case in known.items() if case in cases}
    else:
        targets = known

    print(f"Renewing download windows for {len(targets)} case(s). Nothing is regenerated.")
    refused: list[tuple[str, str]] = []
    for name, case_id in sorted(targets.items()):
        try:
            expires_at = workbench.manifests.reauthorize_download(workbench.principal, case_id)
        except PublicationError as error:
            # Stable codes only. "unpublished" means this case has no committed output to
            # re-authorize, which is a different problem from a refused renewal.
            refused.append((name, error.code))
            print(f"  {case_id} ({name}): refused - {error.code}")
            continue
        _report(f"{case_id} ({name})", expires_at)

    if refused:
        print(f"\n{len(refused)} case(s) refused; inspect the code above before retrying.")
        return EXIT_REFUSED
    print("\nDownload the artifact through the service now; the window is short by design.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--case",
        action="append",
        dest="cases",
        help="Case identifier to renew; repeatable. Defaults to every registered case.",
    )
    args = parser.parse_args()
    return asyncio.run(_run(args.directory.absolute(), args.port, args.cases))


if __name__ == "__main__":
    raise SystemExit(main())
