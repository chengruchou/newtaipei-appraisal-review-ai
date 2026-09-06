"""Versioned page/section reference lookup, without retrieval infrastructure."""

from typing import Protocol

from appraisal_review.domain.document_models import SourceCitation


class ReferenceDocuments(Protocol):
    def section(self, section_id: str) -> list[SourceCitation]: ...
