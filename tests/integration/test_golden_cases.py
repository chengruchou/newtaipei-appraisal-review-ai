"""Full-case acceptance: the review, the completion gate and the writer share one golden.

The manifests under `tests/goldens` are the reviewed expectations. These tests read them
from disk and never write them back, so a behaviour change shows up as a failing case
rather than as a silently updated expected value.
"""

import asyncio
import uuid
from functools import lru_cache
from pathlib import Path

import pytest

from appraisal_review.adapters.local.fake_pdf import FakePDFWriter
from appraisal_review.adapters.local.golden_cases import (
    FACTORS,
    MEASUREMENTS,
    REGIONAL,
    GoldenAuthorization,
    golden_adapters,
    golden_fixtures,
    load_golden_manifests,
    manifest_documents,
    prefix,
)
from appraisal_review.adapters.local.service import public_verification
from appraisal_review.application.bootstrap import build_controller
from appraisal_review.config import Settings
from appraisal_review.domain.case_review import CaseReviewer
from appraisal_review.domain.factor_models import Grade
from appraisal_review.domain.golden_contract import GoldenSuite
from appraisal_review.domain.golden_validator import (
    compare_case_review,
    compare_review_run,
    compare_service_result,
    verify_manifest,
)
from appraisal_review.domain.service_contracts import (
    ExecutionStatus,
    RevisionReference,
    RunReference,
    ServiceResult,
)

GOLDENS = Path(__file__).resolve().parents[1] / "goldens"
FIXTURES = golden_fixtures()


@lru_cache(maxsize=1)
def _reviewed_suite() -> GoldenSuite:
    """Read the committed manifests once; the acceptance suite reads them many times."""
    return load_golden_manifests(GOLDENS)


def reviewed(case_key: str):
    return next(case for case in _reviewed_suite().cases if case.case_key == case_key)


def controller_run(fixture, *, writer=None):
    controller = build_controller(
        Settings.model_construct(
            runtime_mode="local", synthetic_demo=False, min_extraction_confidence=0.85
        ),
        adapters=golden_adapters(fixture, pdf_writer=writer or FakePDFWriter()),
    )
    return asyncio.run(controller.review(fixture.request))


def test_committed_manifests_match_the_fixture_generator():
    """A fixture edit without a manifest review is a failing case, not a silent update."""
    expected = manifest_documents(GoldenSuite(cases=tuple(f.case for f in FIXTURES)))
    actual = {path.name: path.read_text(encoding="utf-8") for path in GOLDENS.glob("*.json")}
    assert actual == expected


def test_reviewed_suite_loads_and_covers_every_case():
    suite = _reviewed_suite()
    assert {case.case_key for case in suite.cases} == {f.case_key for f in FIXTURES}


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f.case_key)
def test_reviewed_manifest_still_follows_from_its_fixture(fixture):
    report = verify_manifest(reviewed(fixture.case_key), fixture.material)
    assert report.ok, report.describe()


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f.case_key)
def test_case_review_matches_the_reviewed_expectations(fixture):
    material = fixture.material
    authority = GoldenAuthorization(material) if fixture.authorized else None
    result = CaseReviewer(authority).review(
        material.policy, material.facts, material.policy.registry
    )
    report = compare_case_review(reviewed(fixture.case_key), result)
    assert report.ok, report.describe()


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f.case_key)
def test_controller_run_matches_the_reviewed_expectations(fixture):
    report = compare_review_run(reviewed(fixture.case_key), controller_run(fixture))
    assert report.ok, report.describe()


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f.case_key)
def test_independent_verifier_rejects_a_forged_comparison(fixture):
    """The same fixtures feed the claim checker, not only the first-party review."""
    material = fixture.material
    authority = GoldenAuthorization(material) if fixture.authorized else None
    reviewer = CaseReviewer(authority)
    honest = reviewer.review(material.policy, material.facts, material.policy.registry)
    replayed = reviewer.review(
        material.policy, material.facts, material.policy.registry, claimed=honest.comparisons
    )
    claim = next(f for f in replayed.findings if f.id == "claimed_results")
    assert claim.status == "verified"
    forged = [comparison.model_copy(deep=True) for comparison in honest.comparisons]
    if not forged:
        pytest.skip("This case never reaches an evaluated comparison")
    forged[0].summary.total_adjustment_percent = 999
    checked = reviewer.review(
        material.policy, material.facts, material.policy.registry, claimed=forged
    )
    assert checked.status == "failed"
    assert any(f.id == "claimed_results" and f.status == "failed" for f in checked.findings)


def test_published_service_envelope_matches_the_reviewed_case():
    """The sanitized envelope B2 and B3 publish is held to the same golden."""
    fixture = next(f for f in FIXTURES if f.case_key == "normal-complete")
    run = controller_run(fixture)
    result = ServiceResult(
        run=RunReference(
            run_id=uuid.uuid4(),
            revision=RevisionReference(
                case_id=fixture.case.identity.case_id,
                revision_id=fixture.case.identity.version,
                material_digest=fixture.case.fixture.material_digest,
            ),
        ),
        result_version=1,
        execution_status=ExecutionStatus.SUCCEEDED,
        business_status=run.status,
        artifact_status=run.artifact_status,
        findings=tuple(run.case_review.findings) if run.case_review else (),
        verification=public_verification(run.verification),
    )
    report = compare_service_result(reviewed("normal-complete"), result)
    assert report.ok, report.describe()


def test_open_cases_publish_one_public_diagnostic_for_each_blocking_finding():
    for fixture in FIXTURES:
        case = reviewed(fixture.case_key)
        published = public_verification(controller_run(fixture).verification)
        assert published is not None
        assert published.status is case.expected_verification.status
        assert published.critical_errors == case.expected_verification.critical
        # The name of this test is a claim about the relationship, so assert it directly.
        blocking = [f for f in case.expected_findings if f.status != "verified"]
        assert len(published.critical_errors) == len(blocking)


def test_a_wrong_deterministic_result_fails_its_case():
    """The central claim: an expectation derived from the criteria catches a wrong result."""
    fixture = next(f for f in FIXTURES if f.case_key == "normal-complete")
    material = fixture.material
    result = CaseReviewer(GoldenAuthorization(material)).review(
        material.policy, material.facts, material.policy.registry
    )
    assert compare_case_review(reviewed("normal-complete"), result).ok
    wrong = result.model_copy(deep=True)
    wrong.comparisons[0].results[0].adjustment_percent = 9.0
    report = compare_case_review(reviewed("normal-complete"), wrong)
    assert not report.ok
    assert any(m.check == "comparison" for m in report.mismatches)
    regraded = result.model_copy(deep=True)
    regraded.comparisons[0].results[0].target_grade = Grade.INFERIOR
    assert not compare_case_review(reviewed("normal-complete"), regraded).ok
    misreported = result.model_copy(deep=True)
    finding = next(f for f in misreported.findings if f.id == "observed/regional-total")
    finding.observed = "99"
    assert not compare_case_review(reviewed("normal-complete"), misreported).ok


def test_a_duplicated_engine_finding_fails_the_case():
    """Multiplicity is part of the contract: the same finding twice is a difference."""
    fixture = next(f for f in FIXTURES if f.case_key == "normal-complete")
    material = fixture.material
    result = CaseReviewer(GoldenAuthorization(material)).review(
        material.policy, material.facts, material.policy.registry
    )
    doubled = result.model_copy(deep=True)
    doubled.findings.append(doubled.findings[0].model_copy(deep=True))
    report = compare_case_review(reviewed("normal-complete"), doubled)
    assert not report.ok
    assert any(m.check == "findings" for m in report.mismatches)


def test_every_level_of_the_form_carries_a_different_number():
    """Without distinct values, rate/subtotal/total confusion would pass every case."""
    case = reviewed("normal-complete")
    numbers = {
        slot.slot_id: slot.independent.value
        for slot in case.expected_slots
        if slot.observed.unit == "percent_points" and slot.independent is not None
    }
    rates = {v for k, v in numbers.items() if k.endswith("-rate")}
    subtotals = {v for k, v in numbers.items() if k.endswith("-subtotal")}
    total = numbers[f"{prefix(REGIONAL)}-total"]
    assert len(rates) == len(FACTORS) > 1
    assert total not in rates
    # A multi-factor group's subtotal is its own number, distinct from every rate it sums.
    assert numbers[f"{prefix(REGIONAL)}-site-subtotal"] not in rates
    assert total not in subtotals


def test_the_two_comparison_contexts_are_distinguishable():
    case = reviewed("multi-context")

    def numbers(scope: str) -> list[str]:
        return [
            s.independent.value
            for s in case.expected_slots
            if s.slot_id.startswith(scope) and s.observed.unit == "percent_points"
        ]

    assert numbers("regional") and numbers("regional") != numbers("individual")


def test_a_swapped_total_and_subtotal_fails_the_case():
    fixture = next(f for f in FIXTURES if f.case_key == "normal-complete")
    material = fixture.material
    result = CaseReviewer(GoldenAuthorization(material)).review(
        material.policy, material.facts, material.policy.registry
    )
    assert compare_case_review(reviewed("normal-complete"), result).ok
    swapped = result.model_copy(deep=True)
    name = prefix(REGIONAL)
    finding = next(f for f in swapped.findings if f.id == f"observed/{name}-total")
    finding.observed = finding.expected = str(
        next(
            s.independent.value
            for s in reviewed("normal-complete").expected_slots
            if s.slot_id == f"{name}-site-subtotal"
        )
    )
    assert not compare_case_review(reviewed("normal-complete"), swapped).ok


def test_the_flagship_case_sits_on_an_interval_boundary():
    """AGENTS.md lists interval boundaries first; the fixture must actually reach one."""
    boundary = [f for f in FACTORS if MEASUREMENTS["regional"][f.key][0] == f.threshold]
    assert boundary, "no regional measurement sits on a band threshold"
    factor = boundary[0]
    case = reviewed("normal-complete")
    slot = next(
        s
        for s in case.expected_slots
        if s.slot_id == f"{prefix(REGIONAL)}-{factor.key}-target-grade"
    )
    assert slot.independent is not None
    assert slot.independent.classification is not None
    assert slot.independent.classification.measurement == factor.threshold
    # The lower bound is inclusive, so the threshold itself is the better grade.
    assert slot.independent.value == "excellent"


def test_an_exclusive_lower_bound_would_fail_the_boundary_case():
    """Flip the rule's inclusivity and the reviewed grade no longer follows from the band."""
    fixture = next(f for f in FIXTURES if f.case_key == "normal-complete")
    material = fixture.material.model_copy(deep=True)
    for scoped in material.policy.rule_sets:
        for rule in scoped.rules.rules:
            for band in rule.intervals:
                if band.minimum is not None:
                    band.minimum_inclusive = False
    report = verify_manifest(reviewed("normal-complete"), material)
    assert not report.ok
    assert any(m.check == "classification" for m in report.mismatches)
