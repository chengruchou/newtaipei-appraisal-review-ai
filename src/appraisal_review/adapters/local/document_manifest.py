"""Allowlisted local input configuration shared by preparation and service factories."""

from pathlib import Path

from pydantic import Field, model_validator

from appraisal_review.adapters.local.pdf_parser import DocumentInput, LocalPDFParser
from appraisal_review.domain.document_models import Digest, DocumentModel
from appraisal_review.domain.review_contracts import CaseIdentity
from appraisal_review.domain.source_handling import BatchHandling


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
    handling: BatchHandling = BatchHandling()

    @model_validator(mode="after")
    def handling_covers_known_documents(self) -> "InputManifest":
        unknown = self.handling.covered_document_ids() - {d.document_id for d in self.documents}
        if unknown:
            # An exemption for a document this batch does not contain would silently apply
            # to whichever later batch happens to reuse that identifier.
            raise ValueError(
                f"Handling records name documents outside this batch: {sorted(unknown)}"
            )
        return self

    def privacy_required(self, document_id: str) -> bool:
        """Whether this batch still requires local privacy processing for a document."""
        return self.handling.privacy_required(document_id)

    def parser(self) -> LocalPDFParser:
        specs = []
        for spec in self.documents:
            if spec.role not in {"criteria", "forms", "reference", "brief"}:
                raise ValueError("Unsupported source role")
            specs.append(DocumentInput(**spec.model_dump()))
        return LocalPDFParser(specs)
