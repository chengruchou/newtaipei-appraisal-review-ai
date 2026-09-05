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


def controller_run(material, *, writer=None):
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
    material.policy.inventory.slots[0].derivable_blank = True
    material.facts.observed[0].state, material.facts.observed[0].value = "blank", None
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
