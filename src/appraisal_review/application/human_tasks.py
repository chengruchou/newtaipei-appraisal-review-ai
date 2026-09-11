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
from collections.abc import Awaitable, Callable
from uuid import UUID, uuid4

from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.application.service_guards import (
    Principal,
    ServiceFault,
    admit_response,
    response_digest,
)
from appraisal_review.domain.case_review import matching_rule_sets
from appraisal_review.domain.confidence import Side, confirm_side, confirmation_digest
from appraisal_review.domain.document_models import SourceCitation
from appraisal_review.domain.factor_models import (
    EvidencedPair,
    FactorObservation,
    NormalizedValue,
    ReviewMaterial,
    ScopedRules,
)
from appraisal_review.domain.job_contracts import JobReference
from appraisal_review.domain.models import EvidenceRef
from appraisal_review.domain.review_contracts import content_digest
from appraisal_review.domain.service_contracts import (
    AcceptedResponse,
    ActorReference,
    FactSideReference,
    HumanResponse,
    HumanTask,
    Permission,
    PublicValue,
    ResponseAction,
    RuleReference,
    RunReference,
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
from appraisal_review.domain.workbench_contracts import (
    CaseContextView,
    CaseObservationView,
    PausedReviewView,
    PinnedRuleView,
    RuleBundleView,
    RuleSelectionView,
)
from appraisal_review.ports.human_tasks import HumanTaskStore, TaskRecord
from appraisal_review.ports.jobs import ConditionFailed, JobRecord
from appraisal_review.ports.workbench import WorkbenchReadAccess

Clock = Callable[[], int]
RevisionIdFactory = Callable[[], str]

# Rule, material and publication approval are recorded through the existing exact-material
# authority, not through this API. Admitting such a task here and reporting "answered"
# would advertise an authorization the service never wrote.
MATERIAL_KINDS = frozenset({TaskKind.FACT, TaskKind.CORRECTION, TaskKind.EVIDENCE})


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
        assessment_reader: Callable[
            [RunReference, RevisionSnapshot], Awaitable[PausedReviewView | None]
        ]
        | None = None,
        workbench_access: WorkbenchReadAccess | None = None,
    ) -> None:
        self.store = store
        self.clock = clock
        self.new_revision_id = new_revision_id
        self.assessment_reader = assessment_reader
        self.workbench_access = workbench_access

    # -- reads ---------------------------------------------------------------

    async def list_tasks(self, principal: Principal, job_id: UUID) -> TaskListView:
        job = await self._authorized_job(principal, job_id)
        records = await self.store.list_tasks(job_id=job_id)
        if any(not self._visible(principal, record) for record in records):
            raise ServiceFault(ServiceErrorCode.NOT_FOUND)
        await self._task_read_authority(principal, records)
        return TaskListView(
            job=JobReference(case_id=job.case_id, job_id=job_id),
            tasks=tuple(task_view(record.task) for record in records),
        )

    async def read_task(self, principal: Principal, task_id: UUID) -> TaskView:
        record = await self._authorized(principal, task_id)
        await self._task_read_authority(principal, (record,))
        return task_view(record.task)

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
        await self._task_read_authority(principal, (record,))
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
        revisions = await self.store.list_revisions(job_id=job_id)
        if self.workbench_access is not None:
            for revision in revisions:
                await self.workbench_access.read(principal, revision.reference)
            await self.workbench_access.require_current(
                principal, tuple(r.reference for r in revisions)
            )
        return RevisionListView(
            job=JobReference(case_id=job.case_id, job_id=job_id),
            revisions=revisions,
        )

    async def read_context(self, principal: Principal, job_id: UUID) -> CaseContextView:
        job = await self._authorized_job(principal, job_id)
        revision = job.current_run.revision
        snapshot = await self._read_workbench(principal, job)
        material = snapshot.material
        if (
            snapshot.revision.reference != revision
            or content_digest(material) != revision.material_digest
        ):
            raise ServiceFault(ServiceErrorCode.CONFLICT)

        def reference(scoped: ScopedRules) -> RuleReference:
            # Read from the canonical snapshot, never from client-supplied rule labels.
            return RuleReference(
                rule_set_id=scoped.rules.rule_set_id,
                version=scoped.rules.version,
                content_hash=content_digest(scoped),
                context=scoped.context,
            )

        rules = tuple(
            PinnedRuleView(
                reference=reference(scoped),
                applicability=scoped.rules.applicability,
                zone=scoped.zone,
                declared_status=scoped.rules.status,
                evidence=tuple(scoped.evidence),
            )
            for scoped in sorted(material.policy.rule_sets, key=lambda item: item.context.key())
        )
        selections = []
        for entry in material.policy.inventory.contexts:
            matches = matching_rule_sets(material.policy, entry.context)
            selections.append(
                RuleSelectionView(
                    context=entry.context,
                    status="missing"
                    if not matches
                    else "unique"
                    if len(matches) == 1
                    else "ambiguous",
                    matches=tuple(reference(scoped) for scoped in matches),
                )
            )
        sides: tuple[Side, ...] = ("target", "comparable")
        result = CaseContextView(
            job=JobReference(case_id=job.case_id, job_id=job_id),
            revision=revision,
            identity=material.policy.identity,
            documents=snapshot.revision.documents,
            rule_bundle=RuleBundleView.model_validate(
                material.policy.rule_bundle.model_dump(exclude={"catalog"})
            )
            if material.policy.rule_bundle
            else None,
            rule_bundle_id=material.policy.rule_bundle.bundle_id
            if material.policy.rule_bundle
            else None,
            rules=rules,
            selections=tuple(selections),
            observations=tuple(
                CaseObservationView(
                    side=FactSideReference(
                        context=pair.context,
                        factor_id=pair.pair.factor_id,
                        side=side,
                        input_digest=confirmation_digest(pair, side),
                    ),
                    observation=_public(getattr(pair.pair, side), getattr(pair, f"{side}_sources")),
                )
                for pair in material.facts.pairs
                for side in sides
            ),
        )

        await self._read_workbench(principal, job)
        return result

    async def read_assessment(self, principal: Principal, job_id: UUID) -> PausedReviewView:
        job = await self._authorized_job(principal, job_id)
        if self.assessment_reader is None:
            raise ServiceFault(ServiceErrorCode.CAPABILITY)
        if job.status.value != "waiting_for_human":
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        records = await self.store.list_tasks(job_id=job_id)
        if any(not self._visible(principal, record) for record in records):
            raise ServiceFault(ServiceErrorCode.NOT_FOUND)
        runs = {
            record.task.run.model_dump_json(): record.task.run
            for record in records
            if record.task.task_id in job.open_task_ids
            and record.task.state == "open"
            and record.task.run.run_id == job.current_run.run_id
            and record.task.run.revision == job.current_run.revision
        }
        if len(runs) != 1:
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        run = next(iter(runs.values()))
        snapshot = await self._read_workbench(principal, job)
        result = await self.assessment_reader(run, snapshot)
        if result is None or result.run != run:
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        await self._read_workbench(principal, job)
        return result

    async def read_response(self, principal: Principal, task_id: UUID, key: str) -> ResponseReceipt:
        record = await self._authorized(principal, task_id)
        if self.workbench_access is None:
            raise ServiceFault(ServiceErrorCode.CAPABILITY)
        references = (record.task.run.revision,)
        await self.workbench_access.require_current(principal, references)
        stored = await self.store.read_receipt(principal_id=principal.actor.actor_id, key=key)
        await self.workbench_access.require_current(principal, references)
        if stored is None or stored.receipt.task_id != task_id:
            raise ServiceFault(ServiceErrorCode.NOT_FOUND)
        return stored.receipt

    async def _read_workbench(self, principal: Principal, job: JobRecord) -> RevisionSnapshot:
        if self.workbench_access is None:
            raise ServiceFault(ServiceErrorCode.CAPABILITY)
        snapshot = await self.workbench_access.read(principal, job.current_run.revision)
        if (
            snapshot.revision.reference != job.current_run.revision
            or content_digest(snapshot.material) != job.current_run.revision.material_digest
            or await self._authorized_job(principal, job.job_id) != job
        ):
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        await self.workbench_access.require_current(principal, (job.current_run.revision,))
        return snapshot

    async def _task_read_authority(
        self, principal: Principal, records: tuple[TaskRecord, ...]
    ) -> None:
        if self.workbench_access is None:
            return
        references = tuple(dict.fromkeys(record.task.run.revision for record in records))
        for reference in references:
            await self.workbench_access.read(principal, reference)
        await self.workbench_access.require_current(principal, references)

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
        digest = response_digest(command)

        # Replay before admission, deliberately. The first use of this key already moved
        # the task off the version the retry still names, so admission would reject an
        # exact retry that in fact succeeded.
        stored = await self.store.read_receipt(
            principal_id=principal.actor.actor_id, key=command.idempotency_key
        )
        if stored is not None:
            if self.workbench_access is not None:
                await self.workbench_access.require_current(principal, (record.task.run.revision,))
            if stored.payload_digest != digest:
                raise ServiceFault(ServiceErrorCode.CONFLICT)
            return stored.receipt

        accepted = admit_response(record.task, command, principal, current=record.current_revision)
        await self._task_read_authority(principal, (record,))
        next_revision = await self._next_revision(record, accepted)
        if self.workbench_access is not None:
            await self.workbench_access.require_current(principal, (record.current_revision,))
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
        if material.policy.rule_bundle is not None:
            if material.policy.rule_bundle.identity != material.policy.identity:
                raise ServiceFault(ServiceErrorCode.CONFLICT)
            material.policy.rule_bundle.identity.version = revision_id
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
        # A correction never raises a raw score: human authority is recorded as a
        # confirmation on the next round, not by inflating the extractor's confidence.
        observation.confidence = min(
            observation.confidence,
            proposed.confidence if proposed.confidence is not None else observation.confidence,
        )
        if accepted.command.action == ResponseAction.SUPPLY_EVIDENCE:
            registry = material.policy.registry
            if not proposed.evidence or any(
                not registry.resolves(citation) for citation in proposed.evidence
            ):
                raise ServiceFault(ServiceErrorCode.VALIDATION)
            # Evidence is selected from the pinned registry. It does not authorize
            # a new source, raise confidence or replace the next confirmation.
            sources = list(proposed.evidence)
            setattr(pair, f"{side}_sources", sources)
            observation.evidence = [
                EvidenceRef(
                    document_id=citation.document_id,
                    source_file=next(
                        doc.uri
                        for doc in registry.documents
                        if doc.document_id == citation.document_id
                    ),
                    page=citation.page,
                    confidence=observation.confidence,
                    bounding_box=citation.bbox,
                    coordinate_system="pdf_bottom_left",
                )
                for citation in sources
            ]
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
