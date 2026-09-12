"""Local refill contracts; publisher metadata and plans alone confer no authority."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Literal

from pydantic import UUID4, Field, model_validator

from appraisal_review.domain.privacy_models import (
    Digest,
    LocalPrivacyModel,
    PrivacyPage,
    PrivacyRegion,
    placeholder_text,
    validate_pages,
    validate_region,
)
from appraisal_review.domain.privacy_review import PrivacyPagePreview
from appraisal_review.domain.privacy_scan import TextObservation


class RefillTarget(LocalPrivacyModel):
    occurrence_id: UUID4
    entity_id: UUID4
    output_field_id: UUID4
    region: PrivacyRegion
    present: bool = True


def overlaps(a: PrivacyRegion, b: PrivacyRegion) -> bool:
    return (
        a.page == b.page
        and max(a.bbox[0], b.bbox[0]) < min(a.bbox[2], b.bbox[2])
        and (max(a.bbox[1], b.bbox[1]) < min(a.bbox[3], b.bbox[3]))
    )


class PublishedRefillDescriptor(LocalPrivacyModel):
    """Proposed #26/#28 publisher contract, authenticated by a separately trusted port."""

    case_id: UUID4
    document_id: UUID4
    run_id: UUID4
    revision_id: UUID4
    artifact_digest: Digest
    base_sanitized_digest: Digest
    template_digest: Digest
    pages: tuple[PrivacyPage, ...] = Field(min_length=1)
    targets: tuple[RefillTarget, ...] = Field(max_length=1000)

    @model_validator(mode="after")
    def geometry(self) -> PublishedRefillDescriptor:
        validate_pages(self.pages)
        for name in ("occurrence_id", "output_field_id"):
            values = [getattr(target, name) for target in self.targets]
            if len(values) != len(set(values)):
                raise ValueError("Duplicate refill target")
        for index, target in enumerate(self.targets):
            validate_region(target.region, self.pages)
            if any(overlaps(target.region, prior.region) for prior in self.targets[:index]):
                raise ValueError("Overlapping refill targets")
        return self


@dataclass(frozen=True)
class PublishedRefillArtifact:
    descriptor: PublishedRefillDescriptor
    pdf: bytes = field(repr=False)


@dataclass(frozen=True)
class RefillRenderedPage:
    preview: PrivacyPagePreview
    native: tuple[TextObservation, ...] = field(repr=False)


class FinalLocalManifest(LocalPrivacyModel):
    case_id: UUID4
    document_id: UUID4
    map_id: UUID4
    run_id: UUID4
    revision_id: UUID4
    input_artifact_digest: Digest
    final_digest: Digest
    byte_size: int = Field(gt=0)
    filename: Literal["final-local.pdf"] = "final-local.pdf"
    state: Literal["refilled_local"] = "refilled_local"
    business_authority: Literal["unchanged"] = "unchanged"
    signature_effect: Literal["visual_only_no_digital_signature"] = (
        "visual_only_no_digital_signature"
    )
    restored_fields: tuple[UUID4, ...]
    omitted_fields: tuple[UUID4, ...]


@dataclass(frozen=True)
class FinalLocalArtifact:
    manifest: FinalLocalManifest
    pdf: bytes = field(repr=False)


TOKEN = re.compile(r"(?<![a-z0-9_])PT_[0-9a-f]{32}(?![a-z0-9_])", re.IGNORECASE)


def verify_occurrences(
    observations: tuple[TextObservation, ...],
    targets: tuple[RefillTarget, ...],
    pages: tuple[PrivacyPage, ...],
    *,
    require_all: bool,
) -> None:
    """Check identifiers, counts and positions; never infer authority from OCR text."""
    checked = tuple(TextObservation.model_validate(value) for value in observations)
    verify_text_regions(
        tuple((value.text, value.region) for value in checked),
        targets,
        pages,
        require_all=require_all,
    )


def verify_text_regions(
    readings: tuple[tuple[str, PrivacyRegion], ...],
    targets: tuple[RefillTarget, ...],
    pages: tuple[PrivacyPage, ...],
    *,
    require_all: bool,
) -> None:
    """Shared deterministic inventory check for measured or separately reviewed readings."""
    seen = set()
    for raw_text, region in readings:
        validate_region(region, pages)
        text = unicodedata.normalize("NFKC", raw_text)
        matches = list(TOKEN.finditer(text))
        if text.casefold().count("pt_") != len(matches):
            raise ValueError("Malformed placeholder")
        for match in matches:
            possible = []
            for target in targets:
                a, b = target.region.bbox, region.bbox
                if (
                    target.present
                    and target.occurrence_id not in seen
                    and (
                        placeholder_text(target.entity_id).casefold() == match.group().casefold()
                        and target.region.page == region.page
                        and a[0] <= b[0] < b[2] <= a[2]
                        and a[1] <= b[1] < b[3] <= a[3]
                    )
                ):
                    possible.append(target)
            if len(possible) != 1:
                raise ValueError("Unexpected or misplaced placeholder")
            seen.add(possible[0].occurrence_id)
    if require_all and seen != {t.occurrence_id for t in targets if t.present}:
        raise ValueError("Missing placeholder")
