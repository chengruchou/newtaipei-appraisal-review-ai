from copy import deepcopy
from pathlib import Path

from appraisal_review.domain.models import CanonicalCase, CheckStatus
from appraisal_review.domain.rule_engine import RuleEngine

ROOT = Path(__file__).parents[2]


def load_case() -> CanonicalCase:
    return CanonicalCase.model_validate_json(
        (ROOT / "tests/fixtures/canonical_case.json").read_text(encoding="utf-8")
    )


def load_engine() -> RuleEngine:
    return RuleEngine.from_yaml(ROOT / "configs/rules/demo.yaml")


def test_valid_case_passes_all_rules() -> None:
    result = load_engine().evaluate(load_case())

    assert result.overall_status is CheckStatus.PASS
    assert {finding.status for finding in result.findings} == {CheckStatus.PASS}


def test_incorrect_sum_fails() -> None:
    case = load_case()
    payload = deepcopy(case.model_dump())
    payload["fields"]["summary.region_total_adjustment"]["value"] = 4.0

    result = load_engine().evaluate(CanonicalCase.model_validate(payload))

    assert result.overall_status is CheckStatus.FAIL
    assert result.findings[0].status is CheckStatus.FAIL
    assert result.findings[0].expected == "2.5"
    assert result.findings[0].actual == "4.0"


def test_missing_evidence_requires_review() -> None:
    case = load_case()
    payload = deepcopy(case.model_dump())
    payload["fields"]["region.nuisance_adjustment"]["value"] = None

    result = load_engine().evaluate(CanonicalCase.model_validate(payload))

    assert result.overall_status is CheckStatus.NEEDS_REVIEW
    assert result.findings[0].status is CheckStatus.NEEDS_REVIEW
    assert "region.nuisance_adjustment" in result.findings[0].message
