"""Real loopback manual review, actual PDF bytes, explicitly synthetic OCR readings."""

import hashlib
from unittest.mock import Mock

import pymupdf
from test_privacy_bridge import bridge as bridge
from test_privacy_restore_resolver import published

from appraisal_review.domain.privacy_models import placeholder_text
from appraisal_review.domain.privacy_scan import TextObservation


def pending(bridge):
    coordinator, state, processor, _ = published(bridge)
    target = state.value.forms.attestation.claims.manifest.occurrences[0]
    before_png = processor.render(
        state.value.pdf, state.value.forms.attestation.claims.manifest.pages
    )[0].preview.png
    before = TextObservation(
        text=placeholder_text(target.entity_id), region=target.region, origin="ocr", confidence=0.0
    )
    after = before.model_copy(update={"text": "Synthetic measured crop", "confidence": 0.42})
    coordinator.ocr.read = lambda preview, page, timeout: (
        before if preview.png == before_png else after,
    )
    coordinator.ocr_identity = lambda: "a" * 64
    bridge.owner.results = coordinator
    writer = Mock(wraps=processor.write)
    processor.write = writer
    response = bridge.client.post(f"/restore/{state.id}", json={})
    assert response.status_code == 409
    assert response.json()["code"] == "local_privacy_review_required"
    identifier = response.json()["review_id"]
    route = f"/restore-reviews/{identifier}"
    view = bridge.client.get(route).json()
    assert view["observations"][0]["confidence"] == 0.0
    writer.assert_not_called()
    return state, route, writer


def confirm_stage(bridge, route):
    view = bridge.client.get(route).json()
    for item in view["items"]:
        page = bridge.client.get(f"{route}/pages/{item['page']}")
        assert page.status_code == 200 and page.content.startswith(b"\x89PNG")
        response = bridge.client.post(
            f"{route}/items/{item['item_id']}/confirm",
            json={
                "review_digest": view["review_digest"],
                "page_image_sha256": hashlib.sha256(page.content).hexdigest(),
                "reading": item["expected_text"] or "Synthetic crop explicitly reviewed",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["observations"] == view["observations"]
    return response.json()


def test_http_two_stage_review_download_replay_and_revocation(bridge):
    state, route, writer = pending(bridge)
    original = bridge.config.sources[bridge.source_id].path.read_bytes()
    downloads = {p: p.read_bytes() for p in bridge.path.glob("authorized-download-*/published.pdf")}
    assert len(downloads) == 1
    first = confirm_stage(bridge, route)
    assert len(first["receipts"]) == 1
    response = bridge.client.post(f"/restore/{state.id}", json={})
    assert response.status_code == 409
    assert response.json()["code"] == "local_privacy_review_required"
    writer.assert_called_once()
    assert not list(bridge.path.glob("restored-*/final-local.pdf"))
    assert bridge.client.get(route).json()["stage"] == "restored"
    assert (
        bridge.client.get(route + "/stages/published").json()["observations"]
        == first["observations"]
    )
    second = confirm_stage(bridge, route)
    assert len(second["receipts"]) == 2
    response = bridge.client.post(f"/restore/{state.id}", json={})
    assert response.status_code == 200, response.text
    completed = response.json()
    assert len(completed["ocr_review_receipts"]) == 2
    route_download = f"/restored/{completed['local_id']}"
    download = bridge.client.get(route_download)
    assert download.status_code == 200
    assert hashlib.sha256(download.content).hexdigest() == completed["manifest"]["final_digest"]
    with pymupdf.open(stream=download.content, filetype="pdf") as final:
        assert len(final) == 1
    assert bridge.client.post(f"/restore/{state.id}", json={}).json() == completed
    writer.assert_called_once()
    assert len(list(bridge.path.glob("restored-*/final-local.pdf"))) == 1
    assert bridge.config.sources[bridge.source_id].path.read_bytes() == original
    assert all(path.read_bytes() == content for path, content in downloads.items())
    state.active = False
    for request_path in (route, route_download, route + "/pages/1"):
        assert bridge.client.get(request_path).status_code == 409
    assert bridge.client.post(f"/restore/{state.id}", json={}).status_code == 409


def test_receipt_from_another_session_and_unviewed_region_cannot_authorize(bridge):
    state, route, writer = pending(bridge)
    view = bridge.client.get(route).json()
    item = view["items"][0]
    command = {
        "review_digest": view["review_digest"],
        "page_image_sha256": view["pages"][0]["image_sha256"],
        "reading": item["expected_text"],
    }
    assert (
        bridge.client.post(f"{route}/items/{item['item_id']}/confirm", json=command).status_code
        == 409
    )
    assert (
        bridge.client.get(
            route, headers={"Authorization": f"Bearer {bridge.other_token}"}
        ).status_code
        == 409
    )
    assert bridge.client.post(f"/restore/{state.id}", json={}).status_code == 409
    writer.assert_not_called()
    assert not list(bridge.path.glob("restored-*/final-local.pdf"))
