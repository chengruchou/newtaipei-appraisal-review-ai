"""Purpose-specific local human tasks, corrections and deterministic re-entry."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

from pydantic import ValidationError

from appraisal_review.application.material_corrections import MaterialSubjectMap
from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.domain.case_review import CaseReviewer
from appraisal_review.domain.document_models import SourceCitation
from appraisal_review.domain.factor_models import CaseReviewResult, EvaluationStatus
from appraisal_review.domain.review_contracts import content_digest
from appraisal_review.domain.service_contracts import (
    AuthorizationRecord,
    FailureCategory,
    HumanResponse,
    HumanResponseResult,
    HumanReviewHandoff,
    HumanTask,
    Permission,
    ResponseAction,
    RunReference,
    ServiceErrorCode,
    TaskKind,
    WorkflowBlocker,
)
from appraisal_review.domain.source_purpose import SourcePurposes
from appraisal_review.ports.approval import ReviewAuthorization
from appraisal_review.ports.service import (
    HumanTaskContext,
    HumanTaskRepository,
    HumanTaskTransition,
    PrincipalResolver,
)

_TASK_ACTIONS = {
    TaskKind.FACT: (Permission.CONFIRM, ResponseAction.CONFIRM),
    TaskKind.CORRECTION: (Permission.CORRECT, ResponseAction.CORRECT),
    TaskKind.EVIDENCE: (Permission.CORRECT, ResponseAction.SUPPLY_EVIDENCE),
    TaskKind.RULES: (Permission.APPROVE_RULES, ResponseAction.APPROVE),
    TaskKind.MATERIAL: (Permission.APPROVE_MATERIAL, ResponseAction.APPROVE),
    TaskKind.PUBLICATION: (Permission.PUBLISH, ResponseAction.AUTHORIZE_PUBLICATION),
}
_SIDE_TASKS = {TaskKind.FACT, TaskKind.CORRECTION, TaskKind.EVIDENCE}


@dataclass(frozen=True)
class HumanTaskBinding:
    """Trusted mapping from one workflow blocker to stored deterministic findings."""

    blocker_id: str
    kind: TaskKind
    finding_ids: tuple[str, ...]
    subject_id: str | None = None


class HumanTaskService:
    def __init__(
        self,
        *,
        repository: HumanTaskRepository,
        principals: PrincipalResolver,
        subjects: MaterialSubjectMap,
        authorization: ReviewAuthorization | None = None,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        self._repository = repository
        self._principals = principals
        self._subjects = subjects
        self._authorization = authorization
        self._id = id_factory

    async def handoff_for_run(
        self, run: RunReference, bindings: tuple[HumanTaskBinding, ...]
    ) -> HumanReviewHandoff:
        """Derive a complete handoff from actual findings without persisting tasks.

        Binding configuration chooses response purposes; it cannot hide findings.
        The subsequent admitted human action calls create_from_handoff atomically.
        """
        principal = await self._principals.current_principal()
        if principal.actor.kind not in {"system", "human"}:
            raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
        context = await self._repository.context(principal, run)
        covered = {finding for binding in bindings for finding in binding.finding_ids}
        covered_contexts = {
            finding.context.key()
            for finding in context.review.findings
            if finding.id in covered and finding.context is not None
        }
        # Aggregate calculations are recomputed, never confirmed as side facts.
        # They remain in the stored result; only a mapped cause in that exact
        # comparison permits deferring the aggregate to the mandatory re-review.
        required = {
            finding.id
            for finding in context.review.findings
            if finding.status != "verified"
            and not (
                finding.kind == "calculation"
                and finding.context is not None
                and finding.context.key() in covered_contexts
            )
        }
        if (
            not required
            or required != covered
            or len({binding.blocker_id for binding in bindings}) != len(bindings)
        ):
            raise ServiceFault(ServiceErrorCode.CAPABILITY)
        drafts = tuple(
            self._build_task(
                context,
                kind=binding.kind,
                finding_ids=binding.finding_ids,
                subject_id=binding.subject_id,
            )
            for binding in bindings
        )
        blockers = tuple(
            WorkflowBlocker(
                blocker_id=binding.blocker_id,
                reason_code=task.reason_code or "review-findings",
                affected_subject_ids=task.affected_subject_ids,
                evidence=task.evidence,
            )
            for binding, task in zip(bindings, drafts, strict=True)
        )
        evidence = {
            citation.model_dump_json(): citation for task in drafts for citation in task.evidence
        }
        return HumanReviewHandoff(
            reason_code="review-findings",
            category=FailureCategory.DETERMINISTIC_REVIEW_BLOCKER,
            question="Review all located findings before authorizing subsequent work.",
            revision=run.revision,
            blockers=blockers,
            affected_subject_ids=tuple(
                dict.fromkeys(subject for task in drafts for subject in task.affected_subject_ids)
            ),
            evidence=tuple(evidence.values()),
        )

    async def create_task(
        self,
        run: RunReference,
        *,
        kind: TaskKind,
        finding_ids: tuple[str, ...],
        subject_id: str | None = None,
    ) -> HumanTask:
        """Trusted application chooses purpose; every finding comes from stored review."""
        principal = await self._principals.current_principal()
        if principal.actor.kind not in {"system", "human"}:
            raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
        context = await self._repository.context(principal, run)
        task = self._build_task(context, kind=kind, finding_ids=finding_ids, subject_id=subject_id)
        return await self._repository.create_task(principal, task)

    async def create_from_handoff(
        self,
        run: RunReference,
        handoff: HumanReviewHandoff,
        bindings: tuple[HumanTaskBinding, ...],
    ) -> tuple[HumanTask, ...]:
        """Resolve all deterministic-review blockers into one atomic local task batch."""
        handoff = HumanReviewHandoff.model_validate_json(handoff.model_dump_json())
        principal = await self._principals.current_principal()
        context = await self._repository.context(principal, run)
        if handoff.revision != run.revision:
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        blockers = {blocker.blocker_id: blocker for blocker in handoff.blockers}
        if (
            not blockers
            or len(bindings) != len(blockers)
            or {binding.blocker_id for binding in bindings} != set(blockers)
        ):
            raise ServiceFault(ServiceErrorCode.CAPABILITY)
        tasks = []
        for binding in bindings:
            task = self._build_task(
                context,
                kind=binding.kind,
                finding_ids=binding.finding_ids,
                subject_id=binding.subject_id,
            )
            blocker = blockers[binding.blocker_id]
            if (
                blocker.reason_code != task.reason_code
                or set(blocker.affected_subject_ids) != set(task.affected_subject_ids)
                or any(citation not in task.evidence for citation in blocker.evidence)
            ):
                raise ServiceFault(ServiceErrorCode.CONFLICT)
            tasks.append(task)
        return await self._repository.create_tasks(principal, tuple(tasks))

    def _build_task(
        self,
        context: HumanTaskContext,
        *,
        kind: TaskKind,
        finding_ids: tuple[str, ...],
        subject_id: str | None,
    ) -> HumanTask:
        run = context.run
        findings = [finding for finding in context.review.findings if finding.id in finding_ids]
        if (
            not finding_ids
            or len(set(finding_ids)) != len(finding_ids)
            or set(finding_ids) != {finding.id for finding in findings}
            or kind not in _TASK_ACTIONS
        ):
            raise ServiceFault(ServiceErrorCode.VALIDATION)
        if kind == TaskKind.PUBLICATION:
            if context.review.status != EvaluationStatus.VERIFIED or not self._permits(context):
                raise ServiceFault(ServiceErrorCode.CAPABILITY)
        elif any(finding.status == "verified" for finding in findings):
            raise ServiceFault(ServiceErrorCode.CONFLICT)

        side = None
        evidence = [citation for finding in findings for citation in finding.evidence]
        material = context.snapshot.material
        if kind in _SIDE_TASKS:
            if subject_id is None:
                raise ServiceFault(ServiceErrorCode.VALIDATION)
            try:
                side = self._subjects.side_reference(context.snapshot, subject_id)
            except ValueError as error:
                raise ServiceFault(ServiceErrorCode.VALIDATION) from error
            if any(
                (finding.context is not None and finding.context != side.context)
                or (finding.factor_id is not None and finding.factor_id != side.factor_id)
                or (
                    (finding.context is None or finding.factor_id is None)
                    and finding.id != f"{side.context.key()}/factor/{side.factor_id}"
                )
                for finding in findings
            ):
                raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
            evidence += list(self._subjects.public_value(context.snapshot, subject_id).evidence)
            question = (
                f"{_TASK_ACTIONS[kind][1].value.replace('_', ' ').capitalize()} "
                f"{subject_id} ({side.context.scope}, {side.side}) against the cited source. "
                "This response will require a full review of the resulting material."
            )
        else:
            if subject_id is not None:
                raise ServiceFault(ServiceErrorCode.VALIDATION)
            subject_id = {
                TaskKind.RULES: "rules",
                TaskKind.MATERIAL: "material",
                TaskKind.PUBLICATION: "publication",
            }[kind]
            if kind in {TaskKind.RULES, TaskKind.MATERIAL} and any(
                finding.kind not in {"approval", "rule_rejected", "rule_source"}
                for finding in findings
            ):
                raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
            if kind == TaskKind.RULES:
                if not any(
                    scoped.rules.status == "candidate" for scoped in material.policy.rule_sets
                ):
                    raise ServiceFault(ServiceErrorCode.CONFLICT)
                required_findings = {
                    finding.id
                    for finding in context.review.findings
                    if finding.status != "verified"
                    and finding.kind in {"approval", "rule_rejected", "rule_source"}
                }
                if set(finding_ids) != required_findings:
                    raise ServiceFault(ServiceErrorCode.VALIDATION)
                evidence += [ref for scoped in material.policy.rule_sets for ref in scoped.evidence]
                scopes = ", ".join(scoped.context.key() for scoped in material.policy.rule_sets)
                question = f"Approve the cited candidate rules for these exact contexts: {scopes}?"
            elif kind == TaskKind.MATERIAL:
                question = (
                    "Approve this exact material after separate fact and rule review? "
                    "A current signed local material receipt is required."
                )
            else:
                question = (
                    "Authorize publication of this exact verified result? "
                    "Artifact writing and publication checks remain required."
                )
        if any(not material.policy.registry.resolves(citation) for citation in evidence):
            raise ServiceFault(ServiceErrorCode.VALIDATION)
        current_evidence = {citation.model_dump_json(): citation for citation in evidence}
        unique_evidence: tuple[SourceCitation, ...] = tuple(current_evidence.values())
        reason_kinds = {finding.kind for finding in findings}
        reason = next(iter(reason_kinds)) if len(reason_kinds) == 1 else "multiple-review-findings"
        permission, response = _TASK_ACTIONS[kind]
        task = HumanTask(
            task_id=self._id(),
            run=run,
            version=1,
            kind=kind,
            required_permission=permission,
            side=side,
            result_digest=content_digest(context.review) if kind == TaskKind.PUBLICATION else None,
            question=question,
            evidence=unique_evidence,
            finding_ids=finding_ids,
            allowed_responses=(response, ResponseAction.REJECT),
            reason_code=reason,
            affected_subject_ids=(
                tuple(
                    f"rules-{content_digest(scoped.context)}"
                    for scoped in material.policy.rule_sets
                )
                if kind == TaskKind.RULES
                else (subject_id,)
            ),
        )
        return task

    def _permits(self, context: HumanTaskContext) -> bool:
        return self._authorization is not None and self._authorization.permits(
            context.snapshot.material
        )

    async def respond(self, command: HumanResponse) -> HumanResponseResult:
        principal = await self._principals.current_principal()
        command = HumanResponse.model_validate_json(command.model_dump_json())

        def transition(task: HumanTask, context: HumanTaskContext) -> HumanTaskTransition:
            snapshot = context.snapshot
            if task.side is not None and (
                len(task.affected_subject_ids) != 1
                or self._subjects.side_reference(snapshot, task.affected_subject_ids[0])
                != task.side
            ):
                raise ServiceFault(ServiceErrorCode.CONFLICT)
            if task.kind == TaskKind.PUBLICATION and (
                task.result_digest != content_digest(context.review)
                or context.review.status != EvaluationStatus.VERIFIED
                or not self._permits(context)
            ):
                raise ServiceFault(ServiceErrorCode.CONFLICT)
            if command.action == ResponseAction.REJECT:
                return HumanTaskTransition(snapshot)
            authorization = None
            if task.kind in {TaskKind.CORRECTION, TaskKind.EVIDENCE}:
                correction = command.correction
                if (
                    correction is None
                    or task.side is None
                    or correction.subject_id not in task.affected_subject_ids
                ):
                    raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
                snapshot = self._subjects.correct(
                    snapshot,
                    correction,
                    actor=principal.actor,
                    revision_id=f"revision-{self._id().hex}",
                    allow_missing_evidence=task.kind == TaskKind.EVIDENCE,
                )
            elif task.kind == TaskKind.FACT:
                snapshot = self._subjects.confirm(
                    snapshot,
                    task.affected_subject_ids[0],
                    actor=principal.actor,
                    revision_id=f"revision-{self._id().hex}",
                    retained_confirmations=context.confirmed_sides,
                )
            elif task.kind == TaskKind.RULES:
                material = snapshot.material
                purposes = SourcePurposes.selected(material.policy.registry)
                if any(
                    scoped.rules.status == "rejected"
                    or not purposes.allows(scoped.evidence, "rule")
                    for scoped in material.policy.rule_sets
                ):
                    raise ServiceFault(ServiceErrorCode.VALIDATION)
                for scoped in material.policy.rule_sets:
                    scoped.rules.status = "approved"
                snapshot = snapshot.revise(
                    material, f"revision-{self._id().hex}", changes=snapshot.revision.changes
                )
                authorization = AuthorizationRecord(
                    authorization_id=self._id(),
                    purpose="rules",
                    revision=snapshot.revision,
                    actor=principal.actor,
                )
            elif task.kind == TaskKind.MATERIAL:
                if not self._permits(context) or any(
                    s.rules.status != "approved" for s in snapshot.material.policy.rule_sets
                ):
                    raise ServiceFault(ServiceErrorCode.CAPABILITY)
                authorization = AuthorizationRecord(
                    authorization_id=self._id(),
                    purpose="material",
                    revision=snapshot.revision,
                    actor=principal.actor,
                )
            elif task.kind == TaskKind.PUBLICATION:
                authorization = AuthorizationRecord(
                    authorization_id=self._id(),
                    purpose="publication",
                    revision=snapshot.revision,
                    actor=principal.actor,
                    result_digest=task.result_digest,
                )
            next_run = (
                None
                if task.kind == TaskKind.PUBLICATION
                else RunReference(run_id=self._id(), revision=snapshot.revision.reference)
            )
            return HumanTaskTransition(snapshot, next_run, authorization)

        try:
            return await self._repository.respond(principal, command, transition)
        except (ValueError, PermissionError, ValidationError) as error:
            raise ServiceFault(ServiceErrorCode.VALIDATION) from error

    async def reenter(self, run: RunReference) -> CaseReviewResult:
        principal = await self._principals.current_principal()

        def review(context: HumanTaskContext) -> CaseReviewResult:
            material = context.snapshot.material
            return CaseReviewer(self._authorization).review(
                material.policy, material.facts, material.policy.registry
            )

        return await self._repository.reenter(principal, run, review)
