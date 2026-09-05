"""Synthetic counterexamples for source, cell, dependency and audit boundaries."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from test_case_review import ApprovedFixture, arithmetic_material, controller_run, evaluate

from appraisal_review.adapters.local.synthetic import synthetic_material, synthetic_request
from appraisal_review.application.controller import ReviewAgentController
from appraisal_review.domain.document_models import SourceCitation, SourceRegion
from appraisal_review.domain.pdf_models import PDFWriteResult
from appraisal_review.domain.review_contracts import ArithmeticCheck, ObservedValue, ReviewSlot
from appraisal_review.ports.workflow import ParsedDocument


def cell(material, name, text="5", *, column=1):
    source = material.policy.registry.documents[1]
    index = len(source.pages[0].regions)
    region = SourceRegion(
        id=name,
        kind="cell",
        bbox=(1, 40 + index * 4, 90, 44 + index * 4),
        text=text,
        column=column,
        row=index,
    )
    source.pages[0].regions.append(region)
    return SourceCitation(
        document_id=source.document_id,
        content_hash=source.content_hash,
        version=source.version,
        page=1,
        region_id=name,
        bbox=region.bbox,
        excerpt=text,
    )


def add_subtotal(material, name, text="123"):
    ref = cell(material, name, text)
    material.policy.inventory.slots.append(
        ReviewSlot(
            id=name,
            context=material.policy.inventory.contexts[0].context,
            value="subtotal",
            evidence=[ref],
        )
    )
    material.facts.observed.append(
        ObservedValue(
            slot_id=name,
            state="present",
            value=text,
            unit="percent_points",
            raw_text=text,
            evidence=[ref],
        )
    )
    return ref


@pytest.mark.parametrize("mismatch", ["requested", "parsed", "both", "roles", "pages", "extra"])
def test_controller_rejects_source_substitution_before_success_spy(mismatch):
    material = synthetic_material()
    request = synthetic_request("completed")
    docs = {
        d.uri: ParsedDocument(document_uri=d.uri, page_count=1, source=d.model_copy(deep=True))
        for d in material.policy.registry.documents
    }
    forms = docs[request.case_document_uri]
    if mismatch == "requested":
        request.case_document_uri = "file:///private/unreviewed.pdf"
        docs[request.case_document_uri] = forms
    elif mismatch == "parsed":
        forms.document_uri = "file:///private/unreviewed.pdf"
    elif mismatch == "both":
        forms.document_uri = forms.source.uri = "file:///private/unreviewed.pdf"
    elif mismatch == "roles":
        forms.source.role = "criteria"
        docs[request.criteria_document_uri].source.role = "forms"
    elif mismatch == "pages":
        forms.page_count = 2
    else:
        extra = material.policy.registry.documents[0].model_copy(deep=True)
        extra.document_id, extra.uri, extra.role = (
            "reference",
            "file:///synthetic/reference.pdf",
            "reference",
        )
        material.policy.registry.documents.append(extra)
        docs[extra.uri] = ParsedDocument(document_uri=extra.uri, page_count=1, source=extra)
        docs[extra.uri].document_uri = "file:///private/unreviewed.pdf"

    # Typed success spy proves rejection before invocation; it creates no PDF.
    writer = AsyncMock(
        write_pdf=AsyncMock(
            side_effect=lambda r: PDFWriteResult(
                output_uri=r.destination_uri,
                page_count=1,
                written_field_ids=[f.field_id for f in r.field_map.fields],
                artifact_created=True,
            )
        )
    )
    controller = ReviewAgentController(
        parser=AsyncMock(parse_document=AsyncMock(side_effect=lambda uri: docs[uri])),
        rule_provider=AsyncMock(load_or_build_rules=AsyncMock(return_value=material.policy)),
        fact_extractor=AsyncMock(extract_facts=AsyncMock(return_value=material.facts)),
        authorization=ApprovedFixture(material),
        pdf_writer=writer,
    )
    run = asyncio.run(controller.review(request))
    assert run.status.value not in {"verified", "completed"}
    assert not run.verification.can_complete
    assert "source_binding" in run.model_dump_json()
    assert "private/unreviewed" not in run.model_dump_json()
    writer.write_pdf.assert_not_called()


@pytest.mark.parametrize("mode", ["same-value", "different-column", "blank", "derivable", "mixed"])
def test_observation_must_use_the_slot_cell_before_arithmetic(mode):
    material = arithmetic_material()
    slot = material.policy.inventory.slots[1]
    observation = material.facts.observed[1]
    text = "" if mode in {"blank", "derivable"} else "5"
    correct = cell(material, "original", text)
    other = cell(material, "other-column", text, column=2)
    slot.evidence = [correct]
    observation.evidence, observation.raw_text = [other], text
    if mode in {"blank", "derivable"}:
        slot.derivable_blank = mode == "derivable"
        observation.state, observation.value = "blank", None
    if mode == "mixed":
        observation.evidence.insert(0, correct)
    original = material.model_dump()
    writer = AsyncMock()
    run = controller_run(material, writer=writer)
    assert not run.verification.can_complete
    assert any(
        f.id == "observed/subtotal" and f.kind == "observed_source_binding"
        for f in run.case_review.findings
    )
    assert not any(
        f.id == "arithmetic/sum" and f.status == "verified" for f in run.case_review.findings
    )
    assert material.model_dump() == original
    writer.write_pdf.assert_not_called()


@pytest.mark.parametrize("kind", ["equals", "sum"])
def test_arithmetic_contract_rejects_direct_self_reference(kind):
    material = synthetic_material()
    with pytest.raises(ValidationError, match="self"):
        ArithmeticCheck(
            id="self",
            kind=kind,
            inputs=["subtotal"],
            target="subtotal",
            evidence=material.policy.inventory.slots[0].evidence,
        )


@pytest.mark.parametrize("count", [1, 2, 3])
def test_cycles_with_distinct_correctly_bound_cells_cannot_create_expected(count):
    material = synthetic_material()
    refs = [add_subtotal(material, f"subtotal-{i}") for i in range(count)]
    for i in range(count):
        check = ArithmeticCheck(
            id=f"cycle-{i}",
            kind="equals",
            inputs=["road-rate"],
            target=f"subtotal-{i}",
            evidence=[refs[i]],
        )
        # Exercise the mutable-instance boundary, including direct self-reference.
        check.inputs = [f"subtotal-{(i + 1) % count}"]
        material.policy.inventory.checks.append(check)
    writer = AsyncMock()
    run = controller_run(material, writer=writer)
    assert not run.verification.can_complete
    assert run.case_review.coverage.missing
    assert not any(f.kind == "observed_source_binding" for f in run.case_review.findings)
    writer.write_pdf.assert_not_called()


def test_schema_two_audit_records_loaded_and_actually_evaluated_rules():
    run = controller_run(synthetic_material())
    events = {e.event_type: e for e in run.audit_events}
    assert events["rules_loaded"].rule_ids == ["synthetic-road.v1"]
    assert events["factors_evaluated"].rule_ids == ["synthetic-road.v1"]
    assert (
        events["rules_loaded"].sequence
        < events["factors_evaluated"].sequence
        < events["results_verified"].sequence
    )
    assert events["rules_loaded"].details["rule_sets"][0]["version"] == "1.0.0"
    assert events["factors_evaluated"].details["comparisons"][0]["outcome"] == "verified"


def test_correctly_bound_distinct_cells_and_multilevel_dag_pass():
    material = arithmetic_material()
    for slot, observed in zip(
        material.policy.inventory.slots, material.facts.observed, strict=True
    ):
        ref = cell(material, slot.id)
        slot.evidence = observed.evidence = [ref]
        observed.raw_text = "5"
    assert evaluate(material).status.value == "verified"


@pytest.mark.parametrize(
    "uri", ["file://localhost/synthetic/verified.pdf", "file:///synthetic/./verified.pdf"]
)
def test_equivalent_request_uri_uses_reviewed_forms_identity_for_writer(uri):
    material = synthetic_material()
    request = synthetic_request("completed")
    original = request.case_document_uri
    request.case_document_uri = uri
    docs = {
        d.uri: ParsedDocument(document_uri=d.uri, page_count=1, source=d)
        for d in material.policy.registry.documents
    }
    docs[uri] = docs[original]
    from appraisal_review.adapters.local.fake_pdf import FakePDFWriter

    writer = AsyncMock(write_pdf=AsyncMock(side_effect=FakePDFWriter().write_pdf))
    controller = ReviewAgentController(
        parser=AsyncMock(parse_document=AsyncMock(side_effect=lambda value: docs[value])),
        rule_provider=AsyncMock(load_or_build_rules=AsyncMock(return_value=material.policy)),
        fact_extractor=AsyncMock(extract_facts=AsyncMock(return_value=material.facts)),
        authorization=ApprovedFixture(material),
        pdf_writer=writer,
    )
    run = asyncio.run(controller.review(request))
    assert run.verification.can_complete and run.artifact_status == "simulated"
    assert writer.write_pdf.call_args.args[0].source_uri == original
    assert run.output_pdf_uri is None


def test_unrooted_subtotal_with_valid_cell_is_not_covered():
    material = synthetic_material()
    add_subtotal(material, "arbitrary")
    writer = AsyncMock()
    run = controller_run(material, writer=writer)
    assert not run.verification.can_complete
    assert "observed/arbitrary" in run.case_review.coverage.missing
    assert not any(f.kind == "observed_source_binding" for f in run.case_review.findings)
    writer.write_pdf.assert_not_called()


@pytest.mark.parametrize("other_version", [False, True])
def test_resolving_other_document_or_version_is_not_the_observed_cell(other_version):
    material = synthetic_material()
    ref = cell(material, "rate")
    material.policy.inventory.slots[0].evidence = [ref]
    material.facts.observed[0].evidence = [ref]
    material.facts.observed[0].raw_text = "5"
    other = material.policy.registry.documents[1].model_copy(deep=True)
    other.document_id, other.uri, other.role = (
        "other-forms",
        "file:///synthetic/other.pdf",
        "reference",
    )
    if other_version:
        other.version = "2"
    material.policy.registry.documents.append(other)
    citation = ref.model_copy(update={"document_id": other.document_id, "version": other.version})
    assert material.policy.registry.resolves(citation)
    material.facts.observed[0].evidence = [citation]
    run = controller_run(material)
    assert not run.verification.can_complete
    assert any(f.kind == "observed_source_binding" for f in run.case_review.findings)


@pytest.mark.parametrize(
    "selection", ["candidate", "rejected", "not_applicable", "conflict", "missing"]
)
def test_audit_distinguishes_loaded_candidates_from_actual_evaluation(selection):
    material = synthetic_material()
    rules = material.policy.rule_sets[0]
    if selection == "rejected":
        rules.rules.status = "rejected"
    elif selection == "not_applicable":
        rules.zone = "other"
    elif selection == "conflict":
        material.policy.rule_sets.append(rules.model_copy(deep=True))
    elif selection == "missing":
        material.facts.pairs = []
    run = controller_run(material)
    events = {e.event_type: e for e in run.audit_events}
    loaded, evaluated = events["rules_loaded"], events["factors_evaluated"]
    assert loaded.details["rule_sets"][0]["status"] == rules.rules.status
    assert loaded.details["rule_sets"][0]["context"] == rules.context.model_dump()
    if selection == "candidate":
        assert evaluated.details["comparisons"][0]["factors"][0]["state"] == "computed"
    elif selection == "missing":
        assert evaluated.details["comparisons"][0]["factors"] == []
        assert "missing_factor" in evaluated.details["selection"][0]["reasons"]
    else:
        assert evaluated.details["comparisons"] == []
        assert evaluated.rule_ids == []
        assert evaluated.details["selection"][0]["outcome"] == "not_computed"
    assert loaded.sequence < evaluated.sequence < events["results_verified"].sequence


def test_legacy_audit_and_writer_failure_preserve_event_order():
    from test_controller import Extractor, Parser, RuleProvider, rule_set

    from appraisal_review.domain.factor_models import AgentReviewRequest

    legacy = ReviewAgentController(
        parser=Parser(), rule_provider=RuleProvider(rule_set()), fact_extractor=Extractor()
    )
    run = asyncio.run(
        legacy.review(
            AgentReviewRequest(
                case_id="legacy",
                criteria_document_uri="criteria.pdf",
                case_document_uri="file:///synthetic/legacy.pdf",
            )
        )
    )
    events = {e.event_type: e for e in run.audit_events}
    assert events["rules_loaded"].details["rule_sets"][0]["id"] == "portable-v1"
    assert events["factors_evaluated"].details["comparisons"][0]["version"] == "1.0.0"
    assert events["factors_evaluated"].rule_ids == ["road.v1"]
    failed = controller_run(
        synthetic_material(),
        writer=AsyncMock(write_pdf=AsyncMock(side_effect=RuntimeError("synthetic failure"))),
    )
    assert failed.status.value == "failed" and failed.case_review.findings
    names = [e.event_type for e in failed.audit_events]
    assert (
        names.index("factors_evaluated")
        < names.index("results_verified")
        < names.index("pdf_write_failed")
    )
    assert [e.sequence for e in failed.audit_events] == list(range(1, len(names) + 1))
