"""Document extraction port."""

from typing import Protocol

from appraisal_review.domain.models import CanonicalCase


class DocumentExtractor(Protocol):
    async def extract(self, document_uri: str, *, case_id: str) -> CanonicalCase:
        """Extract and normalize documents into a canonical case."""
        ...
