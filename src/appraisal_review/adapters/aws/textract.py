"""Thin asynchronous Textract client boundary.

Canonical field mapping intentionally lives outside this client because it is
form- and policy-specific.
"""

from typing import Any

from appraisal_review.ports.document_extraction import ExtractionBoundaryError


class TextractDocumentAnalyzer:
    def __init__(self, client: Any) -> None:
        self._client = client

    @classmethod
    def from_default_session(cls, *, region_name: str) -> "TextractDocumentAnalyzer":
        raise ExtractionBoundaryError("privacy_unavailable")

    def start(self, *, bucket: str, key: str) -> str:
        """Raw unversioned object starts are closed pending Phase 4 integration."""
        raise ExtractionBoundaryError("privacy_unavailable")

    def get_page(self, *, job_id: str, next_token: str | None = None) -> dict[str, Any]:
        raise ExtractionBoundaryError("privacy_unavailable")

    def _get_page(self, *, job_id: str, next_token: str | None = None) -> dict[str, Any]:
        request: dict[str, Any] = {"JobId": job_id, "MaxResults": 1000}
        if next_token:
            request["NextToken"] = next_token
        return dict(self._client.get_document_analysis(**request))
