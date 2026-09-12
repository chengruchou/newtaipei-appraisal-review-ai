"""Isolated synthetic regressions for local task identity, not real acceptance."""

from uuid import uuid4

import pytest

from appraisal_review.adapters.local.original_workbench import LocalCandidateExecution
from appraisal_review.adapters.local.synthetic import synthetic_material
from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.domain.confidence import confirm_side
from appraisal_review.domain.job_contracts import JobStatus
from appraisal_review.domain.service_contracts import RunReference
from appraisal_review.ports.jobs import JobRecord


@pytest.mark.parametrize("reviewer,expected", [("old-actor", 1), ("current-actor", 0)])
def test_only_exact_current_reviewer_confirmation_suppresses_task(reviewer, expected):
    material = synthetic_material()
    pair = material.facts.pairs[0]
    pair.pair.target.confidence = 0
    for evidence in pair.pair.target.evidence:
        evidence.confidence = 0
    confirm_side(pair, "target", reviewer=reviewer)
    original = pair.model_dump_json()
    snapshot = RevisionSnapshot.capture(material, "unit-r1")
    record = JobRecord(
        job_id=uuid4(),
        case_id=material.policy.identity.case_id,
        principal_id="current-actor",
        status=JobStatus.RUNNING,
        current_run=RunReference(run_id=uuid4(), revision=snapshot.revision.reference),
    )
    tasks = LocalCandidateExecution._side_tasks(record, pair, "target", "unit-finding")
    assert len(tasks) == expected
    assert pair.model_dump_json() == original
    assert pair.pair.target.confidence == 0
    assert all(e.confidence == 0 for e in pair.pair.target.evidence)
    pair.target_reliability.confirmation.input_digest = "f" * 64
    assert len(LocalCandidateExecution._side_tasks(record, pair, "target", "unit-finding")) == 1
    pair.target_reliability.unresolved.append("Unit source ambiguity")
    assert not LocalCandidateExecution._side_tasks(record, pair, "target", "unit-finding")
