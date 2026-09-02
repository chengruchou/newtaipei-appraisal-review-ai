"""Typed contracts for portable factor rules and deterministic evaluation."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from appraisal_review.domain.models import EvidenceRef


class StrictFactorModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Grade(StrEnum):
    EXCELLENT = "excellent"
    SLIGHTLY_SUPERIOR = "slightly_superior"
    NORMAL = "normal"
    SLIGHTLY_INFERIOR = "slightly_inferior"
    INFERIOR = "inferior"


class EvaluationStatus(StrEnum):
    VERIFIED = "verified"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"


class ExtractionStatus(StrEnum):
    EXTRACTED = "extracted"
    NEEDS_REVIEW = "needs_review"
    MISSING = "missing"


class WorkflowStatus(StrEnum):
    RECEIVED = "received"
    NEEDS_REVIEW = "needs_review"
    VERIFIED = "verified"
    COMPLETED = "completed"
    FAILED = "failed"


class NormalizedValue(StrictFactorModel):
    type: Literal["number", "text", "category", "boolean"]
    value: str | float | bool
    unit: str | None = None

    @model_validator(mode="after")
    def validate_value_type(self) -> NormalizedValue:
        if self.type == "number" and (
            isinstance(self.value, bool) or not isinstance(self.value, float)
        ):
            raise ValueError("number values must be numeric and not boolean")
        if self.type == "boolean" and not isinstance(self.value, bool):
            raise ValueError("boolean values must be boolean")
        if self.type in {"text", "category"} and not isinstance(self.value, str):
            raise ValueError(f"{self.type} values must be strings")
        return self


class ExtractedFact(StrictFactorModel):
    factor_id: str
    raw_text: str | None
    normalized_value: NormalizedValue | None
    evidence: list[EvidenceRef] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    status: ExtractionStatus


class RuleApplicability(StrictFactorModel):
    jurisdiction: str
    land_use_category: str
    effective_from: date | None = None
    effective_to: date | None = None

    @model_validator(mode="after")
    def validate_dates(self) -> RuleApplicability:
        if (
            self.effective_from is not None
            and self.effective_to is not None
            and self.effective_to < self.effective_from
        ):
            raise ValueError("effective_to must not precede effective_from")
        return self


class RuleSource(StrictFactorModel):
    document_id: str
    content_hash: str
    pages: list[int] = Field(default_factory=list)


class IntervalBand(StrictFactorModel):
    grade: Grade
    minimum: float | None = None
    minimum_inclusive: bool = True
    maximum: float | None = None
    maximum_inclusive: bool = False

    @model_validator(mode="after")
    def validate_bounds(self) -> IntervalBand:
        if self.minimum is not None and self.maximum is not None and self.minimum >= self.maximum:
            raise ValueError("interval minimum must be less than maximum")
        return self


class CategoryBand(StrictFactorModel):
    grade: Grade
    values: list[str] = Field(min_length=1)


class CorrectionMatrix(StrictFactorModel):
    row_axis: Literal["target_grade"] = "target_grade"
    column_axis: Literal["comparable_grade"] = "comparable_grade"
    values: dict[str, dict[str, float]]


class FactorRule(StrictFactorModel):
    id: str
    factor_id: str
    kind: Literal["numeric_interval", "distance_interval", "category"]
    unit: str | None = None
    intervals: list[IntervalBand] = Field(default_factory=list)
    categories: list[CategoryBand] = Field(default_factory=list)
    correction_matrix: CorrectionMatrix
    critical: bool = True

    @model_validator(mode="after")
    def validate_definition(self) -> FactorRule:
        if self.kind in {"numeric_interval", "distance_interval"}:
            if not self.intervals or self.categories:
                raise ValueError("interval rules require intervals and no categories")
            self._validate_intervals()
            grades = {band.grade.value for band in self.intervals}
        else:
            if not self.categories or self.intervals:
                raise ValueError("category rules require categories and no intervals")
            aliases = [
                value.strip().casefold() for band in self.categories for value in band.values
            ]
            if len(aliases) != len(set(aliases)):
                raise ValueError("category aliases must be unique within a rule")
            grades = {band.grade.value for band in self.categories}
        self._validate_matrix(grades)
        return self

    def _validate_intervals(self) -> None:
        if self.intervals[0].minimum is not None or self.intervals[-1].maximum is not None:
            raise ValueError("intervals must cover values from negative to positive infinity")
        for previous, current in zip(self.intervals, self.intervals[1:], strict=False):
            if previous.maximum != current.minimum:
                raise ValueError("intervals must be contiguous and ordered")
            if previous.maximum_inclusive == current.minimum_inclusive:
                raise ValueError("a shared interval boundary must belong to exactly one band")

    def _validate_matrix(self, grades: set[str]) -> None:
        rows = self.correction_matrix.values
        if set(rows) != grades:
            raise ValueError("matrix rows must match every classification grade")
        if any(set(row) != grades for row in rows.values()):
            raise ValueError("matrix columns must match every classification grade")


class FactorRuleSet(StrictFactorModel):
    rule_set_id: str
    version: str
    status: Literal["candidate", "approved", "rejected"]
    applicability: RuleApplicability
    source_document: RuleSource
    rules: list[FactorRule] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_rules(self) -> FactorRuleSet:
        ids = [rule.id for rule in self.rules]
        factor_ids = [rule.factor_id for rule in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError("rule IDs must be unique")
        if len(factor_ids) != len(set(factor_ids)):
            raise ValueError("factor IDs must be unique within a rule set")
        return self


class FactorObservation(StrictFactorModel):
    raw_text: str | None = None
    value: NormalizedValue | None = None
    evidence: list[EvidenceRef] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class FactorPair(StrictFactorModel):
    factor_id: str
    target: FactorObservation
    comparable: FactorObservation


class FactorEvaluationRequest(StrictFactorModel):
    case_id: str
    rule_set_id: str
    factors: list[FactorPair]


class FactorEvaluationResult(StrictFactorModel):
    factor_id: str
    target_grade: Grade | None = None
    comparable_grade: Grade | None = None
    adjustment_percent: float | None = None
    rule_id: str | None = None
    calculation_trace: str
    status: EvaluationStatus
    warnings: list[str] = Field(default_factory=list)


class EvaluationSummary(StrictFactorModel):
    total_adjustment_percent: float | None
    status: EvaluationStatus


class FactorReviewResult(StrictFactorModel):
    case_id: str
    rule_set_id: str
    rule_version: str
    results: list[FactorEvaluationResult]
    summary: EvaluationSummary


class VerificationReport(StrictFactorModel):
    status: EvaluationStatus
    critical_errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def can_complete(self) -> bool:
        return self.status is EvaluationStatus.VERIFIED and not self.critical_errors


class PDFField(StrictFactorModel):
    field_id: str
    page: int = Field(ge=1)
    bounding_box: tuple[float, float, float, float]
    max_characters: int | None = Field(default=None, ge=1)


class PDFFieldMap(StrictFactorModel):
    template_id: str
    page_numbering: Literal["one_based"] = "one_based"
    coordinate_system: Literal["pdf_bottom_left"] = "pdf_bottom_left"
    fields: list[PDFField]

    def lookup(self, field_id: str) -> PDFField:
        matches = [field for field in self.fields if field.field_id == field_id]
        if len(matches) != 1:
            raise KeyError(f"expected exactly one PDF field mapping for {field_id!r}")
        return matches[0]


class AuditEvent(StrictFactorModel):
    case_id: str
    sequence: int = Field(ge=1)
    event_type: str
    status: str
    tool: str
    rule_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class AgentReviewRequest(StrictFactorModel):
    case_id: str
    criteria_document_uri: str
    case_document_uri: str
    output_pdf_uri: str | None = None
    field_map: PDFFieldMap | None = None


class AgentReviewRun(StrictFactorModel):
    case_id: str
    status: WorkflowStatus
    review: FactorReviewResult | None = None
    verification: VerificationReport | None = None
    output_pdf_uri: str | None = None
    audit_events: list[AuditEvent] = Field(default_factory=list)
