"""Export document-v1 public schemas; no signatures, keys or private source fixtures."""

import json
from pathlib import Path

from pydantic.json_schema import models_json_schema

from appraisal_review.domain.document_transfer import (
    DocumentMetadata,
    DocumentProblem,
    PrivacyAttestation,
    RunSourceSnapshot,
)
from appraisal_review.domain.privacy_models import PrivacyManifest

MODELS = (PrivacyManifest, PrivacyAttestation, DocumentMetadata, RunSourceSnapshot, DocumentProblem)


def export(root: Path) -> None:
    _, schema = models_json_schema([(model, "validation") for model in MODELS])
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "Authorized sanitized document contracts"
    target = root / "schemas/document-v1.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    export(Path(__file__).resolve().parents[1])
