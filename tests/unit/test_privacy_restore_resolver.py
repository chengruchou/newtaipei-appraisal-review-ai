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
