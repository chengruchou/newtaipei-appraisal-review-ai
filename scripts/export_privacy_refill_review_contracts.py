"""Export the separate loopback OCR-review schema; never add it to a cloud API."""

import json
from pathlib import Path

from pydantic.json_schema import models_json_schema

from appraisal_review.domain.privacy_refill_review import (
    OCRReviewConfirmation,
    OCRReviewReceipt,
    OCRReviewView,
)


def export(root: Path) -> None:
    _, schema = models_json_schema(
        [
            (model, "serialization")
            for model in (OCRReviewView, OCRReviewConfirmation, OCRReviewReceipt)
        ],
        title="local-privacy-refill-review-v1",
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    output = root / "schemas/local-privacy-refill-review-v1.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    export(Path(__file__).resolve().parents[1])
