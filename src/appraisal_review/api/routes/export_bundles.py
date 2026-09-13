"""Transport for parent download requests (pdf / excel / both as one bundle).

Rules live in the service; an unwired plane answers capability_unavailable. The
ZIP download streams through the same content checks as every single artifact.
"""

from typing import Annotated, Any
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from appraisal_review.api.dependencies import get_principal
from appraisal_review.application.export_bundles import (
    BundleCommand,
    BundleView,
    ExportBundleService,
)
from appraisal_review.application.service_guards import Principal, ServiceFault
from appraisal_review.domain.service_contracts import ServiceErrorCode, ServiceProblem

router = APIRouter(prefix="/v1/review-jobs/{job_id}/export-bundles", tags=["export-bundles"])

BUNDLE_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: {"model": ServiceProblem, "description": "Unauthenticated or not a case member"},
    404: {"model": ServiceProblem, "description": "No such bundle"},
    409: {"model": ServiceProblem, "description": "Not complete, stale content or key reuse"},
    422: {"model": ServiceProblem, "description": "Invalid command"},
    503: {"model": ServiceProblem, "description": "Bundles are not configured"},
}


def get_export_bundles(request: Request) -> ExportBundleService:
    service: ExportBundleService | None = getattr(request.app.state, "export_bundles", None)
    if service is None:
        raise ServiceFault(ServiceErrorCode.CAPABILITY)
    return service


ServiceDependency = Annotated[ExportBundleService, Depends(get_export_bundles)]
PrincipalDependency = Annotated[Principal, Depends(get_principal)]


@router.post("", status_code=202, response_model=BundleView, responses=BUNDLE_RESPONSES)
async def submit_bundle(
    job_id: UUID,
    command: BundleCommand,
    principal: PrincipalDependency,
    service: ServiceDependency,
) -> BundleView:
    """One durable parent request; children replay instead of duplicating."""
    return await service.submit(principal, job_id, command)


@router.get("/{bundle_id}", response_model=BundleView, responses=BUNDLE_RESPONSES)
async def read_bundle(
    job_id: UUID,
    bundle_id: UUID,
    principal: PrincipalDependency,
    service: ServiceDependency,
) -> BundleView:
    """Live aggregate of the children; partial never reports complete."""
    return await service.read(principal, job_id, bundle_id)


@router.get("/{bundle_id}/content", responses=BUNDLE_RESPONSES)
async def download_bundle(
    job_id: UUID,
    bundle_id: UUID,
    principal: PrincipalDependency,
    service: ServiceDependency,
) -> Response:
    """The ZIP with its manifest; every file re-passes the content plane checks."""
    data, filename = await service.content(principal, job_id, bundle_id)
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f"attachment; filename=bundle.zip; filename*=UTF-8''{quote(filename)}"
            )
        },
    )


BUNDLE_ENDPOINTS = frozenset({submit_bundle, read_bundle, download_bundle})
