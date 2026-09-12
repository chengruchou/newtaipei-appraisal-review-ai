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
    ) -> None:
        self.jobs = jobs
        self.store = store
        self.assets = assets
        self.filler = filler
        self.snapshots = snapshots
        self.policy = policy
        self.clock = clock

    async def _current_snapshot(
        self, principal: Principal, job_id: UUID
    ) -> tuple[str, CalculationSnapshot, RunReference]:
        status = await self.jobs.status(principal, job_id)
        principal.require(status.job.case_id, Permission.REVIEW)
        current = status.current_run
        if current is None:
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        snapshot = self.snapshots.read(status.job.case_id, current.revision.revision_id)
        if snapshot is None:
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        return status.job.case_id, snapshot, current

    async def readiness(self, principal: Principal, job_id: UUID) -> ReportReadiness:
        _, snapshot, _ = await self._current_snapshot(principal, job_id)
        return evaluate_readiness(snapshot, self.policy)

    async def latest_for_current_binding(
        self, principal: Principal, job_id: UUID
    ) -> ReportApproval | None:
        """The approval the current content would publish under, if any."""
        _, snapshot, _current = await self._current_snapshot(principal, job_id)
        binding = self._binding(snapshot)
        return self.store.latest_for_binding(job_id, binding.digest())

    def _binding(self, snapshot: CalculationSnapshot) -> ReportVersionBinding:
        hashes: dict[OfficialTable, str] = {}
        for table, asset in sorted(self.assets.tables.items()):
            filled = self.filler(asset.template_path.read_bytes(), asset.mapping, snapshot)
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
        _case_id, snapshot, current = await self._current_snapshot(principal, job_id)
        if command.run.run_id != current.run_id or command.run.revision != current.revision:
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        if command.calculation_snapshot_digest != snapshot.digest():
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        if command.template_bundle != self.assets.bundle:
            raise ServiceFault(ServiceErrorCode.VALIDATION)
        readiness = evaluate_readiness(snapshot, self.policy)
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
            readiness_policy_version=self.policy.policy_version,
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
        # resolved from the authenticated session - never from the request body.
        principal.require(status.job.case_id, Permission.PUBLISH)
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
