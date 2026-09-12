"""Transport only for authorized source and artifact bytes.

These routes previously existed only on the local composition root, which kept them out of
the exported OpenAPI document and the generated browser client. They are declared here so
one definition serves both the running service and the published contract; a composition
that wires no content plane answers capability_unavailable rather than 404, which would
otherwise read as "this artifact does not exist".

No route here publishes, authorizes or converts anything. A download stays gated by the
publication grant the adapter checks, so an expired grant is a 403 and not a fresh one.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from appraisal_review.api.dependencies import get_content_plane, get_principal
from appraisal_review.application.service_guards import Principal
from appraisal_review.domain.document_models import Digest
from appraisal_review.domain.service_contracts import ServiceProblem
from appraisal_review.ports.content import ContentPlane, DeliveredContent

router = APIRouter(prefix="/v1", tags=["content"])

CONTENT_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "content": {
            "application/pdf": {"schema": {"type": "string", "format": "binary"}},
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {
                "schema": {"type": "string", "format": "binary"}
            },
        },
        "description": "Verified bytes for the requested source or published artifact",
    },
    403: {"model": ServiceProblem, "description": "No current grant for this case or artifact"},
    404: {"model": ServiceProblem, "description": "No such document or artifact"},
    409: {"model": ServiceProblem, "description": "The job has no current run to read from"},
    422: {"model": ServiceProblem, "description": "Invalid identifier, version or hash"},
    503: {"model": ServiceProblem, "description": "No content plane is configured"},
}

PlaneDependency = Annotated[ContentPlane, Depends(get_content_plane)]
PrincipalDependency = Annotated[Principal, Depends(get_principal)]


def _delivery(content: DeliveredContent) -> Response:
    headers = (
        {"Content-Disposition": f'attachment; filename="{content.filename}"'}
        if content.filename is not None
        else None
    )
    return Response(content.data, media_type=content.media_type, headers=headers)


@router.get(
    "/documents/{document_id}/content",
    response_class=Response,
    response_model=None,
    responses=CONTENT_RESPONSES,
)
async def read_source_content(
    document_id: UUID,
    principal: PrincipalDependency,
    plane: PlaneDependency,
    version: Annotated[str, Query(min_length=1, max_length=128)],
    content_hash: Annotated[Digest, Query()],
) -> Response:
    """Read an admitted source, pinned to the exact version and content hash requested."""
    return _delivery(
        await plane.read_source(
            principal, document_id=document_id, version=version, content_hash=content_hash
        )
    )


@router.get(
    "/review-jobs/{job_id}/artifacts/{artifact_id}/content",
    response_class=Response,
    response_model=None,
    responses=CONTENT_RESPONSES,
)
async def read_artifact_content(
    job_id: UUID,
    artifact_id: UUID,
    principal: PrincipalDependency,
    plane: PlaneDependency,
) -> Response:
    """Download a published artifact of the job's current run under a current grant."""
    return _delivery(await plane.read_artifact(principal, job_id=job_id, artifact_id=artifact_id))


CONTENT_ENDPOINTS = frozenset({read_source_content, read_artifact_content})
