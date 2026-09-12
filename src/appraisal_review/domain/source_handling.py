"""Recorded per-batch source handling scope; a decision record, never a grant.

A batch of standard formula/criteria forms that a professional has cleared needs no
local privacy processing. That exemption belongs in source configuration as an
explicit record naming the deciding authority, so the scope is auditable long after
the batch is processed.

Three properties matter and are enforced here rather than left to convention:

- An exemption is not a privacy receipt. ``not_required`` states that the pipeline was
  never asked to sanitize these bytes. It never claims a scan ran, passed, or produced
  a manifest, and nothing here can be presented as :mod:`privacy_models` evidence.
- An exemption is not an end-user step. It is supplied with the batch configuration by
  the operator who owns the sources, not collected from a reviewer at upload time.
- An exemption never spreads. It binds exact document identifiers, and the absence of a
  record means processing is still required, so an unrelated later batch cannot inherit
  another batch's scope.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import Field, model_validator

from appraisal_review.domain.document_models import DocumentModel

PrivacyProcessing = Literal["required", "not_required"]


class SourceHandlingScope(DocumentModel):
    """Why a named authority placed these exact documents outside privacy processing."""

    schema_version: Literal["source-handling-v1"] = "source-handling-v1"
    privacy_processing: PrivacyProcessing
    document_ids: tuple[str, ...] = ()
    decided_by: str = ""
    decided_on: date | None = None
    rationale: str = ""

    @model_validator(mode="after")
    def recorded_decision(self) -> SourceHandlingScope:
        if self.privacy_processing == "not_required":
            # An unattributed, undated or unexplained exemption is indistinguishable from
            # someone quietly switching the pipeline off, so refuse to represent one.
            if not self.document_ids:
                raise ValueError("An exemption must name the exact documents it covers")
            if len(set(self.document_ids)) != len(self.document_ids):
                raise ValueError("Exempted document identifiers must be unique")
            if not self.decided_by.strip():
                raise ValueError("An exemption must record the deciding authority")
            if self.decided_on is None:
                raise ValueError("An exemption must record the date it was decided")
            if not self.rationale.strip():
                raise ValueError("An exemption must record why processing is not required")
        elif self.document_ids or self.decided_by.strip() or self.rationale.strip():
            raise ValueError("Required processing carries no exemption attribution")
        return self

    def exempts(self, document_id: str) -> bool:
        """True only for a document this record explicitly covers."""
        return self.privacy_processing == "not_required" and document_id in self.document_ids


class BatchHandling(DocumentModel):
    """Batch-level default plus the per-document exemptions recorded against it."""

    schema_version: Literal["batch-handling-v1"] = "batch-handling-v1"
    scopes: tuple[SourceHandlingScope, ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def single_decision_per_document(self) -> BatchHandling:
        covered: set[str] = set()
        for scope in self.scopes:
            overlap = covered & set(scope.document_ids)
            if overlap:
                raise ValueError(f"Conflicting handling records for {sorted(overlap)}")
            covered |= set(scope.document_ids)
        return self

    def covered_document_ids(self) -> frozenset[str]:
        return frozenset(d for scope in self.scopes for d in scope.document_ids)

    def privacy_required(self, document_id: str) -> bool:
        """Default to required, so an unrecorded document is never treated as exempt."""
        return not any(scope.exempts(document_id) for scope in self.scopes)
