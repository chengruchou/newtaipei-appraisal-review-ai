"""Revision lineage must not manufacture extraction or reviewer authority."""

import pytest

from appraisal_review.adapters.local.approval import LocalApprovalStore, current_reviewer
from appraisal_review.adapters.local.synthetic import synthetic_material
from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.domain.case_review import CaseReviewer
from appraisal_review.domain.confidence import confirm_side
from appraisal_review.domain.review_contracts import content_digest


@pytest.mark.parametrize("side", ["target", "comparable"])
def test_changed_side_cannot_regain_native_authority_by_relabeling(tmp_path, side):
    original = synthetic_material()
    reviewer = current_reviewer()
    store = LocalApprovalStore.initialize(tmp_path / "synthetic-approval", reviewer)
    receipt = store.approve(original, expected_digest=content_digest(original))
    receipts_before = {p.name: p.read_bytes() for p in store.root.iterdir()}
    parent = RevisionSnapshot.capture(original, "r1")
    changed = parent.material
    observation = getattr(changed.facts.pairs[0].pair, side)
    observation.value.value += 1 if side == "target" else -1
    observation.confidence = 0
    for evidence in observation.evidence:
        evidence.confidence = 0
    corrected = parent.revise(changed, "r2")
    assert getattr(corrected.material.facts.pairs[0], f"{side}_reliability").method == (
        "model_proposed"
    )
    relabeled = corrected.material
    getattr(relabeled.facts.pairs[0], f"{side}_reliability").method = "native_numeric"
    child = corrected.revise(relabeled, "r3")
    material = child.material

    # Check the real approval boundary, not just the serialized method label.
    with pytest.raises(ValueError, match="requires current reviewer confirmation"):
        store.approve(material, expected_digest=content_digest(material))
    assert not store.permits(material)
    result = CaseReviewer(store).review(material.policy, material.facts, material.policy.registry)
    assert result.status == "needs_review"
    assert getattr(material.facts.pairs[0], f"{side}_reliability").method == "model_proposed"
    other_side = "comparable" if side == "target" else "target"
    assert getattr(material.facts.pairs[0], f"{other_side}_reliability").method == "native_numeric"
    assert {p.name: p.read_bytes() for p in store.root.iterdir()} == receipts_before
    assert store.permits(original) and parent.material == original

    # A new, explicit side confirmation and exact-material approval still work.
    before = getattr(material.facts.pairs[0].pair, side).model_dump()
    confirm_side(material.facts.pairs[0], side, reviewer=f"{reviewer.uid}:{reviewer.name}")
    assert getattr(material.facts.pairs[0].pair, side).model_dump() == before
    assert before["confidence"] == 0
    assert all(evidence["confidence"] == 0 for evidence in before["evidence"])
    assert not store.permits(material)
    new_receipt = store.approve(material, expected_digest=content_digest(material))
    assert new_receipt.material_digest != receipt.material_digest
    assert store.permits(material) and store.permits(original)
    assert (
        CaseReviewer(store).review(material.policy, material.facts, material.policy.registry).status
        == "verified"
    )


@pytest.mark.parametrize("side", ["target", "comparable"])
@pytest.mark.parametrize("previous_method", ["model_proposed", "reviewer_confirmed"])
def test_non_native_parent_cannot_gain_native_authority_without_content_change(
    side, previous_method
):
    material = synthetic_material()
    pair = material.facts.pairs[0]
    if previous_method == "reviewer_confirmed":
        confirm_side(pair, side, reviewer="fixture-reviewer")
    else:
        getattr(pair, f"{side}_reliability").method = previous_method
    parent = RevisionSnapshot.capture(material, "r1")
    candidate = parent.material
    getattr(candidate.facts.pairs[0], f"{side}_reliability").method = "native_numeric"
    child = parent.revise(candidate, "r2")
    reliability = getattr(child.material.facts.pairs[0], f"{side}_reliability")
    assert reliability.method == "model_proposed" and reliability.confirmation is None


def test_unchanged_native_revision_can_receive_its_own_approval(tmp_path):
    original = synthetic_material()
    store = LocalApprovalStore.initialize(tmp_path / "synthetic-approval", current_reviewer())
    store.approve(original, expected_digest=content_digest(original))
    child = RevisionSnapshot.capture(original, "r1").revise(original, "r2")
    material = child.material
    assert material.facts.pairs == original.facts.pairs
    assert not store.permits(material)
    store.approve(material, expected_digest=content_digest(material))
    assert store.permits(original) and store.permits(material)


@pytest.mark.parametrize("side", ["target", "comparable"])
def test_revision_cannot_clean_invalid_native_confirmation_into_authority(side):
    material = synthetic_material()
    pair = material.facts.pairs[0]
    confirm_side(pair, side, reviewer="fixture-reviewer")
    getattr(pair, f"{side}_reliability").method = "native_numeric"
    parent = RevisionSnapshot.capture(material, "r1")
    candidate = parent.material
    getattr(candidate.facts.pairs[0], f"{side}_reliability").confirmation = None
    child = parent.revise(candidate, "r2")
    assert getattr(child.material.facts.pairs[0], f"{side}_reliability").method == "model_proposed"


@pytest.mark.parametrize("method", ["native_proposed", "manual_proposed"])
@pytest.mark.parametrize("changed", [False, True])
def test_local_proposal_revisions_retain_origin_without_native_authority(method, changed):
    """Synthetic lineage regression, separate from real correction acceptance."""
    material = synthetic_material()
    pair = material.facts.pairs[0]
    pair.target_reliability.method = method
    pair.pair.target.confidence = 0
    for evidence in pair.pair.target.evidence:
        evidence.confidence = 0
    assert pair.target_reliability.method == method
    assert pair.target_reliability.confirmation is None
    parent = RevisionSnapshot.capture(material, "local-r1")
    proposed = parent.material
    if changed:
        proposed.facts.pairs[0].pair.target.value.value += 1
    # A caller-provided label must not turn a local proposal into native authority.
    proposed.facts.pairs[0].target_reliability.method = "native_numeric"
    child = parent.revise(proposed, "local-r2")
    current = child.material.facts.pairs[0]
    assert current.target_reliability.method == ("manual_proposed" if changed else method)
    assert current.target_reliability.confirmation is None
    assert current.pair.target.confidence == 0
    assert current.pair.target.evidence == pair.pair.target.evidence
    assert parent.material == material
    assert (
        child.revise(child.material, "local-r3").material.facts.pairs[0].target_reliability.method
        == current.target_reliability.method
    )
