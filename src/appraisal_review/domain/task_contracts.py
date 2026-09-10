"""Human-task control-plane contracts, added to the service-v1 bundle without widening v1.

A reviewer needs to see which questions are open, answer exactly one of them, and observe
what that answer committed. Frozen v1 models forbid extra fields, so those three views are
new models rather than additions to HumanTask or ServiceResult.

Every model here is an authorized projection. None carries a storage URI, a principal
roster, a lease owner, a queue identity or a raw material payload. See
docs/service-contracts.md.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from appraisal_review.domain.job_contracts import JobReference, JobStatus
from appraisal_review.domain.service_contracts import (
    HumanTask,
    MaterialRevision,
    ResponseAction,
    RevisionReference,
    RunReference,
    ServiceModel,
)


class TaskView(ServiceModel):
    """One task plus the names a client would otherwise have to re-derive to answer it.

    `subject_id` is the ledger name of the observation the task is about. It is published
    rather than left to the caller because deriving it means canonicalizing the comparison
    context, and two languages do not canonicalize alike: Python's json.dumps escapes
    non-ASCII by default and JavaScript's JSON.stringify does not, so a browser would
    compute a different name for any case whose identifiers are not ASCII. Every identifier
    in this project's real cases is Chinese.
    """

    task: HumanTask
    subject_id: str | None = None

    @model_validator(mode="after")
    def named_side(self) -> TaskView:
        if (self.task.side is None) != (self.subject_id is None):
            raise ValueError("A task about an exact side carries that side's subject name")
        return self


class TaskListView(ServiceModel):
    """The tasks of one job the caller may act on, ordered oldest first.

    Superseded tasks stay listed: a reviewer who is holding a stale task page needs to be
    able to discover that it was superseded, rather than to see it silently disappear.
    """

    job: JobReference
    tasks: tuple[TaskView, ...] = ()

    @model_validator(mode="after")
    def one_case(self) -> TaskListView:
        if any(view.task.run.revision.case_id != self.job.case_id for view in self.tasks):
            raise ValueError("Every listed task must belong to the job's case")
        if len({view.task.task_id for view in self.tasks}) != len(self.tasks):
            raise ValueError("Duplicate listed task")
        return self


class RevisionListView(ServiceModel):
    """The revision chain this job produced, oldest first and explicitly linked.

    The order is part of the contract: a consumer reconstructing what a reviewer changed
    must not have to re-sort by a timestamp the service does not publish.
    """

    job: JobReference
    revisions: tuple[MaterialRevision, ...] = ()

    @model_validator(mode="after")
    def linked_chain(self) -> RevisionListView:
        if any(r.reference.case_id != self.job.case_id for r in self.revisions):
            raise ValueError("Every listed revision must belong to the job's case")
        for parent, child in zip(self.revisions, self.revisions[1:], strict=False):
            if child.parent != parent.reference:
                raise ValueError("Listed revisions must form one parent-linked chain")
        return self


class ResponseReceipt(ServiceModel):
    """What one accepted response actually committed. Never an approval or a review result.

    The receipt is the idempotent body: an exact replay of the same principal, key and
    payload returns this same value, so a reviewer who retries a lost response can tell
    that their answer landed once rather than twice.
    """

    task_id: UUID
    consumed_version: int = Field(ge=1, strict=True)
    task_state: Literal["answered"] = "answered"
    action: ResponseAction
    job: JobReference
    job_status: JobStatus
    revision: RevisionReference | None = None
    resumed_run: RunReference | None = None
    superseded_task_ids: tuple[UUID, ...] = ()

    @model_validator(mode="after")
    def committed_effect(self) -> ResponseReceipt:
        # A revision that nothing is scheduled to review is stranded work, and a run
        # without a revision reviews nothing. Neither may be advertised alone.
        if (self.revision is None) != (self.resumed_run is None):
            raise ValueError("A new revision and its scheduled run are committed together")
        if self.revision is not None:
            if self.revision.case_id != self.job.case_id:
                raise ValueError("The committed revision must belong to the job's case")
            if self.resumed_run is None or self.resumed_run.revision != self.revision:
                raise ValueError("The resumed run must review the committed revision")
            if self.resumed_run.attempt_id is not None:
                raise ValueError("A resumed run is scheduled before any attempt")
        # Refusing to confirm is a recorded answer, not a correction: it changes no
        # material, so it can never present itself as having produced a revision.
        if (self.action == ResponseAction.REJECT) and self.revision is not None:
            raise ValueError("A rejection produces no new revision")
        if len(set(self.superseded_task_ids)) != len(self.superseded_task_ids):
            raise ValueError("Duplicate superseded task reference")
        if self.task_id in self.superseded_task_ids:
            raise ValueError("The answered task is not superseded by its own response")
        return self
