"""Real loopback HTTP, PDF and ciphertext checks; synthetic OCR/backend authority only."""

import hashlib
import socket
import threading
import time
from uuid import uuid4

import httpx
import pymupdf
import pytest
import uvicorn
from privacy_bridge_fixture import build_synthetic_privacy_bridge
from test_privacy_refill import (
    SyntheticPlanAuthority,
    SyntheticPublisher,
    SyntheticRefillOCR,
)

from appraisal_review.adapters.local.privacy.refill import IsolatedPrivacyRefillProcessor
from appraisal_review.adapters.local.privacy_bridge import (
    PrivacyBridgeRestore,
)
from appraisal_review.domain.privacy_models import (
    PrivacyPage,
    PrivacyRegion,
    RehydrationField,
    RehydrationPlan,
    placeholder_text,
)
from appraisal_review.domain.privacy_refill import (
    PublishedRefillArtifact,
    PublishedRefillDescriptor,
    RefillTarget,
)


@pytest.fixture
def bridge(tmp_path):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    state = build_synthetic_privacy_bridge(tmp_path, f"127.0.0.1:{port}", "http://127.0.0.1:5173")
    server = uvicorn.Server(
        uvicorn.Config(
            state.app,
            host="127.0.0.1",
            port=port,
            proxy_headers=False,
            access_log=False,
            log_level="critical",
        )
    )
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}/local-privacy",
            timeout=30,
            headers={"Authorization": f"Bearer {state.token}", "Origin": state.config.origin},
            trust_env=False,
        ) as client:
            state.client = client
            yield state
    finally:
        server.should_exit = True
        thread.join(10)
        listener.close()
        state.close()
    assert not thread.is_alive()


def version(response):
    command = response.json()["command"]
    return {
        "case_id": command["source"]["case_id"],
        "snapshot_id": command["source"]["snapshot_id"],
        "revision": command["selection_revision"],
    }


def approved(bridge):
    client = bridge.client
    response = client.post(f"/sources/{bridge.source_id}/open", json={})
    assert response.status_code == 200, response.text
    assert client.post("/exports/preview", json={}).status_code == 409
    # Reading the PNG is necessary; a review click before viewing is rejected.
    assert client.post("/review/pages", json={**version(response), "page": 1}).status_code == 409
    image = client.get("/pages/1")
    assert image.status_code == 200 and image.content.startswith(b"\x89PNG")
    response = client.post("/review/pages", json={**version(response), "page": 1})
    assert response.status_code == 200, response.text
    digest = response.headers["X-Privacy-Review-Digest"]
    assert "X-Privacy-Review-Digest" in response.headers["Access-Control-Expose-Headers"]
    response = client.post("/review/confirm", json={**version(response), "review_digest": digest})
    assert response.status_code == 200 and response.json()["state"] == "confirmed"
    return response


def prepared(bridge):
    approved(bridge)
    response = bridge.client.post("/exports/preview", json={"reviewer_text": "Check 測試姓名"})
    assert response.status_code == 200, response.text
    preview = response.json()
    assert "測試姓名" not in preview["reviewer_text"]
    base = f"/exports/{preview['preview_id']}"
    assert bridge.client.post(base + "/transfer", json={}).status_code == 409
    assert (
        bridge.client.post(
            base + "/confirm", json={"payload_digest": preview["payload_digest"]}
        ).status_code
        == 409
    )
    pdf = bridge.client.get(base + "/pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert (
        bridge.client.post(base + "/confirm", json={"payload_digest": "0" * 64}).status_code == 409
    )
    assert (
        bridge.client.post(
            base + "/confirm", json={"payload_digest": preview["payload_digest"]}
        ).status_code
        == 200
    )
    return preview, pdf.content


@pytest.mark.parametrize(
    "violation", ["origin", "host", "forwarded", "token", "no-origin", "duplicate-host"]
)
def test_real_http_rejects_cross_site_rebinding_and_invalid_session(bridge, violation):
    client = bridge.client
    headers = dict(client.headers)
    if violation == "origin":
        headers["origin"] = "https://untrusted.invalid"
    elif violation == "host":
        headers["host"] = "rebinding.invalid"
    elif violation == "forwarded":
        headers["x-forwarded-for"] = "127.0.0.1"
    elif violation == "token":
        headers["authorization"] = "Bearer invalid"
    elif violation == "no-origin":
        headers.pop("origin")
    else:
        # Raw socket sends duplicate Host; h11 itself may reject before application admission.
        with socket.create_connection(
            ("127.0.0.1", int(bridge.config.authority.split(":")[1]))
        ) as sock:
            sock.sendall(
                b"GET /local-privacy/sources HTTP/1.1\r\nHost: localhost\r\n"
                b"Host: rebinding.invalid\r\nConnection: close\r\n\r\n"
            )
            assert b"400" in sock.recv(1024)
        bridge.sink.accept.assert_not_called()
        return
    request = client.build_request("GET", "/sources", headers=headers)
    if violation == "no-origin":
        del request.headers["origin"]
    response = client.send(request)
    assert response.status_code in {401, 403}
    bridge.sink.accept.assert_not_called()


def test_preflight_is_explicit_and_has_no_session_or_file_side_effect(bridge):
    response = bridge.client.options(
        "/sources",
        headers={
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert response.status_code == 204
    assert response.headers["Access-Control-Allow-Origin"] == bridge.config.origin
    assert response.headers["Cache-Control"] == "no-store"
    bridge.tracker.build.assert_not_called()


@pytest.mark.parametrize(
    "body",
    [
        {"path": "../../private.pdf"},
        {"url": "file:///private.pdf"},
        {"principal_id": "owner", "role": "admin"},
    ],
)
def test_body_cannot_select_paths_urls_or_principals(bridge, body):
    response = bridge.client.post(f"/sources/{bridge.source_id}/open", json=body)
    assert response.status_code == 400
    assert "private.pdf" not in response.text
    bridge.sink.accept.assert_not_called()


def test_unknown_and_foreign_source_handles_are_denied(bridge):
    assert bridge.client.post(f"/sources/{uuid4()}/open", json={}).status_code == 404
    response = bridge.client.post(
        f"/sources/{bridge.source_id}/open",
        json={},
        headers={"Authorization": f"Bearer {bridge.other_token}"},
    )
    assert response.status_code == 404
    assert (
        bridge.client.get(
            "/review", headers={"Authorization": f"Bearer {bridge.other_token}"}
        ).status_code
        == 409
    )


@pytest.mark.parametrize("failure", ["key", "store", "source"])
def test_mapping_or_source_failure_has_zero_http_transfer(bridge, failure):
    preview, _ = prepared(bridge)
    if failure == "key":
        bridge.keys.lock()
    elif failure == "store":
        bridge.maps.close()
    else:
        (bridge.path / "synthetic-original.pdf").write_bytes(b"replaced")
    response = bridge.client.post(f"/exports/{preview['preview_id']}/transfer", json={})
    assert response.status_code == 409
    bridge.sink.accept.assert_not_called()


def test_edit_invalidates_exact_preview_and_keeps_known_text(bridge):
    preview, _ = prepared(bridge)
    view = bridge.client.get("/review")
    selection = view.json()["command"]["selections"][0]
    candidate = selection["candidate"]
    response = bridge.client.post(
        "/review/edit",
        json={
            **version(view),
            "candidate_id": candidate["candidate_id"],
            "region": candidate["region"],
            "category": "case_contact",
            "entity_id": selection["entity_id"],
        },
    )
    assert response.status_code == 200
    assert response.json()["command"]["selections"][0]["candidate"]["raw_text"] == "測試姓名"
    assert (
        bridge.client.post(f"/exports/{preview['preview_id']}/transfer", json={}).status_code == 409
    )
    bridge.sink.accept.assert_not_called()


def published_restore(bridge, manifest):
    handle = next(iter(bridge.owner._maps.values()))
    occurrence = manifest.occurrences[0]
    target = RefillTarget(
        occurrence_id=occurrence.occurrence_id,
        entity_id=occurrence.entity_id,
        output_field_id=uuid4(),
        region=PrivacyRegion(page=1, bbox=(20.0, 290.0, 240.0, 350.0)),
        present=True,
    )
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=500, height=400)
        page.insert_text((25, 75), placeholder_text(occurrence.entity_id), fontsize=9)
        page.insert_text((25, 200), "Cloud total 123456.78", fontsize=12)
        cloud = pdf.tobytes()
    download = bridge.path / "authorized-download.pdf"
    download.write_bytes(cloud)
    descriptor = PublishedRefillDescriptor(
        case_id=manifest.case_id,
        document_id=manifest.document_id,
        run_id=uuid4(),
        revision_id=uuid4(),
        artifact_digest=hashlib.sha256(cloud).hexdigest(),
        base_sanitized_digest=manifest.sanitized_digest,
        template_digest="d" * 64,
        pages=(PrivacyPage(number=1, width=500.0, height=400.0),),
        targets=(target,),
    )
    plan = RehydrationPlan(
        case_id=manifest.case_id,
        document_id=manifest.document_id,
        map_id=handle.map_id,
        run_id=descriptor.run_id,
        revision_id=descriptor.revision_id,
        artifact_digest=descriptor.artifact_digest,
        template_digest=descriptor.template_digest,
        pages=descriptor.pages,
        fields=(
            RehydrationField(
                occurrence_id=occurrence.occurrence_id,
                entity_id=occurrence.entity_id,
                output_field_id=target.output_field_id,
                operation="restore_text",
                destination=target.region,
            ),
        ),
    )
    publisher = SyntheticPublisher(PublishedRefillArtifact(descriptor, cloud), plan)
    result = PrivacyBridgeRestore(
        plan,
        publisher,
        SyntheticPlanAuthority(plan),
        IsolatedPrivacyRefillProcessor(
            bridge.path, font_digest=hashlib.sha256(pymupdf.Font("cjk").buffer).hexdigest()
        ),
        SyntheticRefillOCR(target),
        download,
    )
    identifier = uuid4()

    def resolve(principal, result_id):
        if principal != bridge.owner.principal_id or result_id != identifier:
            raise ValueError("Synthetic unauthorized result")
        return result

    bridge.resolver.resolve.side_effect = resolve
    return identifier, result, cloud


def test_real_http_exact_export_and_authorized_pdf_restore(bridge):
    from appraisal_review.domain.privacy_models import PrivacyManifest

    original = (bridge.path / "synthetic-original.pdf").read_bytes()
    preview, shown_pdf = prepared(bridge)
    base = f"/exports/{preview['preview_id']}"
    response = bridge.client.post(base + "/transfer", json={})
    assert response.status_code == 200, response.text
    bridge.tracker.build.assert_called_once()
    bridge.sink.accept.assert_called_once()
    payload = bridge.sink.accept.call_args.args[0]
    assert payload.pdf == shown_pdf
    assert hashlib.sha256(payload.pdf).hexdigest() == preview["manifest"]["sanitized_digest"]
    assert bridge.source.source_digest not in payload.manifest_json
    assert str(bridge.source.snapshot_id) not in payload.manifest_json
    manifest = PrivacyManifest.model_validate_json(payload.manifest_json)
    handle = next(iter(bridge.owner._maps.values()))
    assert bridge.owner.mappings.read(handle).manifest == manifest
    assert bridge.client.post(base + "/transfer", json={}).status_code == 409
    identifier, result, cloud = published_restore(bridge, manifest)
    response = bridge.client.post(f"/restore/{identifier}", json={})
    assert response.status_code == 200, response.text
    local = response.json()["local_id"]
    final = bridge.client.get(f"/restored/{local}")
    assert final.status_code == 200
    assert (
        bridge.client.get(
            f"/restored/{local}", headers={"Authorization": f"Bearer {bridge.other_token}"}
        ).status_code
        == 404
    )
    with (
        pymupdf.open(stream=final.content, filetype="pdf") as pdf,
        pymupdf.open(stream=cloud, filetype="pdf") as before,
    ):
        assert "測試姓名" in pdf[0].get_text()
        clip = pymupdf.Rect(15, 175, 360, 235)
        assert (
            pdf[0].get_pixmap(clip=clip, dpi=144).samples
            == before[0].get_pixmap(clip=clip, dpi=144).samples
        )
    assert result.download_path.read_bytes() == cloud
    assert (bridge.path / "synthetic-original.pdf").read_bytes() == original
    assert (bridge.path / f"restored-{local}" / "final-local.pdf").read_bytes() == final.content
    bridge.sink.accept.assert_called_once()


def test_ciphertext_readback_failure_blocks_actual_http_transfer(bridge, monkeypatch):
    import json

    preview, _ = prepared(bridge)
    create = bridge.maps.create

    def tamper(envelope):
        create(envelope)
        path = next((bridge.path / "maps").glob("*.map"))
        record = json.loads(path.read_bytes())
        record["ciphertext_hex"] = "00" + record["ciphertext_hex"][2:]
        path.write_text(json.dumps(record))

    monkeypatch.setattr(bridge.maps, "create", tamper)
    response = bridge.client.post(f"/exports/{preview['preview_id']}/transfer", json={})
    assert response.status_code == 409
    bridge.sink.accept.assert_not_called()
    assert len(bridge.owner._maps) == 1


def test_unknown_sink_outcome_is_not_retried_and_errors_are_value_free(bridge, caplog):
    preview, _ = prepared(bridge)
    bridge.sink.accept.side_effect = RuntimeError("SYNTHETIC-PRIVATE-ERROR")
    base = f"/exports/{preview['preview_id']}/transfer"
    first = bridge.client.post(base, json={})
    assert first.status_code == 409
    assert "SYNTHETIC-PRIVATE-ERROR" not in first.text + caplog.text
    assert bridge.client.post(base, json={}).status_code == 409
    bridge.sink.accept.assert_called_once()
    handle = next(iter(bridge.owner._maps.values()))
    assert (
        bridge.owner.mappings.read(handle).manifest.sanitized_digest
        == (preview["manifest"]["sanitized_digest"])
    )


def test_expired_and_foreign_preview_cannot_be_confirmed_or_transferred(bridge):
    preview, _ = prepared(bridge)
    base = f"/exports/{preview['preview_id']}"
    assert (
        bridge.client.get(
            base + "/pdf",
            headers={
                "Authorization": f"Bearer {bridge.other_token}",
            },
        ).status_code
        == 409
    )
    bridge.owner._preview.deadline = 0
    assert bridge.client.post(base + "/transfer", json={}).status_code == 409
    assert (
        bridge.client.post(
            base + "/confirm",
            json={
                "payload_digest": preview["payload_digest"],
            },
        ).status_code
        == 409
    )
    bridge.sink.accept.assert_not_called()


@pytest.mark.parametrize("violation", ["body-limit", "query", "content-type", "extra-confirm"])
def test_http_input_surface_is_bounded_and_allowlisted(bridge, violation):
    if violation == "body-limit":
        response = bridge.client.post(
            "/exports/preview", content=b"x" * 65537, headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 413
    elif violation == "query":
        response = bridge.client.get("/sources?path=../../private.pdf")
        assert response.status_code == 403
    elif violation == "content-type":
        response = bridge.client.post(
            f"/sources/{bridge.source_id}/open",
            content=b"{}",
            headers={"Content-Type": "text/plain"},
        )
        assert response.status_code == 415
    else:
        response = bridge.client.post("/review/confirm", json={"approved": True, "role": "owner"})
        assert response.status_code == 409
    bridge.tracker.build.assert_not_called()
    bridge.sink.accept.assert_not_called()


@pytest.mark.parametrize("violation", ["revoked", "wrong-download", "outside", "output-symlink"])
def test_restore_protects_original_download_and_workspace(bridge, monkeypatch, violation):
    from dataclasses import replace

    from appraisal_review.adapters.local import privacy_bridge
    from appraisal_review.domain.privacy_models import PrivacyManifest

    preview, _ = prepared(bridge)
    response = bridge.client.post(f"/exports/{preview['preview_id']}/transfer", json={})
    assert response.status_code == 200
    payload = bridge.sink.accept.call_args.args[0]
    identifier, result, cloud = published_restore(
        bridge, PrivacyManifest.model_validate_json(payload.manifest_json)
    )
    original_path = bridge.path / "synthetic-original.pdf"
    original = original_path.read_bytes()
    if violation == "revoked":
        result.publisher.active = False
    elif violation == "wrong-download":
        result = replace(result, download_path=original_path)
    elif violation == "outside":
        outside = bridge.path.parent / f"outside-{uuid4()}.pdf"
        outside.write_bytes(cloud)
        result = replace(result, download_path=outside)
    else:
        local_id = uuid4()
        (bridge.path / f"restored-{local_id}").symlink_to(bridge.path, target_is_directory=True)
        monkeypatch.setattr(privacy_bridge, "uuid4", lambda: local_id)
    bridge.resolver.resolve.side_effect = None
    bridge.resolver.resolve.return_value = result
    response = bridge.client.post(f"/restore/{identifier}", json={})
    assert response.status_code == 409, response.text
    assert original_path.read_bytes() == original
    assert (bridge.path / "authorized-download.pdf").read_bytes() == cloud
    assert not bridge.owner._restored
    bridge.sink.accept.assert_called_once()


def test_cancelled_request_keeps_session_lock_until_worker_finishes():
    import asyncio

    from appraisal_review.adapters.local.privacy_bridge import _run_sync

    started, finish = threading.Event(), threading.Event()

    def operation():
        started.set()
        assert finish.wait(5)

    async def exercise():
        lock = asyncio.Lock()

        async def request():
            async with lock:
                await _run_sync(operation)

        task = asyncio.create_task(request())
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        assert lock.locked() and not task.done()
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not lock.locked()

    asyncio.run(exercise())
