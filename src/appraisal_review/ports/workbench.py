"""Current source authorization for additive workbench read projections."""

from typing import Protocol

from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.application.service_guards import Principal
from appraisal_review.domain.service_contracts import RevisionReference


class WorkbenchReadAccess(Protocol):
    async def read(self, principal: Principal, reference: RevisionReference) -> RevisionSnapshot:
        """Resolve exact material and recheck current actor, case and document grants.

        Revalidate document purpose, immutable version and content before exposing
        observations. Refresh principal authority after asynchronous source I/O.
        Absence is a failure, not an empty case or a source-preparation assertion.
        """
        ...

    async def require_current(
        self, principal: Principal, references: tuple[RevisionReference, ...]
    ) -> None:
        """Refresh actor and all source-purpose grants after the final store await.

        This authorizes metadata/receipt reads without requiring PDF availability.
        It never grants access solely from case membership or a caller reference.
        """
        ...
