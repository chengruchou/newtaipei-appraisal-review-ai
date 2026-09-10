"""Amazon Bedrock explanation adapter."""

from typing import Any

from appraisal_review.domain.models import CanonicalCase, Finding
from appraisal_review.ports.document_extraction import ExtractionBoundaryError


class BedrockExplanationGenerator:
    def __init__(self, client: Any, *, model_id: str) -> None:
        if not model_id:
            raise ValueError("model_id must be supplied by runtime configuration")
        self._client = client
        self._model_id = model_id

    @classmethod
    def from_default_session(
        cls, *, region_name: str, model_id: str
    ) -> "BedrockExplanationGenerator":
        raise ExtractionBoundaryError("privacy_unavailable")

    async def explain(self, case: CanonicalCase, findings: list[Finding]) -> str:
        """Closed until a separately reviewed sanitized findings projection exists."""
        raise ExtractionBoundaryError("privacy_unavailable")
