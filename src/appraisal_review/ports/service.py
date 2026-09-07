"""Reserved B/D service ports. M0 supplies no production persistence implementation."""

from dataclasses import dataclass
from typing import Protocol

from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.application.service_guards import Principal
from appraisal_review.domain.service_contracts import (
    AcceptedResponse,
    DocumentReference,
    HumanTask,
    MaterialRevision,
    ReviewSubmission,
    RevisionReference,
    RunReference,
    ServiceResult,
)


@dataclass(frozen=True)
class ResolvedDocument:
    """Internal resolver output; the URI must not enter external service responses."""

    reference: DocumentReference
    storage_uri: str


class PrincipalResolver(Protocol):
    async def current_principal(self) -> Principal:
        """Authenticate adapter-bound request context; never trust body actor/roles."""
        ...


class DocumentResolver(Protocol):
    async def resolve(self, principal: Principal, reference: DocumentReference) -> ResolvedDocument:
        """Authorize case/document/version before storage access; missing -> not_found."""
        ...


class RevisionRepository(Protocol):
    async def create(
        self,
        principal: Principal,
        snapshot: RevisionSnapshot,
        *,
        expected_parent: RevisionReference | None,
    ) -> MaterialRevision:
        """Append via CAS; None means create only if absent; stale -> version_conflict."""
        ...


class HumanTaskRepository(Protocol):
    async def accept(
        self,
        principal: Principal,
        response: AcceptedResponse,
        *,
        task: HumanTask,
        next_revision: RevisionSnapshot,
    ) -> MaterialRevision:
        """Atomically recheck permission/version, consume key and append revision/outbox.

        Exact replay is idempotent; changed payload conflicts. Release old attempts.
        No old confirmation or authorization can migrate to the new exact revision.
        """
        ...


class JobRepository(Protocol):
    async def submit(self, principal: Principal, request: ReviewSubmission) -> RunReference:
        """Atomically persist principal/key/digest, fixed run input and dispatch outbox."""
        ...

    async def publish(
        self,
        principal: Principal,
        result: ServiceResult,
        *,
        fencing_token: int,
        expected_result_version: int,
    ) -> None:
        """Require publication authority and current attempt/lease/token/version via CAS.

        Persist only verified manifest references; old attempts cannot publish.
        Store implementations must provide cross-process transactions/recovery.
        M0's pure guards provide none of these durability guarantees.
        """
        ...
