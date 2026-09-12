"""Export local-only Phase 2 schema and a synthetic blocked coverage fixture."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from pydantic.json_schema import models_json_schema

from appraisal_review.domain.privacy_models import LocalSourcePage, LocalSourceSnapshot
from appraisal_review.domain.privacy_scan import (
    PageScan,
    PrivacyCapabilities,
    PrivacyScanReport,
    ScanIssue,
)

MODELS = (PrivacyScanReport, PrivacyCapabilities)


def export(root: Path) -> None:
    _, schema = models_json_schema(
        [(model, "serialization") for model in MODELS], title="local-privacy-scan-v1"
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    snapshot = LocalSourceSnapshot(
        case_id=UUID("00000000-0000-4000-8000-000000000001"),
        document_id=UUID("00000000-0000-4000-8000-000000000002"),
        snapshot_id=UUID("00000000-0000-4000-8000-000000000003"),
        source_revision=1,
        source_digest="a" * 64,
        byte_size=100,
        pages=(
            LocalSourcePage(
                number=1, width=300.0, height=400.0, rotation=0, crop_box=(0.0, 0.0, 300.0, 400.0)
            ),
        ),
    )
    report = PrivacyScanReport(
        source=snapshot,
        native_engine_version="synthetic-fixture",
        status="blocked",
        pages=(
            PageScan(page=1, mode="scanned", status="blocked", issues=(ScanIssue.OCR_UNAVAILABLE,)),
        ),
    )
    for relative, data in (
        ("schemas/local-privacy-scan-v1.json", schema),
        ("examples/privacy-scan-v1/blocked.json", report.model_dump(mode="json")),
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    export(Path(__file__).resolve().parents[1])
