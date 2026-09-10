"""Local scan evidence and coverage; none of these records may enter cloud DTOs."""

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from appraisal_review.domain.privacy_models import (
    Digest,
    LocalSourcePage,
    LocalSourceSnapshot,
    LocalText,
    PrivacyModel,
    PrivacyRegion,
    SensitiveCandidate,
    SensitiveCategory,
    validate_region,
)


class ScanIssue(StrEnum):
    OCR_UNAVAILABLE = "ocr_unavailable"
    OCR_EMPTY = "ocr_empty"
    OCR_FAILED = "ocr_failed"
    LOW_CONFIDENCE = "low_confidence"
    TIMEOUT = "processing_timeout"
    RESOURCE_LIMIT = "resource_limit"
    INVALID_INPUT = "invalid_scan_input"


class TextObservation(PrivacyModel):
    """Native block or OCR word; confidence is unmodified, or unknown for native text."""

    text: LocalText = Field(repr=False)
    region: PrivacyRegion
    origin: Literal["native", "ocr"]
    confidence: float | None = Field(default=None, ge=0, le=1)


class PageScan(PrivacyModel):
    page: int = Field(ge=1)
    mode: Literal["native", "scanned", "mixed"]
    status: Literal["processed", "blocked"]
    issues: tuple[ScanIssue, ...] = ()
    observations: tuple[TextObservation, ...] = Field(default=(), repr=False)
    ocr_engine_version: LocalText | None = None
    ocr_model_digests: tuple[Digest, ...] = ()
    manual_categories: tuple[SensitiveCategory, ...] = tuple(SensitiveCategory)

    @model_validator(mode="after")
    def truthful_coverage(self) -> "PageScan":
        blockers = set(self.issues) - {ScanIssue.LOW_CONFIDENCE}
        if (self.status == "blocked") != bool(blockers):
            raise ValueError("Page status must match processing blockers")
        if set(self.manual_categories) != set(SensitiveCategory):
            raise ValueError("Every page requires complete manual privacy review")
        if any(o.region.page != self.page for o in self.observations):
            raise ValueError("Observation belongs to another page")
        if self.status == "processed" and not self.observations:
            raise ValueError("Empty extraction is not a processed page")
        has_ocr = any(o.origin == "ocr" for o in self.observations)
        if self.mode == "native" and has_ocr:
            raise ValueError("Native-only pages cannot claim OCR observations")
        if (
            self.status == "processed"
            and self.mode != "native"
            and (not has_ocr or self.ocr_engine_version is None or not self.ocr_model_digests)
        ):
            raise ValueError("Image-page completion requires OCR provenance")
        return self


class PrivacyScanReport(PrivacyModel):
    schema_version: Literal["local-privacy-scan-v1"] = "local-privacy-scan-v1"
    source: LocalSourceSnapshot
    native_engine_version: LocalText
    status: Literal["needs_review", "blocked"]
    pages: tuple[PageScan, ...] = Field(min_length=1)
    candidates: tuple[SensitiveCandidate, ...] = ()

    @model_validator(mode="after")
    def full_coverage(self) -> "PrivacyScanReport":
        if tuple(p.page for p in self.pages) != tuple(p.number for p in self.source.pages):
            raise ValueError("Scan report must account for every source page")
        if (self.status == "blocked") != any(p.status == "blocked" for p in self.pages):
            raise ValueError("Scan status must match page blockers")
        ids = [c.candidate_id for c in self.candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate scan candidate")
        for page in self.pages:
            for observation in page.observations:
                validate_region(observation.region, self.source.pages)
        for candidate in self.candidates:
            validate_region(candidate.region, self.source.pages)
        return self


class PrivacyCapabilities(PrivacyModel):
    schema_version: Literal["local-privacy-scan-v1"] = "local-privacy-scan-v1"
    native_pdf: bool
    local_ocr: bool
    languages: tuple[Literal["eng", "chi_tra"], ...] = ()
    engine_version: str | None = None
    runtime_platform: Literal["linux", "development"]
    visual_detector: Literal["manual_review_required"] = "manual_review_required"
    network_isolation: Literal["not_verified"] = "not_verified"


def pixel_region(
    box: tuple[float, float, float, float], width: int, height: int, page: LocalSourcePage
) -> PrivacyRegion:
    """Map actual unrotated raster dimensions, including DPI rounding, to PDF points."""
    if width <= 0 or height <= 0:
        raise ValueError("Invalid raster dimensions")
    x0, y0, x1, y1 = box
    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
        raise ValueError("Pixel region outside raster")
    return PrivacyRegion(
        page=page.number,
        bbox=(
            x0 * page.width / width,
            (height - y1) * page.height / height,
            x1 * page.width / width,
            (height - y0) * page.height / height,
        ),
    )
