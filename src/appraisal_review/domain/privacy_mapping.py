"""Local mapping plaintext and fixed errors; never serialize these to cloud."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import UUID4, AwareDatetime, Field, model_validator

from appraisal_review.domain.privacy_models import (
    Digest,
    LocalPrivacyModel,
    PrivacyManifest,
    PrivacyReviewCommand,
)


class LocalMappingHandle(LocalPrivacyModel):
    case_id: UUID4
    map_id: UUID4
    key_reference: UUID4
    expires_at: AwareDatetime
    envelope_digest: Digest


class MappingError(StrEnum):
    INVALID = "mapping_invalid"
    LOCKED = "mapping_locked"
    EXPIRED = "mapping_expired"
    AUTHENTICATION = "mapping_authentication_failed"
    CONFLICT = "mapping_conflict"
    IO = "mapping_storage_failed"
    PLATFORM = "mapping_platform_unavailable"


class MappingFault(Exception):
    def __init__(self, code: MappingError) -> None:
        self.code = code
        super().__init__(code.value)


class LocalMappingRecord(LocalPrivacyModel):
    """Encrypted payload. Original crop references require the owned source at refill."""

    mapping_version: Literal["privacy-mapping-v1"] = "privacy-mapping-v1"
    map_id: UUID4
    created_at: AwareDatetime
    expires_at: AwareDatetime
    command: PrivacyReviewCommand = Field(repr=False)
    manifest: PrivacyManifest = Field(repr=False)

    @model_validator(mode="after")
    def binding(self) -> LocalMappingRecord:
        source = self.command.source
        if (
            self.created_at >= self.expires_at
            or (self.expires_at - self.created_at).total_seconds() > 604800
        ):
            raise ValueError("Invalid retention")
        if (
            source.case_id != self.manifest.case_id
            or source.document_id != self.manifest.document_id
        ):
            raise ValueError("Mapping lineage differs")
        if source.source_digest == self.manifest.sanitized_digest or len(source.pages) != len(
            self.manifest.pages
        ):
            raise ValueError("Invalid sanitized identity")
        if set(self.command.reviewed_pages) != set(p.number for p in source.pages):
            raise ValueError("Incomplete page review")
        selections = [s for s in self.command.selections if s.disposition == "redact"]
        if len(selections) != len(self.manifest.occurrences) or any(
            s.entity_id != o.entity_id or s.candidate.region.page != o.region.page
            for s, o in zip(selections, self.manifest.occurrences, strict=True)
        ):
            raise ValueError("Mapping occurrence differs")
        return self
