"""FastAPI entry point for local validation and future AWS deployment."""

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict

from appraisal_review.domain.models import CanonicalCase, ReviewResult, RuleSet
from appraisal_review.domain.rule_engine import RuleEngine

app = FastAPI(
    title="Agentic AI Real Estate Valuation Reviewer",
    version="0.1.0",
    description="Local deterministic validation baseline for valuation review cases.",
)


class ValidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case: CanonicalCase
    rule_set: RuleSet


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/validate", response_model=ReviewResult)
def validate(request: ValidationRequest) -> ReviewResult:
    return RuleEngine(request.rule_set).evaluate(request.case)
