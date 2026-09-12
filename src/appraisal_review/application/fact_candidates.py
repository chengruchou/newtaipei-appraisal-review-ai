"""Externally fetched fact candidates and named-human confirmation receipts.

Every value fetched from an official open-data source enters the system as a
FactCandidate only. A candidate never becomes an adopted fact here: a named
human holding the confirmation permission must first accept the exact fetched
value, unit, applicable date and evidence digest, producing a durable
CandidateConfirmation receipt in the same SQLite transaction that flips the
candidate's status. Even then the value is only "confirmed, awaiting adoption":
writing confirmed values into review snapshots or the three official tables is
deliberately outside this module (the calculation-engine path owns adoption),
and confirmed_unadopted exists precisely so the frontend can say so honestly.

Integration contract (the composition root wires all of it):
- ``store``: any :class:`CandidateStore`;
  ``appraisal_review.adapters.local.candidate_store.SQLiteCandidateStore`` is
  the provided SQLite implementation (it takes the shared ReviewDatabase).
- the HTTP plane lives at ``app.state.fact_candidates`` (see
  api/routes/fact_candidates.py).
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal, Protocol
from uuid import uuid4

from pydantic import Field, model_validator

from appraisal_review.application.service_guards import Principal, ServiceFault
from appraisal_review.domain.document_models import Digest
from appraisal_review.domain.review_contracts import content_digest
from appraisal_review.domain.service_contracts import (
    ActorReference,
    OpaqueID,
    Permission,
    ServiceErrorCode,
    ServiceModel,
)

CandidateStatus = Literal["candidate", "confirmed", "rejected", "superseded"]
ConfirmationDecision = Literal["accept", "reject"]

#: Mean Earth radius in meters (IUGG, consistent with the WGS84 ellipsoid axes),
#: used by the spherical haversine formula below.
_EARTH_RADIUS_M = 6_371_008.8


class CandidateEvidence(ServiceModel):
    """Exact provenance of one candidate value; mirrors the adapter's FetchEvidence."""

    url: str = Field(min_length=1, max_length=1000)
    sha256: Digest
    retrieved_at: int = Field(ge=0, strict=True)
    excerpt: str = Field(default="", max_length=1024)
    row_locator: str | None = Field(default=None, min_length=1, max_length=200)


class FactCandidate(ServiceModel):
    """One externally fetched value, waiting for a named human decision."""

    candidate_id: OpaqueID
    case_id: OpaqueID
    revision_id: OpaqueID
    subject_id: str = Field(min_length=1, max_length=100)
    field_key: str = Field(min_length=1, max_length=200)
    value: str = Field(max_length=1000)
    unit: str | None = Field(default=None, min_length=1, max_length=50)
    applicable_date: str | None = Field(default=None, min_length=1, max_length=64)
    source_id: OpaqueID
    evidence: CandidateEvidence
    status: CandidateStatus = "candidate"
    created_by: ActorReference
    created_at: int = Field(ge=0, strict=True)


class RegisterCandidateCommand(ServiceModel):
    """Untrusted body; the server mints the identifier and stamps actor and time."""

    idempotency_key: OpaqueID
    revision_id: OpaqueID
    subject_id: str = Field(min_length=1, max_length=100)
    field_key: str = Field(min_length=1, max_length=200)
    value: str = Field(max_length=1000)
    unit: str | None = Field(default=None, min_length=1, max_length=50)
    applicable_date: str | None = Field(default=None, min_length=1, max_length=64)
    source_id: OpaqueID
    evidence: CandidateEvidence

    def payload_digest(self) -> str:
        return content_digest(self)


class ConfirmCandidateCommand(ServiceModel):
    """Untrusted decision body; the accepted fields must echo the candidate exactly."""

    idempotency_key: OpaqueID
    decision: ConfirmationDecision
    reason: str | None = Field(default=None, min_length=1, max_length=1000)
    expected_revision: OpaqueID
    accepted_value: str = Field(max_length=1000)
    accepted_unit: str | None = Field(default=None, min_length=1, max_length=50)
    accepted_applicable_date: str | None = Field(default=None, min_length=1, max_length=64)
    evidence_sha256: Digest

    @model_validator(mode="after")
    def rejection_reason(self) -> ConfirmCandidateCommand:
        if self.decision == "reject" and self.reason is None:
            raise ValueError("A rejection requires a reason")
        return self

    def payload_digest(self) -> str:
        return content_digest(self)


class CandidateConfirmation(ServiceModel):
    """Durable receipt of one named-human decision on one exact candidate value."""

    receipt_id: OpaqueID
    candidate_id: OpaqueID
    case_id: OpaqueID
    subject_id: str = Field(min_length=1, max_length=100)
    field_key: str = Field(min_length=1, max_length=200)
    expected_revision: OpaqueID
    accepted_value: str = Field(max_length=1000)
    accepted_unit: str | None = Field(default=None, min_length=1, max_length=50)
    accepted_applicable_date: str | None = Field(default=None, min_length=1, max_length=64)
    evidence_sha256: Digest
    decision: ConfirmationDecision
    reason: str | None = Field(default=None, min_length=1, max_length=1000)
    actor: ActorReference
    decided_at: int = Field(ge=0, strict=True)
    idempotency_key: OpaqueID

    @model_validator(mode="after")
    def named_human_decision(self) -> CandidateConfirmation:
        if self.actor.kind != "human":
            raise ValueError("A confirmation receipt requires a named human actor")
        if self.decision == "reject" and self.reason is None:
            raise ValueError("A rejection receipt requires a reason")
        return self


class CandidateList(ServiceModel):
    case_id: OpaqueID
    candidates: tuple[FactCandidate, ...] = ()


class CandidateStore(Protocol):
    """Durable candidates and receipts with exact idempotent replay.

    ``register`` returns ``(stored, created)``: a replay of a known
    ``(case, actor, idempotency_key)`` with the same payload digest returns the
    original candidate with ``created=False``; the same key with a different
    digest raises ``ServiceFault(CONFLICT)``. ``confirm`` applies the status
    flip and the receipt insert in ONE transaction, conditionally on the stored
    status still being ``candidate``; its replay mapping follows the same rules
    and returns the original receipt.
    """

    def register(
        self, candidate: FactCandidate, *, actor_id: str, idempotency_key: str, payload_digest: str
    ) -> tuple[FactCandidate, bool]: ...

    def read(self, case_id: str, candidate_id: str) -> FactCandidate | None: ...

    def list_candidates(self, case_id: str) -> tuple[FactCandidate, ...]: ...

    def confirmed_unadopted(self, case_id: str) -> tuple[FactCandidate, ...]: ...

    def confirm(
        self,
        updated: FactCandidate,
        receipt: CandidateConfirmation,
        *,
        actor_id: str,
        idempotency_key: str,
        payload_digest: str,
    ) -> tuple[CandidateConfirmation, bool]: ...


class CandidateService:
    def __init__(
        self,
        *,
        store: CandidateStore,
        clock: Callable[[], int] = lambda: int(time.time()),
        new_id: Callable[[], str] = lambda: str(uuid4()),
    ) -> None:
        self.store = store
        self.clock = clock
        self.new_id = new_id

    async def register_candidate(
        self, principal: Principal, case_id: str, command: RegisterCandidateCommand
    ) -> FactCandidate:
        command = RegisterCandidateCommand.model_validate_json(command.model_dump_json())
        # Registration only requires case membership with the baseline REVIEW
        # permission: a model or system fetcher may PROPOSE a candidate. What it
        # can never do is confirm one - that path demands a named human below.
        principal.require(case_id, Permission.REVIEW)
        candidate = FactCandidate(
            candidate_id=self.new_id(),
            case_id=case_id,
            revision_id=command.revision_id,
            subject_id=command.subject_id,
            field_key=command.field_key,
            value=command.value,
            unit=command.unit,
            applicable_date=command.applicable_date,
            source_id=command.source_id,
            evidence=command.evidence,
            status="candidate",
            created_by=principal.actor,
            created_at=self.clock(),
        )
        stored, _created = self.store.register(
            candidate,
            actor_id=principal.actor.actor_id,
            idempotency_key=command.idempotency_key,
            payload_digest=command.payload_digest(),
        )
        return stored

    async def list_candidates(self, principal: Principal, case_id: str) -> CandidateList:
        principal.require(case_id, Permission.REVIEW)
        return CandidateList(case_id=case_id, candidates=self.store.list_candidates(case_id))

    async def confirmed_unadopted(self, principal: Principal, case_id: str) -> CandidateList:
        """Confirmed values that NO snapshot or table has adopted.

        Everything this component confirms stays unadopted by design; the list
        exists so the frontend can label these values "confirmed, awaiting
        adoption" instead of pretending they already sit in a table.
        """
        principal.require(case_id, Permission.REVIEW)
        return CandidateList(case_id=case_id, candidates=self.store.confirmed_unadopted(case_id))

    async def confirm(
        self,
        principal: Principal,
        case_id: str,
        candidate_id: str,
        command: ConfirmCandidateCommand,
    ) -> CandidateConfirmation:
        command = ConfirmCandidateCommand.model_validate_json(command.model_dump_json())
        # Permission.CONFIRM is the same authority human fact-confirmation tasks
        # gate with; REVIEW alone must never accept an external value.
        principal.require(case_id, Permission.CONFIRM)
        if principal.actor.kind != "human":
            raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
        candidate = self.store.read(case_id, candidate_id)
        if candidate is None:
            raise ServiceFault(ServiceErrorCode.NOT_FOUND)
        if command.expected_revision != candidate.revision_id:
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        echoed = (command.accepted_value, command.accepted_unit, command.accepted_applicable_date)
        if echoed != (candidate.value, candidate.unit, candidate.applicable_date):
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        if command.evidence_sha256 != candidate.evidence.sha256:
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        status: CandidateStatus = "confirmed" if command.decision == "accept" else "rejected"
        updated = FactCandidate.model_validate(
            candidate.model_dump(mode="json") | {"status": status}
        )
        receipt = CandidateConfirmation(
            receipt_id=self.new_id(),
            candidate_id=candidate.candidate_id,
            case_id=candidate.case_id,
            subject_id=candidate.subject_id,
            field_key=candidate.field_key,
            expected_revision=command.expected_revision,
            accepted_value=command.accepted_value,
            accepted_unit=command.accepted_unit,
            accepted_applicable_date=command.accepted_applicable_date,
            evidence_sha256=command.evidence_sha256,
            decision=command.decision,
            reason=command.reason,
            actor=principal.actor,
            decided_at=self.clock(),
            idempotency_key=command.idempotency_key,
        )
        stored, _created = self.store.confirm(
            updated,
            receipt,
            actor_id=principal.actor.actor_id,
            idempotency_key=command.idempotency_key,
            payload_digest=command.payload_digest(),
        )
        return stored


def distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> int:
    """Great-circle STRAIGHT-LINE distance in whole meters (haversine).

    Spherical haversine on the WGS84 mean Earth radius, rounded half-up to an
    integer meter via Decimal. This is a straight-line (as the crow flies)
    distance only: it says nothing about road-network or walking distance, so
    any criterion defined over travel distance must not be judged with it.
    """
    for latitude in (lat1, lat2):
        if not isinstance(latitude, (int, float)) or not -90.0 <= float(latitude) <= 90.0:
            raise ValueError("Latitude must be a number between -90 and 90")
    for longitude in (lng1, lng2):
        if not isinstance(longitude, (int, float)) or not -180.0 <= float(longitude) <= 180.0:
            raise ValueError("Longitude must be a number between -180 and 180")
    phi1, phi2 = math.radians(float(lat1)), math.radians(float(lat2))
    delta_phi = math.radians(float(lat2) - float(lat1))
    delta_lambda = math.radians(float(lng2) - float(lng1))
    half_chord = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    arc = 2 * math.asin(min(1.0, math.sqrt(half_chord)))
    meters = Decimal(repr(_EARTH_RADIUS_M * arc)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(meters)
