"""Export operations port: submit one format request, read its progress.

The transport stays thin because everything meaningful about an export is decided
elsewhere: the domain contract (official_export) fixes what an operation may claim,
the executor decides what actually gets produced, and the store makes the operation
durable. An unwired composition answers capability_unavailable at the route, so this
port has no "maybe available" state of its own.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from appraisal_review.application.service_guards import Principal
from appraisal_review.domain.official_export import ExportOperation, ExportRequest


class ExportOperations(Protocol):
    async def submit(
        self, principal: Principal, job_id: UUID, request: ExportRequest
    ) -> ExportOperation:
        """Accept or replay one export request; a changed payload under a used key conflicts."""
        ...

    async def read(self, principal: Principal, job_id: UUID, export_id: UUID) -> ExportOperation:
        """Current state of one operation, authorized against the current principal."""
        ...
