"""Canonical data contracts shared by extraction and validation."""

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """Base model that rejects silently misspelled contract fields."""

    model_config = ConfigDict(extra="forbid")


class EvidenceRef(StrictModel):
    """Location and confidence for a value found in a source document."""

    document_id: str
    page: int = Field(ge=1)
    block_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    bounding_box: tuple[float, float, float, float] | None = None


class ExtractedField(StrictModel):
    """A normalized field that retains its original representation."""

    raw_value: str | None
    value: str | int | float | bool | None
    evidence: list[EvidenceRef] = Field(default_factory=list)


class CanonicalCase(StrictModel):
    """Provider-neutral representation of one appraisal review case."""

    case_id: str
    schema_version: str = "0.1.0"
    fields: dict[str, ExtractedField]
    source_versions: dict[str, str] = Field(default_factory=dict)


class CheckStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    NEEDS_REVIEW = "needs_review"


class RuleDefinition(StrictModel):
    """A versioned deterministic validation rule."""

    id: str
    kind: Literal["sum", "equals"]
    description: str
    inputs: list[str] = Field(min_length=1)
    target: str
    tolerance: float = Field(default=0.0, ge=0.0)
    severity: Literal["info", "warning", "error"] = "error"


class RuleSet(StrictModel):
    version: str
    description: str = ""
    rules: list[RuleDefinition]


class Finding(StrictModel):
    rule_id: str
    rule_version: str
    status: CheckStatus
    severity: Literal["info", "warning", "error"]
    message: str
    expected: Any = None
    actual: Any = None
    evidence: list[EvidenceRef] = Field(default_factory=list)


class ReviewResult(StrictModel):
    case_id: str
    schema_version: str
    rule_version: str
    findings: list[Finding]

    @property
    def overall_status(self) -> CheckStatus:
        statuses = {finding.status for finding in self.findings}
        if CheckStatus.FAIL in statuses:
            return CheckStatus.FAIL
        if CheckStatus.NEEDS_REVIEW in statuses:
            return CheckStatus.NEEDS_REVIEW
        return CheckStatus.PASS
