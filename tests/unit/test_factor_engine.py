from copy import deepcopy

import pytest
from pydantic import ValidationError

from appraisal_review.domain.factor_engine import FactorRuleEngine
from appraisal_review.domain.factor_models import (
    CategoryBand,
    CorrectionMatrix,
    EvaluationStatus,
    FactorEvaluationRequest,
    FactorObservation,
    FactorPair,
    FactorRule,
    FactorRuleSet,
    Grade,
    IntervalBand,
    NormalizedValue,
    RuleApplicability,
    RuleSource,
)
from appraisal_review.domain.models import EvidenceRef


def evidence() -> list[EvidenceRef]:
    return [
        EvidenceRef(
            document_id="synthetic-case",
            source_file="synthetic.pdf",
            page=1,
            confidence=0.99,
        )
    ]


def matrix(step: float = 5.0) -> CorrectionMatrix:
    return CorrectionMatrix(
        values={
            "inferior": {"inferior": 0.0, "excellent": -step},
            "excellent": {"inferior": step, "excellent": 0.0},
        }
    )


def road_rule() -> FactorRule:
    return FactorRule(
        id="road-width.v1",
        factor_id="regional.transport.main_road_width",
        kind="numeric_interval",
        unit="m",
        intervals=[
            IntervalBand(
                grade=Grade.INFERIOR,
                maximum=10.0,
                maximum_inclusive=False,
            ),
            IntervalBand(
                grade=Grade.EXCELLENT,
                minimum=10.0,
                minimum_inclusive=True,
            ),
        ],
        correction_matrix=matrix(),
    )


def approved_rule_set(*rules: FactorRule) -> FactorRuleSet:
    return FactorRuleSet(
        rule_set_id="district-commercial-v1",
        version="1.0.0",
        status="approved",
        applicability=RuleApplicability(
            jurisdiction="example-district",
            land_use_category="commercial",
        ),
        source_document=RuleSource(
            document_id="criteria",
            content_hash="sha256:synthetic",
        ),
        rules=list(rules),
    )


def observation(value: float, unit: str = "m", confidence: float = 0.99) -> FactorObservation:
    return FactorObservation(
        raw_text=f"{value} {unit}",
        value=NormalizedValue(type="number", value=value, unit=unit),
        evidence=evidence(),
        confidence=confidence,
    )


def request(target: FactorObservation, comparable: FactorObservation) -> FactorEvaluationRequest:
    return FactorEvaluationRequest(
        case_id="case-1",
        rule_set_id="district-commercial-v1",
        factors=[
            FactorPair(
                factor_id="regional.transport.main_road_width",
                target=target,
                comparable=comparable,
            )
        ],
    )


def test_boundary_unit_conversion_and_matrix_orientation() -> None:
    engine = FactorRuleEngine(approved_rule_set(road_rule()))

    result = engine.evaluate(request(observation(0.01, "km"), observation(999.0, "cm")))

    item = result.results[0]
    assert item.status is EvaluationStatus.VERIFIED
    assert item.target_grade is Grade.EXCELLENT
    assert item.comparable_grade is Grade.INFERIOR
    assert item.adjustment_percent == 5.0
    assert result.summary.total_adjustment_percent == 5.0


def test_low_confidence_requires_review_and_has_no_partial_total() -> None:
    engine = FactorRuleEngine(approved_rule_set(road_rule()), minimum_confidence=0.9)

    result = engine.evaluate(request(observation(10.0, confidence=0.5), observation(9.0)))

    assert result.results[0].status is EvaluationStatus.NEEDS_REVIEW
    assert result.summary.status is EvaluationStatus.NEEDS_REVIEW
    assert result.summary.total_adjustment_percent is None


def test_missing_evidence_requires_review() -> None:
    target = observation(10.0)
    target.evidence = []

    result = FactorRuleEngine(approved_rule_set(road_rule())).evaluate(
        request(target, observation(9.0))
    )

    assert result.results[0].status is EvaluationStatus.NEEDS_REVIEW
    assert "evidence is missing" in result.results[0].warnings[0]


def test_unknown_factor_fails_explicitly() -> None:
    payload = request(observation(10.0), observation(9.0)).model_dump()
    payload["factors"][0]["factor_id"] = "unsupported.factor"

    result = FactorRuleEngine(approved_rule_set(road_rule())).evaluate(
        FactorEvaluationRequest.model_validate(payload)
    )

    assert result.results[0].status is EvaluationStatus.FAILED
    assert "Unknown factor ID" in result.results[0].warnings[0]


def test_category_aliases_are_data_driven() -> None:
    rule = FactorRule(
        id="drainage.v1",
        factor_id="regional.natural.drainage",
        kind="category",
        categories=[
            CategoryBand(grade=Grade.INFERIOR, values=["frequently flooded"]),
            CategoryBand(grade=Grade.EXCELLENT, values=["well drained", "排水良好"]),
        ],
        correction_matrix=matrix(step=2.5),
    )
    categorical = lambda value: FactorObservation(  # noqa: E731
        raw_text=value,
        value=NormalizedValue(type="category", value=value),
        evidence=evidence(),
        confidence=0.99,
    )
    evaluation = FactorEvaluationRequest(
        case_id="case-1",
        rule_set_id="district-commercial-v1",
        factors=[
            FactorPair(
                factor_id="regional.natural.drainage",
                target=categorical("排水良好"),
                comparable=categorical("frequently flooded"),
            )
        ],
    )

    result = FactorRuleEngine(approved_rule_set(rule)).evaluate(evaluation)

    assert result.results[0].adjustment_percent == 2.5


def test_interval_gap_is_rejected_before_evaluation() -> None:
    payload = road_rule().model_dump()
    payload["intervals"] = deepcopy(payload["intervals"])
    payload["intervals"][1]["minimum"] = 11.0

    with pytest.raises(ValidationError, match="contiguous"):
        FactorRule.model_validate(payload)


def test_another_land_use_changes_data_not_engine_code() -> None:
    original_engine = FactorRuleEngine(approved_rule_set(road_rule()))
    changed_rule_payload = road_rule().model_dump()
    changed_rule_payload["intervals"][0]["maximum"] = 20.0
    changed_rule_payload["intervals"][1]["minimum"] = 20.0
    changed_rule = FactorRule.model_validate(changed_rule_payload)
    changed_rule_set_payload = approved_rule_set(changed_rule).model_dump()
    changed_rule_set_payload["rule_set_id"] = "district-industrial-v1"
    changed_rule_set_payload["applicability"]["land_use_category"] = "industrial"
    changed_engine = FactorRuleEngine(FactorRuleSet.model_validate(changed_rule_set_payload))
    original_request = request(observation(15.0), observation(9.0))
    changed_request = original_request.model_copy(update={"rule_set_id": "district-industrial-v1"})

    original_result = original_engine.evaluate(original_request)
    changed_result = changed_engine.evaluate(changed_request)

    assert original_result.results[0].target_grade is Grade.EXCELLENT
    assert changed_result.results[0].target_grade is Grade.INFERIOR
