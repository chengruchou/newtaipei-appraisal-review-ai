"""Transport only for authenticated human tasks. No route here reviews or approves anything.

The response route is a POST to a sub-collection rather than a PATCH on the task, because
a response is an immutable record of what one reviewer answered at one version, not a
mutable field of the task. Two reviewers racing therefore produce one accepted response
and one 409, instead of a last-writer-wins overwrite.

There is deliberately no route to create, reassign, reopen or delete a task: tasks are
raised by a review attempt, and an endpoint that could mint one would let a caller invent
the very question whose answer authorizes a change.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from appraisal_review.api.dependencies import get_human_task_service, get_principal
from appraisal_review.application.human_tasks import HumanTaskService
from appraisal_review.application.service_guards import Principal, ServiceFault
from appraisal_review.domain.job_contracts import JobReference
from appraisal_review.domain.service_contracts import (
    HumanResponse,
    OpaqueID,
    ServiceErrorCode,
    ServiceProblem,
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
    PausedReviewView,
    ReviewSessionView,
)

router = APIRouter(tags=["human-tasks"])

PROBLEM_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: {"model": ServiceProblem, "description": "The principal lacks the case permission"},
    404: {"model": ServiceProblem, "description": "No such task or job for this principal"},
    409: {"model": ServiceProblem, "description": "The task or revision moved first"},
    422: {"model": ServiceProblem, "description": "Invalid response"},
    503: {"model": ServiceProblem, "description": "No human-task store is configured"},
}

ServiceDependency = Annotated[HumanTaskService, Depends(get_human_task_service)]
PrincipalDependency = Annotated[Principal, Depends(get_principal)]


@router.get(
    "/v1/review-jobs/{job_id}/tasks", response_model=TaskListView, responses=PROBLEM_RESPONSES
)
async def list_job_tasks(
    job_id: UUID, service: ServiceDependency, principal: PrincipalDependency
) -> TaskListView:
    """Answered and superseded tasks stay listed, so a stale page can discover why it is."""
    return await service.list_tasks(principal, job_id)


@router.get(
    "/v1/review-jobs/{job_id}/revisions",
    response_model=RevisionListView,
    responses=PROBLEM_RESPONSES,
)
async def list_job_revisions(
    job_id: UUID, service: ServiceDependency, principal: PrincipalDependency
) -> RevisionListView:
    return await service.list_revisions(principal, job_id)


@router.get("/v1/review-tasks/{task_id}", response_model=TaskView, responses=PROBLEM_RESPONSES)
async def read_task(
    task_id: UUID, service: ServiceDependency, principal: PrincipalDependency
) -> TaskView:
    """Carries the subject name of its own side, so no client re-derives a canonical key."""
    return await service.read_task(principal, task_id)


@router.get(
    "/v1/review-tasks/{task_id}/subject",
    response_model=TaskSubjectView,
    responses=PROBLEM_RESPONSES,
)
async def read_task_subject(
    task_id: UUID, service: ServiceDependency, principal: PrincipalDependency
) -> TaskSubjectView:
    return await service.read_subject(principal, task_id)


@router.post(
    "/v1/review-tasks/{task_id}/responses",
    response_model=ResponseReceipt,
    responses=PROBLEM_RESPONSES,
)
async def submit_task_response(
    task_id: UUID,
    command: HumanResponse,
    service: ServiceDependency,
    principal: PrincipalDependency,
) -> ResponseReceipt:
    """200 means the answer is committed; an exact replay returns the same receipt body."""
    return await service.respond(principal, task_id, command)


@router.get("/v1/review-session", response_model=ReviewSessionView, responses=PROBLEM_RESPONSES)
async def read_review_session(
    request: Request, principal: PrincipalDependency
) -> ReviewSessionView:
    configured = getattr(request.app.state, "configured_workbench_jobs", None)
    jobs = []
    if configured is not None:
        service = get_human_task_service(request)
        for job_id in configured():
            try:
                view = await service.list_tasks(principal, UUID(str(job_id)))
                jobs.append(JobReference(case_id=view.job.case_id, job_id=view.job.job_id))
            except ServiceFault as fault:
                if fault.problem.code not in {
                    ServiceErrorCode.UNAUTHORIZED,
                    ServiceErrorCode.NOT_FOUND,
                }:
                    raise
    return ReviewSessionView(
        actor=principal.actor,
        data_mode=getattr(request.app.state, "workbench_data_mode", "unspecified"),
        configured_jobs=tuple(jobs),
    )


@router.get(
    "/v1/review-jobs/{job_id}/context", response_model=CaseContextView, responses=PROBLEM_RESPONSES
)
async def read_case_context(
    job_id: UUID, service: ServiceDependency, principal: PrincipalDependency
) -> CaseContextView:
    return await service.read_context(principal, job_id)


@router.get(
    "/v1/review-jobs/{job_id}/assessment",
    response_model=PausedReviewView,
    responses=PROBLEM_RESPONSES,
)
async def read_paused_assessment(
    job_id: UUID, service: ServiceDependency, principal: PrincipalDependency
) -> PausedReviewView:
    return await service.read_assessment(principal, job_id)


@router.get(
    "/v1/review-tasks/{task_id}/responses/{key}",
    response_model=ResponseReceipt,
    responses=PROBLEM_RESPONSES,
)
async def read_task_response(
    task_id: UUID,
    key: OpaqueID,
    service: ServiceDependency,
    principal: PrincipalDependency,
) -> ResponseReceipt:
    return await service.read_response(principal, task_id, key)


HUMAN_TASK_ENDPOINTS = frozenset(
    {
        list_job_tasks,
        list_job_revisions,
        read_task,
        read_task_subject,
        submit_task_response,
        read_review_session,
        read_case_context,
        read_paused_assessment,
        read_task_response,
    }
)
