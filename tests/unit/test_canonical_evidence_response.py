"""The workflow evidence action uses the canonical committed task transaction."""

import asyncio
from uuid import uuid4

import pytest

from appraisal_review.adapters.local.review_database import SQLiteReviewDatabase
from appraisal_review.adapters.local.sqlite_human_task_store import SQLiteHumanTaskStore
from appraisal_review.adapters.local.sqlite_job_store import SQLiteJobStore
from appraisal_review.application.human_tasks import HumanTaskService
from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.domain.service_contracts import ResponseAction, ReviewSubmission, TaskKind
from appraisal_review.testing.job_store_contract import NOW
from tests.unit.test_human_task_service import Harness, caller, correcting, correction_task


@pytest.mark.parametrize("storage", ["memory", "sqlite"])
@pytest.mark.parametrize("raw_confidence", [0.0, 0.5, 0.95])
@pytest.mark.parametrize("proposed_confidence", [None, 0.0, 1.0])
@pytest.mark.parametrize("evidence", ["current", "missing", "forged"])
def test_evidence_supply_requires_current_citations_and_preserves_raw_confidence(
    evidence, raw_confidence, proposed_confidence, storage, tmp_path
):
    async def exercise():
        h = Harness()
        if storage == "sqlite":
            h.jobs = SQLiteJobStore(SQLiteReviewDatabase(tmp_path / "review.sqlite"))
            h.tasks = SQLiteHumanTaskStore(h.jobs)
            h.service = HumanTaskService(h.tasks, clock=lambda: NOW + 2)
        material = h.snapshot.material
        material.facts.pairs[0].pair.target.confidence = raw_confidence
        h.snapshot = RevisionSnapshot.capture(material, "r1")
        task = correction_task(h.snapshot, h.run_id).model_copy(
            update={
                "kind": TaskKind.EVIDENCE,
                "allowed_responses": (ResponseAction.SUPPLY_EVIDENCE, ResponseAction.REJECT),
                "reason_code": "missing-evidence",
                "affected_subject_ids": ("target-road-width",),
            }
        )
        if storage == "sqlite":
            await h.jobs.create_job(
                caller(),
                ReviewSubmission(
                    revision=h.snapshot.revision.reference,
                    documents=h.snapshot.revision.documents,
                    idempotency_key="evidence-job",
                ),
                job_id=h.job_id,
                run_id=h.run_id,
                now=NOW,
            )
            attempt = await h.jobs.claim(
                job_id=h.job_id, run_id=h.run_id, owner=uuid4(), lease_seconds=60, now=NOW
            )
            await h.tasks.finish_with_tasks(
                attempt, snapshot=h.snapshot, tasks=(task,), now=NOW + 1
            )
        else:
            await h.setup((task,), principal=caller())
        command = correcting(h, task)
        before = h.snapshot.material.facts.pairs[0]
        citations = tuple(before.target_sources)
        if evidence == "missing":
            citations = ()
        elif evidence == "forged":
            citations = (citations[0].model_copy(update={"content_hash": "f" * 64}),)
        proposed = command.correction.proposed.model_copy(
            update={"evidence": citations, "confidence": proposed_confidence}
        )
        command = command.model_copy(
            update={
                "action": ResponseAction.SUPPLY_EVIDENCE,
                "correction": command.correction.model_copy(update={"proposed": proposed}),
            }
        )
        if evidence != "current":
            with pytest.raises(ServiceFault):
                await h.service.respond(caller(), task.task_id, command)
            assert (await h.tasks.read_task(task_id=task.task_id)).task.state == "open"
            assert len(await h.tasks.list_revisions(job_id=h.job_id)) == 1
            return
        receipt = await h.service.respond(caller(), task.task_id, command)
        assert await h.service.respond(caller(), task.task_id, command) == receipt
        assert receipt.resumed_run.revision == receipt.revision
        stored = await h.tasks.read_snapshot(revision=receipt.revision)
        pair = stored.material.facts.pairs[0]
        assert pair.pair.target.confidence == raw_confidence
        assert pair.target_sources == list(citations)
        assert pair.pair.target.evidence[0].confidence == raw_confidence
        assert pair.target_reliability.method == "model_proposed"
        assert pair.target_reliability.confirmation is None
        change = stored.revision.changes[0]
        assert change.corrected.evidence == citations
        assert change.original.confidence == raw_confidence
        assert change.proposed.confidence == proposed_confidence
        assert change.corrected.confidence == raw_confidence
        assert change.corrected_by == caller().actor
        assert len(await h.tasks.list_revisions(job_id=h.job_id)) == 2

    asyncio.run(exercise())
