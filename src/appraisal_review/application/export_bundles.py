"""Parent download requests: one bundle coordinating the existing single-format exports.

pdf / excel / both are one durable parent request each: the parent pins the same
run, snapshot digest and template bundle, creates (or replays) one child export
per format through the EXISTING export service - so every child keeps its own
idempotency, approval fence and revision fence - and only reports complete when
every required child delivered every table. The ZIP content is assembled by
reading each artifact through the content plane itself, so approval, withdrawal
and revision checks run per file at download time; the bundle adds a manifest,
never a bypass.
"""

from __future__ import annotations

import io
import json
import time
import zipfile
from collections.abc import Callable
from typing import Literal, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import Field, model_validator

from appraisal_review.application.exports import ExportService
from appraisal_review.application.service_guards import Principal, ServiceFault
from appraisal_review.domain.document_models import Digest
from appraisal_review.domain.official_export import (
    ExportFormat,
    ExportMode,
    ExportOperation,
    ExportRequest,
    TemplateBundleReference,
)
from appraisal_review.domain.review_contracts import content_digest
from appraisal_review.domain.service_contracts import (
    ActorReference,
    OpaqueID,
    Permission,
    RunReference,
    ServiceErrorCode,
    ServiceModel,
)

BundleStatus = Literal["pending", "succeeded", "failed"]


class BundleCommand(ServiceModel):
    """Untrusted parent request; eligibility is re-decided by each child export."""

    idempotency_key: OpaqueID
    run: RunReference
    calculation_snapshot_digest: Digest
    template_bundle: TemplateBundleReference
    requested_mode: ExportMode
    formats: tuple[ExportFormat, ...] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def unique_formats(self) -> BundleCommand:
        if len(set(self.formats)) != len(self.formats):
            raise ValueError("Each format appears at most once in a bundle")
        return self

    def payload_digest(self) -> str:
        return content_digest(self)


class BundleRecord(ServiceModel):
    """The durable parent request; child status is always read live, never cached."""

    bundle_id: UUID
    job_id: UUID
    run: RunReference
    requested_mode: ExportMode
    formats: tuple[ExportFormat, ...]
    child_exports: dict[ExportFormat, UUID]
    requested_by: ActorReference
    requested_at: int = Field(ge=0)
    payload_digest: Digest


class BundleView(ServiceModel):
    """The parent request plus the live aggregate of its children."""

    bundle: BundleRecord
    status: BundleStatus
    children: tuple[ExportOperation, ...]


class BundleStorePort(Protocol):
    def create(
        self,
        record: BundleRecord,
        *,
        actor_id: str,
        idempotency_key: str,
        payload_digest: str,
    ) -> tuple[BundleRecord, bool]: ...

    def read(self, job_id: UUID, bundle_id: UUID) -> BundleRecord | None: ...


class ExportReaderPort(Protocol):
    def read(self, job_id: UUID, export_id: UUID) -> ExportOperation | None: ...


class DeliveredBytes(Protocol):
    data: bytes


class ContentReaderPort(Protocol):
    async def read_artifact(
        self, principal: Principal, *, job_id: UUID, artifact_id: UUID
    ) -> DeliveredBytes: ...


class ExportBundleService:
    def __init__(
        self,
        *,
        exports: ExportService,
        export_reader: ExportReaderPort,
        store: BundleStorePort,
        content: ContentReaderPort,
        clock: Callable[[], int] = lambda: int(time.time()),
    ) -> None:
        self.exports = exports
        self.export_reader = export_reader
        self.store = store
        self.content_reader = content
        self.clock = clock

    async def submit(
        self, principal: Principal, job_id: UUID, command: BundleCommand
    ) -> BundleView:
        command = BundleCommand.model_validate_json(command.model_dump_json())
        children: dict[ExportFormat, UUID] = {}
        for export_format in command.formats:
            # Child keys derive from the parent key, so a parent retry replays the
            # exact same children instead of minting duplicates; every eligibility
            # rule (revision, snapshot, template, formal approval) runs in the
            # child submit itself.
            child = await self.exports.submit(
                principal,
                job_id,
                ExportRequest(
                    idempotency_key=f"{command.idempotency_key}.{export_format}",
                    run=command.run,
                    calculation_snapshot_digest=command.calculation_snapshot_digest,
                    template_bundle=command.template_bundle,
                    requested_mode=command.requested_mode,
                    export_format=export_format,
                ),
            )
            children[export_format] = child.export_id
        record = BundleRecord(
            bundle_id=uuid5(
                NAMESPACE_URL,
                f"appraisal-export-bundle/{job_id}/{principal.actor.actor_id}/"
                f"{command.idempotency_key}",
            ),
            job_id=job_id,
            run=command.run,
            requested_mode=command.requested_mode,
            formats=command.formats,
            child_exports=children,
            requested_by=principal.actor,
            requested_at=self.clock(),
            payload_digest=command.payload_digest(),
        )
        stored, _created = self.store.create(
            record,
            actor_id=principal.actor.actor_id,
            idempotency_key=command.idempotency_key,
            payload_digest=command.payload_digest(),
        )
        return self._view(stored)

    async def read(self, principal: Principal, job_id: UUID, bundle_id: UUID) -> BundleView:
        await self._authorize(principal, job_id)
        record = self.store.read(job_id, bundle_id)
        if record is None:
            raise ServiceFault(ServiceErrorCode.NOT_FOUND)
        return self._view(record)

    async def _authorize(self, principal: Principal, job_id: UUID) -> None:
        status = await self.exports.jobs.status(principal, job_id)
        principal.require(status.job.case_id, Permission.REVIEW)

    def _view(self, record: BundleRecord) -> BundleView:
        children: list[ExportOperation] = []
        for export_id in record.child_exports.values():
            operation = self.export_reader.read(record.job_id, export_id)
            if operation is not None:
                children.append(operation)
        status: BundleStatus
        if len(children) < len(record.child_exports):
            status = "pending"
        elif any(child.status == "failed" for child in children):
            status = "failed"
        elif all(
            child.status == "succeeded" and all(outcome.delivered for outcome in child.tables)
            for child in children
        ):
            status = "succeeded"
        else:
            status = "pending"
        return BundleView(bundle=record, status=status, children=tuple(children))

    async def content(
        self, principal: Principal, job_id: UUID, bundle_id: UUID
    ) -> tuple[bytes, str]:
        """The ZIP for a complete bundle; every file re-passes the content plane."""
        await self._authorize(principal, job_id)
        record = self.store.read(job_id, bundle_id)
        if record is None:
            raise ServiceFault(ServiceErrorCode.NOT_FOUND)
        view = self._view(record)
        if view.status != "succeeded":
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        manifest: dict[str, object] = {
            "bundle_id": str(record.bundle_id),
            "job_id": str(record.job_id),
            "revision_id": record.run.revision.revision_id,
            "requested_mode": record.requested_mode,
            "formats": list(record.formats),
            "files": [],
        }
        files: list[tuple[str, bytes]] = []
        for child in view.children:
            for artifact in child.artifacts:
                delivered = await self.content_reader.read_artifact(
                    principal, job_id=job_id, artifact_id=artifact.artifact_id
                )
                name = f"{artifact.table}.{child.export_format}"
                files.append((name, delivered.data))
                manifest_files = manifest["files"]
                assert isinstance(manifest_files, list)
                manifest_files.append(
                    {
                        "name": name,
                        "artifact_id": str(artifact.artifact_id),
                        "export_id": str(child.export_id),
                        "format": child.export_format,
                        "sha256": artifact.content_hash,
                    }
                )
                if child.approval_id is not None:
                    manifest["approval_id"] = str(child.approval_id)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in files:
                archive.writestr(name, data)
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        filename = f"official-tables-{record.bundle_id}.zip"
        return buffer.getvalue(), filename
