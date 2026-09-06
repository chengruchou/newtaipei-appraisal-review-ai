"""Explicit manual sections and proposed source-backed procedural checks."""

from pydantic import Field

from appraisal_review.domain.document_models import DocumentModel, SourceCitation, SourceRegistry
from appraisal_review.domain.review_contracts import ArithmeticCheck


class ReferenceSection(DocumentModel):
    id: str
    title: str
    evidence: list[SourceCitation] = Field(min_length=1)


class ReferenceCatalog(DocumentModel):
    registry: SourceRegistry
    sections: list[ReferenceSection]

    def section(self, section_id: str) -> list[SourceCitation]:
        matches = [s for s in self.sections if s.id == section_id]
        if len(matches) != 1 or not all(self.registry.resolves(r) for r in matches[0].evidence):
            raise ValueError("Reference section does not resolve uniquely in this version")
        return matches[0].evidence

    def propose_copy_check(
        self, section_id: str, *, id: str, source_slot: str, target_slot: str
    ) -> ArithmeticCheck:
        """A candidate only; exact material still requires trusted applicability review."""
        return ArithmeticCheck(
            id=id,
            kind="equals",
            inputs=[source_slot],
            target=target_slot,
            evidence=self.section(section_id),
        )
