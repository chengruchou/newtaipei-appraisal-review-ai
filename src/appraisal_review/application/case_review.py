"""What a case member needs in order to start a review, read from the server.

A review submission must pin an exact admitted material revision and its exact
document references. The browser cannot invent either, so this read reports what
the server would accept for a case - and, when nothing is admitted yet, says so
in words a reviewer can act on instead of leaving a dead button.

This plane grants nothing. It answers only for cases the principal already holds
REVIEW on, it never returns document content, and the submission it describes is
re-validated in full by the job service: membership, admitted material, document
resolution and snapshot creation all run again there.
"""

from __future__ import annotations

from typing import Literal, Protocol
from uuid import UUID

from appraisal_review.application.service_guards import Principal, ServiceFault
from appraisal_review.domain.service_contracts import (
    DocumentReference,
    MaterialRevision,
    OpaqueID,
    Permission,
    RevisionReference,
    ServiceModel,
)
from appraisal_review.ports.jobs import JobRecord

#: Why a case cannot start a review yet. Stable codes; the sentence is for people.
BasisState = Literal["ready", "no_material"]

NO_MATERIAL_REASON = (
    "This case has no admitted material revision yet. Uploaded files are stored and "
    "hashed, but a review runs on a controlled document batch, which an operator "
    "admits separately."
)


class CaseReviewBasis(ServiceModel):
    """The exact submission ingredients for one case, or an honest refusal."""

    case_id: OpaqueID
    state: BasisState
    #: Present only when state is "no_material"; never a stack trace or a path.
    reason: str | None = None
    revision: RevisionReference | None = None
    documents: tuple[DocumentReference, ...] = ()
    #: A review this principal already has on the case. Present means "open that one":
    #: one case carries one review, so a second is never opened alongside the first.
    #: Two concurrent reviews on one case strand each other - the older one's revision
    #: is superseded the moment the newer one adopts a fact - so the caller opens this
    #: instead of submitting again.
    existing_job_id: UUID | None = None


class ReviewableCaseList(ServiceModel):
    """Every case this principal could start or continue a review on."""

    cases: tuple[CaseReviewBasis, ...] = ()


class PreparedMaterialReader(Protocol):
    """Admitted material lookup by case; None when nothing is admitted."""

    def latest_for_case(self, case_id: str) -> MaterialRevision | None: ...


class CaseJobReader(Protocol):
    """This case's jobs from durable state, most presentable first."""

    async def jobs_for_case(self, *, case_id: str, principal_id: str) -> tuple[JobRecord, ...]: ...


class JobStatusReader(Protocol):
    """The live service read a job page will actually perform."""

    async def status(self, principal: Principal, job_id: UUID) -> object: ...


class CaseReviewService:
    def __init__(
        self,
        *,
        materials: PreparedMaterialReader,
        jobs: CaseJobReader | None = None,
        status: JobStatusReader | None = None,
    ) -> None:
        self.materials = materials
        self.jobs = jobs
        # The durable store outlives what a given boot can serve: a reseeded demo
        # leaves completed jobs whose materials are no longer registered this boot,
        # and those answer not-found despite being listed. With a status reader
        # wired, every candidate is probed with the same read the job page performs,
        # and the first that actually answers is the one named.
        self.status = status

    async def basis(self, principal: Principal, case_id: str) -> CaseReviewBasis:
        # Membership plus REVIEW is the same gate every other case read uses; a
        # non-member cannot learn whether the case exists, let alone its material.
        principal.require(case_id, Permission.REVIEW)
        return await self._with_existing(principal, self._basis(case_id))

    async def reviewable(self, principal: Principal) -> ReviewableCaseList:
        """Only the principal's own memberships are considered, never a global scan."""
        found = []
        for case_id in sorted(principal.case_ids):
            if Permission.REVIEW not in principal.permissions:
                break
            basis = self._basis(case_id)
            if basis.state == "ready":
                found.append(await self._with_existing(principal, basis))
        return ReviewableCaseList(cases=tuple(found))

    async def _with_existing(self, principal: Principal, basis: CaseReviewBasis) -> CaseReviewBasis:
        """Name the review this case already has, if any; a lookup failure just omits it."""
        if self.jobs is None or basis.state != "ready":
            return basis
        records = await self.jobs.jobs_for_case(
            case_id=basis.case_id, principal_id=principal.actor.actor_id
        )
        for record in records:
            if self.status is not None:
                try:
                    await self.status.status(principal, record.job_id)
                except ServiceFault:
                    continue
            return basis.model_copy(update={"existing_job_id": record.job_id})
        return basis

    def _basis(self, case_id: str) -> CaseReviewBasis:
        material = self.materials.latest_for_case(case_id)
        if material is None:
            return CaseReviewBasis(case_id=case_id, state="no_material", reason=NO_MATERIAL_REASON)
        return CaseReviewBasis(
            case_id=case_id,
            state="ready",
            revision=material.reference,
            documents=tuple(material.documents),
        )
