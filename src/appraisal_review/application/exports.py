"""Export operations: accept one format request, execute per table, claim only outcomes.

The service does three narrow things. It admits a request only when everything the
request pins - the run, the snapshot digest, the template bundle - matches what the
server actually holds, so a stale page cannot export yesterday's numbers. It replays
exactly or conflicts, with the format inside the payload digest. And it executes each
of the three tables independently, so one broken sheet degrades the operation to a
named partial instead of poisoning the other two.

Nothing here upgrades a draft. A formal request without an established formal gate is
delivered as a draft with the refusal listed, which is the honest version of "you asked
for formal and the system cannot certify that yet". PDF is produced by converting the
exact workbook bytes this same execution filled; the workbook is stored for traceability
either way, but the operation delivers only the chosen format.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from appraisal_review.application.review_jobs import ReviewJobService
from appraisal_review.application.service_guards import Principal, ServiceFault
from appraisal_review.domain.artifact_publication import ArtifactKey
from appraisal_review.domain.calculation_snapshot import CalculationSnapshot
from appraisal_review.domain.official_export import (
    REQUIRED_TABLES,
    ConvertedPDFArtifact,
    ExportArtifact,
    ExportBasis,
    ExportOperation,
    ExportRequest,
    TableOutcome,
    TemplateBundleReference,
    WorkbookArtifact,
)
from appraisal_review.domain.official_table_mapping import OfficialTable, TableMapping
from appraisal_review.domain.service_contracts import (
    Permission,
    RunReference,
    ServiceErrorCode,
    ServiceProblem,
)
from appraisal_review.ports.workbook_conversion import (
    ConversionUnavailable,
    ConvertedWorkbook,
)

WRITER_VERSION = "workbook-writer-1"


class FilledWorkbookLike(Protocol):
    @property
    def content(self) -> bytes: ...

    @property
    def sheet_name(self) -> str: ...

    @property
    def written_cells(self) -> tuple[str, ...]: ...


WorkbookFiller = Callable[[bytes, TableMapping, CalculationSnapshot], FilledWorkbookLike]


class WorkbookConverter(Protocol):
    def convert(
        self,
        workbook: bytes,
        *,
        expected_sha256: str,
        verify_text: tuple[str, ...] = (),
        expected_page_count: int | None = None,
    ) -> ConvertedWorkbook: ...


class SnapshotProvider(Protocol):
    def read(self, case_id: str, revision_id: str) -> CalculationSnapshot | None: ...


class ExportStore(Protocol):
    def create(
        self, operation: ExportOperation, request: ExportRequest, actor_id: str
    ) -> tuple[ExportOperation, bool]: ...

    def save(self, operation: ExportOperation) -> None: ...

    def read(self, job_id: UUID, export_id: UUID) -> ExportOperation | None: ...

    def pending(self) -> tuple[ExportOperation, ...]: ...

    def put_object(
        self, *, artifact_id: UUID, export_id: UUID, key: str, data: bytes, delivered: bool
    ) -> str: ...


@dataclass(frozen=True)
class TableAsset:
    """One official template pinned by content, plus the mapping authored against it."""

    table: OfficialTable
    template_path: Path
    template_sha256: str
    mapping: TableMapping


@dataclass(frozen=True)
class ExportAssets:
    bundle: TemplateBundleReference
    tables: dict[OfficialTable, TableAsset]

    @classmethod
    def load(
        cls,
        *,
        bundle_id: str,
        version: str,
        templates: dict[OfficialTable, Path],
        mappings: dict[OfficialTable, TableMapping],
    ) -> ExportAssets:
        assets: dict[OfficialTable, TableAsset] = {}
        digests: list[str] = []
        for table in sorted(templates):
            data = templates[table].read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            mapping = mappings[table]
            if mapping.template_digest != digest:
                # A mapping authored against different bytes writes the wrong cells.
                raise ValueError(f"Mapping for {table} does not match the template bytes")
            assets[table] = TableAsset(
                table=table,
                template_path=templates[table],
                template_sha256=digest,
                mapping=mapping,
            )
            digests.append(digest)
        bundle_hash = hashlib.sha256("\n".join(digests).encode()).hexdigest()
        return cls(
            bundle=TemplateBundleReference(
                bundle_id=bundle_id, version=version, bundle_hash=bundle_hash
            ),
            tables=assets,
        )


def _artifact_id(export_id: UUID, table: str, kind: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"appraisal-export/{export_id}/{table}/{kind}")


class ExportService:
    """Submit, replay and execute export operations against one durable store."""

    def __init__(
        self,
        *,
        jobs: ReviewJobService,
        store: ExportStore,
        assets: ExportAssets,
        filler: WorkbookFiller,
        converter: WorkbookConverter | None,
        snapshots: SnapshotProvider,
    ) -> None:
        self.jobs = jobs
        self.store = store
        self.assets = assets
        self.filler = filler
        self.converter = converter
        self.snapshots = snapshots
        self._lock = asyncio.Lock()

    async def submit(
        self, principal: Principal, job_id: UUID, request: ExportRequest
    ) -> ExportOperation:
        request = ExportRequest.model_validate_json(request.model_dump_json())
        status = await self.jobs.status(principal, job_id)
        case_id = status.job.case_id
        principal.require(case_id, Permission.REVIEW)
        current = status.current_run
        if current is None or request.run.run_id != current.run_id:
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        if request.run.revision != current.revision:
            # The page exported against a revision that a correction has since replaced.
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        if request.template_bundle != self.assets.bundle:
            raise ServiceFault(ServiceErrorCode.VALIDATION)
        snapshot = self.snapshots.read(case_id, current.revision.revision_id)
        if snapshot is None:
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        if snapshot.digest() != request.calculation_snapshot_digest:
            raise ServiceFault(ServiceErrorCode.CONFLICT)

        blockers: list[str] = []
        if request.requested_mode == "formal":
            blockers.append("Formal eligibility is not established this round; delivering a draft.")
        blockers.extend(f"{key}: {reason}" for key, reason in sorted(snapshot.gaps.items()))
        operation = ExportOperation(
            export_id=uuid5(
                NAMESPACE_URL,
                f"appraisal-export-op/{job_id}/{principal.actor.actor_id}/"
                f"{request.idempotency_key}",
            ),
            job_id=job_id,
            run=request.run,
            export_format=request.export_format,
            requested_mode=request.requested_mode,
            effective_mode="draft",
            status="queued",
            payload_digest=request.payload_digest(),
            calculation_snapshot_digest=request.calculation_snapshot_digest,
            template_bundle=self.assets.bundle,
            blockers=tuple(blockers),
        )
        stored, _created = self.store.create(operation, request, principal.actor.actor_id)
        return stored

    async def basis(self, principal: Principal, job_id: UUID) -> ExportBasis:
        """The server-held facts a client compiles into a valid request."""
        status = await self.jobs.status(principal, job_id)
        principal.require(status.job.case_id, Permission.REVIEW)
        current = status.current_run
        if current is None:
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        snapshot = self.snapshots.read(status.job.case_id, current.revision.revision_id)
        if snapshot is None:
            # No registered calculation for this revision yet; exporting would have
            # nothing honest to fill from.
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        return ExportBasis(
            job_id=job_id,
            run=RunReference(run_id=current.run_id, revision=current.revision),
            calculation_snapshot_digest=snapshot.digest(),
            template_bundle=self.assets.bundle,
            blockers=tuple(f"{key}: {reason}" for key, reason in sorted(snapshot.gaps.items())),
        )

    async def read(self, principal: Principal, job_id: UUID, export_id: UUID) -> ExportOperation:
        status = await self.jobs.status(principal, job_id)
        principal.require(status.job.case_id, Permission.REVIEW)
        operation = self.store.read(job_id, export_id)
        if operation is None:
            raise ServiceFault(ServiceErrorCode.NOT_FOUND)
        return operation

    async def run_pending(self) -> int:
        """Execute queued operations; called from the composition's worker loop."""
        async with self._lock:
            pending = self.store.pending()
            for operation in pending:
                await asyncio.to_thread(self._execute, operation)
            return len(pending)

    def _execute(self, operation: ExportOperation) -> None:
        snapshot = self.snapshots.read(
            operation.run.revision.case_id, operation.run.revision.revision_id
        )
        if snapshot is None or snapshot.digest() != operation.calculation_snapshot_digest:
            self.store.save(
                operation.model_copy(
                    update={
                        "status": "failed",
                        "problem": ServiceProblem(code=ServiceErrorCode.CONFLICT),
                    }
                )
            )
            return
        outcomes: list[TableOutcome] = []
        artifacts: list[ExportArtifact] = []
        for table in sorted(REQUIRED_TABLES):
            asset = self.assets.tables.get(table)
            if asset is None:
                outcomes.append(
                    TableOutcome(
                        table=table,
                        delivered=False,
                        problem=ServiceProblem(code=ServiceErrorCode.CAPABILITY),
                    )
                )
                continue
            try:
                outcome, produced = self._table(operation, asset, snapshot)
            except ServiceFault as fault:
                outcome, produced = (
                    TableOutcome(table=table, delivered=False, problem=fault.problem),
                    [],
                )
            except ConversionUnavailable as unavailable:
                outcome, produced = (
                    TableOutcome(
                        table=table,
                        delivered=False,
                        problem=ServiceProblem(code=ServiceErrorCode.CAPABILITY),
                    ),
                    [],
                )
                _ = unavailable
            except Exception:
                outcome, produced = (
                    TableOutcome(
                        table=table,
                        delivered=False,
                        problem=ServiceProblem(code=ServiceErrorCode.EXECUTION),
                    ),
                    [],
                )
            outcomes.append(outcome)
            artifacts.extend(produced)
        delivered = [outcome for outcome in outcomes if outcome.delivered]
        if len(delivered) == len(REQUIRED_TABLES):
            status = "succeeded"
        elif delivered:
            status = "partial"
        else:
            status = "failed"
        problem = None
        if status == "failed":
            problem = next(
                (outcome.problem for outcome in outcomes if outcome.problem is not None),
                ServiceProblem(code=ServiceErrorCode.EXECUTION),
            )
        blockers = operation.blockers
        if status == "succeeded" and blockers:
            # The domain refuses a succeeded operation carrying blockers; a still-blocked
            # export is at best partial. Downgrade honestly rather than dropping the list.
            status = "partial"
        self.store.save(
            operation.model_copy(
                update={
                    "status": status,
                    "tables": tuple(outcomes),
                    "artifacts": tuple(artifacts),
                    "problem": problem,
                }
            )
        )

    def _table(
        self,
        operation: ExportOperation,
        asset: TableAsset,
        snapshot: CalculationSnapshot,
    ) -> tuple[TableOutcome, list[ExportArtifact]]:
        template = asset.template_path.read_bytes()
        if hashlib.sha256(template).hexdigest() != asset.template_sha256:
            raise ServiceFault(ServiceErrorCode.CAPABILITY)
        filled = self.filler(template, asset.mapping, snapshot)
        workbook_bytes = filled.content
        workbook_digest = hashlib.sha256(workbook_bytes).hexdigest()
        run = self._export_run(operation)
        workbook_id = _artifact_id(operation.export_id, asset.table, "official_workbook")
        workbook_key = ArtifactKey.for_run(run, workbook_id, suffix="xlsx").key()
        deliver_workbook = operation.export_format == "xlsx"
        self.store.put_object(
            artifact_id=workbook_id,
            export_id=operation.export_id,
            key=workbook_key,
            data=workbook_bytes,
            delivered=deliver_workbook,
        )
        produced: list[ExportArtifact] = []
        if deliver_workbook:
            artifact = WorkbookArtifact(
                artifact_id=workbook_id,
                key=workbook_key,
                table=asset.table,
                content_hash=workbook_digest,
                size_bytes=len(workbook_bytes),
                filename=f"{asset.table}-{workbook_id}.xlsx",
                template_bundle=self.assets.bundle,
                template_hash=asset.template_sha256,
                snapshot_digest=operation.calculation_snapshot_digest,
                writer_version=WRITER_VERSION,
                sheet_name=filled.sheet_name,
                written_range=f"{asset.mapping.bindings[0].cell}:{asset.mapping.bindings[-1].cell}",
                written_cells=filled.written_cells,
            )
            produced.append(artifact)
            return (
                TableOutcome(table=asset.table, delivered=True, artifact_id=workbook_id),
                produced,
            )
        if self.converter is None:
            raise ConversionUnavailable("converter_missing")
        converted = self.converter.convert(workbook_bytes, expected_sha256=workbook_digest)
        pdf_id = _artifact_id(operation.export_id, asset.table, "converted_pdf")
        pdf_key = ArtifactKey.for_run(run, pdf_id, suffix="pdf").key()
        self.store.put_object(
            artifact_id=pdf_id,
            export_id=operation.export_id,
            key=pdf_key,
            data=converted.content,
            delivered=True,
        )
        pdf_artifact = ConvertedPDFArtifact(
            artifact_id=pdf_id,
            key=pdf_key,
            table=asset.table,
            content_hash=hashlib.sha256(converted.content).hexdigest(),
            size_bytes=len(converted.content),
            filename=f"{asset.table}-{pdf_id}.pdf",
            template_bundle=self.assets.bundle,
            template_hash=asset.template_sha256,
            snapshot_digest=operation.calculation_snapshot_digest,
            writer_version=WRITER_VERSION,
            source_workbook_hash=workbook_digest,
            page_count=converted.page_count,
        )
        produced.append(pdf_artifact)
        return TableOutcome(table=asset.table, delivered=True, artifact_id=pdf_id), produced

    @staticmethod
    def _export_run(operation: ExportOperation) -> RunReference:
        # Export artifacts are scoped by the export operation itself: the operation id
        # takes the attempt slot so one run's exports never collide across operations.
        return RunReference(
            run_id=operation.run.run_id,
            revision=operation.run.revision,
            attempt_id=operation.export_id,
        )
