"""Allowlisted local input configuration shared by preparation and service factories."""

from pathlib import Path

from pydantic import Field

from appraisal_review.adapters.local.pdf_parser import DocumentInput, LocalPDFParser
from appraisal_review.domain.document_models import Digest, DocumentModel
from appraisal_review.domain.review_contracts import CaseIdentity


class InputSpec(DocumentModel):
    path: Path
    document_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    version: str
    role: str
    expected_hash: Digest
    document_date: str | None = None


class InputManifest(DocumentModel):
    identity: CaseIdentity
    documents: list[InputSpec] = Field(min_length=2)

    def parser(self) -> LocalPDFParser:
        specs = []
        for spec in self.documents:
            if spec.role not in {"criteria", "forms", "reference", "brief"}:
                raise ValueError("Unsupported source role")
            specs.append(DocumentInput(**spec.model_dump()))
        return LocalPDFParser(specs)
