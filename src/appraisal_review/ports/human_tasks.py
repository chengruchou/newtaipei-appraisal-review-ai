"""Human-task store primitives. One coarse transaction, because the effects are inseparable.

Unlike ports.jobs, this port is deliberately *not* fine-grained. Answering a task marks
the task answered, appends a new immutable revision, supersedes the sibling tasks bound to
the old revision, consumes the idempotency key and schedules the follow-up run. A store
that could apply four of those five would let a reviewer's answer produce a revision that
nothing is scheduled to review, or a scheduled run for a revision that was never stored.

So `commit_response` is one method and one conditional write. A DynamoDB adapter implements
it as a single TransactWriteItems; the in-memory adapter implements it under one lock and
rolls back if the job plane refuses. Everything the transaction must recheck is passed in,
because a store that trusts the caller's earlier read cannot make the recheck conditional.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.domain.document_models import Digest
from appraisal_review.domain.service_contracts import (
    AcceptedResponse,
    HumanTask,
    MaterialRevision,
    RevisionReference,
)
from appraisal_review.domain.task_contracts import ResponseReceipt


@dataclass(frozen=True)
class TaskRecord:
    """A stored task plus the job that owns it. Authorization is the caller's decision.

    The store never decides who may see a task: it has no principal context, and a store
    that silently filtered rows would make an authorization bug invisible to its tests.
    """

    task: HumanTask
    job_id: UUID
    case_id: str
    principal_id: str
    # The case's head revision, which is not the task's own revision once someone else has
    # answered. Admission compares the two, so a stale task cannot overwrite newer state.
    current_revision: RevisionReference


@dataclass(frozen=True)
class StoredReceipt:
    """A consumed idempotency key and the exact body its first use committed."""

    principal_id: str
    key: str
    payload_digest: Digest
    receipt: ResponseReceipt


class HumanTaskStore(Protocol):
    async def read_task(self, *, task_id: UUID) -> TaskRecord | None:
        """Strongly consistent read of one task, or None when no such task exists."""
        ...

    async def list_tasks(self, *, job_id: UUID) -> tuple[TaskRecord, ...]:
        """Every task of one job, oldest first, including answered and superseded ones."""
        ...

    async def list_revisions(self, *, job_id: UUID) -> tuple[MaterialRevision, ...]:
        """The job's revision chain, oldest first and parent-linked."""
        ...

    async def read_snapshot(self, *, revision: RevisionReference) -> RevisionSnapshot | None:
        """The exact stored material for one revision, for deriving the next one."""
        ...

    async def read_receipt(self, *, principal_id: str, key: str) -> StoredReceipt | None:
        """Fast replay path only. The authoritative key check is inside commit_response.

        Reading here first keeps an exact retry from failing admission on the task version
        its own first attempt already consumed. It is a cache, never the decision.
        """
        ...

    async def commit_response(
        self,
        accepted: AcceptedResponse,
        *,
        task: HumanTask,
        next_revision: RevisionSnapshot | None,
        payload_digest: Digest,
        now: int,
    ) -> ResponseReceipt:
        """Apply the whole answer atomically, rechecking every condition inside the write.

        Rechecks that the task is still open at `accepted.command.expected_version`, that
        the task still hangs off its stored revision, that the actor still matches the
        principal that was authorized, and that the key is unused or was used for this
        exact payload. Same principal, key and payload replays the stored receipt; a
        changed payload raises ServiceFault(CONFLICT).

        `next_revision` is None only for a rejection, which records an answer and commits
        no material. Otherwise the new revision is appended under compare-and-swap on its
        stored parent, and the follow-up run is scheduled in the same write.

        Raises ConditionFailed when another writer moved first, which is never a retry
        signal: the caller re-reads and decides again.
        """
        ...
