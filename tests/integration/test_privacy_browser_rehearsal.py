"""Real loopback paired privacy admission and new-source handoff; no browser claim."""

import asyncio
import importlib
import importlib.util
import json
import socket
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import uvicorn

from appraisal_review.adapters.document_extraction import DocumentSnapshotResolver
from appraisal_review.domain.document_transfer import canonical_bytes, digest_bytes
from appraisal_review.domain.extraction_contracts import (
    ExtractionContext,
    PageRequest,
    SanitizedSourceReference,
)
from appraisal_review.domain.service_contracts import RunReference


def load_privacy_scripts(monkeypatch):
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    loaded = {}
    for name in ("privacy_raster_fixture", "privacy_browser_rehearsal"):
        spec = importlib.util.spec_from_file_location(name, scripts / f"{name}.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        loaded[name] = module
    return loaded["privacy_browser_rehearsal"], loaded["privacy_raster_fixture"]


@pytest.fixture
def rehearsal_workspace():
    # The real launcher deliberately rejects workspaces outside this repository.
    # Exercise that same confinement with pytest's default temporary root as well.
    parent = Path(__file__).resolve().parents[2] / "artifacts"
    parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="privacy-test-", dir=parent) as directory:
        yield Path(directory)


@pytest.fixture
def rehearsal(monkeypatch, rehearsal_workspace):
    module, raster_helper = load_privacy_scripts(monkeypatch)
    original_prepare = raster_helper.material_from_admitted
    preparation_errors = []

    async def prepare(*args, **kwargs):
        try:
            return await original_prepare(*args, **kwargs)
        except Exception:
            preparation_errors.append(traceback.format_exc())
            raise

    monkeypatch.setattr(raster_helper, "material_from_admitted", prepare)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    authority = f"127.0.0.1:{listener.getsockname()[1]}"
    ready = []
    fixture = module.create_privacy_rehearsal(
        rehearsal_workspace / "private",
        authority=authority,
        origin="http://127.0.0.1:4174",
        on_ready=ready.append,
    )
    fixture.preparation_errors = preparation_errors
    server = uvicorn.Server(
        uvicorn.Config(
            fixture.app,
            host="127.0.0.1",
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
            base_url=f"http://{authority}/local-privacy",
            timeout=60,
            trust_env=False,
            headers={"Origin": fixture.config.origin, "Authorization": f"Bearer {fixture.token}"},
        ) as client:
            yield fixture, client, ready
    finally:
        server.should_exit = True
        thread.join(10)
        listener.close()
        fixture.close()
    assert not thread.is_alive()


def version(response):
    command = response.json()["command"]
    return {
        "case_id": command["source"]["case_id"],
        "snapshot_id": command["source"]["snapshot_id"],
        "revision": command["selection_revision"],
    }


def upload(client, source_id, *, transfer_status=200):
    response = client.post(f"/sources/{source_id}/open", json={})
    assert response.status_code == 200, response.text
    command = response.json()["command"]
    for page in command["source"]["pages"]:
        assert client.get(f"/pages/{page['number']}").content.startswith(b"\x89PNG")
        response = client.post("/review/pages", json={**version(response), "page": page["number"]})
        assert response.status_code == 200, response.text
    response = client.post(
        "/review/confirm",
        json={**version(response), "review_digest": response.headers["X-Privacy-Review-Digest"]},
    )
    assert response.status_code == 200, response.text
    response = client.post("/exports/preview", json={})
    assert response.status_code == 200, response.text
    preview = response.json()
    route = f"/exports/{preview['preview_id']}"
    assert client.post(route + "/transfer", json={}).status_code == 409
    pdf = client.get(route + "/pdf")
    assert pdf.status_code == 200
    assert digest_bytes(pdf.content) == preview["manifest"]["sanitized_digest"]
    assert (
        client.post(
            route + "/confirm", json={"payload_digest": preview["payload_digest"]}
        ).status_code
        == 200
    )
    response = client.post(route + "/transfer", json={})
    assert response.status_code == transfer_status, response.text
    return pdf.content


def test_paired_actual_http_c2_handoff_uses_only_new_raster_sources(rehearsal):
    fixture, client, ready = rehearsal
    assert fixture.snapshot is None and not fixture.sink.receipts and not ready
    originals = {
        s.snapshot.document_id: s.path.read_bytes() for s in fixture.config.sources.values()
    }
    exported = []
    for index, source_id in enumerate(fixture.source_ids):
        try:
            exported.append(upload(client, source_id))
        except AssertionError:
            assert not fixture.preparation_errors, "\n".join(fixture.preparation_errors)
            raise
        assert len(ready) == (1 if index == 1 else 0)
    assert fixture.snapshot == ready[0]
    snapshot = ready[0]
    assert {r.purpose for r in snapshot.revision.documents} == {"criteria", "forms"}
    assert set(r.document_id for r in snapshot.revision.documents).isdisjoint(map(str, originals))
    assert not any(
        page.has_text
        for source in snapshot.material.policy.registry.documents
        for page in source.pages
    )
    assert all(rule.rules.status == "candidate" for rule in snapshot.material.policy.rule_sets)
    for pair in snapshot.material.facts.pairs:
        for side in ("target", "comparable"):
            assert getattr(pair.pair, side).confidence == 0
            assert getattr(pair, f"{side}_reliability").confirmation is None
            for citation in getattr(pair, f"{side}_sources"):
                assert citation.excerpt == ""
                assert snapshot.material.policy.registry.resolves(citation)
    run = RunReference(run_id=uuid4(), revision=snapshot.revision.reference)
    fixture.documents.create_snapshot(fixture.principal, run, snapshot.revision)
    for receipt, expected in zip(fixture.sink.receipts, exported, strict=True):
        manifest = receipt.attestation.claims.manifest
        request = PageRequest(
            run=run,
            source=SanitizedSourceReference(
                document=receipt.reference,
                privacy_contract_version="privacy-v1",
                privacy_manifest_digest=digest_bytes(canonical_bytes(manifest)),
                page_count=len(manifest.pages),
            ),
            page=1,
            context=ExtractionContext(
                language="zh-Hant",
                task="propose_rules" if receipt.reference.purpose == "criteria" else "propose_case",
            ),
        )
        resolved = asyncio.run(
            DocumentSnapshotResolver(fixture.documents).resolve(fixture.principal, request)
        )
        assert resolved.content == expected
        assert resolved.source.content_hash == digest_bytes(expected)
    for source in fixture.config.sources.values():
        assert source.path.read_bytes() == originals[source.snapshot.document_id]
        assert source.snapshot.source_digest not in snapshot.material.model_dump_json()
    assert "合成機敏姓名" not in snapshot.material.model_dump_json()
    # C2 admission alone never creates an authorized published backend result.
    assert client.post(f"/restore/{uuid4()}", json={}).status_code == 409


def test_private_browser_config_no_approval_and_restore_requires_publisher(rehearsal):
    fixture, client, ready = rehearsal
    path = fixture.config.workspace / "browser-private.json"
    fixture.write_browser_fixture(path)
    config = json.loads(path.read_text())
    assert config["token"] == fixture.token
    assert config["source_ids"] == list(map(str, fixture.source_ids))
    assert path.stat().st_mode & 0o777 == 0o600
    assert fixture.token not in repr(fixture)
    assert "restore_result_id" not in config
    assert client.post(f"/restore/{uuid4()}", json={}).status_code == 409
    assert not ready and not fixture.sink.receipts
    with pytest.raises(ValueError):
        fixture.write_browser_fixture(fixture.config.workspace.parent / "escaped.json")


def test_mapping_failure_never_registers_case_or_calls_c2(rehearsal):
    fixture, client, ready = rehearsal
    source_id = fixture.source_ids[0]
    fixture.keys.lock()
    upload(client, source_id, transfer_status=409)
    assert not fixture.sink.receipts and fixture.snapshot is None and not ready


def test_authored_canary_cannot_be_omitted_from_synthetic_raster(rehearsal):
    fixture, client, ready = rehearsal
    response = client.post(f"/sources/{fixture.source_ids[0]}/open", json={})
    assert response.status_code == 200
    candidate = response.json()["command"]["selections"][0]["candidate"]
    response = client.post(
        "/review/remove",
        json={
            **version(response),
            "candidate_id": candidate["candidate_id"],
            "reason": "Synthetic negative regression deliberately removes the known canary",
        },
    )
    assert response.status_code == 200
    assert client.get("/pages/1").status_code == 200
    response = client.post("/review/pages", json={**version(response), "page": 1})
    assert response.status_code == 200
    response = client.post(
        "/review/confirm",
        json={
            **version(response),
            "review_digest": response.headers["X-Privacy-Review-Digest"],
        },
    )
    assert response.status_code == 200
    assert client.post("/exports/preview", json={}).status_code == 409
    assert not fixture.sink.receipts and not ready


def test_rehearsal_configured_listener_rejects_cross_site_host_and_body_paths(rehearsal):
    fixture, client, ready = rehearsal
    assert client.get("/sources", headers={"Origin": "http://untrusted.invalid"}).status_code == 403
    assert client.get("/sources", headers={"Host": "rebinding.invalid"}).status_code == 403
    assert client.post(f"/sources/{uuid4()}/open", json={}).status_code == 404
    assert (
        client.post(
            f"/sources/{fixture.source_ids[0]}/open",
            json={
                "path": "../originals/synthetic-forms.pdf",
            },
        ).status_code
        == 400
    )
    assert not fixture.sink.receipts and not ready
