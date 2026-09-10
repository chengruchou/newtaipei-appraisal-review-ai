"""A golden manifest must follow from its fixture, not from a review result."""

import pytest
from pydantic import ValidationError

from appraisal_review.adapters.local.golden_cases import (
    FACTOR_ID,
    REGIONAL,
    RULE_ID,
    RULE_SET_ID,
    FixtureSpec,
    build_material,
    golden_fixtures,
)
from appraisal_review.domain.golden_contract import (
    ExpectedHumanTask,
    GoldenCase,
    GoldenDimension,
)
from appraisal_review.domain.golden_validator import expected_task_contract, verify_manifest
from appraisal_review.domain.review_contracts import content_digest
from appraisal_review.domain.service_contracts import (
    Permission,
    ResponseAction,
    RevisionReference,
    RunReference,
    TaskKind,
)


def case_named(key: str):
    return next(f for f in golden_fixtures() if f.case_key == key)


def slot_index(payload: dict, slot_id: str) -> int:
    return next(
        index for index, slot in enumerate(payload["expected_slots"]) if slot["slot_id"] == slot_id
    )


def test_every_reviewed_dimension_is_covered_by_a_case():
    dimensions = {fixture.case.dimension for fixture in golden_fixtures()}
    assert dimensions == set(GoldenDimension)


def test_matrix_covers_both_completable_and_human_cases():
    fixtures = golden_fixtures()
    completable = [f for f in fixtures if f.case.expected_status == "verified"]
    human = [f for f in fixtures if f.case.expected_tasks]
    assert completable and human
    assert all(not f.case.expected_tasks for f in completable)


def test_normal_case_covers_grade_rate_subtotal_total_copy_and_two_pages():
    case = case_named("normal-complete").case
    assert {slot.slot_value for slot in case.expected_slots} == {
        "target_grade",
        "comparable_grade",
        "adjustment_percent",
        "subtotal",
        "total",
    }
    copies = [s for s in case.expected_slots if s.slot_id.endswith("copied-total")]
    pages = {s.observed.citation.page for s in case.expected_slots if s.observed.citation}
    assert copies and pages == {1, 2}


def test_fixtures_declare_synthetic_provenance_only():
    for fixture in golden_fixtures():
        assert fixture.case.data_use.origin == "synthetic"
        assert fixture.case.data_use.contains_real_case_data is False
        for document in fixture.material.policy.registry.documents:
            assert document.uri.startswith("file:///golden/")


@pytest.mark.parametrize("fixture", golden_fixtures(), ids=lambda f: f.case_key)
def test_manifest_is_rederivable_from_its_fixture(fixture):
    report = verify_manifest(fixture.case, fixture.material)
    assert report.ok, report.describe()


def test_manifest_authored_against_other_material_is_rejected():
    fixture = case_named("normal-complete")
    other = build_material(FixtureSpec(copied_total="7"))
    report = verify_manifest(fixture.case, other)
    assert not report.ok
    assert any(m.check == "fixture" for m in report.mismatches)


def test_expected_value_must_be_printed_in_the_cited_excerpt():
    fixture = case_named("normal-complete")
    material = build_material(FixtureSpec())
    observation = next(v for v in material.facts.observed if v.slot_id.endswith("-rate"))
    observation.value = "9"
    payload = fixture.case.model_dump(mode="json")
    payload["fixture"]["material_digest"] = content_digest(material)
    payload["expected_slots"][slot_index(payload, observation.slot_id)]["observed"]["value"] = "9"
    report = verify_manifest(GoldenCase.model_validate(payload), material)
    assert not report.ok
    assert any("not printed" in m.detail for m in report.mismatches)


def test_a_rewritten_correction_rate_fails_the_matrix_rederivation():
    fixture = case_named("normal-complete")
    payload = fixture.case.model_dump(mode="json")
    payload["expected_slots"][slot_index(payload, "regional-rate")]["independent"]["value"] = "9"
    report = verify_manifest(GoldenCase.model_validate(payload), fixture.material)
    assert not report.ok
    assert any(m.check == "correction" for m in report.mismatches)


def test_a_rewritten_subtotal_fails_the_arithmetic_rederivation():
    fixture = case_named("normal-complete")
    payload = fixture.case.model_dump(mode="json")
    index = slot_index(payload, "regional-subtotal")
    payload["expected_slots"][index]["independent"]["value"] = "9"
    report = verify_manifest(GoldenCase.model_validate(payload), fixture.material)
    assert not report.ok
    assert any(m.check == "arithmetic" for m in report.mismatches)


def test_a_grade_outside_its_band_fails_the_classification_rederivation():
    fixture = case_named("normal-complete")
    payload = fixture.case.model_dump(mode="json")
    slot = payload["expected_slots"][slot_index(payload, "regional-target-grade")]
    slot["independent"]["value"] = "inferior"
    slot["independent"]["classification"]["grade"] = "inferior"
    report = verify_manifest(GoldenCase.model_validate(payload), fixture.material)
    assert not report.ok
    assert any(m.check == "classification" for m in report.mismatches)


def test_an_unresolvable_citation_fails_verification():
    fixture = case_named("normal-complete")
    payload = fixture.case.model_dump(mode="json")
    index = slot_index(payload, "regional-rate")
    payload["expected_slots"][index]["observed"]["citation"]["region_id"] = "not-a-region"
    report = verify_manifest(GoldenCase.model_validate(payload), fixture.material)
    assert not report.ok
    assert any(m.check == "citation" for m in report.mismatches)


def test_a_disputed_question_cannot_ground_an_expected_value():
    fixture = case_named("conflicting-sources")
    payload = fixture.case.model_dump(mode="json")
    payload["adjudications"][0]["outcome"] = "disputed"
    payload["adjudications"][0]["decision"] = None
    with pytest.raises(ValidationError, match="unresolved"):
        GoldenCase.model_validate(payload)


def test_a_disputed_question_stays_recorded_as_unresolved():
    fixture = case_named("missing-observation")
    payload = fixture.case.model_dump(mode="json")
    payload["unresolved"] = []
    with pytest.raises(ValidationError, match="unresolved"):
        GoldenCase.model_validate(payload)


def test_an_agreed_adjudication_must_record_its_decision():
    fixture = case_named("conflicting-sources")
    payload = fixture.case.model_dump(mode="json")
    payload["adjudications"][0]["decision"] = None
    with pytest.raises(ValidationError, match="agreed adjudication"):
        GoldenCase.model_validate(payload)


def test_a_task_cannot_block_on_a_verified_finding():
    fixture = case_named("missing-page")
    payload = fixture.case.model_dump(mode="json")
    payload["expected_tasks"][0]["finding_ids"] = ["sources"]
    with pytest.raises(ValidationError, match="verified finding"):
        GoldenCase.model_validate(payload)


def test_declared_status_must_agree_with_the_expected_findings():
    fixture = case_named("conflicting-sources")
    payload = fixture.case.model_dump(mode="json")
    payload["expected_status"] = "needs_review"
    with pytest.raises(ValidationError, match="Declared case status"):
        GoldenCase.model_validate(payload)


def test_a_verified_field_needs_an_independent_expectation():
    fixture = case_named("normal-complete")
    payload = fixture.case.model_dump(mode="json")
    payload["expected_slots"][slot_index(payload, "regional-rate")]["independent"] = None
    with pytest.raises(ValidationError, match="independent expected value"):
        GoldenCase.model_validate(payload)


def test_an_expected_task_must_match_the_frozen_service_contract():
    fixture = case_named("zero-confidence")
    task = fixture.case.expected_tasks[0]
    run = RunReference(
        run_id=__import__("uuid").uuid4(),
        revision=RevisionReference(
            case_id=fixture.case.identity.case_id,
            revision_id=fixture.case.identity.version,
            material_digest=content_digest(fixture.material),
        ),
    )
    contract = expected_task_contract(fixture.case, task, fixture.material, run)
    assert contract.kind is TaskKind.FACT
    assert contract.side is not None and contract.side.factor_id == FACTOR_ID
    mismatched = ExpectedHumanTask(
        task_ref=task.task_ref,
        kind=TaskKind.FACT,
        required_permission=Permission.APPROVE_MATERIAL,
        allowed_responses=(ResponseAction.CONFIRM,),
        finding_ids=task.finding_ids,
        side=task.side,
        question=task.question,
        rationale=task.rationale,
    )
    with pytest.raises(ValidationError):
        expected_task_contract(fixture.case, mismatched, fixture.material, run)


def test_rule_candidacy_and_material_approval_are_separate_facts():
    approved = case_named("normal-complete").case.expected_rules[0]
    unapproved = case_named("unapproved-material").case.expected_rules[0]
    assert (approved.rule_set_id, approved.version) == (RULE_SET_ID, "1.0.0")
    assert approved.authored_status == unapproved.authored_status == "candidate"
    assert approved.business_approval == unapproved.business_approval == "pending"
    assert approved.material_authority == "exact_material_approved"
    assert unapproved.material_authority == "not_approved"


def test_rule_identity_is_stable_across_the_matrix():
    for fixture in golden_fixtures():
        rule = next(
            r for s in fixture.material.policy.rule_sets for r in s.rules.rules if r.id == RULE_ID
        )
        assert rule.factor_id == FACTOR_ID
        inventoried = [entry.context for entry in fixture.material.policy.inventory.contexts]
        assert all(slot.context in inventoried for slot in fixture.case.expected_slots)
        assert REGIONAL in inventoried


# Soundness regressions. Each of these manifests was accepted by an earlier validator that
# checked a derivation for internal consistency without binding it back to the fixture.


def test_a_consistently_inverted_chain_cannot_re_derive():
    """Inverting the grade pair and every value that follows it must still be caught."""
    fixture = case_named("normal-complete")
    payload = fixture.case.model_dump(mode="json")
    for slot in payload["expected_slots"]:
        if slot["slot_id"] == "regional-rate":
            slot["independent"]["value"] = "-5"
            slot["independent"]["correction"]["target_grade"] = "inferior"
            slot["independent"]["correction"]["comparable_grade"] = "excellent"
        elif slot["slot_id"] in {"regional-subtotal", "regional-total", "regional-copied-total"}:
            slot["independent"]["value"] = "-5"
    report = verify_manifest(GoldenCase.model_validate(payload), fixture.material)
    assert not report.ok
    assert any(m.check in {"correction", "summary"} for m in report.mismatches)


def test_a_grade_cannot_be_grounded_on_the_other_side():
    fixture = case_named("normal-complete")
    payload = fixture.case.model_dump(mode="json")
    slot = payload["expected_slots"][slot_index(payload, "regional-target-grade")]
    slot["independent"]["classification"]["side"] = "comparable"
    slot["independent"]["classification"]["measurement"] = "8"
    slot["independent"]["classification"]["grade"] = "inferior"
    slot["independent"]["value"] = "inferior"
    report = verify_manifest(GoldenCase.model_validate(payload), fixture.material)
    assert not report.ok
    assert any("cannot be grounded on" in m.detail for m in report.mismatches)


def test_a_circular_derivation_grounds_nothing():
    fixture = case_named("normal-complete")
    payload = fixture.case.model_dump(mode="json")
    circular = {
        "regional-subtotal": "regional-total",
        "regional-total": "regional-subtotal",
    }
    for slot in payload["expected_slots"]:
        source = circular.get(slot["slot_id"])
        if source is None:
            continue
        slot["independent"] = {
            "basis": "arithmetic_derivation",
            "value": "9",
            "classification": None,
            "correction": None,
            "summary": None,
            "arithmetic": {
                "operation": "sum",
                "input_slot_ids": [source],
                "quantum": "0.01",
            },
            "adjudication_id": None,
            "rationale": "Circular derivation under test.",
        }
    report = verify_manifest(GoldenCase.model_validate(payload), fixture.material)
    assert not report.ok
    assert any("circular" in m.detail for m in report.mismatches)


def test_a_citation_must_be_the_slots_own_source_cell():
    fixture = case_named("normal-complete")
    payload = fixture.case.model_dump(mode="json")
    total = payload["expected_slots"][slot_index(payload, "regional-total")]
    subtotal = payload["expected_slots"][slot_index(payload, "regional-subtotal")]
    subtotal["observed"]["citation"] = total["observed"]["citation"]
    report = verify_manifest(GoldenCase.model_validate(payload), fixture.material)
    assert not report.ok
    assert any("own reviewed source cells" in m.detail for m in report.mismatches)


def test_an_arithmetic_input_needs_its_own_grounded_expectation():
    """A derivation may not fall back to the printed value of an untrusted field."""
    fixture = case_named("normal-complete")
    payload = fixture.case.model_dump(mode="json")
    payload["expected_slots"][slot_index(payload, "regional-total")]["independent"] = None
    payload["expected_slots"][slot_index(payload, "regional-total")]["expected_status"] = (
        "needs_review"
    )
    for finding in payload["expected_findings"]:
        if finding["id"] == "observed/regional-total":
            finding["status"] = "needs_review"
    payload["expected_status"] = "needs_review"
    payload["expected_coverage"]["missing"] = sorted(
        {*payload["expected_coverage"]["missing"], "observed/regional-total"}
    )
    payload["expected_verification"]["status"] = "needs_review"
    payload["expected_verification"]["critical"] = [
        {
            "code": "verification_blocker",
            "message": (
                "Verification could not pass; inspect review findings or request human review."
            ),
        }
    ]
    payload["expected_artifact"] = {
        "artifact_status": "not_requested",
        "field_ids": [],
        "context": None,
        "rationale": "Not exercised by this regression.",
    }
    report = verify_manifest(GoldenCase.model_validate(payload), fixture.material)
    assert not report.ok
    assert any("independently grounded expectation" in m.detail for m in report.mismatches)


def test_a_table_binding_change_moves_the_fixture_hash():
    """The document hash must cover table binding, or a table edit leaves manifests matching."""
    from appraisal_review.adapters.local import golden_cases

    material = build_material(FixtureSpec())
    forms = golden_cases.forms_document(material)
    before = forms.content_hash
    rebuilt = golden_cases._document(
        forms.document_id,
        forms.uri,
        "forms",
        [
            page.model_copy(
                update={
                    "regions": [
                        region.model_copy(update={"table_id": None}) if region.table_id else region
                        for region in page.regions
                    ]
                }
            )
            for page in forms.pages
        ],
    )
    assert rebuilt.content_hash != before
