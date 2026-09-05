"""Amazon Bedrock explanation adapter."""

import json
from typing import Any

from appraisal_review.domain.models import CanonicalCase, Finding


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
        import boto3  # Optional `aws` extra; type stubs are in the `dev` extra.

        return cls(boto3.client("bedrock-runtime", region_name=region_name), model_id=model_id)

    async def explain(self, case: CanonicalCase, findings: list[Finding]) -> str:
        payload = {
            "case_id": case.case_id,
            "rule_version": findings[0].rule_version if findings else None,
            "findings": [finding.model_dump(mode="json") for finding in findings],
        }
        prompt = (
            "Explain the supplied deterministic appraisal-review findings in concise "
            "Traditional Chinese. Do not change any status, expected value, actual value, "
            "or invent missing evidence.\n\n" + json.dumps(payload, ensure_ascii=False)
        )
        response = self._client.converse(
            modelId=self._model_id,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"temperature": 0, "maxTokens": 800},
        )
        return str(response["output"]["message"]["content"][0]["text"])
