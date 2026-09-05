"""Measured uncertainty and controlled human confirmation have distinct gates."""

from unittest.mock import AsyncMock

import pytest
from test_case_review import ApprovedFixture, controller_run
from test_review_trust_boundaries import cell

from appraisal_review.adapters.local.synthetic import synthetic_material
from appraisal_review.domain.case_review import CaseReviewer
from appraisal_review.domain.confidence import confirm_side
from appraisal_review.domain.factor_models import ReviewMaterial


@pytest.mark.parametrize("side", ["target", "comparable"])
@pytest.mark.parametrize("score,allowed", [(0.89, False), (0.90, True), (0.91, True)])
def test_measured_evidence_threshold_is_inclusive_on_each_side(side, score, allowed):
    material = synthetic_material()
    observation = getattr(material.facts.pairs[0].pair, side)
    observation.confidence = 0.99
    observation.evidence[0].confidence = score
    before = material.model_dump()
    writer = AsyncMock()
    run = controller_run(material, minimum_confidence=0.90)
    assert run.verification.can_complete is allowed
    assert run.case_review.comparisons[0].summary.status.value == (
        "verified" if allowed else "needs_review"
    )
    assert material.model_dump() == before
    if not allowed:
        assert not controller_run(
            material, writer=writer, minimum_confidence=0.90
        ).verification.can_complete
        writer.write_pdf.assert_not_called()


@pytest.mark.parametrize("side", ["target", "comparable"])
def test_multiple_measured_anchors_use_conservative_score_and_complete_mapping(side):
    material = synthetic_material()
    pair = material.facts.pairs[0]
    observation = getattr(pair.pair, side)
    ref = cell(material, "measurement", observation.raw_text)
    getattr(pair, f"{side}_sources").append(ref)
    evidence = observation.evidence[0].model_copy(
        update={"bounding_box": ref.bbox, "confidence": 0.1}
    )
    observation.evidence.append(evidence)
    assert not controller_run(material).verification.can_complete
    observation.evidence[-1].confidence = 0.99
    assert controller_run(material).verification.can_complete
    observation.evidence.pop()
    assert not controller_run(material).verification.can_complete


@pytest.mark.parametrize(
    "condition", ["unknown", "localization", "no-producer", "model", "method-only", "empty"]
)
def test_unknown_or_model_provenance_cannot_become_native_or_confirmed(condition):
    material = synthetic_material()
    pair = material.facts.pairs[0]
    reliability = pair.target_reliability
    if condition == "unknown":
        reliability.confidence_kind = "unknown"
    elif condition == "localization":
        reliability.confidence_kind = "localization_only"
        reliability.provenance = "parser_registry"
    elif condition == "no-producer":
        reliability.producer = None
    elif condition == "model":
        reliability.method = "model_proposed"
        reliability.model_confidence = 1
    elif condition == "method-only":
        reliability.method = "reviewer_confirmed"
    else:
        pair.pair.target.evidence = []
    assert not controller_run(material).verification.can_complete


def confirmed_material(kind="localization_only"):
    material = synthetic_material()
    pair = material.facts.pairs[0]
    for side in ("target", "comparable"):
        reliability = getattr(pair, f"{side}_reliability")
        reliability.confidence_kind = kind
        reliability.provenance = (
            "parser_registry" if kind == "localization_only" else "native_extraction"
        )
        reliability.method = "model_proposed" if kind == "localization_only" else "native_numeric"
        reliability.model_confidence = 0.99
        observation = getattr(pair.pair, side)
        observation.confidence = observation.evidence[0].confidence = 0.1
        confirm_side(pair, side, reviewer="synthetic-reviewer")
    return material


@pytest.mark.parametrize("kind", ["measured", "localization_only"])
def test_human_confirmation_preserves_low_scores_and_requires_separate_authorization(kind):
    material = confirmed_material(kind)
    before = material.model_dump()
    result = CaseReviewer(None, minimum_confidence=0.95).review(
        material.policy, material.facts, material.policy.registry
    )
    assert result.status.value == "needs_review"
    assert controller_run(material, minimum_confidence=0.95).verification.can_complete
    assert material.model_dump() == before
    for side in ("target", "comparable"):
        observation = getattr(material.facts.pairs[0].pair, side)
        assert observation.confidence == observation.evidence[0].confidence == 0.1


@pytest.mark.parametrize(
    "tamper", ["confidence", "evidence", "provenance", "citation", "context", "unresolved"]
)
def test_confirmation_and_exact_material_digest_reject_later_tampering(tamper):
    material = confirmed_material()
    authority = ApprovedFixture(material)
    pair = material.facts.pairs[0]
    if tamper == "confidence":
        pair.pair.target.confidence = 0.99
    elif tamper == "evidence":
        pair.pair.target.evidence[0].confidence = 0.99
    elif tamper == "provenance":
        pair.target_reliability.provenance = "native_extraction"
    elif tamper == "citation":
        pair.target_sources[0].excerpt = "Synthetic target"
    elif tamper == "context":
        pair.context.comparable_id = "other"
    else:
        pair.target_reliability.unresolved = ["ambiguous value"]
    assert not authority.permits(material)
    result = CaseReviewer(authority).review(
        material.policy, material.facts, material.policy.registry
    )
    assert result.status.value != "verified"
    # Even a new whole-material authorization cannot repair a stale side confirmation.
    assert not controller_run(material).verification.can_complete


def test_old_serialized_reliability_loads_unknown_and_requires_explicit_migration():
    material = synthetic_material()
    old = material.model_dump(mode="json")
    for side in ("target", "comparable"):
        old["facts"]["pairs"][0][f"{side}_reliability"] = {
            "method": "reviewer_confirmed",
            "selection": "not_applicable",
            "model_confidence": None,
            "unresolved": [],
        }
    loaded = ReviewMaterial.model_validate(old)
    assert loaded.facts.pairs[0].target_reliability.confidence_kind == "unknown"
    assert not controller_run(loaded).verification.can_complete
    with pytest.raises(ValueError, match="provenance"):
        confirm_side(loaded.facts.pairs[0], "target", reviewer="synthetic-reviewer")
