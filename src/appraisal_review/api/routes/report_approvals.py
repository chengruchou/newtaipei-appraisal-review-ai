"""Transport for report approvals: submit a version, read it, decide it.

Every meaningful rule lives in the service: readiness policy, content binding,
publish-permission checks and conditional transitions. The routes stay thin and an
unwired composition answers capability_unavailable.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends

from appraisal_review.api.dependencies import get_principal, get_report_approvals
from appraisal_review.application.report_approvals import ReportApprovalService
from appraisal_review.application.service_guards import Principal
from appraisal_review.domain.report_approval import (
    ApprovalDecisionCommand,
    ReportApproval,
    SubmitReportApproval,
)
from appraisal_review.domain.service_contracts import ServiceProblem

router = APIRouter(prefix="/v1/review-jobs/{job_id}/report-approvals", tags=["report-approvals"])

APPROVAL_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: {"model": ServiceProblem, "description": "Missing case or publish permission"},
    404: {"model": ServiceProblem, "description": "No such job or approval"},
    409: {
        "model": ServiceProblem,
        "description": "Not ready, stale content, wrong state, or a reused key",
    },
    422: {"model": ServiceProblem, "description": "Invalid command"},
    503: {"model": ServiceProblem, "description": "Approvals are not configured"},
}

ServiceDependency = Annotated[ReportApprovalService, Depends(get_report_approvals)]
PrincipalDependency = Annotated[Principal, Depends(get_principal)]


@router.post(
    "",
    status_code=202,
    response_model=None,
    responses={
        202: {"model": ReportApproval, "description": "The version is submitted for approval"},
        200: {"model": ReportApproval, "description": "Exact replay of a known submission"},
        **APPROVAL_RESPONSES,
    },
)
async def submit_report_approval(
    job_id: UUID,
    command: SubmitReportApproval,
    principal: PrincipalDependency,
    service: ServiceDependency,
) -> ReportApproval:
    """Pin the current content as one report version and submit it for approval."""
    return await service.submit(principal, job_id, command)


@router.get("/{approval_id}", response_model=ReportApproval, responses=APPROVAL_RESPONSES)
async def read_report_approval(
    job_id: UUID,
    approval_id: UUID,
    principal: PrincipalDependency,
    service: ServiceDependency,
) -> ReportApproval:
    return await service.read(principal, job_id, approval_id)


@router.post(
    "/{approval_id}/decisions",
    response_model=ReportApproval,
    responses=APPROVAL_RESPONSES,
)
async def decide_report_approval(
    job_id: UUID,
    approval_id: UUID,
    command: ApprovalDecisionCommand,
    principal: PrincipalDependency,
    service: ServiceDependency,
) -> ReportApproval:
    """Approve, return or withdraw - by the authenticated publish-permission holder."""
    return await service.decide(principal, job_id, approval_id, command)


APPROVAL_ENDPOINTS = frozenset(
    {submit_report_approval, read_report_approval, decide_report_approval}
)
