"""Export privacy schemas and synthetic proposals, never authorization evidence."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from pydantic.json_schema import models_json_schema

from appraisal_review.domain.privacy_models import (
    EncryptedMappingEnvelope,
    LocalPrivacyApproval,
    LocalPrivacyModel,
    LocalSourcePage,
    LocalSourceSnapshot,
    PlaceholderOccurrence,
    PrivacyErrorCode,
    PrivacyManifest,
    PrivacyModel,
    PrivacyPage,
    PrivacyProblem,
    PrivacyRegion,
    PrivacyReviewCommand,
    RehydrationField,
    RehydrationPlan,
    ReviewSelection,
    SensitiveCandidate,
    SensitiveCategory,
    privacy_review_digest,
)

PUBLIC_MODELS = (PrivacyManifest, PrivacyProblem)
LOCAL_MODELS = (
    LocalSourceSnapshot,
    SensitiveCandidate,
    ReviewSelection,
    PrivacyReviewCommand,
    LocalPrivacyApproval,
    EncryptedMappingEnvelope,
    RehydrationPlan,
)


def fixture_id(value: int) -> UUID:
    """Fixed version-4-shaped test identifiers; never use as production issuance."""
    return UUID(f"00000000-0000-4000-8000-{value:012x}")


def fixtures() -> dict[str, PrivacyModel]:
    page = PrivacyPage(number=1, width=600.0, height=800.0)
    region = PrivacyRegion(page=1, bbox=(10.0, 20.0, 110.0, 40.0))
    source = LocalSourceSnapshot(
        case_id=fixture_id(1),
        document_id=fixture_id(2),
        snapshot_id=fixture_id(3),
        source_revision=1,
        source_digest="a" * 64,
        byte_size=100,
        pages=(
            LocalSourcePage(**page.model_dump(), rotation=0, crop_box=(0.0, 0.0, 600.0, 800.0)),
        ),
    )
    candidate = SensitiveCandidate(
        candidate_id=fixture_id(4),
        category=SensitiveCategory.NAME,
        region=region,
        raw_text="SYNTHETIC-LOCAL-ONLY-VALUE",
        confidence=0.8,
        detector_id="synthetic-detector",
        detector_version="fixture-v1",
    )
    selection = ReviewSelection(candidate=candidate, disposition="redact", entity_id=fixture_id(5))
    command = PrivacyReviewCommand(
        source=source,
        selection_revision=1,
        policy_digest="b" * 64,
        selections=(selection,),
        reviewed_pages=(1,),
    )
    now = datetime(2026, 9, 10, tzinfo=UTC)
    approval = LocalPrivacyApproval(
        approval_id=fixture_id(6),
        case_id=source.case_id,
        snapshot_id=source.snapshot_id,
        review_digest=privacy_review_digest(command),
        principal_id="synthetic-human",
        approved_at=now,
        expires_at=now + timedelta(hours=1),
    )
    occurrence = PlaceholderOccurrence(
        occurrence_id=fixture_id(7), entity_id=fixture_id(5), region=region
    )
    manifest = PrivacyManifest(
        case_id=source.case_id,
        document_id=source.document_id,
        sanitized_digest="c" * 64,
        byte_size=101,
        pages=(page,),
        occurrences=(occurrence,),
    )
    envelope = EncryptedMappingEnvelope(
        case_id=source.case_id,
        map_id=fixture_id(8),
        key_reference=fixture_id(9),
        nonce_hex="00" * 12,
        ciphertext_hex="00" * 16,
        expires_at=now + timedelta(days=1),
    )
    refill = RehydrationPlan(
        case_id=source.case_id,
        document_id=source.document_id,
        map_id=envelope.map_id,
        run_id=fixture_id(10),
        revision_id=fixture_id(11),
        artifact_digest="d" * 64,
        template_digest="e" * 64,
        pages=(page,),
        fields=(
            RehydrationField(
                occurrence_id=occurrence.occurrence_id,
                entity_id=occurrence.entity_id,
                output_field_id=fixture_id(12),
                destination=region,
                operation="restore_text",
            ),
        ),
    )
    return {
        "source.json": source,
        "candidate.json": candidate,
        "selection.json": selection,
        "review-command.json": command,
        "approval.json": approval,
        "manifest.json": manifest,
        "mapping-envelope.json": envelope,
        "rehydration-plan.json": refill,
        "problem.json": PrivacyProblem(code=PrivacyErrorCode.CAPABILITY_UNAVAILABLE),
    }


def export(root: Path) -> None:
    def write(relative: str, value: object) -> None:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")

    for name, models in (("privacy-v1", PUBLIC_MODELS), ("local-privacy-v1", LOCAL_MODELS)):
        _, schema = models_json_schema([(model, "serialization") for model in models], title=name)
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        write(f"schemas/{name}.json", schema)
    index = {}
    for name, model in fixtures().items():
        visibility = "local" if isinstance(model, LocalPrivacyModel) else "public"
        relative = f"{visibility}/{name}"
        index[relative] = type(model).__name__
        write(f"examples/privacy-v1/{relative}", model.model_dump(mode="json"))
    write("examples/privacy-v1/index.json", index)


if __name__ == "__main__":
    export(Path(__file__).resolve().parents[1])
