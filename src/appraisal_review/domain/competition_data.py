"""Exact local evidence for competition data admission, separate from redaction."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Literal, get_args

from pydantic import Field, model_validator

from appraisal_review.domain.document_models import Digest, DocumentModel

RULE_SOURCE_SHA256 = "64bbda4d8056d3edd913ced8e96330f282621a00fe9d4152341d162fd385aec0"
DataCategory = Literal[
    "personal_data",
    "regulated_data",
    "financial_information",
    "race_or_ethnicity",
    "political_views",
    "religious_or_philosophical_views",
    "trade_union_membership",
    "genetic_data",
    "biometric_data_or_identifiers",
    "sexual_orientation_or_sex_life",
    "health_data",
    "payment_processing_data",
    "malicious_code_or_malware",
]
DataSurface = Literal[
    "pdf",
    "page_image",
    "extraction_json",
    "model_prompt",
    "human_text",
    "filename",
    "metadata",
    "log",
    "restored_output",
    "mapping",
]
DataPartID = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]{0,79}$")]


@dataclass(frozen=True, repr=False)
class DataPart:
    """Immutable actual outgoing bytes; never serialize content into a diagnostic."""

    part_id: str
    surface: DataSurface
    content: bytes = field(repr=False)

    def binding(self) -> PartBinding:
        if type(self.content) is not bytes:
            raise ValueError("Immutable admission content required")
        return PartBinding(
            part_id=self.part_id,
            surface=self.surface,
            sha256=hashlib.sha256(self.content).hexdigest(),
            byte_size=len(self.content),
        )


class PartBinding(DocumentModel):
    part_id: DataPartID
    surface: DataSurface
    sha256: Digest
    byte_size: int = Field(ge=0)


def envelope_digest(parts: tuple[DataPart, ...]) -> str:
    if not parts or len(parts) > 1000 or len({p.part_id for p in parts}) != len(parts):
        raise ValueError("A unique bounded admission envelope is required")
    bindings = [p.binding().model_dump(mode="json") for p in parts]
    bindings.sort(key=lambda item: item["part_id"])
    return hashlib.sha256(
        json.dumps(bindings, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class CategoryAssessment(DocumentModel):
    category: DataCategory
    classification: Literal["absent", "present", "unknown"]


class PartAssessment(DocumentModel):
    binding: PartBinding
    categories: tuple[CategoryAssessment, ...]
    inspection_evidence_sha256: Digest

    @model_validator(mode="after")
    def complete_categories(self) -> PartAssessment:
        if len(self.categories) != len(get_args(DataCategory)) or {
            c.category for c in self.categories
        } != set(get_args(DataCategory)):
            raise ValueError("Every prohibited category requires an explicit assessment")
        return self


class CompetitionDataPolicy(DocumentModel):
    schema_version: Literal["competition-data-policy-v1"] = "competition-data-policy-v1"
    source_version: Literal["20260722"] = "20260722"
    source_sha256: Literal["64bbda4d8056d3edd913ced8e96330f282621a00fe9d4152341d162fd385aec0"] = (
        "64bbda4d8056d3edd913ced8e96330f282621a00fe9d4152341d162fd385aec0"
    )
    source_page: Literal[1] = 1
    source_rule: Literal["general-2"] = "general-2"
    # This must be an actual organizer clarification covered by the trusted policy
    # digest. Its existence is never inferred from a local synthetic demonstration.
    synthetic_financial_clarification_sha256: Digest | None = None


class DataReviewRecord(DocumentModel):
    """A local record alone is not authorization; a trusted authority must pin it."""

    schema_version: Literal["competition-data-review-v1"] = "competition-data-review-v1"
    envelope_sha256: Digest
    policy_sha256: Digest
    origin: Literal["synthetic_from_scratch", "real_case", "transformed_real_case", "unknown"]
    provenance_evidence_sha256: Digest
    generator_sha256: Digest | None = None
    reviewer_id: Annotated[str, Field(pattern=r"^[a-zA-Z0-9_.-]{1,80}$")]
    reviewed_at: datetime
    expires_at: datetime
    assessments: tuple[PartAssessment, ...]

    @model_validator(mode="after")
    def exact_record(self) -> DataReviewRecord:
        if (
            self.reviewed_at.tzinfo is None
            or self.expires_at.tzinfo is None
            or self.reviewed_at >= self.expires_at
        ):
            raise ValueError("Bounded timezone-aware review validity required")
        if not self.assessments or len({a.binding.part_id for a in self.assessments}) != len(
            self.assessments
        ):
            raise ValueError("Unique complete part reviews required")
        return self


def record_digest(record: DocumentModel) -> str:
    return hashlib.sha256(record.model_dump_json().encode()).hexdigest()


class CompetitionDataFault(Exception):
    """Stable value-free code; no content, digest, filename or source path leaks."""

    def __init__(
        self,
        code: Literal[
            "competition_data_unreviewed",
            "competition_data_prohibited",
            "competition_data_provenance",
            "competition_data_policy",
        ],
    ) -> None:
        self.code = code
        super().__init__(code)
