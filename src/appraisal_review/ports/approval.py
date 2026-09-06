"""Server-injected trust boundary. A request or model cannot supply this authority."""

from typing import Protocol

from appraisal_review.domain.factor_models import ReviewMaterial


class ReviewAuthorization(Protocol):
    def permits(self, material: ReviewMaterial) -> bool:
        """Verify approval of exact policy, inventory, sources, facts and case version."""
        ...
