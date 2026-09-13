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

from appraisal_review.application.service_guards import Principal
from appraisal_review.domain.service_contracts import (
    DocumentReference,
    MaterialRevision,
    OpaqueID,
    Permission,
    RevisionReference,
    ServiceModel,
)

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


class ReviewableCaseList(ServiceModel):
    """Every case this principal could start or continue a review on."""

    cases: tuple[CaseReviewBasis, ...] = ()


class PreparedMaterialReader(Protocol):
    """Admitted material lookup by case; None when nothing is admitted."""

    def latest_for_case(self, case_id: str) -> MaterialRevision | None: ...


class CaseReviewService:
    def __init__(self, *, materials: PreparedMaterialReader) -> None:
        self.materials = materials

    async def basis(self, principal: Principal, case_id: str) -> CaseReviewBasis:
        # Membership plus REVIEW is the same gate every other case read uses; a
        # non-member cannot learn whether the case exists, let alone its material.
        principal.require(case_id, Permission.REVIEW)
        return self._basis(case_id)

    async def reviewable(self, principal: Principal) -> ReviewableCaseList:
        """Only the principal's own memberships are considered, never a global scan."""
        found = []
        for case_id in sorted(principal.case_ids):
            if Permission.REVIEW not in principal.permissions:
                break
            basis = self._basis(case_id)
            if basis.state == "ready":
                found.append(basis)
        return ReviewableCaseList(cases=tuple(found))

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
