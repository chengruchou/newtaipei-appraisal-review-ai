"""Local-only OCR measurements and separate authenticated visual-review receipts."""

from datetime import datetime
from typing import Literal

from pydantic import UUID4, Field

from appraisal_review.domain.privacy_models import Digest, LocalPrivacyModel


class OCRReviewPage(LocalPrivacyModel):
    number: int = Field(ge=1)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    image_sha256: Digest


class OCRReviewObservation(LocalPrivacyModel):
    observation_id: UUID4
    page: int = Field(ge=1)
    bbox: tuple[float, float, float, float]
    raw_text: str = Field(repr=False)
    confidence: float | None = Field(ge=0, le=1)
    review_item_id: UUID4 | None = None


class OCRReviewItem(LocalPrivacyModel):
    item_id: UUID4
    kind: Literal["observation", "placeholder"]
    page: int = Field(ge=1)
    bbox: tuple[float, float, float, float]
    observation_ids: tuple[UUID4, ...]
    expected_text: str | None = Field(default=None, repr=False)
    confirmed_reading: str | None = Field(default=None, repr=False)


class OCRReviewReceipt(LocalPrivacyModel):
    receipt_id: UUID4
    review_id: UUID4
    stage: Literal["published", "restored"]
    principal_id: str
    binding_digest: Digest
    review_digest: Digest
    engine_digest: Digest
    input_sha256: Digest
    measurements_digest: Digest
    page_image_sha256: tuple[Digest, ...]
    readings: tuple[OCRReviewItem, ...]
    confirmed_at: datetime
    expires_at: datetime
    authority_scope: Literal["local_visual_ocr_readings_only"] = "local_visual_ocr_readings_only"
    business_authority: Literal["unchanged"] = "unchanged"


class OCRReviewView(LocalPrivacyModel):
    review_id: UUID4
    stage: Literal["published", "restored"]
    review_digest: Digest
    engine_digest: Digest
    input_sha256: Digest
    pages: tuple[OCRReviewPage, ...]
    observations: tuple[OCRReviewObservation, ...]
    items: tuple[OCRReviewItem, ...]
    receipts: tuple[OCRReviewReceipt, ...] = ()


class OCRReviewConfirmation(LocalPrivacyModel):
    review_digest: Digest
    page_image_sha256: Digest
    reading: str = Field(min_length=1, max_length=4096, repr=False)
