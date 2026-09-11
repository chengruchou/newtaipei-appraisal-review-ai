"""Export the mounted OpenAPI document, so the browser client is generated, not written.

A hand-written client drifts from the service the moment a field is added, and the frozen
v1 models forbid extra fields, so the drift surfaces as a rejected request rather than a
type error. Generating `web/src/api/schema.d.ts` from this document keeps the browser and
the service on one contract.

Every plane is composed here purely to make its routes appear in the document. Nothing is
started, no store is durable and no principal is resolved; this writes a schema, not a
running service.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from appraisal_review.adapters.local.human_task_store import LocalHumanTaskStore
from appraisal_review.adapters.local.job_store import InMemoryJobStore, InMemoryResultStore
from appraisal_review.api.app import create_app
from appraisal_review.application.human_tasks import HumanTaskService
from appraisal_review.application.review_jobs import ReviewJobService
from appraisal_review.application.service_guards import Principal


class UnusedResolver:
    """Satisfies the composition guard; the document is built without serving a request."""

    async def current_principal(self) -> Principal:
        raise NotImplementedError("Schema export never resolves a principal")


def document() -> dict[str, Any]:
    jobs = InMemoryJobStore()
    app = create_app(
        job_service=ReviewJobService(jobs, InMemoryResultStore()),
        human_task_service=HumanTaskService(LocalHumanTaskStore(jobs)),
        principal_resolver=UnusedResolver(),
    )
    schema: dict[str, Any] = app.openapi()
    return schema


def export(root: Path) -> str:
    return json.dumps(document(), indent=2, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parent.parent
    target = root / "web" / "openapi.json"
    rendered = export(root)
    if "--write" in argv:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered)
        print(f"Wrote {target.relative_to(root)}")
        return 0
    if not target.exists():
        print(f"Missing {target.relative_to(root)}; run with --write", file=sys.stderr)
        return 1
    if target.read_text() != rendered:
        # The committed document is what the browser client was generated from, so a
        # drifted copy means the client no longer describes the mounted service.
        print(
            f"{target.relative_to(root)} is stale; run with --write and regenerate the client",
            file=sys.stderr,
        )
        return 1
    print(f"{target.relative_to(root)} matches the mounted routes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
