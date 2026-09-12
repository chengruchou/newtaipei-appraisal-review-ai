"""Case intake: create a real case, receive its source materials, list them.

This is the smallest honest entry path for a NEW case. It mints a durable case
record, grants the calling principal membership through an injected callback, and
stores uploaded bytes with their exact sha256 so a client can verify what the
server holds. It deliberately touches nothing else: no synthetic grants beyond the
caller's own membership, no fixture publication authority, no demo snapshot
binding, and no fabricated job or parse state. A fresh case's processing status is
simply that its materials exist; review jobs stay on the existing
POST /v1/review-jobs path.

Integration contract (the composition root wires all of it):
- ``store``: any :class:`CaseIntakeStore`;
  ``appraisal_review.adapters.local.case_intake_store.SQLiteCaseIntakeStore`` is
  the provided SQLite implementation (it takes the shared ReviewDatabase plus a
  0700 workbench root ``Path`` for material bytes).
- ``grant``: called as ``grant(actor_id, case_id)`` after the case record is
  durable; ``LocalDirectory.grant_case`` satisfies it directly. It must be
  idempotent - an exact idempotent replay of the create command invokes it again
  so an in-memory directory reconverges after a restart.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from datetime import date
from typing import Protocol
from uuid import uuid4

from pydantic import Field, field_validator

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

#: Matches the deployment's nginx client_max_body_size; the service refuses what
#: the proxy would have refused, instead of silently depending on the proxy.
MAX_MATERIAL_BYTES = 67_108_864

_MEDIA_TYPE_PATTERN = (
    r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,126}/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,126}$"
)


class CreateCaseCommand(ServiceModel):
    """Untrusted body; the server mints the identifier and records the actor."""

    idempotency_key: OpaqueID
    title: str = Field(min_length=1, max_length=200)
    district: str = Field(min_length=1, max_length=100)
    valuation_date: date | None = None

    def payload_digest(self) -> str:
        return content_digest(self)


class CaseRecord(ServiceModel):
    """Durable server-authored record of one real intake case."""

    case_id: OpaqueID
    title: str = Field(min_length=1, max_length=200)
    district: str = Field(min_length=1, max_length=100)
    valuation_date: date | None = None
    created_by: ActorReference
    created_at: int = Field(ge=0, strict=True)


def _plain_filename(value: str) -> str:
    # The name is display metadata only - bytes are stored under the server-minted
    # material_id - but a name that looks like a path invites a traversal bug in
    # any later consumer, so it is refused at the boundary.
    if (
        value in {".", ".."}
        or any(separator in value for separator in ("/", "\\", "\x00"))
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError("A plain file name is required, not a path")
    return value


class MaterialRecord(ServiceModel):
    """Metadata of one stored upload; bytes live under the workbench root."""

    material_id: OpaqueID
    case_id: OpaqueID
    filename: str = Field(min_length=1, max_length=255)
    media_type: str = Field(pattern=_MEDIA_TYPE_PATTERN)
    sha256: Digest
    size: int = Field(ge=1, strict=True)
    uploaded_by: ActorReference
    uploaded_at: int = Field(ge=0, strict=True)

    plain_name = field_validator("filename")(_plain_filename)


class MaterialUpload(ServiceModel):
    """Canonical identity of one upload attempt for idempotent replay checks."""

    idempotency_key: OpaqueID
    filename: str = Field(min_length=1, max_length=255)
    media_type: str = Field(pattern=_MEDIA_TYPE_PATTERN)
    sha256: Digest
    size: int = Field(ge=1, strict=True)

    plain_name = field_validator("filename")(_plain_filename)

    def payload_digest(self) -> str:
        return content_digest(self)


class CaseMaterialList(ServiceModel):
    case_id: OpaqueID
    materials: tuple[MaterialRecord, ...] = ()


class CaseListView(ServiceModel):
    cases: tuple[CaseRecord, ...] = ()


class CaseIntakeStore(Protocol):
    """Durable records with exact idempotent replay, same rules as approval_store.

    ``create_case`` and ``add_material`` return ``(stored, created)``. A replay of a
    known ``(scope, actor, idempotency_key)`` with the same payload digest returns
    the original record with ``created=False``; the same key with a different
    digest raises ``ServiceFault(CONFLICT)``. ``add_material`` persists the bytes
    and the record together - a committed record without its bytes must be
    impossible.
    """

    def create_case(
        self, record: CaseRecord, *, actor_id: str, idempotency_key: str, payload_digest: str
    ) -> tuple[CaseRecord, bool]: ...

    def read_case(self, case_id: str) -> CaseRecord | None: ...

    def memberships(self) -> tuple[tuple[str, str], ...]:
        """(case_id, creator actor_id) pairs for boot-time re-grants and listings."""
        ...

    def add_material(
        self,
        record: MaterialRecord,
        data: bytes,
        *,
        actor_id: str,
        idempotency_key: str,
        payload_digest: str,
    ) -> tuple[MaterialRecord, bool]: ...

    def list_materials(self, case_id: str) -> tuple[MaterialRecord, ...]: ...


class CaseIntakeService:
    def __init__(
        self,
        *,
        store: CaseIntakeStore,
        grant: Callable[[str, str], object],
        clock: Callable[[], int] = lambda: int(time.time()),
        new_id: Callable[[], str] = lambda: str(uuid4()),
        max_material_bytes: int = MAX_MATERIAL_BYTES,
    ) -> None:
        if type(max_material_bytes) is not int or max_material_bytes < 1:
            raise ValueError("A positive material size limit is required")
        self.store = store
        # grant(actor_id, case_id): the directory's own membership operation.
        # LocalDirectory.grant_case already refuses model actors and non-uuid4 case
        # identifiers; this service never widens what the directory would grant.
        self.grant = grant
        self.clock = clock
        # new_id must mint canonical uuid4 strings: LocalDirectory.grant_case
        # refuses anything else, and existing case identifiers share that shape.
        self.new_id = new_id
        self.max_material_bytes = max_material_bytes

    async def create_case(self, principal: Principal, command: CreateCaseCommand) -> CaseRecord:
        command = CreateCaseCommand.model_validate_json(command.model_dump_json())
        # Case creation is a human act, like task responses and approval decisions:
        # a system or model principal cannot open a real case.
        if principal.actor.kind != "human":
            raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
        record = CaseRecord(
            case_id=self.new_id(),
            title=command.title,
            district=command.district,
            valuation_date=command.valuation_date,
            created_by=principal.actor,
            created_at=self.clock(),
        )
        stored, _created = self.store.create_case(
            record,
            actor_id=principal.actor.actor_id,
            idempotency_key=command.idempotency_key,
            payload_digest=command.payload_digest(),
        )
        # Membership follows the durable record, for the caller only. Granting again
        # on an exact replay is deliberate: the directory operation is idempotent and
        # an in-memory directory needs the re-grant after a restart.
        self.grant(principal.actor.actor_id, stored.case_id)
        return stored

    async def add_material(
        self,
        principal: Principal,
        case_id: str,
        *,
        filename: str,
        media_type: str,
        data: bytes,
        idempotency_key: str,
    ) -> MaterialRecord:
        # Permission.REVIEW is the least-privilege choice: it is the baseline
        # participation permission the material catalog and the directory read path
        # already gate case inputs with. CONFIRM/CORRECT/APPROVE_*/PUBLISH each name
        # a stronger human-task authority (confirming observations, correcting
        # values, approving rules or material, publishing artifacts) that supplying
        # intake bytes must not demand.
        principal.require(case_id, Permission.REVIEW)
        if self.store.read_case(case_id) is None:
            raise ServiceFault(ServiceErrorCode.NOT_FOUND)
        if len(data) == 0 or len(data) > self.max_material_bytes:
            raise ServiceFault(ServiceErrorCode.VALIDATION)
        try:
            upload = MaterialUpload(
                idempotency_key=idempotency_key,
                filename=filename,
                media_type=media_type,
                sha256=hashlib.sha256(data).hexdigest(),
                size=len(data),
            )
        except ValueError as error:
            # Header-carried metadata (filename, media type, key) is caller input;
            # a bad value is a sanitized validation refusal, never a 500.
            raise ServiceFault(ServiceErrorCode.VALIDATION) from error
        record = MaterialRecord(
            material_id=self.new_id(),
            case_id=case_id,
            filename=upload.filename,
            media_type=upload.media_type,
            sha256=upload.sha256,
            size=upload.size,
            uploaded_by=principal.actor,
            uploaded_at=self.clock(),
        )
        stored, _created = self.store.add_material(
            record,
            data,
            actor_id=principal.actor.actor_id,
            idempotency_key=upload.idempotency_key,
            payload_digest=upload.payload_digest(),
        )
        return stored

    async def list_materials(self, principal: Principal, case_id: str) -> CaseMaterialList:
        principal.require(case_id, Permission.REVIEW)
        if self.store.read_case(case_id) is None:
            raise ServiceFault(ServiceErrorCode.NOT_FOUND)
        return CaseMaterialList(case_id=case_id, materials=self.store.list_materials(case_id))

    async def read_case(self, principal: Principal, case_id: str) -> CaseRecord:
        principal.require(case_id, Permission.REVIEW)
        record = self.store.read_case(case_id)
        if record is None:
            raise ServiceFault(ServiceErrorCode.NOT_FOUND)
        return record

    async def list_cases(self, principal: Principal) -> CaseListView:
        """The caller's own intake cases: membership decides, never the creator field."""
        records = tuple(
            record
            for case_id, _creator in self.store.memberships()
            if case_id in principal.case_ids
            and (record := self.store.read_case(case_id)) is not None
        )
        return CaseListView(cases=records)
