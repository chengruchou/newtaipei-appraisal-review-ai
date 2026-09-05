from appraisal_review.domain.factor_models import (
    CorrectionMatrix,
    EvaluationStatus,
    EvaluationSummary,
    FactorReviewResult,
    FactorRule,
    FactorRuleSet,
    Grade,
    IntervalBand,
    PDFField,
    PDFFieldMap,
    RuleApplicability,
    RuleSource,
)
from appraisal_review.domain.verification import ReviewVerifier


def road_rule() -> FactorRule:
    return FactorRule(
        id="road-width.v1",
        factor_id="regional.transport.main_road_width",
        kind="numeric_interval",
        unit="m",
        intervals=[
            IntervalBand(grade=Grade.INFERIOR, maximum=10.0),
            IntervalBand(grade=Grade.EXCELLENT, minimum=10.0),
        ],
        correction_matrix=CorrectionMatrix(
            values={
                "inferior": {"inferior": 0.0, "excellent": -5.0},
                "excellent": {"inferior": 5.0, "excellent": 0.0},
            }
        ),
    )


def approved_rule_set(rule: FactorRule) -> FactorRuleSet:
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
        rules=[rule],
    )


def test_missing_critical_factor_prevents_completion() -> None:
    rule_set: FactorRuleSet = approved_rule_set(road_rule())
    result = FactorReviewResult(
        case_id="case-1",
        rule_set_id=rule_set.rule_set_id,
        rule_version=rule_set.version,
        results=[],
        summary=EvaluationSummary(
            total_adjustment_percent=0.0,
            status=EvaluationStatus.VERIFIED,
        ),
    )

    verification = ReviewVerifier().verify(result, rule_set)

    assert not verification.can_complete
    assert verification.status is EvaluationStatus.NEEDS_REVIEW
    assert "Missing critical factor result" in verification.critical_errors[0]


def test_pdf_field_map_requires_exact_lookup() -> None:
    field_map = PDFFieldMap(
        template_id="template-v1",
        fields=[PDFField(field_id="factor.grade", page=2, bounding_box=(1, 2, 3, 4))],
    )

    assert field_map.lookup("factor.grade").page == 2

    try:
        field_map.lookup("unknown")
    except KeyError as error:
        assert "exactly one" in str(error)
    else:
        raise AssertionError("missing field lookup should fail")


def test_forged_verified_result_cannot_complete() -> None:
    """Independent regression: normal/normal is zero, never 999."""
    from appraisal_review.domain.factor_models import FactorEvaluationResult

    rule = FactorRule(
        id="x.v1",
        factor_id="x",
        kind="numeric_interval",
        intervals=[IntervalBand(grade=Grade.NORMAL)],
        correction_matrix=CorrectionMatrix(values={"normal": {"normal": 0.0}}),
    )
    result = FactorReviewResult(
        case_id="case-1",
        rule_set_id="unrelated",
        rule_version="unrelated",
        results=[
            FactorEvaluationResult(
                factor_id="x",
                rule_id="unrelated",
                target_grade=Grade.NORMAL,
                comparable_grade=Grade.NORMAL,
                adjustment_percent=999,
                calculation_trace="Untrusted claimed calculation",
                status=EvaluationStatus.VERIFIED,
            )
        ],
        summary=EvaluationSummary(total_adjustment_percent=999, status=EvaluationStatus.VERIFIED),
    )
    assert not ReviewVerifier().verify(result, approved_rule_set(rule)).can_complete
