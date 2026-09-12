"""FastAPI override point; resolves tools only after payload validation."""

from fastapi import Request

from appraisal_review.application.bootstrap import ControllerFactory
from appraisal_review.application.human_tasks import HumanTaskService
from appraisal_review.application.review_jobs import ReviewJobService
from appraisal_review.application.service_guards import Principal, ServiceFault
from appraisal_review.domain.service_contracts import ServiceErrorCode
from appraisal_review.ports.content import ContentPlane
from appraisal_review.ports.exports import ExportOperations
from appraisal_review.ports.service import PrincipalResolver


def get_controller_factory(request: Request) -> ControllerFactory:
    factory: ControllerFactory = request.app.state.controller_factory
    return factory


def get_job_service(request: Request) -> ReviewJobService:
    """An undeployed durable plane reports capability_unavailable, never a fake success."""
    service: ReviewJobService | None = getattr(request.app.state, "job_service", None)
    if service is None:
        raise ServiceFault(ServiceErrorCode.CAPABILITY)
    return service


def get_human_task_service(request: Request) -> HumanTaskService:
    """Same rule as the job plane: an unconfigured store reports capability_unavailable."""
    service: HumanTaskService | None = getattr(request.app.state, "human_task_service", None)
    if service is None:
        raise ServiceFault(ServiceErrorCode.CAPABILITY)
    return service


def get_content_plane(request: Request) -> ContentPlane:
    """An unwired content plane reports capability_unavailable, never an empty download."""
    plane: ContentPlane | None = getattr(request.app.state, "content_plane", None)
    if plane is None:
        raise ServiceFault(ServiceErrorCode.CAPABILITY)
    return plane


def get_export_operations(request: Request) -> ExportOperations:
    """An unwired export executor reports capability_unavailable, never a fake operation."""
    operations: ExportOperations | None = getattr(request.app.state, "export_operations", None)
    if operations is None:
        raise ServiceFault(ServiceErrorCode.CAPABILITY)
    return operations


async def get_principal(request: Request) -> Principal:
    """Authenticate from the adapter-bound context; a request body never asserts identity."""
    resolver: PrincipalResolver | None = getattr(request.app.state, "principal_resolver", None)
    if resolver is None:
        raise ServiceFault(ServiceErrorCode.CAPABILITY)
    return await resolver.current_principal()
