"""Synthetic unit storage regressions; real interruption acceptance is separate."""

import asyncio
from contextlib import closing
from dataclasses import replace
from uuid import uuid4

import pytest

from appraisal_review.adapters.local.original_workbench import LocalCandidateExecution
from appraisal_review.adapters.local.sqlite_review_store import SQLiteReviewStore
from appraisal_review.adapters.local.synthetic import synthetic_material
from appraisal_review.adapters.local.workflow_runtime import SQLiteWorkflowReviews
from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.domain.factor_models import AgentReviewRun, WorkflowStatus
from appraisal_review.domain.job_contracts import JobStatus
from appraisal_review.domain.service_contracts import RunReference
from appraisal_review.ports.jobs import JobRecord


def test_attempt_output_retains_producer_identity_and_does_not_poison_retry(tmp_path):
    async def scenario():
        store = SQLiteReviewStore(tmp_path / "private" / "state.sqlite")
        snapshot = RevisionSnapshot.capture(synthetic_material(), "unit-r1")
        first = RunReference(
            run_id=uuid4(), attempt_id=uuid4(), revision=snapshot.revision.reference
        )
        second = first.model_copy(update={"attempt_id": uuid4()})
        review = AgentReviewRun(case_id=first.revision.case_id, status="needs_review")
        legacy = SQLiteWorkflowReviews(store)
        await legacy.put(first, review)
        with pytest.raises(ServiceFault):
            await legacy.read(second)
        current = SQLiteWorkflowReviews(store, attempt_scoped=True)
        assert await current.read(first) == review
        assert await current.read(second) is None
        await current.put(second, review)
        assert await current.read(first) == await current.read(second) == review
        with closing(store._connect()) as connection:
            assert connection.execute("SELECT COUNT(*) FROM workflow_reviews").fetchone()[0] == 1
            assert (
                connection.execute("SELECT COUNT(*) FROM workflow_review_attempts").fetchone()[0]
                == 1
            )
        changed = second.model_copy(
            update={"revision": second.revision.model_copy(update={"revision_id": "wrong"})}
        )
        with pytest.raises(ServiceFault):
            await current.read(changed)
        with pytest.raises(ServiceFault):
            await current.put(second, review.model_copy(update={"status": WorkflowStatus.FAILED}))
        reopened = SQLiteWorkflowReviews(SQLiteReviewStore(store.path), attempt_scoped=True)
        assert await reopened.read(second) == review

    asyncio.run(scenario())


def test_retry_task_ids_are_separate_and_exact_attempt_replay_is_stable():
    material = synthetic_material()
    pair = material.facts.pairs[0]
    snapshot = RevisionSnapshot.capture(material, "unit-r1")
    first = RunReference(run_id=uuid4(), attempt_id=uuid4(), revision=snapshot.revision.reference)
    record = JobRecord(
        job_id=uuid4(),
        case_id=first.revision.case_id,
        principal_id="unit-reviewer",
        status=JobStatus.RUNNING,
        current_run=first,
    )
    a = LocalCandidateExecution._side_tasks(record, pair, "target", "finding")
    b = LocalCandidateExecution._side_tasks(
        replace(record, current_run=first.model_copy(update={"attempt_id": uuid4()})),
        pair,
        "target",
        "finding",
    )
    assert a and b and a[0].task_id != b[0].task_id
    assert a == LocalCandidateExecution._side_tasks(record, pair, "target", "finding")
    assert a[0].side == b[0].side
