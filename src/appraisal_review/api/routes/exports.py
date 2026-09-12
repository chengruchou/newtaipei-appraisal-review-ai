"""Transport only for official table export operations.

A caller asks for the three official tables in exactly one format and polls the
operation it got back. Nothing here fills a workbook, converts a PDF or decides
formal eligibility; the executor behind the port does, and the domain contract
already refuses incoherent claims (a "succeeded" export missing a table, a PDF
operation delivering a workbook, a formal result with open blockers).

Idempotency lives in the service: an exact replay returns the original operation,
the same key with any changed field - the format included - is a version conflict.
An unwired composition answers capability_unavailable, never an empty operation.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends

from appraisal_review.api.dependencies import get_export_operations, get_principal
from appraisal_review.application.service_guards import Principal
from appraisal_review.domain.official_export import ExportOperation, ExportRequest
from appraisal_review.domain.service_contracts import ServiceProblem
from appraisal_review.ports.exports import ExportOperations

router = APIRouter(prefix="/v1/review-jobs/{job_id}/exports", tags=["exports"])

EXPORT_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: {"model": ServiceProblem, "description": "The principal lacks the case permission"},
    404: {"model": ServiceProblem, "description": "No such job or export for this principal"},
    409: {
        "model": ServiceProblem,
        "description": "A used idempotency key with a changed payload, or a stale run",
    },
    422: {"model": ServiceProblem, "description": "Invalid export request"},
    503: {"model": ServiceProblem, "description": "No export executor is configured"},
}

OperationsDependency = Annotated[ExportOperations, Depends(get_export_operations)]
PrincipalDependency = Annotated[Principal, Depends(get_principal)]


@router.post(
    "",
    status_code=202,
    response_model=None,
    responses={
        202: {"model": ExportOperation, "description": "The export is durably owned"},
        200: {"model": ExportOperation, "description": "Exact replay of a known request"},
        **EXPORT_RESPONSES,
    },
)
async def submit_export(
    job_id: UUID,
    request: ExportRequest,
    principal: PrincipalDependency,
    operations: OperationsDependency,
) -> ExportOperation:
    """Request the official tables in one format; replaying the same request is safe."""
    return await operations.submit(principal, job_id, request)


@router.get("/{export_id}", response_model=ExportOperation, responses=EXPORT_RESPONSES)
async def read_export(
    job_id: UUID,
    export_id: UUID,
    principal: PrincipalDependency,
    operations: OperationsDependency,
) -> ExportOperation:
    """Current state of one export, including per-table outcomes and blockers."""
    return await operations.read(principal, job_id, export_id)


EXPORT_ENDPOINTS = frozenset({submit_export, read_export})
