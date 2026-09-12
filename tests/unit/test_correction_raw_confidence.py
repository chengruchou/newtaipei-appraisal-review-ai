"""A manual proposal cannot rewrite the extractor's measured confidence."""

import asyncio
from pathlib import Path

import pytest

from appraisal_review.adapters.local.approval import LocalApprovalStore, current_reviewer
from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.domain.case_review import CaseReviewer
from appraisal_review.domain.review_contracts import content_digest
from tests.unit.test_human_task_service import Harness, caller, correcting, correction_task


@pytest.mark.parametrize("raw_confidence", [0.0, 0.4, 0.95])
@pytest.mark.parametrize("proposed_confidence", [None, 0.0, 0.1, 0.9, 1.0])
def test_correction_preserves_raw_confidence_and_original_evidence(
    raw_confidence: float, proposed_confidence: float | None
) -> None:
    async def scenario() -> None:
        harness = Harness()
        original = harness.snapshot.material
        original_pair = original.facts.pairs[0]
        original_pair.pair.target.confidence = raw_confidence
        harness.snapshot = RevisionSnapshot.capture(original, "r1")
        task = correction_task(harness.snapshot, harness.run_id)
        await harness.setup((task,), principal=caller())
        command = correcting(harness, task)
        assert command.correction is not None and command.correction.proposed is not None
        command = command.model_copy(
            update={
                "correction": command.correction.model_copy(
                    update={
                        "proposed": command.correction.proposed.model_copy(
                            update={"confidence": proposed_confidence}
                        )
                    }
                )
            }
        )
        receipt = await harness.service.respond(caller(), task.task_id, command)
        assert receipt.revision is not None
        stored = await harness.tasks.read_snapshot(revision=receipt.revision)
        assert stored is not None
        pair = stored.material.facts.pairs[0]
        assert pair.pair.target.value != original_pair.pair.target.value
        assert pair.pair.target.confidence == raw_confidence
        assert pair.pair.target.evidence == original_pair.pair.target.evidence
        assert pair.target_sources == original_pair.target_sources
        assert pair.target_reliability.confirmation is None
        change = stored.revision.changes[0]
        assert change.original.confidence == raw_confidence
        assert change.proposed is not None and change.corrected is not None
        assert change.proposed.confidence == proposed_confidence
        assert change.corrected.confidence == raw_confidence
        parent = await harness.tasks.read_snapshot(revision=harness.snapshot.revision.reference)
        assert parent is not None and parent.material == original

    asyncio.run(scenario())


def test_preserved_high_confidence_does_not_authorize_a_manual_correction(tmp_path: Path) -> None:
    async def scenario() -> None:
        harness = Harness()
        original = harness.snapshot.material
        approvals = LocalApprovalStore.initialize(tmp_path / "approvals", current_reviewer())
        approvals.approve(original, expected_digest=content_digest(original))
        task = correction_task(harness.snapshot, harness.run_id)
        await harness.setup((task,), principal=caller())
        receipt = await harness.service.respond(caller(), task.task_id, correcting(harness, task))
        assert receipt.revision is not None
        stored = await harness.tasks.read_snapshot(revision=receipt.revision)
        assert stored is not None
        material = stored.material
        observation = material.facts.pairs[0].pair.target
        assert observation.confidence == original.facts.pairs[0].pair.target.confidence
        assert observation.confidence >= 0.85
        assert approvals.permits(original) and not approvals.permits(material)
        with pytest.raises(ValueError, match="requires current reviewer confirmation"):
            approvals.approve(material, expected_digest=content_digest(material))
        result = CaseReviewer(approvals).review(
            material.policy, material.facts, material.policy.registry
        )
        assert result.status == "needs_review"

    asyncio.run(scenario())
