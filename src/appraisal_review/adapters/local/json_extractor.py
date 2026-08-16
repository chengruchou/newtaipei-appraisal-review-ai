"""Load a pre-normalized canonical case from local JSON."""

import json
from pathlib import Path

from appraisal_review.domain.models import CanonicalCase


class JsonDocumentExtractor:
    async def extract(self, document_uri: str, *, case_id: str) -> CanonicalCase:
        path = Path(document_uri.removeprefix("file://"))
        with path.open(encoding="utf-8") as stream:
            payload = json.load(stream)
        payload["case_id"] = case_id
        return CanonicalCase.model_validate(payload)
