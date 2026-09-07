from pathlib import Path

import pytest
from pydantic import ValidationError

from appraisal_review.adapters.local.pdf_config import PDFRenderConfig
from appraisal_review.adapters.local.pdf_values import PDFValueFormatter
from appraisal_review.domain.factor_models import (
    EvaluationStatus,
    EvaluationSummary,
    FactorEvaluationResult,
    FactorReviewResult,
    Grade,
)
from appraisal_review.domain.pdf_models import PDFFieldPlacementError, PDFValueRef


def config(**changes: object) -> PDFRenderConfig:
    values: dict[str, object] = {"font_path": Path.cwd() / "configured.ttf"}
    values.update(changes)
    return PDFRenderConfig.model_validate(values)


def review_result() -> FactorReviewResult:
    return FactorReviewResult(
        case_id="case-1",
        rule_set_id="rules-1",
        rule_version="1",
        results=[
            FactorEvaluationResult(
                factor_id="road.width",
                target_grade=Grade.EXCELLENT,
                comparable_grade=Grade.SLIGHTLY_INFERIOR,
                adjustment_percent=2.675,
                rule_id="road.width.v1",
                calculation_trace="synthetic",
                status=EvaluationStatus.VERIFIED,
            )
        ],
        summary=EvaluationSummary(
            total_adjustment_percent=-0.004,
            status=EvaluationStatus.VERIFIED,
        ),
    )


def reference(value: str, *, factor_id: str | None = "road.width") -> PDFValueRef:
    return PDFValueRef.model_validate(
        {
            "scope": "regional",
            "target_id": "target",
            "comparable_id": "comparison-1",
            "factor_id": factor_id,
            "value": value,
        }
    )


def test_resolves_both_grades_using_explicit_labels() -> None:
    labels = {grade.value: f"label:{grade.value}" for grade in Grade}
    formatter = PDFValueFormatter(config(grade_labels=labels))
    result = review_result()

    assert formatter.resolve(result, reference("target_grade")) == "label:excellent"
    assert formatter.resolve(result, reference("comparable_grade")) == "label:slightly_inferior"


def test_formats_factor_and_total_percentages_deterministically() -> None:
    formatter = PDFValueFormatter(config(decimal_places=2))
    result = review_result()

    assert formatter.resolve(result, reference("adjustment_percent")) == "+2.68%"
    assert (
        formatter.resolve(
            result,
            reference("total_adjustment_percent", factor_id=None),
        )
        == "0.00%"
    )


@pytest.mark.parametrize(
    ("value", "places", "show_sign", "expected"),
    [
        (5.0, 0, True, "+5%"),
        (5.0, 2, False, "5.00%"),
        (-2.675, 2, True, "-2.68%"),
        (0.0, 3, True, "0.000%"),
        (-0.0, 1, True, "0.0%"),
    ],
)
def test_percentage_policy(value: float, places: int, show_sign: bool, expected: str) -> None:
    result = review_result()
    result.results[0].adjustment_percent = value
    formatter = PDFValueFormatter(config(decimal_places=places, show_positive_sign=show_sign))

    assert formatter.resolve(result, reference("adjustment_percent")) == expected


@pytest.mark.parametrize("invalid", [float("inf"), float("-inf"), float("nan")])
def test_nonfinite_percentage_fails_even_for_unvalidated_model(invalid: float) -> None:
    result = review_result()
    result.results[0].adjustment_percent = invalid

    with pytest.raises(PDFFieldPlacementError, match="finite"):
        PDFValueFormatter(config()).resolve(result, reference("adjustment_percent"))


def test_missing_duplicate_and_unknown_factor_fail_explicitly() -> None:
    formatter = PDFValueFormatter(config())
    missing = review_result()
    missing.results = []
    with pytest.raises(PDFFieldPlacementError, match="exactly once"):
        formatter.resolve(missing, reference("adjustment_percent"))

    duplicate = review_result()
    duplicate.results.append(duplicate.results[0].model_copy())
    with pytest.raises(PDFFieldPlacementError, match="exactly once"):
        formatter.resolve(duplicate, reference("adjustment_percent"))

    with pytest.raises(PDFFieldPlacementError, match="exactly once"):
        formatter.resolve(review_result(), reference("adjustment_percent", factor_id="unknown"))


@pytest.mark.parametrize("value", ["target_grade", "comparable_grade", "adjustment_percent"])
def test_missing_factor_value_fails(value: str) -> None:
    result = review_result()
    setattr(result.results[0], value, None)

    with pytest.raises(PDFFieldPlacementError, match="no computed value"):
        PDFValueFormatter(config()).resolve(result, reference(value))


def test_missing_total_fails() -> None:
    result = review_result()
    result.summary.total_adjustment_percent = None

    with pytest.raises(PDFFieldPlacementError, match="no computed value"):
        PDFValueFormatter(config()).resolve(
            result,
            reference("total_adjustment_percent", factor_id=None),
        )


def test_grade_label_configuration_requires_exact_coverage() -> None:
    labels = {grade.value: grade.value for grade in Grade}
    labels.pop(Grade.INFERIOR.value)
    with pytest.raises(ValidationError, match="cover every supported grade"):
        config(grade_labels=labels)

    labels["unexpected"] = "unexpected"
    with pytest.raises(ValidationError, match="cover every supported grade"):
        config(grade_labels=labels)


def test_value_reference_shape_is_not_inferred() -> None:
    with pytest.raises(ValidationError):
        reference("total_adjustment_percent", factor_id="road.width")
    with pytest.raises(ValidationError):
        reference("adjustment_percent", factor_id=None)
