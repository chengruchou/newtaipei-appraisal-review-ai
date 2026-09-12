"""Consumer contract preparation, not browser or authenticated HTTP acceptance."""

import runpy
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from appraisal_review.adapters.local.synthetic import synthetic_material
from appraisal_review.application.service_guards import Principal, ServiceFault, admit_response
from appraisal_review.domain.review_contracts import content_digest
from appraisal_review.domain.service_contracts import (
    ActorReference,
    HumanResponse,
    Permission,
    ResponseAction,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = runpy.run_path(str(ROOT / "scripts/workbench_fixture.py"))


@pytest.fixture(scope="module")
def records():
    return FIXTURE["workbench_fixtures"]()


def test_actual_records_link_task_decision_response_revision_and_recomputed_run(records):
    task = records["workbench-task"]
    pause = records["workbench-pause"]
    response = records["workbench-response"]
    continuation = records["workbench-continuation"]
    before, after = records["workbench-before-review"], records["workbench-after-review"]
    assert task.task_id in pause.task_ids
    assert pause.result.events[-1].linked_task_id == task.task_id
    assert pause.result.events[0].tool_result.result_digest == content_digest(before)
    assert set(task.finding_ids) <= {f.id for f in before.findings}
    assert response.accepted.command == records["workbench-command"]
    assert response.task.version == task.version + 1 and response.task.state == "answered"
    assert response.revision.parent == task.run.revision
    assert continuation.pause_id == pause.pause_id
    assert continuation.response_event_id == response.event_id
    assert continuation.previous_run == task.run
    assert continuation.next_run == response.next_run
    assert after.identity.version == response.revision.reference.revision_id
    assert records["workbench-superseded-task"].state == "superseded"


def test_original_proposal_correction_and_expected_are_not_interchangeable(records):
    original = records["workbench-original"]
    command = records["workbench-command"]
    response = records["workbench-response"]
    change = response.revision.changes[0]
    assert original.value.value == 9
    assert command.correction.original == change.original == original
    assert command.correction.proposed.value.value == 10
    assert command.correction.corrected is None and command.correction.corrected_by is None
    assert change.corrected.value.value == 10
    assert change.corrected_by == response.accepted.actor
    assert change.corrected.confidence == original.confidence
    assert change.corrected.evidence == original.evidence
    assert response.authorization is None
    review = records["workbench-after-review"]
    observed = next(f for f in review.findings if f.id == "observed/road-rate")
    assert observed.observed == "5" and observed.expected is None
    assert review.status.value == "needs_review"
    assert review.coverage.missing


def test_evidence_navigation_joins_exact_source_not_excerpt_or_guess(records):
    registry = synthetic_material().policy.registry
    for citation in records["workbench-task"].evidence:
        assert registry.resolves(citation)
        document = next(d for d in registry.documents if d.document_id == citation.document_id)
        page = document.pages[citation.page - 1]
        region = next(r for r in page.regions if r.id == citation.region_id)
        assert (
            document.version == citation.version and document.content_hash == citation.content_hash
        )
        assert page.number == citation.page == 1
        assert document.page_space == "unrotated_crop_box"
        assert document.coordinate_system == "pdf_bottom_left"
        assert region.bbox == citation.bbox


@pytest.mark.parametrize(
    "mutation",
    [
        {"document_id": "unavailable"},
        {"version": "stale"},
        {"content_hash": "f" * 64},
        {"page": 2},
        {"region_id": "invented"},
        {"bbox": (0, 0, 1, 1)},
        {"excerpt": "not source text"},
    ],
)
def test_unresolved_citation_must_not_become_a_highlight(records, mutation):
    citation = records["workbench-task"].evidence[0].model_copy(update=mutation)
    assert not synthetic_material().policy.registry.resolves(citation)


@pytest.mark.parametrize(
    "mutation",
    [
        {"task_id": uuid4()},
        {"expected_version": 99},
        {"side_digest": "f" * 64},
        {"action": ResponseAction.APPROVE, "correction": None},
    ],
)
def test_form_state_cannot_override_server_task_admission(records, mutation):
    task = records["workbench-task"]
    principal = Principal(
        ActorReference(actor_id="fixture-human", kind="human"),
        frozenset({task.run.revision.case_id}),
        frozenset(Permission),
    )
    command = HumanResponse.model_validate(
        {**records["workbench-command"].model_dump(), **mutation}
    )
    with pytest.raises(ServiceFault):
        admit_response(task, command, principal, current=task.run.revision)


@pytest.mark.parametrize(
    "extra",
    [
        {"actor": "human"},
        {"verified": True},
        {"permissions": ["review"]},
        {"storage_uri": "file:///unavailable"},
    ],
)
def test_browser_command_cannot_supply_authority_or_storage_location(records, extra):
    with pytest.raises(ValidationError):
        HumanResponse.model_validate({**records["workbench-command"].model_dump(), **extra})


def test_trace_display_distinguishes_trusted_execution_from_selector_rationale(records):
    for event in records["workbench-pause"].result.events:
        assert event.proposal.proposer.kind == "system"
        assert event.proposal.model_id is None
        assert event.reviewer_summary and event.reason_code
        assert event.disposition == "executed"
        assert event.tool_result.outcome == "succeeded"
        assert event.budget_consumed.model_calls == 0
    assert records["workbench-task"].allowed_responses == (
        ResponseAction.CORRECT,
        ResponseAction.REJECT,
    )
