"""Local consumer contracts; none of these records is an authorization credential."""

from dataclasses import dataclass, field
from typing import Literal

from pydantic import UUID4, Field

from appraisal_review.domain.privacy_models import (
    Digest,
    LocalPrivacyModel,
    LocalSourceSnapshot,
    LocalText,
    PrivacyRegion,
    PrivacyReviewCommand,
    ReviewSelection,
    Revision,
    SensitiveCategory,
)
from appraisal_review.domain.privacy_scan import PrivacyScanReport


class ReviewVersion(LocalPrivacyModel):
    case_id: UUID4
    snapshot_id: UUID4
    revision: Revision


class AddPrivacyRegion(ReviewVersion):
    region: PrivacyRegion
    category: SensitiveCategory


class EditPrivacyRegion(AddPrivacyRegion):
    candidate_id: UUID4
    entity_id: UUID4 | None = None


class RemovePrivacyRegion(ReviewVersion):
    candidate_id: UUID4
    reason: LocalText = Field(repr=False)


class ReviewPrivacyPage(ReviewVersion):
    page: int = Field(ge=1)


class ConfirmPrivacyReview(ReviewVersion):
    review_digest: Digest


class PrivacyCropRequest(ReviewVersion):
    crop_id: UUID4


class PrivacyCropReference(LocalPrivacyModel):
    """Resolved crop evidence; fetch the source page preview and apply this region."""

    crop_id: UUID4
    source: LocalSourceSnapshot
    region: PrivacyRegion


class PrivacyReviewEvent(LocalPrivacyModel):
    revision: Revision
    action: Literal["scan", "add", "edit", "remove", "review_page"]
    before: ReviewSelection | None = Field(default=None, repr=False)
    after: ReviewSelection | None = Field(default=None, repr=False)
    page: int | None = None


class PrivacyReviewView(LocalPrivacyModel):
    """Only for the trusted local consumer; includes original text and decisions."""

    state: Literal["blocked", "awaiting_confirmation", "confirmed"]
    command: PrivacyReviewCommand = Field(repr=False)
    scan: PrivacyScanReport | None = Field(repr=False)
    events: tuple[PrivacyReviewEvent, ...] = Field(repr=False)


@dataclass(frozen=True)
class PrivacyPagePreview:
    """In-memory PNG of the owned original, with crop-local unrotated geometry."""

    width: int
    height: int
    png: bytes = field(repr=False)
