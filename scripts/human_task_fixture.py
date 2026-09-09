"""Generate deterministic synthetic fixtures by executing the local human-task service."""

from __future__ import annotations

import asyncio
import json
from itertools import count
from uuid import UUID

from appraisal_review.adapters.local.human_tasks import NonDurableInMemoryHumanTaskRepository
from appraisal_review.adapters.local.synthetic import synthetic_material
from appraisal_review.application.human_tasks import HumanTaskService
from appraisal_review.application.material_corrections import MaterialSubject, MaterialSubjectMap
from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.application.service_guards import Principal
from appraisal_review.domain.case_review import CaseReviewer
from appraisal_review.domain.factor_models import EvaluationStatus, NormalizedValue
from appraisal_review.domain.service_contracts import (
    ActorReference,
    HumanResponse,
    Permission,
    PublicValue,
    ResponseAction,
    RunReference,
    ServiceModel,
    TaskKind,
    ValueRevision,
)


class _FixturePrincipalResolver:
    """Synthetic trusted adapter for this fixture; no authentication is claimed."""

    async def current_principal(self) -> Principal:
        return Principal(
            actor=ActorReference(actor_id="synthetic-fixture-reviewer", kind="human"),
            case_ids=frozenset({"synthetic-case"}),
            permissions=frozenset({Permission.REVIEW, Permission.CORRECT}),
        )


async def _execute() -> dict[str, ServiceModel]:
    sequence = count(1700000)

    def next_id() -> UUID:
        return UUID(int=next(sequence))

    material = synthetic_material()
    pair = material.facts.pairs[0]
    pair.pair.target.value = NormalizedValue(type="number", value=9, unit="m")
    pair.pair.target.confidence = 0.4
    pair.pair.target.evidence[0].confidence = 0.3
    pair.target_reliability.method = "model_proposed"
    snapshot = RevisionSnapshot.capture(material, "human-fixture-r1")
    run = RunReference(run_id=next_id(), revision=snapshot.revision.reference)
    initial_review = CaseReviewer(None).review(
        material.policy, material.facts, material.policy.registry
    )
    subject_id = "target-road-width"
    subjects = MaterialSubjectMap(
        (
            MaterialSubject(
                subject_id=subject_id,
                context=pair.context,
                factor_id=pair.pair.factor_id,
                side="target",
            ),
        )
    )
    repository = NonDurableInMemoryHumanTaskRepository(event_id_factory=next_id)
    await repository.register(snapshot, run, initial_review)
    service = HumanTaskService(
        repository=repository,
        principals=_FixturePrincipalResolver(),
        subjects=subjects,
        id_factory=next_id,
    )
    finding_id = f"{pair.context.key()}/factor/{pair.pair.factor_id}"
    task = await service.create_task(
        run,
        kind=TaskKind.CORRECTION,
        finding_ids=(finding_id,),
        subject_id=subject_id,
    )
    original = subjects.public_value(snapshot, subject_id)
    proposed = PublicValue(
        state="present",
        value=NormalizedValue(type="number", value=10, unit="m"),
        raw_text="10 m",
        unit="m",
        confidence=original.confidence,
        evidence=original.evidence,
    )
    response = await service.respond(
        HumanResponse(
            task_id=task.task_id,
            expected_version=task.version,
            revision=run.revision,
            side_digest=task.side.input_digest if task.side is not None else None,
            idempotency_key="human-fixture-correction-1",
            action=ResponseAction.CORRECT,
            correction=ValueRevision(
                subject_id=subject_id,
                original=original,
                proposed=proposed,
            ),
        )
    )
    if response.next_run is None:
        raise AssertionError("Executed correction must queue a new exact revision review")
    recomputed = await service.reenter(response.next_run)
    if (
        recomputed.status != EvaluationStatus.NEEDS_REVIEW
        or recomputed.identity.version != response.revision.reference.revision_id
    ):
        raise AssertionError("Correction fixture still requires confirmation and material approval")
    return {
        "human-task-created": task,
        "human-response-accepted": response,
        "human-material-revised": response.revision,
    }


def human_task_fixtures() -> dict[str, ServiceModel]:
    """Return actual task/response/revision records, without signed approval or files."""
    return asyncio.run(_execute())


if __name__ == "__main__":
    print(
        json.dumps(
            {name: model.model_dump(mode="json") for name, model in human_task_fixtures().items()},
            indent=2,
            ensure_ascii=False,
        )
    )
