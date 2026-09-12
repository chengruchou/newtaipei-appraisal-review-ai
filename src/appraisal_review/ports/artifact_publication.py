"""Internal publication authority; constructed by the trusted runtime composition."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from appraisal_review.application.service_guards import Principal
from appraisal_review.domain.artifact_publication import (
    CommittedManifest,
    ManifestCandidate,
    PublishedArtifact,
)
from appraisal_review.domain.service_contracts import Permission


@dataclass(frozen=True)
class PublicationAttempt:
    """Map from ClaimedAttempt; claims are checked against current persistent rows.

    This is not an authorization token. The publisher must also supply a trusted
    principal and a current, independently approved exact manifest grant.
    """

    job_id: UUID
    owner: UUID
    expected_result_version: int


class ManifestRepository(Protocol):
    """Atomic publication and current access checks, independent of SDK types."""

    clock: Callable[[], float]

    def read(self, case_id: str, run_id: UUID) -> CommittedManifest | None: ...

    def authorize(self, principal: Principal, case_id: str, permission: Permission) -> int:
        """Reauthorize now; return the access grant's expiry in UTC epoch seconds."""
        ...

    def commit(
        self,
        candidate: ManifestCandidate,
        *,
        fencing_token: int,
        principal: Principal,
        attempt: PublicationAttempt,
    ) -> CommittedManifest:
        """Atomically require current job/attempt/lease/fence/version/approval."""
        ...


class ArtifactObjectStore(Protocol):
    """Immutable objects; a create retry must compare bytes, never replace content."""

    def create(self, key: str, data: bytes) -> str:
        """Create atomically and return a concrete opaque version; exact replay returns it."""
        ...

    def read(self, artifact: PublishedArtifact) -> bytes:
        """Read only the named immutable version, with a bounded size and bounded retries."""
        ...
