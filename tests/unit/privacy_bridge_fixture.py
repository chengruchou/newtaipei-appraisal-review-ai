"""Reusable synthetic privacy composition for local HTTP/browser rehearsal only."""

import secrets
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from test_privacy_refill import (
    scenario,
)

from appraisal_review.adapters.local.privacy.mapping_crypto import MappingCipher, SessionMappingKeys
from appraisal_review.adapters.local.privacy.mapping_store import (
    LinuxEncryptedMappingStore,
    MacOSEncryptedMappingStore,
)
from appraisal_review.adapters.local.privacy.review import LocalPrivacyPreviews
from appraisal_review.adapters.local.privacy.sanitize import IsolatedPrivacyRasterProcessor
from appraisal_review.adapters.local.privacy_bridge import (
    BridgeReviewConfirmation,
    PrivacyBridgeConfig,
    PrivacyBridgeSession,
    PrivacyBridgeSource,
    create_privacy_bridge,
)
from appraisal_review.application.privacy_bundle import (
    LocalSanitizedBundleBuilder,
    LocalSanitizedVerifier,
)
from appraisal_review.application.privacy_mapping import LocalMappingService
from appraisal_review.application.privacy_review import LocalPrivacyReviewService
from appraisal_review.domain.privacy_models import (
    placeholder_text,
)
from appraisal_review.domain.privacy_scan import PageScan, PrivacyScanReport, TextObservation


class SyntheticPrivacyBridge(SimpleNamespace):
    def __repr__(self):
        return "<synthetic local privacy fixture>"


def build_synthetic_privacy_bridge(
    workspace,
    authority,
    origin,
    *,
    sink=None,
    results=None,
    principal_id="synthetic-owner",
    case_id=None,
):
    """Own an EMPTY private fixture directory; no server startup or credential output.

    Pass the parent's authorized C2 sink/results resolver to integrate real local
    services. OCR and sensitive-candidate classification are explicit synthetic
    doubles. Keys/session tokens remain in memory and must never enter reports.
    """
    if not workspace.is_dir() or any(workspace.iterdir()):
        raise ValueError("Synthetic privacy fixture requires an empty existing directory")
    tmp_path = workspace
    tmp_path.chmod(0o700)
    executor, mapping, _, _, _, _, _ = scenario(tmp_path)
    source, sources = mapping.command.source, executor._sources
    if case_id is not None:
        source = sources.capture("synthetic-original.pdf", case_id=case_id)
    source_id = uuid4()
    keys, key = SessionMappingKeys(), uuid4()
    keys.provide(key, AESGCM.generate_key(bit_length=256))
    directory = tmp_path / "maps"
    directory.mkdir(mode=0o700)
    cls = MacOSEncryptedMappingStore if sys.platform == "darwin" else LinuxEncryptedMappingStore
    map_store = cls(directory, workspace=tmp_path)
    if sink is None:
        sink = Mock()
    resolver = results
    if resolver is None:
        resolver = Mock()
        resolver.resolve.side_effect = ValueError("Synthetic result unavailable")
    trackers = []

    def session(principal, allowed):
        scanner = Mock()
        scanner.scan = AsyncMock(
            return_value=PrivacyScanReport(
                source=source,
                status="needs_review",
                native_engine_version="synthetic-native-candidates",
                pages=(
                    PageScan(
                        page=1,
                        mode="native",
                        status="processed",
                        observations=sources.inspection(source).pages[0].observations,
                    ),
                ),
                candidates=(mapping.command.selections[0].candidate,),
            )
        )
        human = BridgeReviewConfirmation(principal)
        review = LocalPrivacyReviewService(
            scanner=scanner, sources=sources, previews=LocalPrivacyPreviews(sources), human=human
        )
        processor = IsolatedPrivacyRasterProcessor(tmp_path)
        tracked = Mock(wraps=processor)
        bundles = []

        def build(command, original):
            bundle = processor.build(command, original)
            bundles.append(bundle)
            return bundle

        tracked.build.side_effect = build
        ocr = Mock()
        ocr.read.side_effect = lambda preview, page, timeout: tuple(
            TextObservation(
                text=placeholder_text(o.entity_id), region=o.region, origin="ocr", confidence=0.99
            )
            for o in bundles[-1].manifest.occurrences
            if o.region.page == page.number
        )
        verifier = LocalSanitizedVerifier(tracked, ocr)
        builder = LocalSanitizedBundleBuilder(
            sources=sources, authority=review, processor=tracked, verifier=verifier
        )
        mappings = LocalMappingService(
            store=map_store, cipher=MappingCipher(keys), authority=review, verifier=verifier
        )
        trackers.append(tracked)
        return PrivacyBridgeSession(
            principal_id=principal,
            source_ids=allowed,
            human=human,
            review=review,
            builder=builder,
            verifier=verifier,
            mappings=mappings,
            key_reference=key,
            sink=sink,
            sources=sources,
            results=resolver,
        )

    owner, other = session(principal_id, frozenset({source_id})), session("other", frozenset())
    token, other_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    config = PrivacyBridgeConfig(
        workspace=tmp_path,
        origin=origin,
        authority=authority,
        sources={
            source_id: PrivacyBridgeSource(tmp_path / "synthetic-original.pdf", source, "b" * 64)
        },
    )
    app = create_privacy_bridge(config, {token: owner, other_token: other})

    def close():
        map_store.close()
        keys.lock()

    return SyntheticPrivacyBridge(
        app=app,
        config=config,
        token=token,
        owner=owner,
        other_token=other_token,
        source_id=source_id,
        source=source,
        keys=keys,
        sink=sink,
        maps=map_store,
        resolver=resolver,
        path=tmp_path,
        tracker=trackers[0],
        close=close,
    )
