"""Resolving citations do not authorize a different document purpose."""

from unittest.mock import AsyncMock

import pytest
from test_case_review import controller_run

from appraisal_review.adapters.local.synthetic import synthetic_material


def wrong_purpose_material(role="criteria"):
    material = synthetic_material()
    source = material.policy.registry.documents[0]
    if role != "criteria":
        source = source.model_copy(deep=True)
        source.document_id, source.uri, source.role = role, f"file:///synthetic/{role}.pdf", role
        material.policy.registry.documents.append(source)
    example = material.policy.registry.documents[1].pages[0].regions[0].text
    source.pages[0].regions[0].text += "\n" + example
    pair = material.facts.pairs[0]
    ref = pair.target_sources[0].model_copy(
        update={
            "document_id": source.document_id,
            "content_hash": source.content_hash,
            "version": source.version,
            "excerpt": example,
        }
    )
    for observation in (pair.pair.target, pair.pair.comparable):
        observation.raw_text = ref.excerpt
        observation.evidence[0].document_id = source.document_id
        observation.evidence[0].source_file = source.uri
    pair.target_sources = pair.comparable_sources = [ref]
    material.policy.inventory.slots[0].evidence = [ref]
    material.facts.observed[0].evidence = [ref]
    material.facts.observed[0].raw_text = ref.excerpt
    material.policy.inventory.contexts[0].evidence = [ref]
    assert material.policy.registry.resolves(ref)
    return material


@pytest.mark.parametrize("role", ["criteria", "reference", "brief"])
def test_direct_provider_cannot_use_wrong_role_as_facts_or_case_cells(role):
    material = wrong_purpose_material(role)
    writer = AsyncMock()
    run = controller_run(material, writer=writer)
    assert not run.verification.can_complete
    assert any(f.kind == "source_purpose" for f in run.case_review.findings)
    writer.write_pdf.assert_not_called()


def test_mixed_case_anchors_cannot_borrow_criteria_evidence():
    material = synthetic_material()
    material.facts.pairs[0].target_sources.append(material.policy.rule_sets[0].evidence[0])
    run = controller_run(material)
    assert not run.verification.can_complete
    assert any(f.kind == "source_purpose" for f in run.case_review.findings)


def test_current_forms_selection_cannot_be_replaced_by_another_forms_identity():
    from test_case_review import ApprovedFixture

    from appraisal_review.domain.case_review import CaseReviewer

    material = synthetic_material()
    forms = material.policy.registry.documents[1]
    other = forms.model_copy(deep=True)
    other.document_id, other.uri, other.version = "other-forms", "file:///synthetic/other.pdf", "2"
    material.policy.registry.documents.append(other)
    result = CaseReviewer(ApprovedFixture(material)).review(
        material.policy, material.facts, material.policy.registry, forms_source=other
    )
    assert result.status.value != "verified"
    assert any(f.kind == "source_purpose" for f in result.findings)


def test_forms_values_criteria_rules_and_reference_procedures_remain_valid():
    from test_case_review import arithmetic_material

    material = arithmetic_material()
    reference = material.policy.registry.documents[0].model_copy(deep=True)
    reference.document_id, reference.uri, reference.role = (
        "manual",
        "file:///synthetic/manual.pdf",
        "reference",
    )
    material.policy.registry.documents.append(reference)
    ref = (
        material.policy.rule_sets[0]
        .evidence[0]
        .model_copy(update={"document_id": reference.document_id})
    )
    material.policy.inventory.checks[-1].evidence = [ref]
    assert controller_run(material).verification.can_complete
