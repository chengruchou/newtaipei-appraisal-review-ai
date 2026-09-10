"""Privacy v1 contracts; local records and public manifests have separate roots.

Validation establishes structure and identity consistency, never human authority,
successful sanitization or permission to export. No runtime adapters live here.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import UUID4, AwareDatetime, BaseModel, ConfigDict, Field, model_validator

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Revision = Annotated[int, Field(ge=1)]
LocalText = Annotated[str, Field(min_length=1, max_length=4096)]
Box = tuple[float, float, float, float]


class PrivacyModel(BaseModel):
    model_config = ConfigDict(
        strict=True, extra="forbid", frozen=True, allow_inf_nan=False, revalidate_instances="always"
    )


class LocalPrivacyModel(PrivacyModel):
    """Local-only data. Never serialize to cloud, logs or public errors."""

    schema_version: Literal["local-privacy-v1"] = "local-privacy-v1"


class PrivacyState(StrEnum):
    LOADED = "loaded"
    SCANNED = "scanned"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    CONFIRMED = "confirmed"
    SANITIZED = "sanitized"
    VERIFIED = "verified"
    EXPORTABLE = "exportable"
    BLOCKED = "blocked"


class PrivacyErrorCode(StrEnum):
    INVALID_INPUT = "privacy_invalid_input"
    STALE_CONFIRMATION = "privacy_stale_confirmation"
    UNAUTHORIZED = "privacy_unauthorized"
    CAPABILITY_UNAVAILABLE = "privacy_capability_unavailable"
    VERIFICATION_FAILED = "privacy_verification_failed"


class PrivacyProblem(PrivacyModel):
    schema_version: Literal["privacy-v1"] = "privacy-v1"
    code: PrivacyErrorCode
    message: Literal["Local privacy processing requires review."] = (
        "Local privacy processing requires review."
    )


class PrivacyPage(PrivacyModel):
    number: int = Field(ge=1)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    coordinate_system: Literal["pdf_bottom_left"] = "pdf_bottom_left"
    page_space: Literal["unrotated_crop_box"] = "unrotated_crop_box"


class LocalSourcePage(PrivacyPage):
    """Original page transform metadata, retained locally for coordinate mapping."""

    rotation: Literal[0, 90, 180, 270]
    crop_box: Box

    @model_validator(mode="after")
    def valid_crop(self) -> LocalSourcePage:
        x0, y0, x1, y1 = self.crop_box
        if x1 <= x0 or y1 <= y0:
            raise ValueError("Invalid source CropBox")
        if abs((x1 - x0) - self.width) > 0.001 or abs((y1 - y0) - self.height) > 0.001:
            raise ValueError("Source CropBox dimensions differ from page")
        return self


class PrivacyRegion(PrivacyModel):
    page: int = Field(ge=1)
    bbox: Box

    @model_validator(mode="after")
    def valid_box(self) -> PrivacyRegion:
        x0, y0, x1, y1 = self.bbox
        if not (0 <= x0 < x1 and 0 <= y0 < y1):
            raise ValueError("Invalid privacy region")
        return self


def validate_pages(pages: tuple[PrivacyPage, ...]) -> None:
    if tuple(p.number for p in pages) != tuple(range(1, len(pages) + 1)):
        raise ValueError("Privacy pages must be complete and ordered")


def validate_region(region: PrivacyRegion, pages: tuple[PrivacyPage, ...]) -> None:
    if region.page > len(pages):
        raise ValueError("Privacy region outside document")
    page = pages[region.page - 1]
    if region.bbox[2] > page.width or region.bbox[3] > page.height:
        raise ValueError("Privacy region outside page")


class LocalSourceSnapshot(LocalPrivacyModel):
    """Identity of adapter-owned immutable bytes, not a caller-selected file URI."""

    case_id: UUID4
    document_id: UUID4
    snapshot_id: UUID4
    source_revision: Revision
    source_digest: Digest
    byte_size: int = Field(gt=0)
    pages: tuple[LocalSourcePage, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def page_sequence(self) -> LocalSourceSnapshot:
        validate_pages(self.pages)
        return self


class SensitiveCategory(StrEnum):
    NAME = "name"
    IDENTITY_NUMBER = "identity_number"
    BUSINESS_NUMBER = "business_number"
    BIRTH_DATE = "birth_date"
    PHONE = "phone"
    EMAIL = "email"
    ADDRESS = "address"
    ACCOUNT = "account"
    SIGNATURE = "signature"
    STAMP = "stamp"
    FACE = "face"
    CASE_CONTACT = "case_contact"
    PARCEL = "parcel"
    OWNERSHIP_FINANCIAL = "ownership_financial"


class SensitiveCandidate(LocalPrivacyModel):
    candidate_id: UUID4
    category: SensitiveCategory
    region: PrivacyRegion
    raw_text: LocalText | None = Field(default=None, repr=False)
    crop_id: UUID4 | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    detector_id: LocalText
    detector_version: LocalText

    @model_validator(mode="after")
    def evidence_required(self) -> SensitiveCandidate:
        if self.raw_text is None and self.crop_id is None:
            raise ValueError("Candidate requires local text or crop evidence")
        return self


class ReviewSelection(LocalPrivacyModel):
    """Complete local review set, including explicitly dismissed detections."""

    candidate: SensitiveCandidate
    disposition: Literal["redact", "dismiss"]
    entity_id: UUID4 | None = None
    dismissal_reason: LocalText | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def explicit_decision(self) -> ReviewSelection:
        if self.disposition == "redact":
            if self.entity_id is None or self.dismissal_reason is not None:
                raise ValueError("Redaction requires an explicit entity assignment")
        elif self.dismissal_reason is None or self.entity_id is not None:
            raise ValueError("Dismissal requires a reason and no entity assignment")
        return self


class PrivacyReviewCommand(LocalPrivacyModel):
    """Local consumer input; no actor, approval, verified flag or workflow state."""

    source: LocalSourceSnapshot
    selection_revision: Revision
    policy_digest: Digest
    selections: tuple[ReviewSelection, ...]
    reviewed_pages: tuple[int, ...]

    @model_validator(mode="after")
    def valid_selection(self) -> PrivacyReviewCommand:
        ids = [s.candidate.candidate_id for s in self.selections]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate privacy candidate")
        if len(self.reviewed_pages) != len(set(self.reviewed_pages)) or any(
            p < 1 or p > len(self.source.pages) for p in self.reviewed_pages
        ):
            raise ValueError("Invalid reviewed pages")
        for selection in self.selections:
            validate_region(selection.candidate.region, self.source.pages)
        return self


def privacy_review_digest(command: PrivacyReviewCommand) -> str:
    """Local-only binding; includes source identity, policy, evidence and decisions.

    Canonicalization: privacy-review-json-v1, JSON mode, sorted keys, ASCII escapes,
    compact separators, UTF-8, SHA-256. Array order remains significant.
    """
    command = PrivacyReviewCommand.model_validate(command)
    value = json.dumps(command.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class LocalPrivacyApproval(LocalPrivacyModel):
    """Authority-adapter output, not a credential supplied by a consumer."""

    approval_id: UUID4
    case_id: UUID4
    snapshot_id: UUID4
    review_digest: Digest
    principal_id: LocalText
    approved_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def validity_window(self) -> LocalPrivacyApproval:
        if self.expires_at <= self.approved_at:
            raise ValueError("Invalid privacy approval window")
        return self


class PlaceholderOccurrence(PrivacyModel):
    occurrence_id: UUID4
    entity_id: UUID4
    region: PrivacyRegion


def placeholder_text(entity_id: UUID4) -> str:
    """Render an already-issued random entity ID; never derive it from raw values."""
    return f"PT_{entity_id.hex}"


class PrivacyManifest(PrivacyModel):
    """Allowlisted sanitized identity only; DTO validity does not permit upload.

    IDs must be issued randomly by the future trusted adapter. UUID shape cannot
    prove randomness. Repeated entity IDs are legal; occurrence IDs are unique.
    """

    schema_version: Literal["privacy-v1"] = "privacy-v1"
    processor_version: Literal["privacy-processor-v1"] = "privacy-processor-v1"
    policy_version: Literal["privacy-policy-v1"] = "privacy-policy-v1"
    case_id: UUID4
    document_id: UUID4
    sanitized_digest: Digest
    byte_size: int = Field(gt=0)
    filename: Literal["sanitized.pdf"] = "sanitized.pdf"
    media_type: Literal["application/pdf"] = "application/pdf"
    pages: tuple[PrivacyPage, ...] = Field(min_length=1)
    occurrences: tuple[PlaceholderOccurrence, ...]

    @model_validator(mode="after")
    def valid_occurrences(self) -> PrivacyManifest:
        validate_pages(self.pages)
        ids = [o.occurrence_id for o in self.occurrences]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate placeholder occurrence")
        for occurrence in self.occurrences:
            validate_region(occurrence.region, self.pages)
        return self


def public_manifest_json(manifest: PrivacyManifest) -> str:
    """Serialize only the exact public type; reject local objects and subclasses."""
    if type(manifest) is not PrivacyManifest:
        raise ValueError("Expected public privacy manifest")
    checked = PrivacyManifest.model_validate(manifest)
    return checked.model_dump_json()


class EncryptedMappingEnvelope(LocalPrivacyModel):
    """Local AEAD storage format. Structure alone never proves authenticated encryption."""

    algorithm: Literal["AES-256-GCM"] = "AES-256-GCM"
    case_id: UUID4
    map_id: UUID4
    key_reference: UUID4
    nonce_hex: Annotated[str, Field(pattern=r"^[a-f0-9]{24}$")]
    ciphertext_hex: Annotated[str, Field(pattern=r"^(?:[a-f0-9]{2}){16,}$", repr=False)]
    context_version: Literal["privacy-map-aad-v1"] = "privacy-map-aad-v1"
    expires_at: AwareDatetime


class RehydrationField(LocalPrivacyModel):
    occurrence_id: UUID4
    entity_id: UUID4
    output_field_id: UUID4
    destination: PrivacyRegion | None
    operation: Literal["restore_text", "restore_original_crop", "omit"]

    @model_validator(mode="after")
    def explicit_omission(self) -> RehydrationField:
        if (self.operation == "omit") != (self.destination is None):
            raise ValueError("Only an explicit omission has no destination")
        return self


class RehydrationPlan(LocalPrivacyModel):
    """Proposed local output plan; trusted publisher/writer authorization required."""

    case_id: UUID4
    document_id: UUID4
    map_id: UUID4
    run_id: UUID4
    revision_id: UUID4
    artifact_digest: Digest
    template_digest: Digest
    writer_contract: Literal["privacy-refill-v1"] = "privacy-refill-v1"
    pages: tuple[PrivacyPage, ...] = Field(min_length=1)
    fields: tuple[RehydrationField, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_destinations(self) -> RehydrationPlan:
        validate_pages(self.pages)
        for name in ("occurrence_id", "output_field_id"):
            ids = [getattr(field, name) for field in self.fields]
            if len(ids) != len(set(ids)):
                raise ValueError("Duplicate refill occurrence or field")
        for field in self.fields:
            if field.destination is not None:
                validate_region(field.destination, self.pages)
        return self
