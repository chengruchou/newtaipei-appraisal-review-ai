"""Orchestration for extraction, validation, and optional explanation."""

from dataclasses import dataclass

from appraisal_review.domain.models import ReviewResult
from appraisal_review.domain.rule_engine import RuleEngine
from appraisal_review.ports.explanation import ExplanationGenerator
from appraisal_review.ports.extraction import DocumentExtractor


@dataclass(frozen=True)
class ExplainedReview:
    result: ReviewResult
    explanation: str | None


class ReviewService:
    def __init__(
        self,
        extractor: DocumentExtractor,
        rule_engine: RuleEngine,
        explanation_generator: ExplanationGenerator | None = None,
    ) -> None:
        self.extractor = extractor
        self.rule_engine = rule_engine
        self.explanation_generator = explanation_generator

    async def review(self, document_uri: str, *, case_id: str) -> ExplainedReview:
        case = await self.extractor.extract(document_uri, case_id=case_id)
        result = self.rule_engine.evaluate(case)
        explanation = None
        if self.explanation_generator is not None:
            explanation = await self.explanation_generator.explain(case, result.findings)
        return ExplainedReview(result=result, explanation=explanation)
