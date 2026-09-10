"""Export local mapping contracts with synthetic plaintext, never real keys/cases."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from pydantic.json_schema import models_json_schema

from appraisal_review.domain.privacy_mapping import LocalMappingHandle, LocalMappingRecord
from appraisal_review.domain.privacy_models import PrivacyManifest, PrivacyReviewCommand


def export(root: Path) -> None:
    fixtures = Path(__file__).resolve().parents[1] / "examples/privacy-v1"
    command = PrivacyReviewCommand.model_validate_json(
        (fixtures / "local/review-command.json").read_bytes()
    )
    manifest = PrivacyManifest.model_validate_json((fixtures / "public/manifest.json").read_bytes())
    now = datetime(2026, 9, 10, tzinfo=UTC)
    record = LocalMappingRecord(
        map_id=UUID("00000000-0000-4000-8000-000000000020"),
        created_at=now,
        expires_at=now + timedelta(days=1),
        command=command,
        manifest=manifest,
    )
    _, schema = models_json_schema(
        [(m, "serialization") for m in (LocalMappingRecord, LocalMappingHandle)],
        title="local-privacy-mapping-v1",
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    path = root / "schemas/local-privacy-mapping-v1.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    example = root / "examples/privacy-mapping-v1/synthetic-record.json"
    example.parent.mkdir(parents=True, exist_ok=True)
    example.write_text(record.model_dump_json(indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    export(Path(__file__).resolve().parents[1])
