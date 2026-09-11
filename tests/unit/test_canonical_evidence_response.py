"""The workflow evidence action uses the canonical committed task transaction."""

import asyncio

import pytest

from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.domain.service_contracts import ResponseAction, TaskKind
from tests.unit.test_human_task_service import Harness, caller, correcting, correction_task


@pytest.mark.parametrize("evidence", ["current", "missing", "forged"])
def test_evidence_supply_requires_current_citations_and_preserves_raw_confidence(evidence):
    async def exercise():
        h = Harness()
        task = correction_task(h.snapshot, h.run_id).model_copy(
            update={
                "kind": TaskKind.EVIDENCE,
                "allowed_responses": (ResponseAction.SUPPLY_EVIDENCE, ResponseAction.REJECT),
                "reason_code": "missing-evidence",
                "affected_subject_ids": ("target-road-width",),
            }
        )
        await h.setup((task,), principal=caller())
        command = correcting(h, task)
        before = h.snapshot.material.facts.pairs[0]
        citations = tuple(before.target_sources)
        if evidence == "missing":
            citations = ()
        elif evidence == "forged":
            citations = (citations[0].model_copy(update={"content_hash": "f" * 64}),)
        proposed = command.correction.proposed.model_copy(
            update={"evidence": citations, "confidence": 0.0}
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
        assert pair.pair.target.confidence == 0
        assert pair.target_sources == list(citations)
        assert pair.pair.target.evidence[0].confidence == 0
        assert pair.target_reliability.method == "model_proposed"
        assert pair.target_reliability.confirmation is None
        change = stored.revision.changes[0]
        assert change.corrected.evidence == citations
        assert change.corrected_by == caller().actor
        assert len(await h.tasks.list_revisions(job_id=h.job_id)) == 2

    asyncio.run(exercise())
