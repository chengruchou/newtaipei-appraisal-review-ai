"""Export local SDK consumer schema and synthetic requests; never mint approval."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from pydantic.json_schema import models_json_schema

from appraisal_review.domain.privacy_models import PrivacyRegion, SensitiveCategory
from appraisal_review.domain.privacy_review import (
    AddPrivacyRegion,
    ConfirmPrivacyReview,
    EditPrivacyRegion,
    PrivacyCropReference,
    PrivacyCropRequest,
    PrivacyReviewView,
    RemovePrivacyRegion,
    ReviewPrivacyPage,
    ReviewVersion,
)

MODELS = (
    ReviewVersion,
    AddPrivacyRegion,
    EditPrivacyRegion,
    RemovePrivacyRegion,
    ReviewPrivacyPage,
    ConfirmPrivacyReview,
    PrivacyCropRequest,
    PrivacyCropReference,
    PrivacyReviewView,
)


def export(root: Path) -> None:
    _, schema = models_json_schema(
        [(m, "serialization") for m in MODELS], title="local-privacy-review-v1"
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    version = dict(
        case_id=UUID("00000000-0000-4000-8000-000000000001"),
        snapshot_id=UUID("00000000-0000-4000-8000-000000000003"),
        revision=1,
    )
    fixtures = {
        "add": AddPrivacyRegion(
            **version,
            region=PrivacyRegion(page=1, bbox=(10.0, 20.0, 80.0, 40.0)),
            category=SensitiveCategory.SIGNATURE,
        ),
        "review-page": ReviewPrivacyPage(**version, page=1),
        "remove": RemovePrivacyRegion(
            **version,
            candidate_id=UUID("00000000-0000-4000-8000-000000000004"),
            reason="Synthetic reviewed false positive",
        ),
        "confirm-request": ConfirmPrivacyReview(**version, review_digest="d" * 64),
    }
    path = root / "schemas/local-privacy-review-v1.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    directory = root / "examples/privacy-review-v1"
    directory.mkdir(parents=True, exist_ok=True)
    for name, fixture in fixtures.items():
        (directory / f"{name}.json").write_text(
            fixture.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    export(Path(__file__).resolve().parents[1])
