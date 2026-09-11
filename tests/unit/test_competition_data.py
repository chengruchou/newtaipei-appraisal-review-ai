"""Synthetic local exact-content admission; no real approval or network operation."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import get_args

import pytest

from appraisal_review.adapters.local.competition_data import PinnedDataReviewAuthority
from appraisal_review.application.competition_data import ReviewedCompetitionAdmission
from appraisal_review.domain.competition_data import (
    CategoryAssessment,
    CompetitionDataFault,
    CompetitionDataPolicy,
    DataCategory,
    DataPart,
    DataReviewRecord,
    DataSurface,
    PartAssessment,
    envelope_digest,
    record_digest,
)

NOW = datetime(2026, 9, 11, tzinfo=UTC)


def fixture(
    *,
    category=None,
    surface="pdf",
    state="present",
    origin="synthetic_from_scratch",
    clarification=None,
):
    parts = (DataPart("fixture", surface, b"Invented fixture; no original document."),)
    policy = CompetitionDataPolicy(synthetic_financial_clarification_sha256=clarification)
    record = DataReviewRecord(
        envelope_sha256=envelope_digest(parts),
        policy_sha256=record_digest(policy),
        origin=origin,
        provenance_evidence_sha256="1" * 64,
        generator_sha256="2" * 64,
        reviewer_id="synthetic-test-reviewer",
        reviewed_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=1),
        assessments=(
            PartAssessment(
                binding=parts[0].binding(),
                inspection_evidence_sha256="3" * 64,
                categories=tuple(
                    CategoryAssessment(
                        category=c, classification=state if c == category else "absent"
                    )
                    for c in get_args(DataCategory)
                ),
            ),
        ),
    )
    revoked = set()
    authority = PinnedDataReviewAuthority(
        (record,),
        frozenset({record_digest(record)}),
        {record.reviewer_id: frozenset({record.envelope_sha256})},
        revoked=lambda digest: digest in revoked,
    )
    gate = ReviewedCompetitionAdmission(policy, record_digest(policy), authority, clock=lambda: NOW)
    return parts, record, authority, gate, revoked


@pytest.mark.parametrize("category", get_args(DataCategory))
@pytest.mark.parametrize("surface", get_args(DataSurface))
def test_each_prohibited_category_denies_on_every_surface(category, surface):
    parts, _, _, gate, _ = fixture(category=category, surface=surface)
    with pytest.raises(CompetitionDataFault, match="competition_data_prohibited"):
        gate.check(parts)


@pytest.mark.parametrize("surface", get_args(DataSurface))
def test_exact_synthetic_review_is_usable_but_changed_or_extra_bytes_are_not(surface):
    parts, _, _, gate, _ = fixture(surface=surface)
    gate.check(parts)
    with pytest.raises(CompetitionDataFault, match="unreviewed"):
        gate.check((replace(parts[0], content=b"changed"),))
    with pytest.raises(CompetitionDataFault, match="unreviewed"):
        gate.check((*parts, DataPart("extra", "metadata", b"new")))


@pytest.mark.parametrize("origin", ["real_case", "transformed_real_case", "unknown"])
def test_renaming_scaling_or_redacting_real_material_cannot_assert_synthetic_origin(origin):
    parts, _, _, gate, _ = fixture(origin=origin)
    with pytest.raises(CompetitionDataFault, match="provenance"):
        gate.check(parts)


def test_negative_detector_result_does_not_replace_complete_review():
    parts, _, _, gate, _ = fixture(category="financial_information", state="unknown")
    with pytest.raises(CompetitionDataFault, match="unreviewed"):
        gate.check(parts)


def test_financial_synthetic_needs_exact_trusted_organizer_clarification():
    parts, _, _, gate, _ = fixture(category="financial_information")
    with pytest.raises(CompetitionDataFault, match="prohibited"):
        gate.check(parts)
    # Explicit synthetic authority fixture only, not an organizer approval.
    parts, _, _, clarified, _ = fixture(category="financial_information", clarification="4" * 64)
    clarified.check(parts)
    gate.policy = clarified.policy
    with pytest.raises(CompetitionDataFault, match="policy"):
        gate.check(parts)
    parts, _, _, clarified, _ = fixture(category="personal_data", clarification="4" * 64)
    with pytest.raises(CompetitionDataFault, match="prohibited"):
        clarified.check(parts)


def test_review_is_not_authority_and_expiry_revocation_and_reviewer_scope_are_live():
    parts, record, authority, gate, revoked = fixture()
    gate.check(parts)
    revoked.add(record_digest(record))
    with pytest.raises(CompetitionDataFault, match="unreviewed"):
        gate.check(parts)
    revoked.clear()
    gate.clock = lambda: record.expires_at
    with pytest.raises(CompetitionDataFault, match="unreviewed"):
        gate.check(parts)
    gate.clock = lambda: NOW
    authority._trusted = frozenset()
    with pytest.raises(CompetitionDataFault, match="unreviewed"):
        gate.check(parts)
    authority._trusted = frozenset({record_digest(record)})
    authority._reviewers = {}
    with pytest.raises(CompetitionDataFault, match="unreviewed"):
        gate.check(parts)


def test_full_matrix_and_unknown_adapter_failures_do_not_expose_payload():
    parts, record, authority, gate, _ = fixture()
    assessment = record.assessments[0].model_dump()
    assessment["categories"] = assessment["categories"][:-1]
    with pytest.raises(ValueError):
        PartAssessment.model_validate(assessment)
    authority._revoked = lambda _: (_ for _ in ()).throw(ValueError("private-name"))
    with pytest.raises(CompetitionDataFault) as error:
        gate.check(parts)
    assert str(error.value) == "competition_data_unreviewed"
    assert "Invented" not in repr(parts[0])


def test_review_expiring_during_current_authority_check_cannot_permit_a_send():
    parts, record, authority, gate, _ = fixture()
    current = NOW
    gate.clock = lambda: current

    def slow_current_revocation(_):
        nonlocal current
        current = record.expires_at
        return False

    authority._revoked = slow_current_revocation
    with pytest.raises(CompetitionDataFault, match="competition_data_unreviewed"):
        gate.check(parts)


def test_from_scratch_demo_preserves_arithmetic_without_claiming_cloud_admission():
    from decimal import Decimal

    from appraisal_review.adapters.local.competition_demo import synthetic_financial_example

    example = synthetic_financial_example()
    assert example["origin"] == "synthetic_from_scratch"
    base = Decimal(example["area_square_meters"]) * Decimal(
        example["unit_price_synthetic_currency"]
    )
    correction = sum(map(Decimal, example["corrections_percent_points"]))
    assert Decimal(example["base_amount"]) == base == Decimal("120000")
    assert Decimal(example["total_percent_points"]) == correction == Decimal("9")
    assert Decimal(example["adjusted_amount"]) == base * (1 + correction / 100) == Decimal("130800")
    assert example["cloud_admission"] == "unapproved"
    assert example["organizer_synthetic_financial_permission"] == "unknown"
