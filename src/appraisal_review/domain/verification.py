"""Completion gate for typed factor review results."""

from appraisal_review.domain.factor_models import (
    EvaluationStatus,
    FactorReviewResult,
    FactorRuleSet,
    VerificationReport,
)


class ReviewVerifier:
    def verify(self, result: FactorReviewResult, rule_set: FactorRuleSet) -> VerificationReport:
        critical_factors = {rule.factor_id for rule in rule_set.rules if rule.critical}
        result_by_factor = {item.factor_id: item for item in result.results}
        errors: list[str] = []
        warnings: list[str] = []
        has_failed = result.summary.status is EvaluationStatus.FAILED
        has_unresolved = result.summary.status is EvaluationStatus.NEEDS_REVIEW

        for factor_id in critical_factors:
            item = result_by_factor.get(factor_id)
            if item is None:
                errors.append(f"Missing critical factor result: {factor_id}")
                has_unresolved = True
            elif item.status is not EvaluationStatus.VERIFIED:
                errors.append(f"Critical factor is not verified: {factor_id}")
                has_failed = has_failed or item.status is EvaluationStatus.FAILED
                has_unresolved = has_unresolved or item.status is EvaluationStatus.NEEDS_REVIEW
        for item in result.results:
            warnings.extend(item.warnings)

        if has_failed:
            status = EvaluationStatus.FAILED
        elif has_unresolved:
            status = EvaluationStatus.NEEDS_REVIEW
        else:
            status = EvaluationStatus.VERIFIED
        return VerificationReport(status=status, critical_errors=errors, warnings=warnings)
