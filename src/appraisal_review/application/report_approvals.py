"""Report approval service: evaluate readiness, submit one exact version, decide it.

Submission is where content gets pinned. The service re-reads the job's current run,
verifies the caller's pinned snapshot digest and template bundle against what the
server holds, evaluates the readiness policy, and only then fills the three official
workbooks to fix the hashes a person will approve. A snapshot or bundle that moved, a
policy blocker, or a stale run each refuse with a conflict rather than submitting
something nobody can honestly examine.

Decisions come from the authenticated principal holding the existing publish
permission; the request body never names an approver. Approve, return and withdraw are
conditional transitions on the stored record, so concurrent decisions and replayed old
commands cannot resurrect a withdrawn approval.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from uuid import NAMESPACE_URL, UUID, uuid5

from appraisal_review.adapters.local.approval_store import SQLiteApprovalStore
from appraisal_review.application.exports import ExportAssets, SnapshotProvider, WorkbookFiller
from appraisal_review.application.report_readiness import ReadinessPolicy, evaluate_readiness
from appraisal_review.application.review_jobs import ReviewJobService
from appraisal_review.application.service_guards import Principal, ServiceFault
from appraisal_review.domain.calculation_snapshot import CalculationSnapshot
from appraisal_review.domain.job_contracts import JobStatusView
from appraisal_review.domain.official_table_mapping import OfficialTable
from appraisal_review.domain.report_approval import (
    ApprovalDecision,
    ApprovalDecisionCommand,
    ReportApproval,
    ReportReadiness,
    ReportVersionBinding,
    SubmitReportApproval,
)
from appraisal_review.domain.service_contracts import (
    Permission,
    RunReference,
    ServiceErrorCode,
)

_STATUS_AFTER = {"approve": "approved", "return": "returned", "withdraw": "withdrawn"}
_EXPECTED_BEFORE = {"approve": "submitted", "return": "submitted", "withdraw": "approved"}


class ReportApprovalService:
    def __init__(
        self,
        *,
        jobs: ReviewJobService,
        store: SQLiteApprovalStore,
        assets: ExportAssets,
        filler: WorkbookFiller,
        snapshots: SnapshotProvider,
        policy: ReadinessPolicy,
        clock: Callable[[], int] = lambda: int(time.time()),
        confirmed_references: Callable[[UUID], frozenset[str]] | None = None,
    ) -> None:
        self.jobs = jobs
        self.store = store
        self.assets = assets
        self.filler = filler
        self.snapshots = snapshots
        self.policy = policy
        self.clock = clock
        # job_id -> identifiers of confirmations a human actually committed (answered
        # task ids). Absences claiming human confirmation must cite one of these; with
        # no registry wired, a spec-carrying policy fails closed on every claimed absence.
        self.confirmed_references = confirmed_references

    async def _current_snapshot(
        self, principal: Principal, job_id: UUID
    ) -> tuple[JobStatusView, CalculationSnapshot, RunReference]:
        status = await self.jobs.status(principal, job_id)
        principal.require(status.job.case_id, Permission.REVIEW)
        current = status.current_run
        if current is None:
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        snapshot = self.snapshots.read(status.job.case_id, current.revision.revision_id)
        if snapshot is None:
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        return status, snapshot, current

    def _evaluate(self, status: JobStatusView, snapshot: CalculationSnapshot) -> ReportReadiness:
        # Open human tasks gate submission at the evaluator, not as an afterthought:
        # the job status is the one source that knows them.
        registry = (
            self.confirmed_references(status.job.job_id)
            if self.confirmed_references is not None
            else None
        )
        return evaluate_readiness(
            snapshot,
            self.policy,
            open_task_count=len(status.open_task_ids),
            open_task_ids=tuple(str(task_id) for task_id in status.open_task_ids),
            confirmed_traces=registry,
        )

    async def readiness(self, principal: Principal, job_id: UUID) -> ReportReadiness:
        status, snapshot, _ = await self._current_snapshot(principal, job_id)
        return self._evaluate(status, snapshot)

    async def latest_for_current_binding(
        self, principal: Principal, job_id: UUID
    ) -> ReportApproval | None:
        """The approval the current content would publish under, if any."""
        _status, snapshot, _current = await self._current_snapshot(principal, job_id)
        try:
            binding = self._binding(snapshot)
        except ServiceFault:
            # An unfillable snapshot has no approvals to show; the readiness view and
            # the export draft path carry the actionable errors.
            return None
        return self.store.latest_for_binding(job_id, binding.digest())

    def _binding(self, snapshot: CalculationSnapshot) -> ReportVersionBinding:
        hashes: dict[OfficialTable, str] = {}
        for table, asset in sorted(self.assets.tables.items()):
            try:
                filled = self.filler(asset.template_path.read_bytes(), asset.mapping, snapshot)
            except ServiceFault:
                raise
            except Exception as error:
                # A snapshot the writer refuses (unit mismatch, bad shape) cannot become
                # a report version; the caller sees a validation refusal, not a 500.
                raise ServiceFault(ServiceErrorCode.VALIDATION) from error
            hashes[table] = hashlib.sha256(filled.content).hexdigest()
        return ReportVersionBinding(
            calculation_snapshot_digest=snapshot.digest(),
            template_bundle=self.assets.bundle,
            workbook_hashes=hashes,
        )

    async def submit(
        self, principal: Principal, job_id: UUID, command: SubmitReportApproval
    ) -> ReportApproval:
        command = SubmitReportApproval.model_validate_json(command.model_dump_json())
        status, snapshot, current = await self._current_snapshot(principal, job_id)
        if command.run.run_id != current.run_id or command.run.revision != current.revision:
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        if command.calculation_snapshot_digest != snapshot.digest():
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        if command.template_bundle != self.assets.bundle:
            raise ServiceFault(ServiceErrorCode.VALIDATION)
        readiness = self._evaluate(status, snapshot)
        if readiness.state != "ready_to_submit":
            # The blockers are served by the basis view; submitting cannot bypass them.
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        binding = self._binding(snapshot)
        approval = ReportApproval(
            approval_id=uuid5(
                NAMESPACE_URL,
                f"report-approval/{job_id}/{principal.actor.actor_id}/{command.idempotency_key}",
            ),
            job_id=job_id,
            run=command.run,
            binding=binding,
            status="submitted",
            submitted_by=principal.actor,
            submitted_at=self.clock(),
            readiness_policy_version=readiness.policy_version,
            payload_digest=command.payload_digest(),
        )
        stored, _created = self.store.create(
            approval,
            actor_id=principal.actor.actor_id,
            idempotency_key=command.idempotency_key,
            payload_digest=command.payload_digest(),
            binding_digest=binding.digest(),
        )
        return stored

    async def read(self, principal: Principal, job_id: UUID, approval_id: UUID) -> ReportApproval:
        status = await self.jobs.status(principal, job_id)
        principal.require(status.job.case_id, Permission.REVIEW)
        approval = self.store.read(job_id, approval_id)
        if approval is None:
            raise ServiceFault(ServiceErrorCode.NOT_FOUND)
        return approval

    async def decide(
        self,
        principal: Principal,
        job_id: UUID,
        approval_id: UUID,
        command: ApprovalDecisionCommand,
    ) -> ReportApproval:
        command = ApprovalDecisionCommand.model_validate_json(command.model_dump_json())
        status = await self.jobs.status(principal, job_id)
        # Deciding publishes or blocks a report: it takes the existing publish authority,
        # resolved from the authenticated session - never from the request body - and,
        # like human task responses, only a human principal can carry it. A system or
        # model actor holding publish rights still cannot stand in for a person.
        principal.require(status.job.case_id, Permission.PUBLISH)
        if principal.actor.kind != "human":
            raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
        approval = self.store.read(job_id, approval_id)
        if approval is None:
            raise ServiceFault(ServiceErrorCode.NOT_FOUND)
        expected = _EXPECTED_BEFORE[command.decision]
        updated = approval.model_copy(
            update={
                "status": _STATUS_AFTER[command.decision],
                "decision": ApprovalDecision(
                    decision=command.decision,
                    actor=principal.actor,
                    decided_at=self.clock(),
                    reason=command.reason,
                ),
            }
        )
        updated = ReportApproval.model_validate(updated.model_dump())
        stored, _changed = self.store.transition(
            job_id,
            approval_id,
            updated,
            expected_status=expected,  # type: ignore[arg-type]
            actor_id=principal.actor.actor_id,
            idempotency_key=command.idempotency_key,
            payload_digest=command.payload_digest(),
        )
        return stored

    def publication_authority(self, job_id: UUID, binding_digest: str) -> ReportApproval | None:
        """What a formal export must re-check right before it publishes bytes."""
        approval = self.store.latest_for_binding(job_id, binding_digest)
        if approval is None or not approval.authorizes_publication():
            return None
        return approval
