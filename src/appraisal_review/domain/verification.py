"""Independent completion gate; result status and metadata are untrusted claims."""

from pydantic import ValidationError

from appraisal_review.domain.factor_engine import FactorRuleEngine, validate_minimum_confidence
from appraisal_review.domain.factor_models import (
    EvaluationStatus,
    FactorEvaluationRequest,
    FactorReviewResult,
    FactorRuleSet,
    VerificationReport,
)


class ReviewVerifier:
    def verify(
        self,
        result: FactorReviewResult,
        rule_set: FactorRuleSet,
        *,
        facts: FactorEvaluationRequest | None = None,
        minimum_confidence: float = 0.85,
    ) -> VerificationReport:
        minimum_confidence = validate_minimum_confidence(minimum_confidence)
        errors: list[str] = []
        failed = False
        ids = [r.factor_id for r in result.results]
        for rule in rule_set.rules:
            if rule.critical and rule.factor_id not in ids:
                errors.append(f"Missing critical factor result: {rule.factor_id}")
        if len(ids) != len(set(ids)):
            errors.append("Duplicate factor results")
            failed = True
        if (result.rule_set_id, result.rule_version) != (rule_set.rule_set_id, rule_set.version):
            errors.append("Rule identity or version mismatch")
            failed = True
        if facts is None:
            errors.append("Trusted case facts and applicability verification are required")
        else:
            try:
                rules = FactorRuleSet.model_validate(rule_set.model_dump())
                inputs = FactorEvaluationRequest.model_validate(facts.model_dump())
                claimed = FactorReviewResult.model_validate(result.model_dump())
                expected = FactorRuleEngine(rules, minimum_confidence=minimum_confidence).evaluate(
                    inputs
                )
                # Context/source bindings are checked by the case gate, not derived from result.
                if (claimed.case_id, claimed.results, claimed.summary) != (
                    expected.case_id,
                    expected.results,
                    expected.summary,
                ):
                    errors.append("Result differs from independent fact/rule recalculation")
                    failed = True
                if expected.summary.status is not EvaluationStatus.VERIFIED:
                    errors.append("Independent calculation is unresolved or failed")
                    failed = failed or expected.summary.status is EvaluationStatus.FAILED
            except (ValidationError, ValueError, ArithmeticError):
                errors.append("Invalid rules, facts or result")
                failed = True
        status = (
            EvaluationStatus.FAILED
            if failed
            else EvaluationStatus.NEEDS_REVIEW
            if errors
            else EvaluationStatus.VERIFIED
        )
        return VerificationReport(status=status, critical_errors=errors)
