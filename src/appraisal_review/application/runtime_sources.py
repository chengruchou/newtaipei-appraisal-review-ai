"""C2 admission and current source authorization around durable review execution."""

from __future__ import annotations

import asyncio
from typing import Protocol
from uuid import UUID

from appraisal_review.application.review_jobs import (
    ReviewJobService,
    SubmissionOutcome,
    status_view,
)
from appraisal_review.application.runtime_worker import ExecutedReview, ReviewExecution
from appraisal_review.application.service_guards import Principal, ServiceFault
from appraisal_review.domain.document_transfer import DocumentErrorCode, DocumentFault
from appraisal_review.domain.job_contracts import JobAcceptance
from appraisal_review.domain.service_contracts import (
    MaterialRevision,
    Permission,
    ReviewSubmission,
    RevisionReference,
    RunReference,
    ServiceErrorCode,
)
from appraisal_review.ports.jobs import ClaimedAttempt, JobRecord
from appraisal_review.ports.model_dispatch import dispatch_async_authority
from appraisal_review.ports.runtime_documents import RuntimeDocuments


class RevisionLookup(Protocol):
    async def read(
        self, principal: Principal, reference: RevisionReference
    ) -> MaterialRevision: ...


class CurrentPrincipalLookup(Protocol):
    async def read(self, principal_id: str, case_id: str) -> Principal:
        """Resolve current directory grants, not copied submission-time permissions."""
        ...


class SubmissionLookup(Protocol):
    async def read_submission(self, *, run_id: UUID) -> ReviewSubmission | None: ...


def source_fault(error: DocumentFault) -> ServiceFault:
    if error.problem.code == DocumentErrorCode.UNAVAILABLE:
        return ServiceFault(ServiceErrorCode.CAPABILITY)
    code = (
        ServiceErrorCode.UNAUTHORIZED
        if error.problem.code == DocumentErrorCode.UNAUTHORIZED
        else ServiceErrorCode.VALIDATION
    )
    return ServiceFault(code)


class SnapshotJobService(ReviewJobService):
    """Use with the inherited HTTP contract once its authenticated owner is integrated."""

    def bind_sources(self, documents: RuntimeDocuments, revisions: RevisionLookup) -> None:
        self.documents, self.revisions = documents, revisions

    async def submit(self, principal: Principal, submission: ReviewSubmission) -> SubmissionOutcome:
        if not hasattr(self, "documents") or not hasattr(self, "revisions"):
            raise ServiceFault(ServiceErrorCode.CAPABILITY)
        submission = ReviewSubmission.model_validate_json(submission.model_dump_json())
        principal.require(submission.revision.case_id, Permission.REVIEW)
        material = await self.revisions.read(principal, submission.revision)
        material = MaterialRevision.model_validate_json(material.model_dump_json())
        if material.reference != submission.revision or set(material.documents) != set(
            submission.documents
        ):
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        run = RunReference(run_id=self.new_id(), revision=material.reference)
        try:
            # Immutable orphan snapshots are safe on a crash; no job is accepted beforehand.
            await asyncio.to_thread(self.documents.create_snapshot, principal, run, material)
            for reference in submission.documents:
                await asyncio.to_thread(self.documents.read_snapshot, principal, run, reference)
            record, created = await self.store.create_job(
                principal, submission, job_id=self.new_id(), run_id=run.run_id, now=self.clock()
            )
            if not created:
                if record.current_run == run:
                    for reference in submission.documents:
                        await asyncio.to_thread(
                            self.documents.read_snapshot, principal, record.current_run, reference
                        )
                else:
                    # The job has advanced past the submitted run (a human correction
                    # or a fact adoption), and only executed runs carry per-run
                    # document snapshots. The replay check's purpose - the caller is
                    # still authorized for exactly this material - holds through the
                    # same registered-material read a fresh submit starts with; the
                    # advanced revision carries its documents forward unchanged.
                    current = await self.revisions.read(principal, record.current_run.revision)
                    current = MaterialRevision.model_validate_json(current.model_dump_json())
                    if set(current.documents) != set(submission.documents):
                        raise ServiceFault(ServiceErrorCode.CONFLICT)
        except DocumentFault as error:
            raise source_fault(error) from None
        view = status_view(record)
        return SubmissionOutcome(
            created=created,
            status=view,
            acceptance=JobAcceptance(job=view.job, run=record.current_run) if created else None,
        )


class SnapshotBoundExecution:
    def __init__(
        self,
        execution: ReviewExecution,
        documents: RuntimeDocuments,
        submissions: SubmissionLookup,
        principals: CurrentPrincipalLookup,
    ) -> None:
        self.execution, self.documents = execution, documents
        self.submissions, self.principals = submissions, principals

    async def _require(self, record: JobRecord) -> None:
        principal = await self.principals.read(record.principal_id, record.case_id)
        if principal.actor.actor_id != record.principal_id:
            raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
        principal.require(record.case_id, Permission.REVIEW)
        submission = await self.submissions.read_submission(run_id=record.current_run.run_id)
        if submission is None or submission.revision != record.current_run.revision:
            raise ServiceFault(ServiceErrorCode.VALIDATION)
        try:
            for reference in submission.documents:
                await asyncio.to_thread(
                    self.documents.read_snapshot, principal, record.current_run, reference
                )
        except DocumentFault as error:
            raise source_fault(error) from None

    async def execute(self, record: JobRecord, attempt: ClaimedAttempt) -> ExecutedReview:
        await self._require(record)
        with dispatch_async_authority(lambda: self._require(record)):
            result = await self.execution.execute(record, attempt)
        await self._require(record)
        return result
