"""Real C2/PDF/maps with an explicit revocable publication callback; no OCR claim."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pymupdf
import pytest
from test_privacy_bridge import bridge as bridge
from test_privacy_document_sink import exact_preview, sink_for

from appraisal_review.adapters.local.privacy.refill import IsolatedPrivacyRefillProcessor
from appraisal_review.adapters.local.privacy_restore_resolver import (
    LocalRestoreCoordinator,
    RestorePublication,
)
from appraisal_review.domain.artifact_publication import (
    ArtifactKey,
    PublishedArtifact,
    SourceVersion,
)
from appraisal_review.domain.document_transfer import digest_bytes
from appraisal_review.domain.service_contracts import RevisionReference, RunReference


def published(bridge, *, damaged=False):
    documents, principal, sink, _ = sink_for(bridge)
    base, _, pdf = exact_preview(bridge)
    assert bridge.client.post(base + "/transfer", json={}).status_code == 200
    forms = sink.receipts[0]
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        document[0].insert_text((30, 180), "Published calculation 9.75%", fontsize=10)
        if damaged:
            token = forms.attestation.claims.manifest.occurrences[0].region
            x0, y0, x1, y1 = token.bbox
            document[0].draw_rect(pymupdf.Rect(x0, 400 - y1, x1, 400 - y0), fill=(1, 1, 1))
        content = document.tobytes()
    run = RunReference(
        run_id=uuid4(),
        attempt_id=uuid4(),
        revision=RevisionReference(
            case_id=forms.reference.case_id,
            revision_id=str(uuid4()),
            material_digest="b" * 64,
        ),
    )
    identifier = uuid4()
    artifact = PublishedArtifact(
        artifact_id=identifier,
        key=ArtifactKey.for_run(run, identifier).key(),
        content_hash=digest_bytes(content),
        size_bytes=len(content),
        object_version="1",
        writer_version="2",
        template_id="test-template",
        template_version="1",
        template_hash=digest_bytes(pdf),
        field_map_hash="c" * 64,
        field_ids=("rate",),
        page_count=1,
        source_versions=(
            SourceVersion(
                document_id=forms.reference.document_id,
                version=forms.reference.version,
                content_hash=forms.reference.content_hash,
            ),
        ),
    )
    state = SimpleNamespace(
        active=True,
        id=uuid4(),
        calls=0,
        value=RestorePublication(principal, uuid4(), run, artifact, content, forms),
    )

    def current(actor, result_id):
        state.calls += 1
        if not state.active or actor != principal.actor.actor_id or result_id != state.id:
            raise ValueError("Synthetic publication is not current or authorized")
        return state.value

    processor = IsolatedPrivacyRefillProcessor(bridge.path)
    ocr = Mock()
    ocr.read.side_effect = AssertionError("Plan construction must not invent OCR")
    coordinator = LocalRestoreCoordinator(
        workspace=bridge.path,
        session=bridge.owner,
        documents=documents,
        publication=current,
        processor=processor,
        ocr=ocr,
    )
    return coordinator, state, processor, pdf


def test_changed_published_placeholder_pixels_cannot_authorize_restore(bridge):
    coordinator, state, _, _ = published(bridge, damaged=True)
    with pytest.raises(ValueError):
        coordinator.resolve(bridge.owner.principal_id, state.id)
    assert not list(bridge.path.glob("authorized-download-*"))


def test_exact_map_plan_and_real_original_crop_preserve_cloud_pixels(bridge):
    coordinator, state, processor, template = published(bridge)
    original = bridge.config.sources[bridge.source_id].path.read_bytes()
    result = coordinator.resolve(bridge.owner.principal_id, state.id)
    mapping = bridge.owner.mappings.read(bridge.owner._maps[result.plan.map_id])
    assert result.plan.document_id == mapping.command.source.document_id
    assert str(result.plan.document_id) != state.value.forms.reference.document_id
    assert {f.occurrence_id for f in result.plan.fields} == {
        o.occurrence_id for o in mapping.manifest.occurrences
    }
    assert all(f.operation == "restore_original_crop" for f in result.plan.fields)
    assert result.download_path.read_bytes() == state.value.pdf
    assert result.authority.permits(result.plan)
    output = processor.write(mapping, result.plan, result.publisher.current(result.plan), original)
    before = processor.render(state.value.pdf, result.plan.pages)
    after = processor.render(output, result.plan.pages)
    # Full-page rendering avoids independent clipped glyph rounding and checks
    # the entire protected calculation page, not just one text rectangle.
    for original_page, final_page in zip(before, after, strict=True):
        a, b = pymupdf.Pixmap(original_page.preview.png), pymupdf.Pixmap(final_page.preview.png)
        assert (a.width, a.height, a.n) == (b.width, b.height, b.n)
        left_width = int(mapping.command.source.pages[0].width * 2)
        old_pixels, new_pixels = a.samples, b.samples
        for row in range(a.height):
            start = row * a.width * 3
            assert (
                old_pixels[start : start + left_width * 3]
                == new_pixels[start : start + left_width * 3]
            )
    assert bridge.config.sources[bridge.source_id].path.read_bytes() == original
    assert (
        coordinator.documents.read(state.value.principal, state.value.forms.reference).content
        == template
    )
    assert result.download_path.read_bytes() == state.value.pdf
    assert coordinator.resolve(bridge.owner.principal_id, state.id) is result
    state.active = False
    assert not result.authority.permits(result.plan)
    with pytest.raises(ValueError):
        result.publisher.current(result.plan)


@pytest.mark.parametrize("fault", ["map", "template", "run", "actor", "content"])
def test_wrong_mapping_publication_or_caller_never_creates_download(bridge, fault):
    coordinator, state, _, _ = published(bridge)
    actor = bridge.owner.principal_id
    if fault == "map":
        bridge.owner._maps.clear()
    elif fault == "template":
        state.value = replace(
            state.value,
            artifact=state.value.artifact.model_copy(update={"template_hash": "d" * 64}),
        )
    elif fault == "run":
        state.value = replace(
            state.value, run=state.value.run.model_copy(update={"run_id": uuid4()})
        )
    elif fault == "actor":
        actor = str(uuid4())
    else:
        state.value = replace(state.value, pdf=state.value.pdf + b"tampered")
    with pytest.raises(ValueError):
        coordinator.resolve(actor, state.id)
    assert not list(bridge.path.glob("authorized-download-*"))


def restored_download(bridge, monkeypatch):
    """Real HTTP/C2/maps/PDF creation with the existing explicit OCR double."""
    from test_privacy_refill import SyntheticRefillOCR

    from appraisal_review.domain.privacy_refill import RefillTarget

    coordinator, state, processor, _ = published(bridge)
    occurrence = state.value.forms.attestation.claims.manifest.occurrences[0]
    coordinator.ocr = SyntheticRefillOCR(
        RefillTarget(
            occurrence_id=occurrence.occurrence_id,
            entity_id=occurrence.entity_id,
            output_field_id=occurrence.occurrence_id,
            region=occurrence.region,
        )
    )
    writer = Mock(wraps=processor.write)
    monkeypatch.setattr(processor, "write", writer)
    bridge.owner.results = coordinator
    response = bridge.client.post(f"/restore/{state.id}", json={})
    assert response.status_code == 200, response.text
    local_id = response.json()["local_id"]
    return SimpleNamespace(
        coordinator=coordinator,
        state=state,
        writer=writer,
        route=f"/restored/{local_id}",
        path=bridge.path / f"restored-{local_id}" / "final-local.pdf",
        digest=response.json()["manifest"]["final_digest"],
    )


def change_download_authority(download, bridge, fault):
    if fault == "revoked":
        download.state.active = False
    elif fault == "stale-run":
        value = download.state.value
        run = value.run.model_copy(update={"run_id": uuid4(), "attempt_id": uuid4()})
        download.state.value = replace(
            value,
            run=run,
            artifact=value.artifact.model_copy(
                update={"key": ArtifactKey.for_run(run, value.artifact.artifact_id).key()}
            ),
        )
    elif fault == "locked-map":
        bridge.keys.lock()
    else:
        raise AssertionError("Unknown authority regression")


@pytest.mark.parametrize("fault", ["revoked", "stale-run", "locked-map"])
@pytest.mark.parametrize("moment", ["before", "during"])
def test_cached_restored_download_rechecks_current_authority(bridge, monkeypatch, fault, moment):
    from appraisal_review.adapters.local import privacy_bridge

    download = restored_download(bridge, monkeypatch)
    read = privacy_bridge._read_bound
    reads = []

    def guarded_read(path, workspace, digest):
        data = read(path, workspace, digest)
        if path == download.path:
            reads.append(path)
            if moment == "during":
                change_download_authority(download, bridge, fault)
        return data

    monkeypatch.setattr(privacy_bridge, "_read_bound", guarded_read)
    if moment == "before":
        change_download_authority(download, bridge, fault)
    response = bridge.client.get(download.route)
    assert response.status_code == 409, response.text
    assert response.headers["content-type"].startswith("application/json")
    assert not response.content.startswith(b"%PDF")
    assert len(reads) == (0 if moment == "before" else 1)
    download.writer.assert_called_once()
    assert download.coordinator.ocr.calls == 2
    download.coordinator.documents.ingest.assert_called_once()


def test_cached_restored_download_remains_available_without_new_restore(bridge, monkeypatch):
    download = restored_download(bridge, monkeypatch)
    original = bridge.config.sources[bridge.source_id].path.read_bytes()
    previous_calls = download.state.calls
    for _ in range(2):
        response = bridge.client.get(download.route)
        assert response.status_code == 200, response.text
        assert digest_bytes(response.content) == download.digest
        assert response.content == download.path.read_bytes()
        assert download.state.calls >= previous_calls + 2
        previous_calls = download.state.calls
    assert bridge.config.sources[bridge.source_id].path.read_bytes() == original
    download.writer.assert_called_once()
    assert download.coordinator.ocr.calls == 2
    download.coordinator.documents.ingest.assert_called_once()
