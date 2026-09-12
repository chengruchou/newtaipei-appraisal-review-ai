"""Application service for authenticated human tasks and revision submissions.

Two audiences share this module: a reviewer answering one question, and the durable job
plane that must schedule a follow-up run for whatever the answer changed. Nothing here
runs a review or grants an approval. Answering a task produces an immutable revision; the
next run decides what that revision means.

The trust rule throughout is that the body proposes and the server decides. A response
carries digests, versions and a proposed value; the actor, the corrected value, the new
revision id and the resulting job status are all server-authored.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from uuid import UUID, uuid4

from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.application.service_guards import (
    Principal,
    ServiceFault,
    admit_response,
    response_digest,
)
from appraisal_review.domain.confidence import Side, confirm_side, confirmation_digest
from appraisal_review.domain.document_models import SourceCitation
from appraisal_review.domain.factor_models import (
    EvidencedPair,
    FactorObservation,
    NormalizedValue,
    ReviewMaterial,
)
from appraisal_review.domain.job_contracts import JobReference
from appraisal_review.domain.service_contracts import (
    AcceptedResponse,
    ActorReference,
    FactSideReference,
    HumanResponse,
    HumanTask,
    Permission,
    PublicValue,
    ResponseAction,
    ServiceErrorCode,
    TaskKind,
    ValueRevision,
)
from appraisal_review.domain.task_contracts import (
    ResponseReceipt,
    RevisionListView,
    TaskListView,
    TaskSubjectView,
    TaskView,
)
from appraisal_review.ports.human_tasks import HumanTaskStore, TaskRecord
from appraisal_review.ports.jobs import ConditionFailed, JobRecord

Clock = Callable[[], int]
RevisionIdFactory = Callable[[], str]

# Rule, material and publication approval are recorded through the existing exact-material
# authority, not through this API. Admitting such a task here and reporting "answered"
# would advertise an authorization the service never wrote.
MATERIAL_KINDS = frozenset({TaskKind.FACT, TaskKind.CORRECTION})


def _now() -> int:
    return int(time.time())


def _new_revision_id() -> str:
    return uuid4().hex


def side_subject_id(side: FactSideReference) -> str:
    """Stable name for the one observation a task is about, used in the changes ledger."""
    return f"{side.context.key()}:{side.factor_id}:{side.side}"


def task_view(task: HumanTask) -> TaskView:
    """Publish a task together with the subject name its own correction must carry."""
    subject = None if task.side is None else side_subject_id(task.side)
    return TaskView(task=task, subject_id=subject)


def _locate(material: ReviewMaterial, side: FactSideReference) -> EvidencedPair:
    matches = [
        pair
        for pair in material.facts.pairs
        if pair.context.key() == side.context.key() and pair.pair.factor_id == side.factor_id
    ]
    if len(matches) != 1:
        # Neither an absent nor a duplicated pair can be answered: the task names exactly
        # one observation, and guessing which of two it meant would corrupt the material.
        raise ServiceFault(ServiceErrorCode.CONFLICT)
    return matches[0]


def _public(observation: FactorObservation, sources: list[SourceCitation]) -> PublicValue:
    """URI-free view of a stored observation, for the revision's changes ledger.

    The evidence comes from the pair's typed citations, never from the observation's own
    legacy EvidenceRef list, which carries a local source_file that must not leave here.
    """
    return PublicValue(
        state="present" if observation.value is not None else "blank",
        value=observation.value,
        raw_text=observation.raw_text or "",
        unit=observation.value.unit if observation.value else None,
        confidence=observation.confidence,
        evidence=tuple(sources),
    )


class HumanTaskService:
    """Authenticated task reads and one transactional response, over an injected store."""

    def __init__(
        self,
        store: HumanTaskStore,
        *,
        clock: Clock = _now,
        new_revision_id: RevisionIdFactory = _new_revision_id,
    ) -> None:
        self.store = store
        self.clock = clock
        self.new_revision_id = new_revision_id

    # -- reads ---------------------------------------------------------------

    async def list_tasks(self, principal: Principal, job_id: UUID) -> TaskListView:
        job = await self._authorized_job(principal, job_id)
        records = await self.store.list_tasks(job_id=job_id)
        if any(not self._visible(principal, record) for record in records):
            raise ServiceFault(ServiceErrorCode.NOT_FOUND)
        return TaskListView(
            job=JobReference(case_id=job.case_id, job_id=job_id),
            tasks=tuple(task_view(record.task) for record in records),
        )

    async def read_task(self, principal: Principal, task_id: UUID) -> TaskView:
        return task_view((await self._authorized(principal, task_id)).task)

    async def read_subject(self, principal: Principal, task_id: UUID) -> TaskSubjectView:
        record = await self._authorized(principal, task_id)
        side = record.task.side
        snapshot = await self.store.read_snapshot(revision=record.task.run.revision)
        if side is None or snapshot is None:
            raise ServiceFault(ServiceErrorCode.CAPABILITY)
        pair = _locate(snapshot.material, side)
        if confirmation_digest(pair, side.side) != side.input_digest:
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        value = _public(getattr(pair.pair, side.side), getattr(pair, f"{side.side}_sources"))
        normalized = value.value if isinstance(value.value, NormalizedValue) else None
        return TaskSubjectView(
            task_id=task_id,
            revision=record.task.run.revision,
            subject_id=side_subject_id(side),
            observation=value,
            required_type=normalized.type if normalized else None,
            required_unit=normalized.unit if normalized else None,
            unit_required=normalized is None or normalized.type == "number",
        )

    async def list_revisions(self, principal: Principal, job_id: UUID) -> RevisionListView:
        job = await self._authorized_job(principal, job_id)
        return RevisionListView(
            job=JobReference(case_id=job.case_id, job_id=job_id),
            revisions=await self.store.list_revisions(job_id=job_id),
        )

    async def _authorized_job(self, principal: Principal, job_id: UUID) -> JobRecord:
        job = await self.store.read_job(job_id=job_id)
        if (
            job is None
            or job.principal_id != principal.actor.actor_id
            or job.case_id not in principal.case_ids
        ):
            raise ServiceFault(ServiceErrorCode.NOT_FOUND)
        principal.require(job.case_id, Permission.REVIEW)
        return job

    def _visible(self, principal: Principal, record: TaskRecord) -> bool:
        return (
            record.principal_id == principal.actor.actor_id and record.case_id in principal.case_ids
        )

    async def _authorized(self, principal: Principal, task_id: UUID) -> TaskRecord:
        record = await self.store.read_task(task_id=task_id)
        if record is None or not self._visible(principal, record):
            raise ServiceFault(ServiceErrorCode.NOT_FOUND)
        await self._authorized_job(principal, record.job_id)
        return record

    # -- the one write -------------------------------------------------------

    async def respond(
        self, principal: Principal, task_id: UUID, command: HumanResponse
    ) -> ResponseReceipt:
        """Admit one response and commit its whole effect, or commit nothing at all."""
        command = HumanResponse.model_validate_json(command.model_dump_json())
        if command.task_id != task_id:
            # The path names the task; a body that names another one is not a request for
            # this route, and answering either of them would be a guess.
            raise ServiceFault(ServiceErrorCode.NOT_FOUND)
        record = await self._authorized(principal, task_id)
        # A committed receipt is not a lasting grant to answer this task. Recheck
        # present authority without applying the consumed version's admission gate.
        principal.require(record.case_id, record.task.required_permission)
        if principal.actor.kind != "human":
            raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
        digest = response_digest(command)

        # Replay before admission, deliberately. The first use of this key already moved
        # the task off the version the retry still names, so admission would reject an
        # exact retry that in fact succeeded.
        stored = await self.store.read_receipt(
            principal_id=principal.actor.actor_id, key=command.idempotency_key
        )
        if stored is not None:
            if stored.payload_digest != digest:
                raise ServiceFault(ServiceErrorCode.CONFLICT)
            return stored.receipt

        accepted = admit_response(record.task, command, principal, current=record.current_revision)
        next_revision = await self._next_revision(record, accepted)
        try:
            return await self.store.commit_response(
                accepted,
                task=record.task,
                next_revision=next_revision,
                payload_digest=digest,
                now=self.clock(),
            )
        except ConditionFailed as failure:
            # Another reviewer answered first, or the revision moved. Not a retry signal.
            raise ServiceFault(ServiceErrorCode.CONFLICT) from failure

    async def _next_revision(
        self, record: TaskRecord, accepted: AcceptedResponse
    ) -> RevisionSnapshot | None:
        """Derive the revision this answer commits, without storing or approving it."""
        if accepted.command.action == ResponseAction.REJECT:
            # A refusal is a recorded answer about material that did not change.
            return None
        task = record.task
        if task.kind not in MATERIAL_KINDS:
            raise ServiceFault(ServiceErrorCode.CAPABILITY)
        snapshot = await self.store.read_snapshot(revision=record.current_revision)
        if snapshot is None:
            # The store advertised a head revision whose material it cannot produce.
            raise ServiceFault(ServiceErrorCode.EXECUTION)
        material = snapshot.material
        if task.side is None:
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        pair = _locate(material, task.side)
        side: Side = task.side.side
        revision_id = self.new_revision_id()
        if task.kind == TaskKind.FACT:
            return self._confirm(snapshot, material, pair, side, accepted, revision_id)
        return self._correct(snapshot, material, pair, side, accepted, revision_id, task.side)

    def _confirm(
        self,
        snapshot: RevisionSnapshot,
        material: ReviewMaterial,
        pair: EvidencedPair,
        side: Side,
        accepted: AcceptedResponse,
        revision_id: str,
    ) -> RevisionSnapshot:
        """Bind a reviewer's confirmation to the exact side they were shown.

        This captures rather than revises: `revise` clears every confirmation by design,
        so routing a confirmation through it would erase the assertion being recorded.
        The material is otherwise unchanged, which is why no changes ledger is written.
        """
        try:
            confirm_side(pair, side, reviewer=accepted.actor.actor_id)
        except ValueError as unresolved:
            # The side still has missing facts, unknown provenance or ambiguity, so it is
            # not eligible for confirmation however the reviewer answered.
            raise ServiceFault(ServiceErrorCode.CONFLICT) from unresolved
        material.policy.identity.version = material.facts.identity.version = revision_id
        return RevisionSnapshot.capture(material, revision_id, parent=snapshot.revision.reference)

    def _correct(
        self,
        snapshot: RevisionSnapshot,
        material: ReviewMaterial,
        pair: EvidencedPair,
        side: Side,
        accepted: AcceptedResponse,
        revision_id: str,
        reference: FactSideReference,
    ) -> RevisionSnapshot:
        """Replace one observed value with the reviewer's, keeping its stored evidence."""
        correction = accepted.command.correction
        if correction is None or correction.proposed is None:
            raise ServiceFault(ServiceErrorCode.VALIDATION)
        subject_id = side_subject_id(reference)
        if correction.subject_id != subject_id:
            # The ledger entry must name the observation the task is about; accepting a
            # different subject would file the change against the wrong cell.
            raise ServiceFault(ServiceErrorCode.VALIDATION)
        proposed = correction.proposed
        if proposed.state == "present" and not isinstance(proposed.value, NormalizedValue):
            # Material holds normalized values; a bare string or Decimal would enter the
            # arithmetic path without a declared type or unit.
            raise ServiceFault(ServiceErrorCode.VALIDATION)
        observation = getattr(pair.pair, side)
        sources = getattr(pair, f"{side}_sources")
        original = _public(observation, sources)
        if proposed.state == "present" and isinstance(proposed.value, NormalizedValue):
            stored_value = observation.value
            if stored_value is None or proposed.value.type != stored_value.type:
                raise ServiceFault(ServiceErrorCode.VALIDATION)
            if proposed.value.type == "number" and (
                stored_value.unit is None
                or proposed.value.unit != stored_value.unit
                or proposed.unit != stored_value.unit
            ):
                raise ServiceFault(ServiceErrorCode.VALIDATION)
        observation.value = proposed.value if proposed.state == "present" else None
        observation.raw_text = proposed.raw_text or None
        # Keep the extractor's measured score unchanged, including zero. The
        # submitted score stays in the proposal ledger and grants no authority;
        # the revised value still requires its own explicit confirmation.
        change = ValueRevision(
            subject_id=subject_id,
            original=original,
            proposed=proposed,
            corrected=_public(observation, sources),
            corrected_by=ActorReference(actor_id=accepted.actor.actor_id, kind="human"),
        )
        # `revise` is right here: the observation changed, so every confirmation that was
        # made against the old material must be cleared and re-made.
        return snapshot.revise(material, revision_id, changes=(change,))
