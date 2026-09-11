"""Versioned artifact projections retain coverage without changing legacy shape."""

import json
from pathlib import Path
from uuid import uuid4

import jsonschema
import pytest
from pydantic import ValidationError

from appraisal_review.domain.review_contracts import ComparisonContext
from appraisal_review.domain.service_contracts import ArtifactManifest, FencedArtifactManifest


def manifest():
    contexts = tuple(
        ComparisonContext(scope="individual", target_id="target", comparable_id=name)
        for name in ("first", "second")
    )
    return FencedArtifactManifest(
        artifact_id=uuid4(),
        content_hash="1" * 64,
        context=contexts[0],
        contexts=contexts,
        field_ids=("first-grade", "second-grade"),
        page_count=2,
        template_hash="2" * 64,
        field_map_hash="3" * 64,
        font_hash="4" * 64,
        writer_version="synthetic-writer-v1",
        manifest_digest="5" * 64,
    )


def test_exact_version_roundtrips_through_exported_schema_and_not_legacy():
    data = manifest().model_dump(mode="json")
    schema = json.loads(
        (Path(__file__).resolve().parents[2] / "schemas/service-v1.json").read_text()
    )
    jsonschema.validate(data, {**schema, "$ref": "#/$defs/FencedArtifactManifest"})
    assert FencedArtifactManifest.model_validate(data).model_dump(mode="json") == data
    with pytest.raises(ValidationError):
        ArtifactManifest.model_validate(data)
    assert ArtifactManifest.model_fields["scope"].default == "single_context"
    assert ArtifactManifest.model_fields["publication"].default == "local_only"


@pytest.mark.parametrize(
    "change", ["duplicate_context", "wrong_primary", "duplicate_field", "version", "uri"]
)
def test_inconsistent_or_private_projection_is_not_admitted(change):
    data = manifest().model_dump(mode="json")
    if change == "duplicate_context":
        data["contexts"] = [data["context"], data["context"]]
    elif change == "wrong_primary":
        data["context"] = data["contexts"][1]
    elif change == "duplicate_field":
        data["field_ids"] = ["first-grade", "first-grade"]
    elif change == "version":
        data["schema_version"] = "service-v1"
    else:
        data["download_uri"] = "file:///private/result.pdf"
    with pytest.raises(ValidationError):
        FencedArtifactManifest.model_validate(data)
