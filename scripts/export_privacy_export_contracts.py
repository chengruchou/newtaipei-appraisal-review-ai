"""Export value-free leak report schema and explicitly synthetic outcome examples."""

from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

from appraisal_review.domain.privacy_export import (
    CanaryHit,
    LeakCheck,
    LeakScanReport,
    LeakSurface,
)


def export(root: Path) -> None:
    schema = LeakScanReport.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    path = root / "schemas/privacy-leak-report-v1.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    directory = root / "examples/privacy-export-v1"
    directory.mkdir(parents=True, exist_ok=True)
    for status in ("passed", "failed", "blocked"):
        checks = tuple(
            LeakCheck(
                surface=surface,
                inspected=status != "blocked",
                hits=(CanaryHit(canary_id="c01", count=1),)
                if status == "failed" and surface == "pdf_images"
                else (),
            )
            for surface in get_args(LeakSurface)
        )
        report = LeakScanReport(scope="python_hooks", status=status, checks=checks)
        (directory / f"synthetic-{status}.json").write_text(
            report.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    export(Path(__file__).resolve().parents[1])
