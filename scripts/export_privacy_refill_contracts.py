"""Proposed #26/#28 local handoff fixtures, not authenticated publisher releases."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from pydantic.json_schema import models_json_schema

from appraisal_review.domain.privacy_mapping import LocalMappingRecord
from appraisal_review.domain.privacy_models import PrivacyRegion, RehydrationField, RehydrationPlan
from appraisal_review.domain.privacy_refill import (
    FinalLocalManifest,
    PublishedRefillDescriptor,
    RefillTarget,
)


def export(root: Path) -> None:
    repository = Path(__file__).resolve().parents[1]
    mapping = LocalMappingRecord.model_validate_json(
        (repository / "examples/privacy-mapping-v1/synthetic-record.json").read_bytes()
    )
    occurrence = mapping.manifest.occurrences[0]
    target = RefillTarget(
        occurrence_id=occurrence.occurrence_id,
        entity_id=occurrence.entity_id,
        output_field_id=UUID("00000000-0000-4000-8000-000000000023"),
        region=PrivacyRegion(page=1, bbox=(380.0, 700.0, 580.0, 770.0)),
    )
    descriptor = PublishedRefillDescriptor(
        case_id=mapping.manifest.case_id,
        document_id=mapping.manifest.document_id,
        run_id=UUID("00000000-0000-4000-8000-000000000021"),
        revision_id=UUID("00000000-0000-4000-8000-000000000022"),
        artifact_digest="a" * 64,
        base_sanitized_digest=mapping.manifest.sanitized_digest,
        template_digest="e" * 64,
        pages=mapping.manifest.pages,
        targets=(target,),
    )
    plan = RehydrationPlan(
        case_id=descriptor.case_id,
        document_id=descriptor.document_id,
        map_id=mapping.map_id,
        run_id=descriptor.run_id,
        revision_id=descriptor.revision_id,
        artifact_digest=descriptor.artifact_digest,
        template_digest=descriptor.template_digest,
        pages=descriptor.pages,
        fields=(
            RehydrationField(
                occurrence_id=target.occurrence_id,
                entity_id=target.entity_id,
                output_field_id=target.output_field_id,
                destination=target.region,
                operation="restore_text",
            ),
        ),
    )
    final = FinalLocalManifest(
        case_id=plan.case_id,
        document_id=plan.document_id,
        map_id=plan.map_id,
        run_id=plan.run_id,
        revision_id=plan.revision_id,
        input_artifact_digest=plan.artifact_digest,
        final_digest="f" * 64,
        byte_size=100,
        restored_fields=(target.output_field_id,),
        omitted_fields=(),
    )
    _, schema = models_json_schema(
        [
            (m, "serialization")
            for m in (PublishedRefillDescriptor, RehydrationPlan, FinalLocalManifest)
        ],
        title="local-privacy-refill-v1",
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema_path = root / "schemas/local-privacy-refill-v1.json"
    schema_path.parent.mkdir(parents=True, exist_ok=True)
    schema_path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    directory = root / "examples/privacy-refill-v1"
    directory.mkdir(parents=True, exist_ok=True)
    for name, model in (("publisher", descriptor), ("plan", plan), ("final-manifest", final)):
        (directory / f"{name}.json").write_text(
            model.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    export(Path(__file__).resolve().parents[1])
