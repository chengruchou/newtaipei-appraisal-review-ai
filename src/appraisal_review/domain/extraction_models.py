"""Untrusted page proposals; approval and registry identities remain external."""

from typing import Literal

from pydantic import Field

from appraisal_review.domain.document_models import DocumentModel, SourceCitation
from appraisal_review.domain.factor_models import EvidencedPair, FactorRule
from appraisal_review.domain.review_contracts import (
    ArithmeticCheck,
    EmptyColumn,
    InventoryContext,
    ObservedValue,
    ReviewSlot,
)


class ProposedRule(DocumentModel):
    scope: Literal["regional", "individual"]
    rule: FactorRule
    evidence: list[SourceCitation] = Field(min_length=1)
    unresolved: list[str] = Field(default_factory=list)


class PageProposal(DocumentModel):
    """Only proposed values, never approval receipts or executable instructions."""

    accounted_table_ids: list[str] = Field(default_factory=list)
    rules: list[ProposedRule] = Field(default_factory=list)
    contexts: list[InventoryContext] = Field(default_factory=list)
    pairs: list[EvidencedPair] = Field(default_factory=list)
    observed: list[ObservedValue] = Field(default_factory=list)
    slots: list[ReviewSlot] = Field(default_factory=list)
    checks: list[ArithmeticCheck] = Field(default_factory=list)
    empty_columns: list[EmptyColumn] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    unsupported: list[str] = Field(default_factory=list)


class PageExtraction(DocumentModel):
    document_id: str
    page: int = Field(ge=1)
    content_hash: str
    proposal: PageProposal
    model_id: str
    region: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    elapsed_seconds: float = Field(ge=0)
    attempts: int = Field(ge=1, le=3)
