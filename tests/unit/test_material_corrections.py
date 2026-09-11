"""Trusted subjects constrain corrections and keep raw evidence and authority separate."""

from dataclasses import FrozenInstanceError

import pytest

from appraisal_review.adapters.local.synthetic import synthetic_material
from appraisal_review.application.material_corrections import MaterialSubject, MaterialSubjectMap
from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.domain.case_review import CaseReviewer
from appraisal_review.domain.confidence import confirm_side
from appraisal_review.domain.document_models import SourceCitation, SourceRegion
from appraisal_review.domain.factor_models import NormalizedValue
from appraisal_review.domain.service_contracts import ActorReference, PublicValue, ValueRevision

HUMAN = ActorReference(actor_id="fixture-reviewer", kind="human")


def subjects(material):
    pair = material.facts.pairs[0]
    return MaterialSubjectMap(
        tuple(
            MaterialSubject(
                subject_id=f"road-{side}",
                context=pair.context,
                factor_id=pair.pair.factor_id,
                side=side,
            )
            for side in ("target", "comparable")
        )
    )


def proposal(mapping, snapshot, side="target", number=9.0, **updates):
    original = mapping.public_value(snapshot, f"road-{side}")
    proposed = original.model_copy(
        update={
            "state": "present",
            "raw_text": f"{number:g} m",
            "value": NormalizedValue(type="number", value=number, unit="m"),
            **updates,
        }
    )
    return ValueRevision(subject_id=f"road-{side}", original=original, proposed=proposed)


def extra_citation(material):
    forms = material.policy.registry.documents[1]
    region = SourceRegion(id="other-cell", kind="cell", bbox=(1, 50, 90, 60), text="9 m")
    forms.pages[0].regions.append(region)
    return SourceCitation(
        document_id=forms.document_id,
        content_hash=forms.content_hash,
        version=forms.version,
        page=1,
        region_id=region.id,
        bbox=region.bbox,
        excerpt=region.text,
    )


@pytest.mark.parametrize("side", ["target", "comparable"])
def test_correction_preserves_parent_raw_scores_and_invalidates_confirmations(side):
    material = synthetic_material()
    pair = material.facts.pairs[0]
    observation = getattr(pair.pair, side)
    observation.confidence = 0.4
    observation.evidence[0].confidence = 0.2
    for confirmed_side in ("target", "comparable"):
        confirm_side(pair, confirmed_side, reviewer="previous-reviewer")
    mapping = subjects(material)
    parent = RevisionSnapshot.capture(material, "r1")
    command = proposal(mapping, parent, side, number=9 if side == "target" else 10)

    child = mapping.correct(parent, command, HUMAN, "r2")

    assert parent.material == material
    assert child.revision.parent == parent.revision.reference
    assert child.revision.reference.material_digest != parent.revision.reference.material_digest
    assert child.material.facts.identity.version == child.material.policy.identity.version == "r2"
    corrected_pair = child.material.facts.pairs[0]
    corrected = getattr(corrected_pair.pair, side)
    assert corrected.value == command.proposed.value
    assert corrected.raw_text == command.proposed.raw_text
    assert corrected.confidence == 0.4
    assert corrected.evidence[0].confidence == 0.2
    assert corrected.evidence[0].source_file == material.policy.registry.documents[1].uri
    assert corrected.evidence[0].bounding_box == command.proposed.evidence[0].bbox
    assert corrected.evidence[0].block_ids == [command.proposed.evidence[0].region_id]
    assert corrected_pair.target_reliability.confirmation is None
    assert corrected_pair.comparable_reliability.confirmation is None
    assert getattr(corrected_pair, f"{side}_reliability").method == "model_proposed"
    ledger = child.revision.changes[0]
    assert ledger.original == command.original
    assert ledger.proposed == command.proposed
    assert ledger.corrected == command.proposed
    assert ledger.corrected_by == HUMAN
    ledger.corrected.value.value = 88
    assert child.revision.changes[0].corrected.value.value != 88
    assert (
        CaseReviewer(None)
        .review(child.material.policy, child.material.facts, child.material.policy.registry)
        .status
        == "needs_review"
    )


def test_repeated_corrections_keep_full_attributed_ledger():
    material = synthetic_material()
    mapping = subjects(material)
    parent = RevisionSnapshot.capture(material, "r1")
    first = mapping.correct(parent, proposal(mapping, parent), HUMAN, "r2")
    second = mapping.correct(first, proposal(mapping, first, number=10), HUMAN, "r3")
    assert second.revision.parent == first.revision.reference
    assert len(second.revision.changes) == 2
    assert second.revision.changes[0] == first.revision.changes[0]
    assert second.revision.changes[1].original == first.revision.changes[0].corrected
    assert parent.material.facts.pairs[0].pair.target.value.value == 10
    assert first.material.facts.pairs[0].pair.target.value.value == 9


@pytest.mark.parametrize("operation", ["correct", "confirm"])
@pytest.mark.parametrize("kind", ["model", "system"])
def test_nonhuman_actor_cannot_mutate_material(operation, kind):
    material = synthetic_material()
    mapping = subjects(material)
    snapshot = RevisionSnapshot.capture(material, "r1")
    argument = proposal(mapping, snapshot) if operation == "correct" else "road-target"
    with pytest.raises(PermissionError, match="trusted human"):
        getattr(mapping, operation)(
            snapshot, argument, ActorReference(actor_id="actor", kind=kind), "r2"
        )
    assert snapshot.material == material


@pytest.mark.parametrize("field", ["raw_text", "confidence", "value", "evidence"])
def test_correction_rejects_forged_original(field):
    material = synthetic_material()
    mapping = subjects(material)
    snapshot = RevisionSnapshot.capture(material, "r1")
    command = proposal(mapping, snapshot)
    replacements = {
        "raw_text": "forged original",
        "confidence": 1.0,
        "value": NormalizedValue(type="number", value=88, unit="m"),
        "evidence": (),
    }
    command = command.model_copy(
        update={"original": command.original.model_copy(update={field: replacements[field]})}
    )
    with pytest.raises(ValueError, match="original differs"):
        mapping.correct(snapshot, command, HUMAN, "r2")


@pytest.mark.parametrize(
    "updates,reason",
    [
        ({"confidence": 1.0}, "confidence"),
        ({"confidence": 0.2}, "confidence"),
        ({"confidence": None}, "confidence"),
        ({"unit": "km"}, "units"),
        ({"value": NormalizedValue(type="number", value=9, unit="km"), "unit": "km"}, "unit"),
        ({"value": NormalizedValue(type="text", value="9 m", unit="m")}, "type"),
        ({"raw_text": "uncited transcription"}, "transcription"),
        ({"raw_text": " "}, "transcription"),
        ({"value": "9"}, "normalized"),
        ({"evidence": ()}, "selected case forms"),
    ],
)
def test_correction_rejects_untrusted_value_changes(updates, reason):
    material = synthetic_material()
    mapping = subjects(material)
    snapshot = RevisionSnapshot.capture(material, "r1")
    with pytest.raises(ValueError, match=reason):
        mapping.correct(snapshot, proposal(mapping, snapshot, **updates), HUMAN, "r2")


@pytest.mark.parametrize("proposed", [None, PublicValue(state="missing", raw_text="")])
def test_correction_requires_present_proposal(proposed):
    material = synthetic_material()
    mapping = subjects(material)
    snapshot = RevisionSnapshot.capture(material, "r1")
    command = proposal(mapping, snapshot).model_copy(update={"proposed": proposed})
    with pytest.raises(ValueError, match="present normalized"):
        mapping.correct(snapshot, command, HUMAN, "r2")


def test_response_cannot_prepopulate_corrected_attribution():
    material = synthetic_material()
    mapping = subjects(material)
    snapshot = RevisionSnapshot.capture(material, "r1")
    command = proposal(mapping, snapshot)
    command = command.model_copy(update={"corrected": command.proposed, "corrected_by": HUMAN})
    with pytest.raises(ValueError, match="authored by the application"):
        mapping.correct(snapshot, command, HUMAN, "r2")


@pytest.mark.parametrize(
    "updates",
    [
        {"version": "stale"},
        {"content_hash": "0" * 64},
        {"page": 2},
        {"region_id": "nonexistent"},
        {"bbox": (1, 2, 89, 40)},
        {"excerpt": "forged source text"},
    ],
)
def test_correction_rejects_wrong_source_identity_or_location(updates):
    material = synthetic_material()
    mapping = subjects(material)
    snapshot = RevisionSnapshot.capture(material, "r1")
    ref = material.facts.pairs[0].target_sources[0].model_copy(update=updates)
    with pytest.raises(ValueError, match="selected case forms"):
        mapping.correct(snapshot, proposal(mapping, snapshot, evidence=(ref,)), HUMAN, "r2")


@pytest.mark.parametrize("source", ["criteria", "reference", "other-cell"])
def test_valid_but_wrong_purpose_or_anchor_cannot_replace_fact(source):
    material = synthetic_material()
    if source == "other-cell":
        ref = extra_citation(material)
    else:
        ref = material.policy.rule_sets[0].evidence[0]
        if source == "reference":
            reference = material.policy.registry.documents[0].model_copy(deep=True)
            reference.document_id = "synthetic-reference"
            reference.role = "reference"
            reference.uri = "file:///synthetic/reference.pdf"
            material.policy.registry.documents.append(reference)
            ref = ref.model_copy(update={"document_id": reference.document_id})
    mapping = subjects(material)
    snapshot = RevisionSnapshot.capture(material, "r1")
    with pytest.raises(ValueError, match=r"selected case forms|source anchors"):
        mapping.correct(snapshot, proposal(mapping, snapshot, evidence=(ref,)), HUMAN, "r2")


@pytest.mark.parametrize("missing", ["value", "sources", "evidence"])
def test_supply_requires_explicit_task_and_does_not_invent_measurement(missing):
    material = synthetic_material()
    pair = material.facts.pairs[0]
    refs = tuple(pair.target_sources)
    if missing == "value":
        pair.pair.target.value = None
    elif missing == "sources":
        pair.target_sources = []
        pair.pair.target.evidence = []
    else:
        pair.pair.target.evidence = []
    pair.target_reliability.unresolved = ["manual source check required"]
    pair.target_reliability.provenance = "unknown"
    material.facts.unresolved = ["unresolved case question"]
    mapping = subjects(material)
    snapshot = RevisionSnapshot.capture(material, "r1")
    command = proposal(mapping, snapshot, unit="m", evidence=refs)
    with pytest.raises(ValueError, match="explicit supply task"):
        mapping.correct(snapshot, command, HUMAN, "r2")
    child = mapping.correct(snapshot, command, HUMAN, "r2", allow_missing_evidence=True)
    corrected = child.material.facts.pairs[0]
    assert corrected.pair.target.confidence == pair.pair.target.confidence
    assert corrected.pair.target.evidence[0].confidence == (0.99 if missing == "value" else 0)
    assert corrected.target_reliability.provenance == "unknown"
    assert corrected.target_reliability.unresolved == ["manual source check required"]
    assert child.material.facts.unresolved == ["unresolved case question"]
    with pytest.raises(ValueError, match="Resolve missing facts"):
        mapping.confirm(child, "road-target", HUMAN, "r3")


def test_supply_cannot_move_existing_empty_value_to_another_anchor():
    material = synthetic_material()
    ref = extra_citation(material)
    material.facts.pairs[0].pair.target.value = None
    mapping = subjects(material)
    snapshot = RevisionSnapshot.capture(material, "r1")
    with pytest.raises(ValueError, match="source anchors"):
        mapping.correct(
            snapshot,
            proposal(mapping, snapshot, unit="m", evidence=(ref,)),
            HUMAN,
            "r2",
            allow_missing_evidence=True,
        )


def test_image_anchor_accepts_attributed_human_transcription_without_score_elevation():
    material = synthetic_material()
    forms = material.policy.registry.documents[1]
    forms.pages[0].regions[0].kind = "image"
    forms.pages[0].regions[0].text = ""
    pair = material.facts.pairs[0]
    pair.target_sources[0].excerpt = ""
    pair.pair.target.confidence = 0
    pair.pair.target.evidence[0].confidence = 0
    pair.target_reliability.method = "model_proposed"
    pair.target_reliability.provenance = "parser_registry"
    pair.target_reliability.confidence_kind = "localization_only"
    mapping = subjects(material)
    snapshot = RevisionSnapshot.capture(material, "r1")
    child = mapping.correct(snapshot, proposal(mapping, snapshot), HUMAN, "r2")
    assert child.material.facts.pairs[0].pair.target.raw_text == "9 m"
    assert child.material.facts.pairs[0].pair.target.evidence[0].confidence == 0
    assert child.material.facts.pairs[0].target_reliability.confirmation is None


def test_confirm_creates_single_child_preserves_ledger_and_confirms_only_exact_side():
    material = synthetic_material()
    pair = material.facts.pairs[0]
    pair.pair.target.confidence = 0.2
    pair.pair.target.evidence[0].confidence = 0.1
    confirm_side(pair, "comparable", reviewer="previous-reviewer")
    mapping = subjects(material)
    snapshot = RevisionSnapshot.capture(material, "r1")
    corrected = mapping.correct(snapshot, proposal(mapping, snapshot), HUMAN, "r2")
    before = mapping.side_reference(corrected, "road-target")
    child = mapping.confirm(corrected, "road-target", HUMAN, "r3")
    assert child.revision.parent == corrected.revision.reference
    assert child.revision.reference.revision_id == "r3"
    assert child.revision.reference.material_digest != corrected.revision.reference.material_digest
    assert child.revision.changes == corrected.revision.changes
    assert child.material.facts.pairs[0].pair == corrected.material.facts.pairs[0].pair
    assert mapping.side_reference(child, "road-target") == before
    pair = child.material.facts.pairs[0]
    assert pair.target_reliability.confirmation.reviewer == HUMAN.actor_id
    assert pair.target_reliability.confirmation.input_digest == before.input_digest
    assert pair.target_reliability.method == "reviewer_confirmed"
    assert pair.comparable_reliability.confirmation is None
    assert pair.pair.target.confidence == 0.2
    assert pair.pair.target.evidence[0].confidence == 0.1
    assert corrected.material.facts.pairs[0].target_reliability.confirmation is None
    assert (
        CaseReviewer(None)
        .review(child.material.policy, child.material.facts, child.material.policy.registry)
        .status
        == "needs_review"
    )


def test_confirmation_rejects_wrong_source_purpose():
    material = synthetic_material()
    material.facts.pairs[0].target_sources = material.policy.rule_sets[0].evidence
    mapping = subjects(material)
    snapshot = RevisionSnapshot.capture(material, "r1")
    with pytest.raises(ValueError, match="selected case forms"):
        mapping.confirm(snapshot, "road-target", HUMAN, "r2")


def test_sequential_confirmations_keep_unchanged_sides_from_same_human():
    material = synthetic_material()
    pair = material.facts.pairs[0]
    for side in ("target", "comparable"):
        getattr(pair, f"{side}_reliability").method = "model_proposed"
        getattr(pair.pair, side).confidence = 0.1
    mapping = subjects(material)
    snapshot = RevisionSnapshot.capture(material, "r1")
    target = mapping.confirm(snapshot, "road-target", HUMAN, "r2")
    comparable = mapping.confirm(
        target,
        "road-comparable",
        HUMAN,
        "r3",
        retained_confirmations=(mapping.side_reference(target, "road-target"),),
    )
    assert comparable.revision.parent == target.revision.reference
    result = comparable.material.facts.pairs[0]
    assert (
        result.target_reliability.confirmation
        == target.material.facts.pairs[0].target_reliability.confirmation
    )
    for side in ("target", "comparable"):
        reliability = getattr(result, f"{side}_reliability")
        assert reliability.method == "reviewer_confirmed"
        assert reliability.confirmation.reviewer == HUMAN.actor_id
        assert getattr(result.pair, side).confidence == 0.1
    changed = mapping.correct(comparable, proposal(mapping, comparable), HUMAN, "r4")
    assert changed.material.facts.pairs[0].target_reliability.confirmation is None
    assert changed.material.facts.pairs[0].comparable_reliability.confirmation is None


@pytest.mark.parametrize("invalid", ["actor", "digest", "provenance", "unresolved", "ambiguous"])
def test_confirmation_does_not_carry_invalid_or_other_actor_assertions(invalid):
    material = synthetic_material()
    pair = material.facts.pairs[0]
    confirm_side(pair, "target", reviewer=HUMAN.actor_id)
    prior = pair.target_reliability
    if invalid == "actor":
        prior.confirmation.reviewer = "another-reviewer"
    elif invalid == "digest":
        prior.confirmation.input_digest = "0" * 64
    else:
        if invalid == "provenance":
            prior.provenance = "unknown"
        elif invalid == "unresolved":
            prior.unresolved = ["manual check required"]
        else:
            prior.selection = "ambiguous"
        from appraisal_review.domain.confidence import confirmation_digest

        prior.confirmation.input_digest = confirmation_digest(pair, "target")
    mapping = subjects(material)
    snapshot = RevisionSnapshot.capture(material, "r1")
    child = mapping.confirm(
        snapshot,
        "road-comparable",
        HUMAN,
        "r2",
        retained_confirmations=(mapping.side_reference(snapshot, "road-target"),),
    )
    assert child.material.facts.pairs[0].target_reliability.confirmation is None
    assert child.material.facts.pairs[0].comparable_reliability.confirmation is not None


def test_unsigned_bootstrap_confirmation_metadata_is_not_a_carry_forward_grant():
    material = synthetic_material()
    confirm_side(material.facts.pairs[0], "target", reviewer=HUMAN.actor_id)
    mapping = subjects(material)
    snapshot = RevisionSnapshot.capture(material, "r1")
    child = mapping.confirm(snapshot, "road-comparable", HUMAN, "r2")
    assert child.material.facts.pairs[0].target_reliability.confirmation is None
    assert child.material.facts.pairs[0].comparable_reliability.confirmation is not None


@pytest.mark.parametrize("operation", ["correct", "confirm"])
def test_revision_id_must_change(operation):
    material = synthetic_material()
    mapping = subjects(material)
    snapshot = RevisionSnapshot.capture(material, "r1")
    argument = proposal(mapping, snapshot) if operation == "correct" else "road-target"
    with pytest.raises(ValueError, match="replace its parent"):
        getattr(mapping, operation)(snapshot, argument, HUMAN, "r1")


def test_subject_map_detaches_nested_context_and_rejects_unknown_or_ambiguous_subjects():
    material = synthetic_material()
    pair = material.facts.pairs[0]
    binding = MaterialSubject("road-target", pair.context, pair.pair.factor_id, "target")
    mapping = MaterialSubjectMap((binding,))
    snapshot = RevisionSnapshot.capture(material, "r1")
    binding.context.target_id = "changed-outside-map"
    assert mapping.public_value(snapshot, "road-target").value.value == 10
    with pytest.raises(FrozenInstanceError):
        mapping._bindings = ()
    with pytest.raises(ValueError, match="Duplicate"):
        MaterialSubjectMap((binding, binding))
    with pytest.raises(ValueError, match="Invalid"):
        MaterialSubjectMap((MaterialSubject("", pair.context, pair.pair.factor_id, "target"),))
    with pytest.raises(ValueError, match="Unknown"):
        mapping.public_value(snapshot, "road-target/other")
    for pairs in ([], [snapshot.material.facts.pairs[0]] * 2):
        invalid = snapshot.material
        invalid.facts.pairs = pairs
        invalid_snapshot = RevisionSnapshot.capture(invalid, "invalid")
        with pytest.raises(ValueError, match="exactly one"):
            mapping.side_reference(invalid_snapshot, "road-target")


def test_public_missing_value_preserves_empty_text_and_raw_confidence():
    material = synthetic_material()
    observation = material.facts.pairs[0].pair.target
    observation.value = None
    observation.raw_text = None
    observation.confidence = 0
    mapping = subjects(material)
    snapshot = RevisionSnapshot.capture(material, "r1")
    projected = mapping.public_value(snapshot, "road-target")
    assert projected.state == "missing"
    assert projected.value is projected.unit is None
    assert projected.raw_text == ""
    assert projected.confidence == 0
    assert projected.evidence == tuple(material.facts.pairs[0].target_sources)
