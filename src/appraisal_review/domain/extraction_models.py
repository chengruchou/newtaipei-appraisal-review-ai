"""Untrusted page proposals; approval and registry identities remain external."""

from typing import Literal

from pydantic import Field, model_validator

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
    origin: Literal["model", "native", "manual"] = "model"
    model_id: str | None
    region: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    elapsed_seconds: float = Field(ge=0)
    attempts: int = Field(ge=1, le=3)

    @model_validator(mode="after")
    def extraction_origin(self) -> "PageExtraction":
        if self.origin == "model" and not self.model_id:
            raise ValueError("Model extraction requires its actual model identifier")
        if self.origin != "model" and (
            self.model_id is not None or self.input_tokens or self.output_tokens
        ):
            raise ValueError("Local candidates must not claim model execution or token usage")
        return self
