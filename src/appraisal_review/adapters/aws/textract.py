"""Thin asynchronous Textract client boundary.

Canonical field mapping intentionally lives outside this client because it is
form- and policy-specific.
"""

from typing import Any


class TextractDocumentAnalyzer:
    def __init__(self, client: Any) -> None:
        self._client = client

    @classmethod
    def from_default_session(cls, *, region_name: str) -> "TextractDocumentAnalyzer":
        import boto3  # type: ignore[import-not-found]  # Optional `aws` extra.

        return cls(boto3.client("textract", region_name=region_name))

    def start(self, *, bucket: str, key: str) -> str:
        response = self._client.start_document_analysis(
            DocumentLocation={"S3Object": {"Bucket": bucket, "Name": key}},
            FeatureTypes=["FORMS", "TABLES", "LAYOUT"],
        )
        return str(response["JobId"])

    def get_page(self, *, job_id: str, next_token: str | None = None) -> dict[str, Any]:
        request: dict[str, Any] = {"JobId": job_id, "MaxResults": 1000}
        if next_token:
            request["NextToken"] = next_token
        return dict(self._client.get_document_analysis(**request))
