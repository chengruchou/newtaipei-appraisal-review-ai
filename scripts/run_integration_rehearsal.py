"""Serve fresh synthetic core and privacy cases with actual local restoration ports."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import uvicorn
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from appraisal_review.adapters.local.document_authority import (
    ConfiguredDocumentAuthorization,
    DocumentGrant,
    Ed25519ExportVerifier,
    TrustedExportKey,
)
from appraisal_review.adapters.local.document_storage import SQLiteDocumentStorage
from appraisal_review.adapters.local.privacy.ocr import TesseractConfig, TesseractOCR
from appraisal_review.adapters.local.privacy.pdf_worker import ScanLimits
from appraisal_review.adapters.local.privacy.refill import IsolatedPrivacyRefillProcessor
from appraisal_review.adapters.local.privacy.sanitize import TesseractPrivacyOutputOCR
from appraisal_review.adapters.local.privacy_bridge import PrivacyBridgeRestore, privacy_request_id
from appraisal_review.adapters.local.privacy_restore_resolver import (
    LocalRestoreCoordinator,
    RestorePublication,
)
from appraisal_review.adapters.local.synthetic_workbench import (
    SyntheticWorkbench,
    _private_json,
    prepare_workbench,
)
from appraisal_review.application.document_transfer import DocumentTransferService
from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.domain.artifact_publication import PublicationError, SourceVersion
from appraisal_review.domain.document_transfer import DocumentMetadata, DocumentOperation
from appraisal_review.domain.privacy_models import PrivacyPage
from appraisal_review.domain.privacy_review import PrivacyPagePreview
from appraisal_review.domain.privacy_scan import TextObservation
from appraisal_review.domain.service_contracts import Permission, ServiceResult
from appraisal_review.ports.privacy import PrivacyOutputOCR


class RecordedOCR:
    """Private same-request evidence; preserve all raw observations and exceptions."""

    def __init__(self, delegate: PrivacyOutputOCR, directory: Path) -> None:
        self.delegate, self.directory = delegate, directory

    def read(
        self, preview: PrivacyPagePreview, page: PrivacyPage, *, timeout: float
    ) -> tuple[TextObservation, ...]:
        target = self.directory / f"ocr-{uuid4()}.json"
        record: dict[str, Any] = {
            "request_id": privacy_request_id(),
            "page": page.model_dump(mode="json"),
            "input_sha256": hashlib.sha256(preview.png).hexdigest(),
            "width": preview.width,
            "height": preview.height,
            "observed_at": datetime.now(UTC).isoformat(),
        }
        try:
            observations = self.delegate.read(preview, page, timeout=timeout)
        except Exception as error:
            record.update(status="failed", exception_type=type(error).__name__)
            _private_json(target, record)
            raise
        record.update(
            status="observed",
            observations=[value.model_dump(mode="json") for value in observations],
        )
        _private_json(target, record)
        return observations


class RecordedRestoreResolver:
    """Keep the real plan and exact published bytes before the existing executor."""

    def __init__(self, delegate: LocalRestoreCoordinator, directory: Path) -> None:
        self.delegate, self.directory = delegate, directory

    def resolve(self, principal_id: str, result_id: UUID) -> PrivacyBridgeRestore:
        result = self.delegate.resolve(principal_id, result_id)
        artifact = result.publisher.current(result.plan)
        identifier = str(uuid4())
        target = self.directory / f"published-{identifier}.pdf"
        descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(artifact.pdf)
            stream.flush()
            os.fsync(stream.fileno())
        _private_json(
            self.directory / f"plan-{identifier}.json",
            {
                "observed_at": datetime.now(UTC).isoformat(),
                "restore_result_id": str(result_id),
                "plan": result.plan.model_dump(mode="json"),
                "published_pdf_file": target.name,
                "published_pdf_sha256": hashlib.sha256(artifact.pdf).hexdigest(),
            },
        )
        return result


class PublishedResults:
    """Private opaque handles, resolved afresh against current durable authority."""

    def __init__(
        self, context: SyntheticWorkbench, forms: Callable[[str], DocumentMetadata]
    ) -> None:
        self.context, self.forms = context, forms
        with closing(context.store._connect()) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS rehearsal_restore_handles "
                "(handle TEXT PRIMARY KEY, job_id TEXT NOT NULL, run_id TEXT NOT NULL, "
                "artifact_id TEXT NOT NULL, UNIQUE(job_id,run_id,artifact_id))"
            )

    def _current(self, job_id: UUID) -> ServiceResult:
        context = self.context
        principal = context.directory.authenticate("Bearer " + context.state["session_token"])
        with closing(context.store._connect()) as db:
            db.execute("BEGIN")
            state = context.store._decode(
                db.execute("SELECT payload FROM review_state WHERE singleton=1").fetchone()[0]
            )
            jobs, _ = state.stores()
            job = jobs._jobs.get(job_id)
            if job is None:
                raise ValueError("Current completed job required")
            record = jobs._record(job)
            principal.require(record.case_id, Permission.REVIEW)
            if (
                record.principal_id != principal.actor.actor_id
                or record.status.value != "succeeded"
                or record.cancel_requested
            ):
                raise ValueError("Current completed owned job required")
            body = context.store.results._get(
                jobs, db, run_id=record.current_run.run_id, result_version=record.result_version
            )
            if (
                body is None
                or body.run.run_id != record.current_run.run_id
                or body.run.revision != record.current_run.revision
                or body.run.attempt_id is None
                or body.business_status is None
                or body.business_status.value != "completed"
                or not body.artifacts
            ):
                raise ValueError("Current completed publication required")
        return body

    def bind(self, job_id: UUID) -> UUID:
        body = self._current(job_id)
        artifact_id = body.artifacts[0].artifact_id
        principal = self.context.directory.authenticate(
            "Bearer " + self.context.state["session_token"]
        )
        self.context.resolver.verified_bytes(
            principal, body.run.revision.case_id, body.run.run_id, artifact_id
        )
        key = (str(job_id), str(body.run.run_id), str(artifact_id))
        with closing(self.context.store._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT OR IGNORE INTO rehearsal_restore_handles VALUES(?,?,?,?)",
                (str(uuid4()), *key),
            )
            handle = UUID(
                db.execute(
                    "SELECT handle FROM rehearsal_restore_handles "
                    "WHERE job_id=? AND run_id=? AND artifact_id=?",
                    key,
                ).fetchone()[0]
            )
            db.commit()
        self(principal.actor.actor_id, handle)
        return handle

    def __call__(self, principal_id: str, handle: UUID) -> RestorePublication:
        context = self.context
        principal = context.directory.authenticate("Bearer " + context.state["session_token"])
        if principal.actor.actor_id != principal_id:
            raise ValueError("Local session differs")
        with closing(context.store._connect()) as db:
            row = db.execute(
                "SELECT job_id,run_id,artifact_id FROM rehearsal_restore_handles WHERE handle=?",
                (str(handle),),
            ).fetchone()
        if row is None:
            raise ValueError("Current opaque publication handle required")
        job_id, run_id, artifact_id = map(UUID, row)
        body = self._current(job_id)
        if body.run.run_id != run_id or artifact_id not in {v.artifact_id for v in body.artifacts}:
            raise ValueError("Publication handle is stale")
        artifact, pdf = context.resolver.verified_bytes(
            principal, body.run.revision.case_id, run_id, artifact_id
        )
        forms = self.forms(body.run.revision.case_id)
        if self._current(job_id) != body:
            raise ValueError("Current publication changed")
        return RestorePublication(principal, job_id, body.run, artifact, pdf, forms)


@dataclass
class CombinedRehearsal:
    core: SyntheticWorkbench
    privacy: Any
    results: PublishedResults
    coordinator: LocalRestoreCoordinator
    private_fixture: Path
    case_id: str
    base_fixture: dict[str, Any]

    async def publish_status(self) -> None:
        name = next(
            (name for name, case in self.core.case_ids.items() if case == self.case_id), None
        )
        if name is None or name not in self.core.state.get("job_ids", {}):
            return
        status = await self.core.case_status(self.case_id)
        manifest = self.base_fixture | status
        for key in ("restore_result_id", "restoration_unavailable", "restoration_unavailable_code"):
            manifest.pop(key, None)
        if status.get("completed_job_id"):
            try:
                handle = await asyncio.to_thread(
                    self.results.bind, UUID(status["completed_job_id"])
                )
            except PublicationError as error:
                if error.code != "publication_unauthorized":
                    raise
                manifest.update(
                    restoration_unavailable=True,
                    restoration_unavailable_code=error.code,
                )
            else:
                manifest["restore_result_id"] = str(handle)
        _private_json(self.private_fixture, manifest)

    async def serve(self, *, port: int, privacy_port: int) -> None:
        servers = [
            uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=number, access_log=False))
            for app, number in ((self.core.app, port), (self.privacy.app, privacy_port))
        ]

        async def poll() -> None:
            while not any(server.should_exit for server in servers):
                await self.publish_status()
                await asyncio.sleep(0.25)

        tasks = [asyncio.create_task(server.serve()) for server in servers]
        tasks.append(asyncio.create_task(poll()))
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            for server in servers:
                server.should_exit = True
            tasks[-1].cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.privacy.close()


async def create_rehearsal(
    directory: Path,
    *,
    port: int,
    privacy_port: int,
    origin: str,
    ocr_config: Path,
    ocr_dpi: int = 144,
    raster_dpi: int = 144,
) -> CombinedRehearsal:
    from privacy_browser_rehearsal import create_privacy_rehearsal
    from privacy_raster_fixture import create_sanitized_synthetic_case

    render_limits = ScanLimits(dpi=ocr_dpi)
    root = directory.absolute()
    if root.exists() or root.is_symlink():
        raise ValueError("A fresh private rehearsal directory is required")
    root.mkdir(mode=0o700)
    core = await prepare_workbench(root / "core", port=port)
    await core.settle()
    _private_json(core.root / "fixture.json", core.manifest())
    case, key_id = uuid4(), uuid4()
    core.principal = core.directory.grant_case(core.principal.actor.actor_id, str(case))
    actor = UUID(core.principal.actor.actor_id)
    signing_key = Ed25519PrivateKey.generate()
    storage = SQLiteDocumentStorage(root / "privacy-c2.sqlite")
    documents = DocumentTransferService(
        storage,
        ConfiguredDocumentAuthorization(
            (
                DocumentGrant(
                    actor, case, frozenset({"criteria", "forms"}), frozenset(DocumentOperation)
                ),
            )
        ),
        Ed25519ExportVerifier(
            (TrustedExportKey(key_id, signing_key.public_key(), actor, frozenset({case})),)
        ),
        storage,
    )
    loop = asyncio.get_running_loop()
    bridge: Any = None

    async def register(snapshot: RevisionSnapshot) -> None:
        fixture = await create_sanitized_synthetic_case(
            root / "privacy" / "writer-case",
            core.principal,
            documents,
            tuple(bridge.sink.receipts),
            snapshot=snapshot,
            approve_authored_synthetic_rules=True,
        )
        await core.register_synthetic_case(
            fixture,
            raster_source_versions=tuple(
                SourceVersion(
                    document_id=d.document_id, version=d.version, content_hash=d.content_hash
                )
                for d in fixture.snapshot.revision.documents
            ),
        )

    def on_ready(snapshot: RevisionSnapshot) -> None:
        asyncio.run_coroutine_threadsafe(register(snapshot), loop).result(timeout=120)

    def diagnostic(record: dict[str, object]) -> None:
        _private_json(root / f"restore-diagnostic-{uuid4()}.json", record)

    bridge = create_privacy_rehearsal(
        root / "privacy",
        authority=f"127.0.0.1:{privacy_port}",
        origin=origin,
        principal=core.principal,
        documents=documents,
        signing_key=signing_key,
        key_id=key_id,
        case_id=case,
        on_ready=on_ready,
        raster_dpi=raster_dpi,
        diagnostic=diagnostic,
    )

    def forms(case_id: str) -> DocumentMetadata:
        if case_id != str(case):
            raise ValueError("Exact configured privacy case required")
        receipt = next(r for r in bridge.sink.receipts if r.reference.purpose == "forms")
        return documents.read(core.principal, receipt.reference).metadata

    results = PublishedResults(core, forms)
    ocr = TesseractOCR(TesseractConfig.model_validate_json(ocr_config.read_bytes()), root)
    await asyncio.to_thread(ocr.preflight, timeout=5)
    evidence_directory = bridge.config.workspace / "private-evidence"
    evidence_directory.mkdir(mode=0o700)
    output_ocr = TesseractPrivacyOutputOCR(ocr, dpi=ocr_dpi)
    coordinator = LocalRestoreCoordinator(
        workspace=bridge.config.workspace,
        session=bridge.session,
        documents=documents,
        publication=results,
        processor=IsolatedPrivacyRefillProcessor(bridge.config.workspace, limits=render_limits),
        ocr=RecordedOCR(output_ocr, evidence_directory),
        ocr_identity=output_ocr.identity,
    )
    bridge.session.results = RecordedRestoreResolver(coordinator, evidence_directory)
    private_fixture = bridge.config.workspace / "browser-private.json"
    bridge.write_browser_fixture(private_fixture)
    return CombinedRehearsal(
        core,
        bridge,
        results,
        coordinator,
        private_fixture,
        str(case),
        json.loads(private_fixture.read_bytes()),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--privacy-port", type=int, default=8788)
    parser.add_argument("--origin", default="http://127.0.0.1:4174")
    parser.add_argument("--ocr-config", type=Path, required=True)
    parser.add_argument("--ocr-dpi", type=int, choices=range(72, 301), metavar="DPI", default=144)
    parser.add_argument(
        "--raster-dpi", type=int, choices=range(72, 301), metavar="DPI", default=144
    )
    args = parser.parse_args()

    async def run() -> None:
        rehearsal = await create_rehearsal(
            args.directory,
            port=args.port,
            privacy_port=args.privacy_port,
            origin=args.origin,
            ocr_config=args.ocr_config,
            ocr_dpi=args.ocr_dpi,
            raster_dpi=args.raster_dpi,
        )
        print(f"Synthetic core: http://127.0.0.1:{args.port}", flush=True)
        print(f"Local privacy bridge: http://127.0.0.1:{args.privacy_port}", flush=True)
        await rehearsal.serve(port=args.port, privacy_port=args.privacy_port)

    asyncio.run(run())


if __name__ == "__main__":
    main()
