"""Reserved extraction ports. No production resolver, client or task store is supplied."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Protocol

from appraisal_review.application.service_guards import Principal
from appraisal_review.domain.document_models import SourceDocument, SourceRegistry
from appraisal_review.domain.extraction_contracts import (
    FailureCode,
    HandoffRequest,
    PageOutcome,
    PageRequest,
    ProviderTelemetry,
    SanitizedSourceReference,
    proposal_citations,
)


class ExtractionBoundaryError(Exception):
    """Safe boundary error; no raw dependency message or exception chaining."""

    def __init__(self, code: FailureCode) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class AuthorizedSanitizedSnapshot:
    """Internal trusted resolver output, never deserialize from a public request.

    Construction checks integrity only. The resolver must authenticate the
    principal, verify #22 provenance and #27 ownership/version, and ensure every
    byte and parser region comes from the same sanitized source before returning.
    No constructor, hash or manifest digest provides that authority by itself.
    """

    reference: SanitizedSourceReference
    content: bytes = field(repr=False)
    source_json: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.content, bytes) or not isinstance(self.source_json, bytes):
            raise ValueError("Snapshot storage must use immutable bytes")
        reference = SanitizedSourceReference.model_validate(self.reference)
        source = self.source
        document = reference.document
        if hashlib.sha256(self.content).hexdigest() != document.content_hash:
            raise ValueError("Snapshot byte digest mismatch")
        if (
            source.document_id,
            source.version,
            source.content_hash,
            source.role,
            len(source.pages),
        ) != (
            document.document_id,
            document.version,
            document.content_hash,
            document.purpose,
            reference.page_count,
        ):
            raise ValueError("Snapshot parser identity mismatch")

    @property
    def source(self) -> SourceDocument:
        """Detached parser data; mutating it cannot change the snapshot."""
        return SourceDocument.model_validate_json(self.source_json)

    def validate_request(self, request: PageRequest) -> SourceDocument:
        request = PageRequest.model_validate(request)
        self.__post_init__()
        if request.source != self.reference:
            raise ValueError("Request differs from resolved snapshot")
        return self.source

    def validate_outcome(self, outcome: PageOutcome) -> PageOutcome:
        """Recheck canonical geometry at use, without promoting candidate authority."""
        outcome = PageOutcome.model_validate(outcome)
        source = self.validate_request(outcome.request)
        registry = SourceRegistry(documents=[source])
        if any(not registry.resolves(ref) for ref in proposal_citations(outcome.proposal)):
            raise ValueError("Outcome citation does not resolve in snapshot")
        regions = {r.id for r in source.pages[outcome.request.page - 1].regions}
        if any(
            location.region_id is not None and location.region_id not in regions
            for handoff in outcome.handoffs
            for location in handoff.locations
        ):
            raise ValueError("Handoff region does not resolve in snapshot")
        return outcome


class SanitizedSnapshotResolver(Protocol):
    async def resolve(
        self, principal: Principal, request: PageRequest
    ) -> AuthorizedSanitizedSnapshot:
        """Authorize before storage access; absent privacy integration fails closed.

        Use #22's accepted manifest and #27's immutable byte resolver. Never read
        re-identification maps. A caller-selected URI or sanitized flag is invalid.
        """
        ...


class PageExtractionProvider(Protocol):
    async def extract(
        self, request: PageRequest, snapshot: AuthorizedSanitizedSnapshot
    ) -> PageOutcome:
        """Use the trusted resolver output and preserve canonical proposal checks."""
        ...


class ExtractionTelemetrySink(Protocol):
    async def record(self, request: PageRequest, telemetry: ProviderTelemetry) -> None:
        """Record safe metadata only; never proposals, source JSON or SDK responses."""
        ...


class ExtractionHandoffSink(Protocol):
    async def request_review(self, request: HandoffRequest) -> None:
        """Adapt to #17/#24; a requested handoff is not an accepted human response."""
        ...
