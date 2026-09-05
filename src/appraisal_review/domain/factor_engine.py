"""Deterministic classification and correction-matrix evaluation."""

from decimal import Decimal, InvalidOperation

from appraisal_review.domain.factor_models import (
    EvaluationStatus,
    EvaluationSummary,
    FactorEvaluationRequest,
    FactorEvaluationResult,
    FactorObservation,
    FactorReviewResult,
    FactorRule,
    FactorRuleSet,
    Grade,
    IntervalBand,
)

_LENGTH_IN_METRES = {
    "mm": Decimal("0.001"),
    "cm": Decimal("0.01"),
    "m": Decimal("1"),
    "km": Decimal("1000"),
}


class ObservationNeedsReview(ValueError):
    """Raised when an observation cannot be safely evaluated."""


def validate_minimum_confidence(value: float) -> float:
    if not 0.0 <= value <= 1.0:
        raise ValueError("minimum_confidence must be finite and between 0 and 1")
    return value


class FactorRuleEngine:
    def __init__(
        self,
        rule_set: FactorRuleSet,
        *,
        minimum_confidence: float = 0.85,
        confirmed_sides: frozenset[tuple[str, str]] = frozenset(),
    ) -> None:
        if rule_set.status != "approved":
            raise ValueError("only approved factor rule sets can be evaluated")
        self.confirmed_sides = confirmed_sides
        self.rule_set = rule_set
        self.minimum_confidence = validate_minimum_confidence(minimum_confidence)
        self._rules = {rule.factor_id: rule for rule in rule_set.rules}

    def evaluate(self, request: FactorEvaluationRequest) -> FactorReviewResult:
        if request.rule_set_id != self.rule_set.rule_set_id:
            raise ValueError("evaluation request rule_set_id does not match the loaded rule set")

        results = [
            self._evaluate_pair(pair.factor_id, pair.target, pair.comparable)
            for pair in request.factors
        ]
        statuses = {result.status for result in results}
        if EvaluationStatus.FAILED in statuses:
            summary_status = EvaluationStatus.FAILED
        elif EvaluationStatus.NEEDS_REVIEW in statuses:
            summary_status = EvaluationStatus.NEEDS_REVIEW
        else:
            summary_status = EvaluationStatus.VERIFIED
        total = (
            float(sum(Decimal(str(result.adjustment_percent)) for result in results))
            if summary_status is EvaluationStatus.VERIFIED
            else None
        )
        return FactorReviewResult(
            case_id=request.case_id,
            rule_set_id=self.rule_set.rule_set_id,
            rule_version=self.rule_set.version,
            results=results,
            summary=EvaluationSummary(total_adjustment_percent=total, status=summary_status),
        )

    def _evaluate_pair(
        self,
        factor_id: str,
        target: FactorObservation,
        comparable: FactorObservation,
    ) -> FactorEvaluationResult:
        rule = self._rules.get(factor_id)
        if rule is None:
            return FactorEvaluationResult(
                factor_id=factor_id,
                calculation_trace="No rule exists for the requested factor ID.",
                status=EvaluationStatus.FAILED,
                warnings=[f"Unknown factor ID: {factor_id}"],
            )
        try:
            target_grade = self._classify(rule, target, side="target")
            comparable_grade = self._classify(rule, comparable, side="comparable")
        except ObservationNeedsReview as error:
            return FactorEvaluationResult(
                factor_id=factor_id,
                rule_id=rule.id,
                calculation_trace="Evaluation stopped before matrix lookup.",
                status=EvaluationStatus.NEEDS_REVIEW,
                warnings=[str(error)],
            )

        try:
            adjustment = rule.correction_matrix.values[target_grade.value][comparable_grade.value]
        except KeyError:
            return FactorEvaluationResult(
                factor_id=factor_id,
                target_grade=target_grade,
                comparable_grade=comparable_grade,
                rule_id=rule.id,
                calculation_trace="Classification succeeded but the matrix cell was missing.",
                status=EvaluationStatus.FAILED,
                warnings=["Correction matrix lookup failed."],
            )
        return FactorEvaluationResult(
            factor_id=factor_id,
            target_grade=target_grade,
            comparable_grade=comparable_grade,
            adjustment_percent=adjustment,
            rule_id=rule.id,
            calculation_trace=(
                "Classified target and comparable observations, then queried "
                "the target-grade row and comparable-grade column."
            ),
            status=EvaluationStatus.VERIFIED,
        )

    def _classify(self, rule: FactorRule, observation: FactorObservation, *, side: str) -> Grade:
        if observation.value is None:
            raise ObservationNeedsReview(f"{side} value is missing")
        if not observation.evidence:
            raise ObservationNeedsReview(f"{side} evidence is missing")
        if (
            rule.factor_id,
            side,
        ) not in self.confirmed_sides and observation.confidence < self.minimum_confidence:
            raise ObservationNeedsReview(
                f"{side} confidence {observation.confidence} is below {self.minimum_confidence}"
            )

        if rule.kind in {"numeric_interval", "distance_interval"} or (
            rule.kind == "presence_distance" and observation.value.type == "number"
        ):
            if observation.value.type != "number":
                raise ObservationNeedsReview(f"{side} value is not numeric")
            number = self._normalize_number(observation, expected_unit=rule.unit)
            if rule.kind in {"distance_interval", "presence_distance"} and number < 0:
                raise ObservationNeedsReview("physical distance cannot be negative")
            for band in rule.intervals:
                if self._contains(band, number):
                    return band.grade
            raise ObservationNeedsReview(f"{side} value did not match any interval")

        if observation.value.type not in {"category", "text"}:
            raise ObservationNeedsReview(f"{side} value is not categorical text")
        normalized = str(observation.value.value).strip().casefold()
        for category in rule.categories:
            if normalized in {value.strip().casefold() for value in category.values}:
                return category.grade
        raise ObservationNeedsReview(f"{side} category is unknown: {observation.value.value!r}")

    @staticmethod
    def _contains(band: IntervalBand, value: Decimal) -> bool:
        minimum = Decimal(str(band.minimum)) if band.minimum is not None else None
        maximum = Decimal(str(band.maximum)) if band.maximum is not None else None
        above_minimum = (
            minimum is None or value > minimum or (band.minimum_inclusive and value == minimum)
        )
        below_maximum = (
            maximum is None or value < maximum or (band.maximum_inclusive and value == maximum)
        )
        return above_minimum and below_maximum

    @staticmethod
    def _normalize_number(observation: FactorObservation, *, expected_unit: str | None) -> Decimal:
        assert observation.value is not None
        try:
            value = Decimal(str(observation.value.value))
        except InvalidOperation as error:
            raise ObservationNeedsReview("numeric value is invalid") from error
        if not value.is_finite():
            raise ObservationNeedsReview("numeric value is not finite")
        actual_unit = observation.value.unit
        if expected_unit is None:
            if actual_unit is not None:
                raise ObservationNeedsReview("rule does not declare a unit for the supplied value")
            return value
        if actual_unit is None:
            raise ObservationNeedsReview(f"value is missing required unit {expected_unit!r}")
        if actual_unit == expected_unit:
            return value
        try:
            return value * _LENGTH_IN_METRES[actual_unit] / _LENGTH_IN_METRES[expected_unit]
        except KeyError as error:
            raise ObservationNeedsReview(
                f"unsupported unit conversion from {actual_unit!r} to {expected_unit!r}"
            ) from error
