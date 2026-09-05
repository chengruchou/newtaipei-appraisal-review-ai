"""Review schema 2.0: inventory, observations and trusted policy are separate inputs."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from appraisal_review.domain.document_models import (
    Digest,
    DocumentModel,
    SourceCitation,
    SourceRegistry,
)


class ComparisonContext(DocumentModel):
    scope: Literal["regional", "individual"]
    target_id: str = Field(min_length=1)
    comparable_id: str = Field(min_length=1)

    def key(self) -> str:
        return json.dumps([self.scope, self.target_id, self.comparable_id], separators=(",", ":"))


class CaseIdentity(DocumentModel):
    case_id: str
    version: str
    district: str
    zone: str
    land_use_category: str
    effective_date: date


class ReviewSlot(DocumentModel):
    """A required field from the reviewed inventory, never inferred from successful output."""

    id: str
    context: ComparisonContext
    factor_id: str | None = None
    value: Literal["target_grade", "comparable_grade", "adjustment_percent", "subtotal", "total"]
    derivable_blank: bool = False
    evidence: list[SourceCitation] = Field(min_length=1)


class InventoryContext(DocumentModel):
    context: ComparisonContext
    factor_ids: list[str] = Field(min_length=1)
    evidence: list[SourceCitation] = Field(min_length=1)


class EmptyColumn(DocumentModel):
    id: str
    evidence: list[SourceCitation] = Field(min_length=1)


class ArithmeticCheck(DocumentModel):
    id: str
    kind: Literal["sum", "equals"]
    inputs: list[str] = Field(min_length=1)
    target: str
    quantum: Decimal = Field(default=Decimal("0.01"), gt=0)
    tolerance: Decimal = Field(default=Decimal("0"), ge=0)
    evidence: list[SourceCitation] = Field(min_length=1)

    @model_validator(mode="after")
    def exact_equals(self) -> ArithmeticCheck:
        if self.kind == "equals" and len(self.inputs) != 1:
            raise ValueError("equals requires exactly one input")
        if self.quantum.normalize().as_tuple().digits != (1,):
            raise ValueError("quantum must be a decimal power of ten")
        return self


class ReviewInventory(DocumentModel):
    contexts: list[InventoryContext] = Field(min_length=1)
    slots: list[ReviewSlot] = Field(min_length=1)
    checks: list[ArithmeticCheck] = Field(default_factory=list)
    empty_columns: list[EmptyColumn] = Field(default_factory=list)
    inspected_pages: dict[str, list[int]]
    inspected_tables: dict[str, list[str]] = Field(default_factory=dict)
    unresolved: list[str] = Field(default_factory=list)
    unsupported: list[str] = Field(default_factory=list)


class ObservedValue(DocumentModel):
    slot_id: str
    state: Literal["present", "blank", "missing", "not_present", "not_applicable"]
    value: str | Decimal | None = None
    unit: Literal["percent_points", "ratio", "grade"] | None = None
    raw_text: str
    evidence: list[SourceCitation] = Field(default_factory=list)

    @model_validator(mode="after")
    def state_value(self) -> ObservedValue:
        if (self.state == "present") != (self.value is not None):
            raise ValueError("only present values carry a value, including zero")
        return self


class Reliability(DocumentModel):
    model_confidence: float | None = Field(default=None, ge=0, le=1)
    method: Literal["native_numeric", "reviewer_confirmed", "model_proposed"]
    selection: Literal["checked", "unchecked", "ambiguous", "not_applicable"]
    unresolved: list[str] = Field(default_factory=list)


class ReviewFinding(DocumentModel):
    id: str
    kind: str
    status: Literal["verified", "needs_review", "failed"]
    context: ComparisonContext | None = None
    factor_id: str | None = None
    rule_id: str | None = None
    rule_version: str | None = None
    observed: str | None = None
    expected: str | None = None
    evidence: list[SourceCitation] = Field(default_factory=list)
    trace: str


class Coverage(DocumentModel):
    required: list[str]
    verified: list[str]
    missing: list[str]
    unsupported: list[str] = Field(default_factory=list)


class ReviewBinding(DocumentModel):
    schema_version: Literal["2.0"] = "2.0"
    identity: CaseIdentity
    registry: SourceRegistry
    inventory: ReviewInventory


def content_digest(value: BaseModel) -> Digest:
    data = json.dumps(value.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode()).hexdigest()
