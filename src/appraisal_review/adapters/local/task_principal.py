"""Explicit single-operator POSIX principal adapter; no identity from request bodies."""

from dataclasses import dataclass

from appraisal_review.adapters.local.approval import Reviewer, current_reviewer
from appraisal_review.application.service_guards import Principal, ServiceFault
from appraisal_review.domain.service_contracts import ActorReference, Permission, ServiceErrorCode


@dataclass(frozen=True)
class LocalReviewerPrincipalResolver:
    reviewer: Reviewer
    case_ids: frozenset[str]
    permissions: frozenset[Permission]

    async def current_principal(self) -> Principal:
        actual = current_reviewer()
        if actual != self.reviewer:
            raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
        return Principal(
            actor=ActorReference(actor_id=f"{actual.uid}:{actual.name}", kind="human"),
            case_ids=self.case_ids,
            permissions=self.permissions,
        )
