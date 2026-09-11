"""Candidate questions preserve measurements, confidence and page evidence."""

from test_extraction_contracts import payload

from appraisal_review.adapters.local.synthetic import synthetic_material
from appraisal_review.application.extraction_handoffs import with_candidate_handoffs
from appraisal_review.domain.extraction_contracts import PageOutcome
from appraisal_review.domain.extraction_models import PageProposal


def candidate():
    material = synthetic_material()
    proposal = PageProposal(
        pairs=material.facts.pairs,
        slots=material.policy.inventory.slots,
        observed=material.facts.observed,
    )
    # The request fixture is page one; only one-page synthetic source citations are used.
    data = payload("candidate.json")
    source = material.policy.registry.documents[1]
    data["request"]["source"]["document"].update(
        document_id=source.document_id, version=source.version, content_hash=source.content_hash
    )
    data["request"]["source"]["page_count"] = len(source.pages)
    return PageOutcome.model_validate(data | {"proposal": proposal})


def test_model_confidence_never_removes_human_confirmation_requirement():
    original = candidate()
    for pair in original.proposal.pairs:
        pair.target_reliability.method = "model_proposed"
        pair.target_reliability.model_confidence = 1
        pair.pair.target.confidence = 0
    before = original.proposal.model_dump_json()
    result = with_candidate_handoffs(original)
    assert result.status == "candidate"
    assert result.proposal.model_dump_json() == before
    low = [h for h in result.handoffs if h.reason == "low_confidence"]
    assert low and all(h.locations[0].page == 1 for h in low)
    assert all(h.locations[0].region_id for h in low)
    assert with_candidate_handoffs(result) == result


def test_missing_blank_zero_and_unsupported_are_not_merged():
    original = candidate()
    original.proposal.unsupported = ["synthetic unsupported rule"]
    original.proposal.unresolved = ["synthetic conflicting selection"]
    first = original.proposal.observed[0]
    first.state, first.value = "missing", None
    result = with_candidate_handoffs(original)
    assert {h.reason for h in result.handoffs} >= {"missing", "unsupported", "conflicting"}
    assert result.proposal == original.proposal


def test_failed_outcome_preserves_original_located_question():
    failed = PageOutcome.model_validate(payload("failed-page.json"))
    assert with_candidate_handoffs(failed) == failed
