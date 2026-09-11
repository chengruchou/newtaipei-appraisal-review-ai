"""Sanitized bytes and public manifest only; no original evidence or upload authority."""

from dataclasses import dataclass, field

from appraisal_review.domain.privacy_models import PrivacyManifest


@dataclass(frozen=True)
class SanitizedBundle:
    pdf: bytes = field(repr=False)
    manifest: PrivacyManifest

    @property
    def filename(self) -> str:
        return "sanitized.pdf"
