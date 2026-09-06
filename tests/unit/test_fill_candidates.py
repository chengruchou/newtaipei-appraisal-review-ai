"""Each blank has a real distinct cell; regressions isolate candidate validation."""

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from test_case_review import arithmetic_material, controller_run
from test_review_trust_boundaries import cell

from appraisal_review.adapters.local.fake_pdf import FakePDFWriter
from appraisal_review.domain.pdf_models import PDFWriteResult


def located_material(*, blanks=(), copied="5"):
    material = arithmetic_material()
    for slot, observation in zip(
        material.policy.inventory.slots, material.facts.observed, strict=True
    ):
        text = copied if slot.id == "copied" else "5"
        if slot.id in blanks:
            observation.state, observation.value, observation.unit = "blank", None, None
            slot.derivable_blank, text = True, ""
        else:
            observation.value = text
        ref = cell(material, f"cell-{slot.id}", text)
        slot.evidence = observation.evidence = [ref]
        observation.raw_text = text
    return material


def success_spy():
    # Boundary metadata only: this spy never creates a PDF.
    return AsyncMock(
        write_pdf=AsyncMock(
            side_effect=lambda request: PDFWriteResult(
                output_uri=request.destination_uri,
                page_count=1,
                written_field_ids=[field.field_id for field in request.field_map.fields],
                artifact_created=True,
            )
        )
    )


def test_blank_total_must_satisfy_independent_total_and_all_constraints():
    material = located_material(blanks={"total"}, copied="10")
    material.policy.inventory.checks[1].inputs = ["subtotal", "road-rate"]
    before = material.model_dump_json()
    writer = success_spy()
    run = controller_run(material, writer=writer)
    assert not run.verification.can_complete
    assert run.status.value not in {"verified", "completed"}
    writer.write_pdf.assert_not_called()
    assert material.model_dump_json() == before
    findings = {f.id: f for f in run.case_review.findings}
    assert Decimal(findings["arithmetic/sum"].expected) == 10
    assert Decimal(findings["observed/total"].expected) == 5
    assert findings["observed/total"].status != "verified"
    assert findings["arithmetic/cross-form"].status != "verified"


@pytest.mark.parametrize("reverse", [False, True])
def test_terminal_blank_cannot_take_a_different_candidate_for_each_constraint(reverse):
    material = located_material(blanks={"copied"})
    other = material.policy.inventory.checks[-1].model_copy(deep=True)
    other.id, other.kind, other.inputs = "conflicting-copy", "sum", ["road-rate", "subtotal"]
    material.policy.inventory.checks.append(other)
    if reverse:
        material.policy.inventory.checks.reverse()
    before = material.model_dump_json()
    writer = success_spy()
    run = controller_run(material, writer=writer)
    assert not run.verification.can_complete
    writer.write_pdf.assert_not_called()
    assert material.model_dump_json() == before
    constraints = [
        f for f in run.case_review.findings if f.rule_id in {"cross-form", "conflicting-copy"}
    ]
    assert {Decimal(f.expected) for f in constraints} == {Decimal(5), Decimal(10)}
    assert "observed/copied" in run.case_review.coverage.missing


def test_independent_blank_factor_can_seed_a_multilevel_dag_without_changing_material():
    material = located_material(blanks={"road-rate"})
    before = material.model_dump_json()
    writer = FakePDFWriter()
    run = controller_run(material, writer=writer)
    assert run.verification.can_complete
    assert run.status.value == "verified" and run.artifact_status == "simulated"
    assert not run.pdf_result.artifact_created and run.output_pdf_uri is None
    assert not run.case_review.coverage.missing
    assert material.model_dump_json() == before


@pytest.mark.parametrize(
    "blanks", [{"subtotal"}, {"copied"}, {"road-rate", "subtotal", "total", "copied"}]
)
def test_consistent_intermediate_terminal_and_consecutive_blanks_pass(blanks):
    material = located_material(blanks=blanks)
    before = material.model_dump_json()
    run = controller_run(material, writer=FakePDFWriter())
    assert run.verification.can_complete and run.artifact_status == "simulated"
    for finding in run.case_review.findings:
        if finding.id.startswith("observed/"):
            assert Decimal(finding.expected) == 5 and Decimal(finding.observed) == 5
    assert material.model_dump_json() == before


@pytest.mark.parametrize("value", ["0", "0.05"])
def test_zero_and_ratio_values_keep_their_numeric_meaning(value):
    material = located_material(blanks={"road-rate", "subtotal"})
    if value == "0":
        material.policy.rule_sets[0].rules.rules[0].correction_matrix.values["excellent"][
            "inferior"
        ] = 0
    for observation in material.facts.observed:
        if observation.state == "present":
            observation.value, observation.unit = value, "ratio"
            observation.raw_text = value
            ref = observation.evidence[0]
            region = next(
                r
                for r in material.policy.registry.documents[1].pages[0].regions
                if r.id == ref.region_id
            )
            region.text = ref.excerpt = value
    before = material.model_dump_json()
    run = controller_run(material)
    assert run.verification.can_complete
    assert material.model_dump_json() == before


@pytest.mark.parametrize("state", ["missing", "not_present", "not_applicable"])
def test_absent_factor_never_becomes_a_blank_candidate(state):
    material = located_material(blanks={"road-rate"})
    material.facts.observed[0].state = state
    writer = success_spy()
    run = controller_run(material, writer=writer)
    assert not run.verification.can_complete
    writer.write_pdf.assert_not_called()


@pytest.mark.parametrize("tamper", ["approval", "confidence", "source", "no-root", "nonblank"])
def test_fill_requires_every_trust_precondition(tamper):
    from test_case_review import ApprovedFixture

    from appraisal_review.domain.case_review import CaseReviewer

    material = located_material(blanks={"road-rate", "subtotal", "total", "copied"})
    authority = ApprovedFixture(material)
    if tamper == "approval":
        authority = None
    elif tamper == "confidence":
        material.facts.pairs[0].pair.target.confidence = 0
        authority = ApprovedFixture(material)
    elif tamper == "source":
        material.facts.observed[0].evidence[0].region_id = "missing"
        authority = ApprovedFixture(material)
    elif tamper == "no-root":
        material.policy.inventory.checks[0].inputs = ["copied"]
        authority = ApprovedFixture(material)
    else:
        material.facts.observed[0].raw_text = "not blank"
        authority = ApprovedFixture(material)
    before = material.model_dump_json()
    result = CaseReviewer(authority).review(
        material.policy, material.facts, material.policy.registry
    )
    assert result.status.value != "verified" and result.coverage.missing
    assert material.model_dump_json() == before


@pytest.mark.parametrize("tolerance,allowed", [("0.009", False), ("0.01", True), ("0.011", True)])
def test_independent_blank_candidate_keeps_inclusive_rounding_tolerance(tolerance, allowed):
    material = located_material(blanks={"total", "copied"})
    subtotal = material.facts.observed[1]
    subtotal.value, subtotal.raw_text = "5.005", "5.005"
    ref = subtotal.evidence[0]
    ref.excerpt = "5.005"
    next(
        r for r in material.policy.registry.documents[1].pages[0].regions if r.id == ref.region_id
    ).text = "5.005"
    material.policy.inventory.checks[0].tolerance = Decimal("0.01")
    material.policy.inventory.checks[1].tolerance = Decimal(tolerance)
    before = material.model_dump_json()
    run = controller_run(material)
    assert run.verification.can_complete is allowed
    total = next(f for f in run.case_review.findings if f.id == "observed/total")
    assert Decimal(total.expected) == Decimal(total.observed) == 5
    assert material.model_dump_json() == before
