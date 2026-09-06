"""Synthetic adversarial whole-case fixtures, independent from calculation results."""

import asyncio
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from appraisal_review.adapters.local.synthetic import synthetic_material, synthetic_request
from appraisal_review.application.controller import ReviewAgentController
from appraisal_review.domain.case_review import CaseReviewer
from appraisal_review.domain.factor_models import Grade, ReviewMaterial
from appraisal_review.domain.review_contracts import (
    ArithmeticCheck,
    EmptyColumn,
    ObservedValue,
    ReviewSlot,
    content_digest,
)
from appraisal_review.ports.workflow import ParsedDocument


class ApprovedFixture:
    """Isolated test authority pins the exact material under examination."""

    def __init__(self, material):
        self.digest = content_digest(material)

    def permits(self, material):
        return content_digest(material) == self.digest


def evaluate(material, *, claimed=None, registry=None):
    return CaseReviewer(ApprovedFixture(material)).review(
        material.policy, material.facts, registry or material.policy.registry, claimed=claimed
    )


def arithmetic_material():
    material = synthetic_material()
    policy, facts = material.policy, material.facts
    context = policy.inventory.contexts[0].context
    refs = policy.inventory.slots[0].evidence
    for name, kind in [("subtotal", "subtotal"), ("total", "total"), ("copied", "subtotal")]:
        policy.inventory.slots.append(
            ReviewSlot(id=name, context=context, value=kind, evidence=refs)
        )
        facts.observed.append(
            ObservedValue(
                slot_id=name,
                state="present",
                value="5",
                unit="percent_points",
                raw_text=refs[0].excerpt,
                evidence=refs,
            )
        )
    policy.inventory.checks = [
        ArithmeticCheck(
            id="group", kind="sum", inputs=["road-rate"], target="subtotal", evidence=refs
        ),
        ArithmeticCheck(id="sum", kind="sum", inputs=["subtotal"], target="total", evidence=refs),
        ArithmeticCheck(
            id="cross-form", kind="equals", inputs=["total"], target="copied", evidence=refs
        ),
    ]
    return material


def controller_run(material, *, writer=None, minimum_confidence=0.85):
    documents = {
        d.uri: ParsedDocument(document_uri=d.uri, page_count=len(d.pages), source=d)
        for d in material.policy.registry.documents
    }
    controller = ReviewAgentController(
        parser=AsyncMock(parse_document=AsyncMock(side_effect=lambda uri: documents[uri])),
        rule_provider=AsyncMock(load_or_build_rules=AsyncMock(return_value=material.policy)),
        fact_extractor=AsyncMock(extract_facts=AsyncMock(return_value=material.facts)),
        authorization=ApprovedFixture(material),
        pdf_writer=writer,
        minimum_confidence=minimum_confidence,
    )
    return asyncio.run(controller.review(synthetic_request("completed")))


def test_complete_approved_material_and_independent_result_pass():
    material = arithmetic_material()
    result = evaluate(material)
    assert result.status.value == "verified"
    assert not result.coverage.missing
    assert result.comparisons[0].results[0].adjustment_percent == 5
    assert {f.id for f in result.findings if f.kind == "arithmetic"} == {
        "arithmetic/group",
        "arithmetic/sum",
        "arithmetic/cross-form",
    }
    assert evaluate(material, claimed=result.comparisons).status.value == "verified"
    assert ReviewMaterial.model_validate_json(material.model_dump_json()) == material


@pytest.mark.parametrize(
    "tamper",
    [
        "rule_set",
        "version",
        "rule",
        "case",
        "source",
        "grade",
        "matrix_rate",
        "summary",
        "duplicate",
        "missing",
        "nan",
        "infinity",
    ],
)
def test_forged_claims_rejected_independently(tamper):
    material = synthetic_material()
    claim = evaluate(material).comparisons[0].model_copy(deep=True)
    item = claim.results[0]
    if tamper == "rule_set":
        claim.rule_set_id = "other"
    elif tamper == "version":
        claim.rule_version = "other"
    elif tamper == "rule":
        item.rule_id = "other"
    elif tamper == "case":
        claim.case_id = "other"
    elif tamper == "source":
        claim.source_hashes = {}
    elif tamper == "grade":
        item.target_grade = Grade.INFERIOR
    elif tamper == "matrix_rate":
        item.adjustment_percent = 999
    elif tamper == "summary":
        claim.summary.total_adjustment_percent = 999
    elif tamper == "duplicate":
        claim.results.append(item)
    elif tamper == "missing":
        claim.results = []
    elif tamper == "nan":
        item.adjustment_percent = float("nan")
    else:
        claim.summary.total_adjustment_percent = float("inf")
    result = evaluate(material, claimed=[claim])
    assert result.status.value == "failed"
    assert any(f.id == "claimed_results" and f.status == "failed" for f in result.findings)


@pytest.mark.parametrize(
    "condition",
    [
        "zero_rules",
        "multiple",
        "district",
        "zone",
        "category",
        "before",
        "after",
        "source_version",
        "source_hash",
        "missing_factor",
        "omitted_table",
        "missing_page",
        "unsupported",
        "duplicate_fact",
        "duplicate_slot",
        "duplicate_observed",
        "unknown",
        "model_confidence",
        "selection",
        "unit",
        "citation",
        "case_version",
    ],
)
def test_invalid_material_cannot_complete_even_when_reviewed(condition):
    material = synthetic_material()
    policy, facts = material.policy, material.facts
    rules = policy.rule_sets[0]
    if condition == "zero_rules":
        rules.context.comparable_id = "absent"
    elif condition == "multiple":
        policy.rule_sets.append(rules.model_copy(deep=True))
    elif condition == "district":
        rules.rules.applicability.jurisdiction = "other"
    elif condition == "zone":
        rules.zone = "other"
    elif condition == "category":
        rules.rules.applicability.land_use_category = "other"
    elif condition == "before":
        rules.rules.applicability.effective_from = date(2027, 1, 1)
    elif condition == "after":
        rules.rules.applicability.effective_to = date(2025, 1, 1)
    elif condition == "source_version":
        rules.source_version = "2"
    elif condition == "source_hash":
        rules.rules.source_document.content_hash = "0" * 64
    elif condition == "missing_factor":
        facts.pairs = []
    elif condition == "omitted_table":
        other = policy.inventory.contexts[0].model_copy(deep=True)
        other.context.comparable_id = "existing-second-comparable"
        policy.inventory.contexts.append(other)
    elif condition == "missing_page":
        policy.inventory.inspected_pages = {}
    elif condition == "unsupported":
        policy.inventory.unsupported = ["unmapped mandatory table"]
    elif condition == "duplicate_fact":
        facts.pairs.append(facts.pairs[0])
    elif condition == "duplicate_slot":
        policy.inventory.slots.append(policy.inventory.slots[0])
    elif condition == "duplicate_observed":
        facts.observed.append(facts.observed[0])
    elif condition == "unknown":
        facts.pairs[0].pair.factor_id = "unknown"
    elif condition == "model_confidence":
        facts.pairs[0].target_reliability.method = "model_proposed"
        facts.pairs[0].target_reliability.model_confidence = 0.99
    elif condition == "selection":
        facts.pairs[0].target_reliability.selection = "ambiguous"
    elif condition == "unit":
        facts.pairs[0].pair.target.value.unit = "ambiguous km or m"
    elif condition == "citation":
        facts.pairs[0].target_sources[0].region_id = "invented"
    else:
        facts.identity.version = "2"
    result = evaluate(material)
    assert result.status.value in {"failed", "needs_review"}
    writer = AsyncMock()
    run = controller_run(material, writer=writer)
    assert run.status.value in {"failed", "needs_review"}
    assert run.case_review.findings
    writer.write_pdf.assert_not_called()


@pytest.mark.parametrize("slot", ["road-rate", "subtotal", "total", "copied"])
@pytest.mark.parametrize("state", ["wrong", "missing", "blank", "not_present", "not_applicable"])
def test_observed_arithmetic_and_cross_form_controller_fail_missing(slot, state):
    material = arithmetic_material()
    observed = next(v for v in material.facts.observed if v.slot_id == slot)
    if state == "wrong":
        observed.value = "999"
    else:
        observed.state, observed.value = state, None
    writer = AsyncMock()
    run = controller_run(material, writer=writer)
    assert run.status.value == ("failed" if state == "wrong" else "needs_review")
    assert run.case_review.coverage.missing
    writer.write_pdf.assert_not_called()


def test_scope_multicomparable_and_artifact_capability_are_explicit():
    material = synthetic_material()
    policy, facts = material.policy, material.facts
    second = policy.inventory.contexts[0].model_copy(deep=True)
    second.context.scope = "individual"
    second.context.comparable_id = "second"
    rule = policy.rule_sets[0].model_copy(deep=True)
    rule.context = second.context
    # Same factor ID, deliberately different scope threshold; no key collision.
    rule.rules.rules[0].intervals[0].maximum = 20
    rule.rules.rules[0].intervals[1].minimum = 20
    pair = facts.pairs[0].model_copy(deep=True)
    pair.context = second.context
    slot = policy.inventory.slots[0].model_copy(deep=True)
    slot.id, slot.context = "second-rate", second.context
    observed = facts.observed[0].model_copy(deep=True)
    observed.slot_id, observed.value = slot.id, "0"
    policy.inventory.contexts.append(second)
    policy.rule_sets.append(rule)
    policy.inventory.slots.append(slot)
    facts.pairs.append(pair)
    facts.observed.append(observed)
    policy.inventory.empty_columns.append(EmptyColumn(id="unused-third", evidence=slot.evidence))
    writer = AsyncMock()
    run = controller_run(material, writer=writer)
    assert run.status.value == "verified"
    assert run.artifact_status == "unsupported_contexts"
    assert run.review is None and run.output_pdf_uri is None
    assert [c.summary.total_adjustment_percent for c in run.case_review.comparisons] == [5, 0]
    writer.write_pdf.assert_not_called()


def test_approval_binds_every_input_and_does_not_accept_status_flag():
    material = synthetic_material()
    auth = ApprovedFixture(material)
    material.policy.rule_sets[0].rules.status = "approved"
    result = CaseReviewer(auth).review(material.policy, material.facts, material.policy.registry)
    assert result.status.value == "needs_review"
    assert any(f.kind == "approval" and f.status == "needs_review" for f in result.findings)


def test_blank_output_is_derivable_but_missing_fact_is_not():
    material = synthetic_material()
    from appraisal_review.domain.document_models import SourceRegion

    material.policy.inventory.slots[0].derivable_blank = True
    source = material.policy.registry.documents[1]
    source.pages[0].regions.append(
        SourceRegion(id="blank-output", kind="cell", bbox=(1, 50, 90, 60))
    )
    ref = material.facts.observed[0].evidence[0].model_copy(deep=True)
    ref.region_id, ref.bbox, ref.excerpt = "blank-output", (1, 50, 90, 60), ""
    material.facts.observed[0].evidence = [ref]
    material.policy.inventory.slots[0].evidence = [ref]
    material.facts.observed[0].state, material.facts.observed[0].value = "blank", None
    material.facts.observed[0].raw_text = ""
    assert evaluate(material).status.value == "verified"
    material.facts.pairs[0].pair.target.value = None
    assert evaluate(material).status.value == "needs_review"


def test_percent_ratio_zero_boundary_dates_and_unit_conversion():
    material = synthetic_material()
    rules = material.policy.rule_sets[0].rules
    rules.applicability.effective_from = material.policy.identity.effective_date
    rules.applicability.effective_to = material.policy.identity.effective_date
    target = material.facts.pairs[0].pair.target.value
    target.value, target.unit = 0.01, "km"
    material.facts.observed[0].value, material.facts.observed[0].unit = "0.05", "ratio"
    assert evaluate(material).status.value == "verified"
    target.value = 0.009
    material.facts.observed[0].value = "0"
    assert evaluate(material).status.value == "verified"


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_values_are_rejected_at_contract_boundary(invalid):
    material = synthetic_material()
    material.facts.pairs[0].pair.target.value.value = invalid
    with pytest.raises(ValidationError):
        ReviewMaterial.model_validate(material.model_dump())
    assert evaluate(material).status.value == "failed"


def test_explicit_rounding_and_matrix_orientation():
    from appraisal_review.domain.rule_engine import calculate

    assert calculate("sum", [Decimal("1.005")], quantum=Decimal("0.01")) == Decimal("1.01")
    material = synthetic_material()
    pair = material.facts.pairs[0].pair
    pair.target, pair.comparable = pair.comparable, pair.target
    material.facts.observed[0].value = "-5"
    assert evaluate(material).comparisons[0].summary.total_adjustment_percent == -5
    assert evaluate(material).status.value == "verified"


@pytest.mark.parametrize(
    "side,expected", [("target_grade", "excellent"), ("comparable_grade", "inferior")]
)
def test_observed_grade_is_separate_from_expected(side, expected):
    material = synthetic_material()
    slot = material.policy.inventory.slots[0].model_copy(deep=True)
    slot.id, slot.value = side, side
    value = material.facts.observed[0].model_copy(deep=True)
    value.slot_id, value.unit, value.value = side, "grade", expected
    material.policy.inventory.slots.append(slot)
    material.facts.observed.append(value)
    assert evaluate(material).status.value == "verified"
    value.value = "normal"
    assert evaluate(material).status.value == "failed"


def test_parser_source_update_invalidates_old_registry_even_with_approval():
    material = synthetic_material()
    current = material.policy.registry.model_copy(deep=True)
    current.documents[1].content_hash = "a" * 64
    assert evaluate(material, registry=current).status.value == "failed"


def test_field_map_cannot_relabel_verified_context():
    from appraisal_review.domain.pdf_models import PDFWriteRequest

    request = synthetic_request("completed")
    request.field_map.fields[0].value_ref.comparable_id = "wrong-comparable"
    with pytest.raises(ValueError, match="verified comparison"):
        PDFWriteRequest(
            source_uri=request.case_document_uri,
            destination_uri=request.output_pdf_uri,
            field_map=request.field_map,
            result=evaluate(synthetic_material()).comparisons[0],
        )


def test_legacy_evidence_cannot_disagree_with_source_citations():
    material = synthetic_material()
    material.facts.pairs[0].pair.target.evidence[0].page = 99
    assert evaluate(material).status.value == "needs_review"


def test_blank_cell_location_is_valid_without_invented_text():
    from appraisal_review.domain.document_models import SourceCitation, SourceRegion

    material = synthetic_material()
    source = material.policy.registry.documents[1]
    source.pages[0].regions.append(SourceRegion(id="blank", kind="cell", bbox=(1, 50, 90, 60)))
    ref = SourceCitation(
        document_id=source.document_id,
        content_hash=source.content_hash,
        version=source.version,
        page=1,
        region_id="blank",
        bbox=(1, 50, 90, 60),
        excerpt="",
    )
    assert material.policy.registry.resolves(ref)
    ref.excerpt = "invented value"
    assert not material.policy.registry.resolves(ref)


def test_unaccounted_table_cannot_pass_from_successful_factors_only():
    from appraisal_review.domain.document_models import SourceRegion

    material = synthetic_material()
    material.policy.registry.documents[1].pages[0].regions.append(
        SourceRegion(
            id="omitted-table",
            kind="cell",
            table_id="another-table",
            row=0,
            column=0,
            bbox=(1, 50, 90, 60),
        )
    )
    assert evaluate(material).status.value == "needs_review"
    material.policy.inventory.inspected_tables = {"synthetic-forms": ["another-table"]}
    assert evaluate(material).status.value == "verified"


def test_review_slot_cannot_use_unregistered_comparable_through_arithmetic():
    material = arithmetic_material()
    slot = next(s for s in material.policy.inventory.slots if s.id == "copied")
    slot.context = slot.context.model_copy(deep=True)
    slot.context.comparable_id = "unregistered-comparable"
    writer = AsyncMock()
    run = controller_run(material, writer=writer)
    assert not run.verification.can_complete
    assert run.case_review.coverage.missing
    assert run.case_review.findings
    writer.write_pdf.assert_not_called()


def test_configured_confidence_blocks_reviewed_material_below_runtime_threshold():
    material = synthetic_material()
    material.facts.pairs[0].pair.target.confidence = 0.90
    material.facts.pairs[0].pair.comparable.confidence = 0.90
    writer = AsyncMock()
    run = controller_run(material, writer=writer, minimum_confidence=0.95)
    assert not run.verification.can_complete
    assert run.status.value == "needs_review"
    writer.write_pdf.assert_not_called()


def test_arithmetic_tolerance_survives_final_observed_comparison():
    material = arithmetic_material()
    next(v for v in material.facts.observed if v.slot_id == "copied").value = "5.005"
    material.policy.inventory.checks[-1].tolerance = Decimal("0.01")
    run = controller_run(material)
    assert run.verification.can_complete
    assert run.status.value == "verified"
    assert not run.case_review.coverage.missing


@pytest.mark.parametrize(
    "binding", ["target", "scope", "unknown-factor", "missing-factor", "aggregate-factor"]
)
def test_invalid_slot_bindings_are_located_and_excluded_from_arithmetic(binding):
    material = arithmetic_material()
    slot = material.policy.inventory.slots[-1]
    slot.context = slot.context.model_copy(deep=True)
    if binding == "target":
        slot.context.target_id = "unregistered-target"
    elif binding == "scope":
        slot.context.scope = "individual"
    elif binding == "aggregate-factor":
        slot.factor_id = "synthetic.road_width"
    else:
        slot.value = "adjustment_percent"
        slot.factor_id = "unknown" if binding == "unknown-factor" else None
    writer = AsyncMock()
    run = controller_run(material, writer=writer)
    findings = {f.id: f for f in run.case_review.findings}
    assert not run.verification.can_complete
    assert findings["observed/copied"].kind == "slot_binding"
    assert findings["observed/copied"].context == slot.context
    assert findings["arithmetic/cross-form"].kind == "arithmetic_definition"
    assert "observed/copied" in run.case_review.coverage.missing
    writer.write_pdf.assert_not_called()


def test_cross_form_between_two_registered_complete_contexts_remains_valid():
    material = arithmetic_material()
    policy, facts = material.policy, material.facts
    entry = policy.inventory.contexts[0].model_copy(deep=True)
    entry.context.scope, entry.context.target_id, entry.context.comparable_id = (
        "individual",
        "second-target",
        "second-comparable",
    )
    scoped = policy.rule_sets[0].model_copy(deep=True)
    pair = facts.pairs[0].model_copy(deep=True)
    rate = policy.inventory.slots[0].model_copy(deep=True)
    observed = facts.observed[0].model_copy(deep=True)
    scoped.context = pair.context = rate.context = entry.context
    rate.id = observed.slot_id = "second-rate"
    policy.inventory.contexts.append(entry)
    policy.rule_sets.append(scoped)
    policy.inventory.slots.append(rate)
    facts.pairs.append(pair)
    facts.observed.append(observed)
    next(s for s in policy.inventory.slots if s.id == "copied").context = entry.context
    writer = AsyncMock()
    run = controller_run(material, writer=writer)
    assert run.verification.can_complete
    assert not run.case_review.coverage.missing
    assert len(run.case_review.comparisons) == 2
    assert run.artifact_status == "unsupported_contexts"
    writer.write_pdf.assert_not_called()
    events = {event.event_type: event for event in run.audit_events}
    assert len(events["rules_loaded"].details["rule_sets"]) == 2
    actual = events["factors_evaluated"].details["comparisons"]
    assert {tuple(c["context"].values()) for c in actual} == {
        tuple(c.context.model_dump().values()) for c in policy.inventory.contexts
    }
    assert all(c["version"] == "1.0.0" and c["outcome"] == "verified" for c in actual)


@pytest.mark.parametrize("invalid_end", ["inputs", "target"])
def test_arithmetic_requires_structurally_valid_slots_on_both_ends(invalid_end):
    material = arithmetic_material()
    slot = next(s for s in material.policy.inventory.slots if s.id == "total")
    slot.context = slot.context.model_copy(update={"comparable_id": "not-inventory"})
    check = material.policy.inventory.checks[-1]
    check.inputs, check.target = (
        (["total"], "copied") if invalid_end == "inputs" else (["copied"], "total")
    )
    run = controller_run(material)
    finding = next(f for f in run.case_review.findings if f.id == "arithmetic/cross-form")
    assert finding.kind == "arithmetic_definition" and finding.status != "verified"
    assert not run.verification.can_complete


def fake_writer_spy():
    from appraisal_review.adapters.local.fake_pdf import FakePDFWriter

    return AsyncMock(write_pdf=AsyncMock(side_effect=FakePDFWriter().write_pdf))


@pytest.mark.parametrize(
    "confidence,threshold,allowed",
    [(0.90, 0.85, True), (0.90, 0.95, False), (0.95, 0.95, True), (0.96, 0.95, True)],
)
def test_runtime_threshold_controls_calculation_verification_and_writer(
    confidence, threshold, allowed
):
    material = synthetic_material()
    for observation in (
        material.facts.pairs[0].pair.target,
        material.facts.pairs[0].pair.comparable,
    ):
        observation.confidence = confidence
    original = material.model_dump()
    writer = fake_writer_spy()
    run = controller_run(material, writer=writer, minimum_confidence=threshold)
    assert run.verification.can_complete is allowed
    assert run.status.value == ("verified" if allowed else "needs_review")
    assert writer.write_pdf.call_count == int(allowed)
    assert material.model_dump() == original
    assert run.output_pdf_uri is None


@pytest.mark.parametrize("threshold,allowed", [(0.85, True), (0.95, False)])
def test_http_composition_reads_runtime_confidence_setting(monkeypatch, threshold, allowed):
    from fastapi.testclient import TestClient

    from appraisal_review.api.app import create_app
    from appraisal_review.application.bootstrap import ReviewAdapters
    from appraisal_review.config import Settings

    material = synthetic_material()
    for obs in (material.facts.pairs[0].pair.target, material.facts.pairs[0].pair.comparable):
        obs.confidence = 0.90
    documents = {
        d.uri: ParsedDocument(document_uri=d.uri, page_count=len(d.pages), source=d)
        for d in material.policy.registry.documents
    }
    writer = fake_writer_spy()
    adapters = ReviewAdapters(
        mode="local",
        parser=AsyncMock(parse_document=AsyncMock(side_effect=lambda uri: documents[uri])),
        rule_provider=AsyncMock(load_or_build_rules=AsyncMock(return_value=material.policy)),
        fact_extractor=AsyncMock(extract_facts=AsyncMock(return_value=material.facts)),
        authorization=ApprovedFixture(material),
        pdf_writer=writer,
    )
    monkeypatch.setenv("MIN_EXTRACTION_CONFIDENCE", str(threshold))
    response = TestClient(create_app(settings=Settings(_env_file=None), adapters=adapters)).post(
        "/v1/reviews", json=synthetic_request("completed").model_dump(mode="json")
    )
    assert response.status_code == 200
    assert response.json()["status"] == ("verified" if allowed else "needs_review")
    assert writer.write_pdf.call_count == int(allowed)


@pytest.mark.parametrize("threshold", [-0.01, 1.01, float("nan"), float("inf")])
def test_invalid_runtime_threshold_rejected_at_direct_and_configuration_boundaries(threshold):
    from appraisal_review.config import Settings
    from appraisal_review.domain.verification import ReviewVerifier

    material = synthetic_material()
    with pytest.raises(ValueError):
        CaseReviewer(ApprovedFixture(material), minimum_confidence=threshold)
    with pytest.raises(ValueError):
        controller_run(material, minimum_confidence=threshold)
    with pytest.raises(ValueError):
        Settings(min_extraction_confidence=threshold, _env_file=None)
    with pytest.raises(ValueError):
        ReviewVerifier().verify(
            evaluate(material).comparisons[0],
            material.policy.rule_sets[0].rules,
            minimum_confidence=threshold,
        )


@pytest.mark.parametrize(
    "value,tolerance,allowed",
    [
        ("5.005", "0.01", True),
        ("5.01", "0.01", True),
        ("4.99", "0.01", True),
        ("5.0101", "0.01", False),
        ("4.9899", "0.01", False),
        ("5", "0", True),
        ("5.0001", "0", False),
    ],
)
def test_arithmetic_tolerance_boundaries_through_controller(value, tolerance, allowed):
    material = arithmetic_material()
    next(v for v in material.facts.observed if v.slot_id == "copied").value = value
    material.policy.inventory.checks[-1].tolerance = Decimal(tolerance)
    original = material.model_dump()
    writer = fake_writer_spy()
    run = controller_run(material, writer=writer)
    assert run.verification.can_complete is allowed
    assert run.status.value == ("verified" if allowed else "failed")
    assert writer.write_pdf.call_count == int(allowed)
    observed = next(f for f in run.case_review.findings if f.id == "observed/copied")
    assert observed.observed == value and observed.status == ("verified" if allowed else "failed")
    assert "cross-form" in observed.trace
    assert original == material.model_dump()


@pytest.mark.parametrize("copied,allowed", [("5.01", True), ("5.00", False)])
def test_rounded_arithmetic_expected_uses_half_up_before_tolerance(copied, allowed):
    material = arithmetic_material()
    slot = material.policy.inventory.slots[-1].model_copy(deep=True)
    slot.id = "round-input"
    observation = material.facts.observed[-1].model_copy(deep=True)
    observation.slot_id, observation.value = slot.id, "5.005"
    material.policy.inventory.slots.append(slot)
    material.facts.observed.append(observation)
    material.policy.inventory.checks.append(
        ArithmeticCheck(
            id="round-source",
            kind="equals",
            inputs=["road-rate"],
            target=slot.id,
            tolerance=Decimal("0.006"),
            evidence=slot.evidence,
        )
    )
    next(c for c in material.policy.inventory.checks if c.id == "cross-form").inputs = [slot.id]
    next(v for v in material.facts.observed if v.slot_id == "copied").value = copied
    run = controller_run(material)
    assert run.verification.can_complete is allowed
    finding = next(f for f in run.case_review.findings if f.id == "arithmetic/cross-form")
    assert finding.expected == "5.01" and "ROUND_HALF_UP" in finding.trace


@pytest.mark.parametrize("reverse", [True, False])
@pytest.mark.parametrize("tight,allowed", [("0", False), ("0.006", True)])
def test_every_constraint_on_same_target_is_enforced_regardless_of_order(reverse, tight, allowed):
    material = arithmetic_material()
    next(v for v in material.facts.observed if v.slot_id == "copied").value = "5.005"
    check = material.policy.inventory.checks[-1]
    check.tolerance = Decimal("0.01")
    other = check.model_copy(update={"id": "tight-copy", "tolerance": Decimal(tight)})
    material.policy.inventory.checks.append(other)
    if reverse:
        material.policy.inventory.checks.reverse()
    writer = fake_writer_spy()
    run = controller_run(material, writer=writer)
    assert run.verification.can_complete is allowed
    assert writer.write_pdf.call_count == int(allowed)
    findings = {f.id: f for f in run.case_review.findings}
    assert findings["arithmetic/cross-form"].status == "verified"
    assert findings["arithmetic/tight-copy"].status == ("verified" if allowed else "failed")
    assert findings["observed/copied"].status == ("verified" if allowed else "failed")


def test_total_tolerance_does_not_relax_exact_factor_rate():
    material = arithmetic_material()
    for check in material.policy.inventory.checks:
        check.tolerance = Decimal("0.01")
    next(v for v in material.facts.observed if v.slot_id == "total").value = "5.005"
    next(v for v in material.facts.observed if v.slot_id == "copied").value = "5.01"
    assert controller_run(material).verification.can_complete
    material.facts.observed[0].value = "5.005"
    run = controller_run(material)
    assert not run.verification.can_complete
    assert (
        next(f for f in run.case_review.findings if f.id == "observed/road-rate").status == "failed"
    )
