"""Run an isolated synthetic privacy bridge with real PDF, encrypted mapping and C2 I/O.

Candidate detection and output OCR are authored synthetic adapters. They are not
model accuracy evidence. Source review and exact export require explicit UI actions.
The core workbench owns job execution, human tasks and published result authority.
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from uuid import UUID, uuid4

import uvicorn
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import FastAPI

from appraisal_review.adapters.local.document_authority import (
    ConfiguredDocumentAuthorization,
    DocumentGrant,
    Ed25519ExportVerifier,
    TrustedExportKey,
)
from appraisal_review.adapters.local.document_storage import SQLiteDocumentStorage
from appraisal_review.adapters.local.privacy.mapping_crypto import MappingCipher, SessionMappingKeys
from appraisal_review.adapters.local.privacy.mapping_store import (
    LinuxEncryptedMappingStore,
    MacOSEncryptedMappingStore,
)
from appraisal_review.adapters.local.privacy.pdf_worker import ScanLimits
from appraisal_review.adapters.local.privacy.review import LocalPrivacyPreviews
from appraisal_review.adapters.local.privacy.sanitize import IsolatedPrivacyRasterProcessor
from appraisal_review.adapters.local.privacy.source import IsolatedPrivacyPDF, LocalSnapshotStore
from appraisal_review.adapters.local.privacy_bridge import (
    BridgeReviewConfirmation,
    PrivacyBridgeConfig,
    PrivacyBridgeRestore,
    PrivacyBridgeResultResolver,
    PrivacyBridgeSession,
    PrivacyBridgeSource,
    create_privacy_bridge,
)
from appraisal_review.adapters.local.privacy_document_sink import CloudExportSink
from appraisal_review.application.document_transfer import DocumentTransferService
from appraisal_review.application.privacy_bundle import (
    LocalSanitizedBundleBuilder,
    LocalSanitizedVerifier,
)
from appraisal_review.application.privacy_mapping import LocalMappingService
from appraisal_review.application.privacy_review import LocalPrivacyReviewService
from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.application.service_guards import Principal
from appraisal_review.document_cli import private_json
from appraisal_review.domain.document_transfer import DocumentMetadata, DocumentOperation, Purpose
from appraisal_review.domain.privacy_bundle import SanitizedBundle
from appraisal_review.domain.privacy_models import (
    LocalSourceSnapshot,
    PrivacyPage,
    PrivacyReviewCommand,
    SensitiveCandidate,
    placeholder_text,
)
from appraisal_review.domain.privacy_review import PrivacyPagePreview
from appraisal_review.domain.privacy_scan import PageScan, PrivacyScanReport, TextObservation
from appraisal_review.domain.service_contracts import ActorReference, Permission


class _SyntheticScan:
    def __init__(
        self,
        sources: LocalSnapshotStore,
        candidates: Mapping[UUID, tuple[SensitiveCandidate, ...]],
    ) -> None:
        self.sources, self.candidates = sources, dict(candidates)

    async def scan(self, snapshot: LocalSourceSnapshot) -> PrivacyScanReport:
        inspection = self.sources.inspection(snapshot)
        return PrivacyScanReport(
            source=snapshot,
            status="needs_review",
            native_engine_version=inspection.parser_version,
            pages=tuple(
                PageScan(
                    page=page.geometry.number,
                    mode="native",
                    status="processed",
                    observations=page.observations,
                )
                for page in inspection.pages
            ),
            candidates=self.candidates[snapshot.snapshot_id],
        )


class _SyntheticRaster(IsolatedPrivacyRasterProcessor):
    """Actual isolated processing, plus a fail-closed authored canary selection check."""

    def __init__(
        self,
        directory: Path,
        candidates: Mapping[UUID, tuple[SensitiveCandidate, ...]],
        *,
        raster_dpi: int = 144,
    ) -> None:
        super().__init__(directory, limits=ScanLimits(dpi=raster_dpi))
        self.candidates = dict(candidates)
        self.bundle: SanitizedBundle | None = None

    def build(self, command: PrivacyReviewCommand, original: bytes) -> SanitizedBundle:
        self.bundle = None
        for candidate in self.candidates[command.source.snapshot_id]:
            x0, y0, x1, y1 = candidate.region.bbox
            if not any(
                item.disposition == "redact"
                and item.candidate.region.page == candidate.region.page
                and item.candidate.region.bbox[0] <= x0
                and item.candidate.region.bbox[1] <= y0
                and item.candidate.region.bbox[2] >= x1
                and item.candidate.region.bbox[3] >= y1
                for item in command.selections
            ):
                raise ValueError("Authored synthetic canary must remain fully selected")
        self.bundle = super().build(command, original)
        return self.bundle


class _SyntheticOutputOCR:
    """Authored placeholder observations; never a real OCR accuracy claim."""

    def __init__(self, processor: _SyntheticRaster) -> None:
        self.processor = processor

    def read(
        self,
        preview: PrivacyPagePreview,
        page: PrivacyPage,
        *,
        timeout: float,
    ) -> tuple[TextObservation, ...]:
        bundle = self.processor.bundle
        if bundle is None or timeout <= 0 or not preview.png.startswith(b"\x89PNG"):
            raise ValueError("Synthetic verified raster is unavailable")
        return tuple(
            TextObservation(
                text=placeholder_text(item.entity_id),
                region=item.region,
                origin="ocr",
                confidence=0.99,
            )
            for item in bundle.manifest.occurrences
            if item.region.page == page.number
        )


class _NoPublishedResult:
    def resolve(self, principal_id: str, result_id: UUID) -> PrivacyBridgeRestore:
        raise ValueError("A current authorized backend publication is required")


@dataclass(repr=False)
class PrivacyRehearsal:
    app: FastAPI
    config: PrivacyBridgeConfig
    session: PrivacyBridgeSession
    token: str
    source_ids: tuple[UUID, ...]
    documents: DocumentTransferService
    principal: Principal
    sink: CloudExportSink
    keys: SessionMappingKeys
    _maps: LinuxEncryptedMappingStore | MacOSEncryptedMappingStore
    _snapshot: RevisionSnapshot | None = field(default=None, init=False)
    _lock: RLock = field(default_factory=RLock, init=False)

    def __repr__(self) -> str:
        return "<synthetic privacy rehearsal; credentials omitted>"

    @property
    def snapshot(self) -> RevisionSnapshot | None:
        with self._lock:
            return self._snapshot

    def write_browser_fixture(self, path: Path, *, app_path: str = "/privacy") -> None:
        """Local private runner config only; no approval, result authority or key bytes."""
        root = self.config.workspace
        if path.parent != root or path.exists() or path.is_symlink() or app_path != "/privacy":
            raise ValueError("Fresh private browser configuration path required")
        add_region = {"page": 1, "bbox": [30, 10, 250, 45], "category": "name"}
        private_json(
            path,
            {
                "bridge_url": f"http://{self.config.authority}",
                "origin": self.config.origin,
                "token": self.token,
                "source_ids": list(map(str, self.source_ids)),
                "sources": [
                    {"source_id": str(source_id), "add_region": add_region}
                    for source_id in self.source_ids
                ],
                "add_region": add_region,
                "app_path": app_path,
                "synthetic_only": True,
            },
        )

    def close(self) -> None:
        self.session.review.close()
        self._maps.close()
        self.keys.lock()


def create_privacy_rehearsal(
    directory: Path,
    *,
    authority: str,
    origin: str,
    principal: Principal | None = None,
    documents: DocumentTransferService | None = None,
    signing_key: Ed25519PrivateKey | None = None,
    key_id: UUID | None = None,
    case_id: UUID | None = None,
    on_admitted: Callable[[DocumentMetadata], None] | None = None,
    on_ready: Callable[[RevisionSnapshot], None] | None = None,
    results: PrivacyBridgeResultResolver | None = None,
    raster_dpi: int = 144,
) -> PrivacyRehearsal:
    """Compose a real bridge; hand off the first exact paired candidate revision once.

    Supply all four principal/documents/signing_key/key_id ports together to use
    the workbench C2 store. Its verifier must already pin the supplied key. The
    callback registers candidates with the real workbench, never approves them.
    A callback failure leaves the committed sink receipt for reconciliation.
    """
    from privacy_raster_fixture import material_from_admitted, write_privacy_sources

    ScanLimits(dpi=raster_dpi)
    supplied = (principal, documents, signing_key, key_id)
    if any(item is not None for item in supplied) and any(item is None for item in supplied):
        raise ValueError("Supply the complete trusted C2 configuration")
    repository = Path(__file__).resolve().parents[1]
    directory = directory.absolute()
    if not directory.parent.resolve(strict=True).is_relative_to(repository) or directory.exists():
        raise ValueError("A fresh repository-local private directory is required")
    if directory.is_symlink():
        raise ValueError("A fresh private directory is required")
    directory.mkdir(mode=0o700)
    source_dir = directory / "originals"
    source_dir.mkdir(mode=0o700)
    assets = write_privacy_sources(source_dir)
    if {item.purpose for item in assets} != {"criteria", "forms"} or len(assets) != 2:
        raise ValueError("Exactly one authored criteria and forms source required")
    if principal is None:
        actor, case, key_id = uuid4(), case_id or uuid4(), uuid4()
        principal = Principal(
            ActorReference(actor_id=str(actor), kind="human"),
            frozenset({str(case)}),
            frozenset(Permission),
        )
        signing_key = Ed25519PrivateKey.generate()
        storage = SQLiteDocumentStorage(directory / "c2.sqlite")
        documents = DocumentTransferService(
            storage,
            ConfiguredDocumentAuthorization(
                (
                    DocumentGrant(
                        actor,
                        case,
                        frozenset({"criteria", "forms"}),
                        frozenset(DocumentOperation),
                    ),
                )
            ),
            Ed25519ExportVerifier(
                (
                    TrustedExportKey(
                        key_id,
                        signing_key.public_key(),
                        actor,
                        frozenset({case}),
                    ),
                )
            ),
            storage,
        )
    if documents is None or signing_key is None or key_id is None:
        raise ValueError("Complete trusted C2 configuration required")
    if case_id is None and len(principal.case_ids) != 1:
        raise ValueError("Select one explicit server-configured synthetic case")
    case = case_id or UUID(next(iter(principal.case_ids)))
    principal.require(str(case), Permission.REVIEW)
    sources = LocalSnapshotStore(source_dir, IsolatedPrivacyPDF(directory))
    configured: dict[UUID, PrivacyBridgeSource] = {}
    candidates: dict[UUID, tuple[SensitiveCandidate, ...]] = {}
    purposes: dict[tuple[UUID, UUID], Purpose] = {}
    for asset in assets:
        snapshot = sources.capture(asset.path.relative_to(source_dir).as_posix(), case_id=case)
        candidates[snapshot.snapshot_id] = asset.candidates
        configured[uuid4()] = PrivacyBridgeSource(asset.path, snapshot, "b" * 64)
        purposes[case, snapshot.document_id] = asset.purpose
    keys, map_key = SessionMappingKeys(), uuid4()
    keys.provide(map_key, AESGCM.generate_key(bit_length=256))
    maps_path = directory / "maps"
    maps_path.mkdir(mode=0o700)
    store_type = (
        MacOSEncryptedMappingStore if sys.platform == "darwin" else LinuxEncryptedMappingStore
    )
    maps = store_type(maps_path, workspace=directory)
    human = BridgeReviewConfirmation(principal.actor.actor_id)
    review = LocalPrivacyReviewService(
        scanner=_SyntheticScan(sources, candidates),
        sources=sources,
        previews=LocalPrivacyPreviews(sources),
        human=human,
    )
    processor = _SyntheticRaster(directory, candidates, raster_dpi=raster_dpi)
    verifier = LocalSanitizedVerifier(processor, _SyntheticOutputOCR(processor))
    builder = LocalSanitizedBundleBuilder(
        sources=sources,
        authority=review,
        processor=processor,
        verifier=verifier,
    )
    mappings = LocalMappingService(
        store=maps,
        cipher=MappingCipher(keys),
        authority=review,
        verifier=verifier,
    )
    admitted: dict[Purpose, DocumentMetadata] = {}
    attempted = False
    callback_lock = RLock()

    def record(receipt: DocumentMetadata) -> None:
        nonlocal attempted
        with callback_lock:
            if on_admitted is not None:
                on_admitted(receipt)
            admitted.setdefault(receipt.reference.purpose, receipt)
            if set(admitted) != {"criteria", "forms"} or attempted:
                return
            attempted = True
            snapshot = asyncio.run(
                material_from_admitted(principal, documents, tuple(admitted.values()))
            )
            if on_ready is not None:
                on_ready(snapshot)
            with fixture._lock:
                fixture._snapshot = snapshot

    try:
        sink = CloudExportSink(
            documents, principal, signing_key, key_id, purposes, on_admitted=record
        )
    except BaseException:
        maps.close()
        keys.lock()
        raise
    session = PrivacyBridgeSession(
        principal_id=principal.actor.actor_id,
        source_ids=frozenset(configured),
        human=human,
        review=review,
        builder=builder,
        verifier=verifier,
        mappings=mappings,
        key_reference=map_key,
        sink=sink,
        sources=sources,
        results=results or _NoPublishedResult(),
    )
    config = PrivacyBridgeConfig(
        workspace=directory, origin=origin, authority=authority, sources=configured
    )
    token = secrets.token_urlsafe(32)
    try:
        app = create_privacy_bridge(config, {token: session})
    except BaseException:
        maps.close()
        keys.lock()
        raise
    fixture = PrivacyRehearsal(
        app,
        config,
        session,
        token,
        tuple(configured),
        documents,
        principal,
        sink,
        keys,
        maps,
    )
    return fixture


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument("--origin", default="http://127.0.0.1:4174")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Use an unprivileged loopback port")
    fixture = create_privacy_rehearsal(
        args.directory,
        authority=f"127.0.0.1:{args.port}",
        origin=args.origin,
    )
    try:
        fixture.write_browser_fixture(fixture.config.workspace / "browser-private.json")
        uvicorn.run(
            fixture.app,
            host="127.0.0.1",
            port=args.port,
            proxy_headers=False,
            access_log=False,
            log_level="warning",
        )
    finally:
        fixture.close()


if __name__ == "__main__":
    main()
