"""Provider-neutral tools coordinated by the review agent."""

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from appraisal_review.domain.factor_models import (
    AuditEvent,
    FactorPair,
    FactorReviewResult,
    FactorRuleSet,
    PDFFieldMap,
)


class ParsedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_uri: str
    page_count: int = Field(ge=1)
    content: dict[str, Any] = Field(default_factory=dict)


class DocumentParser(Protocol):
    async def parse_document(self, document_uri: str) -> ParsedDocument: ...


class FactExtractor(Protocol):
    async def extract_facts(
        self, document: ParsedDocument, *, case_id: str
    ) -> list[FactorPair]: ...


class RuleSetProvider(Protocol):
    async def load_or_build_rules(self, criteria: ParsedDocument) -> FactorRuleSet: ...


class PDFWriter(Protocol):
    async def write_pdf(
        self,
        source_uri: str,
        destination_uri: str,
        result: FactorReviewResult,
        field_map: PDFFieldMap,
    ) -> str: ...


class AuditLogger(Protocol):
    async def append(self, event: AuditEvent) -> None: ...
