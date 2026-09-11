"""Real signed C2/SQLite admission from the loopback privacy gate; no AWS calls."""

import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from unittest.mock import Mock
from uuid import uuid4

import pymupdf
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_privacy_bridge import approved
from test_privacy_bridge import bridge as bridge

from appraisal_review.adapters.local.document_authority import (
    ConfiguredDocumentAuthorization,
    DocumentGrant,
    Ed25519ExportVerifier,
    TrustedExportKey,
)
from appraisal_review.adapters.local.document_storage import SQLiteDocumentStorage
from appraisal_review.adapters.local.privacy_document_sink import CloudExportSink
from appraisal_review.application.document_transfer import DocumentTransferService
from appraisal_review.application.service_guards import Principal
from appraisal_review.domain.document_transfer import DocumentFault, DocumentOperation
from appraisal_review.domain.privacy_export import PrivacyExportPayload
from appraisal_review.domain.privacy_models import PrivacyManifest
from appraisal_review.domain.service_contracts import ActorReference, Permission


def documents_for(bridge):
    actor, key_id, key = uuid4(), uuid4(), Ed25519PrivateKey.generate()
    case = bridge.source.case_id
    principal = Principal(
        ActorReference(actor_id=str(actor), kind="human"),
        frozenset({str(case)}),
        frozenset({Permission.REVIEW}),
    )
    bridge.owner.principal_id = str(actor)
    bridge.owner.human.principal_id = str(actor)
    storage = SQLiteDocumentStorage(bridge.path / "c2.sqlite")
    verifier = Ed25519ExportVerifier(
        (TrustedExportKey(key_id, key.public_key(), actor, frozenset({case})),)
    )
    authorization = ConfiguredDocumentAuthorization(
        (
            DocumentGrant(
                actor, case, frozenset({"criteria", "forms"}), frozenset(DocumentOperation)
            ),
        )
    )
    service = DocumentTransferService(storage, authorization, verifier, storage)
    service.ingest = Mock(wraps=service.ingest)
    return service, principal, key, key_id


def sink_for(bridge, *, purpose="forms", callback=None):
    service, principal, key, key_id = documents_for(bridge)
    callback = callback if callback is not None else Mock()
    sink = CloudExportSink(
        service,
        principal,
        key,
        key_id,
        {(bridge.source.case_id, bridge.source.document_id): purpose},
        on_admitted=callback,
        local_rehearsal=True,
    )
    bridge.owner.sink = sink
    return service, principal, sink, callback


def exact_preview(bridge, *, text=None):
    approved(bridge)
    response = bridge.client.post("/exports/preview", json={"reviewer_text": text})
    assert response.status_code == 200, response.text
    preview = response.json()
    base = f"/exports/{preview['preview_id']}"
    pdf = bridge.client.get(base + "/pdf")
    assert pdf.status_code == 200
    response = bridge.client.post(
        base + "/confirm", json={"payload_digest": preview["payload_digest"]}
    )
    assert response.status_code == 200
    return base, preview, pdf.content


@pytest.mark.parametrize("purpose", ["criteria", "forms"])
def test_actual_http_gate_signs_and_admits_exact_pdf_to_c2(bridge, purpose):
    service, principal, sink, callback = sink_for(bridge, purpose=purpose)
    base, preview, pdf = exact_preview(bridge)
    response = bridge.client.post(base + "/transfer", json={})
    assert response.status_code == 200, response.text
    service.ingest.assert_called_once()
    callback.assert_called_once()
    receipt = callback.call_args.args[0]
    assert sink.receipts == (receipt,)
    assert receipt.reference.purpose == purpose
    assert receipt.reference.document_id != str(bridge.source.document_id)
    assert receipt.reference.content_hash == preview["manifest"]["sanitized_digest"]
    assert receipt.attestation.claims.manifest.document_id == bridge.source.document_id
    service.verifier.verify(receipt.attestation, principal)
    readback = service.read(principal, receipt.reference)
    assert readback.content == pdf and readback.metadata == receipt
    with pymupdf.open(stream=readback.content, filetype="pdf") as reopened:
        assert len(reopened) == len(preview["manifest"]["pages"])
    bridge.tracker.build.assert_called_once()
    with closing(sqlite3.connect(service.storage.database)) as connection, connection:
        labels = [json.loads(row[0]) for row in connection.execute("SELECT labels FROM documents")]
    encoded = json.dumps(labels)
    assert "測試姓名" not in encoded and bridge.source.source_digest not in encoded
    assert "synthetic-original.pdf" not in encoded and str(bridge.source.snapshot_id) not in encoded
    assert bridge.client.post(base + "/transfer", json={}).status_code == 409
    service.ingest.assert_called_once()


def test_wrong_signing_key_is_rejected_before_any_c2_ingestion(bridge):
    service, principal, _, key_id = documents_for(bridge)
    with pytest.raises(DocumentFault):
        CloudExportSink(
            service,
            principal,
            Ed25519PrivateKey.generate(),
            key_id,
            {(bridge.source.case_id, bridge.source.document_id): "forms"},
            on_admitted=Mock(),
        )
    service.ingest.assert_not_called()
    with closing(sqlite3.connect(service.storage.database)) as connection, connection:
        assert connection.execute("SELECT count(*) FROM documents").fetchone()[0] == 0


@pytest.mark.parametrize("violation", ["text", "unconfigured-source", "key-revoked"])
def test_unsupported_text_source_or_revoked_key_never_reaches_c2(bridge, violation):
    service, _, sink, callback = sink_for(bridge)
    base, _, _ = exact_preview(bridge, text="reviewed local text" if violation == "text" else None)
    if violation == "unconfigured-source":
        sink._purposes = {(bridge.source.case_id, uuid4()): "forms"}
    elif violation == "key-revoked":
        service.verifier.keys.clear()
    response = bridge.client.post(base + "/transfer", json={})
    assert response.status_code == 409
    service.ingest.assert_not_called()
    callback.assert_not_called()


def test_no_presenter_confirmation_or_payload_substitution_can_ingest(bridge):
    service, _, sink, callback = sink_for(bridge)
    _, preview, pdf = exact_preview(bridge)
    payload = PrivacyExportPayload(
        pdf, PrivacyManifest.model_validate_json(json.dumps(preview["manifest"])).model_dump_json()
    )
    presenter = Mock()
    presenter.confirm.return_value = False
    channel = sink.bind_confirmation(presenter)
    with pytest.raises(DocumentFault):
        channel.accept(payload)
    assert channel.confirm(payload) is False
    with pytest.raises(DocumentFault):
        channel.accept(payload)
    presenter.confirm.return_value = True
    channel = sink.bind_confirmation(presenter)
    assert channel.confirm(payload) is True
    with pytest.raises(DocumentFault):
        channel.accept(replace(payload, pdf=b"substituted"))
    service.ingest.assert_not_called()
    callback.assert_not_called()


def test_actual_pdf_parse_rejects_matching_digest_of_malformed_bytes(bridge):
    import hashlib

    service, _, sink, _ = sink_for(bridge)
    _, preview, _ = exact_preview(bridge)
    data = b"%PDF-1.7\nnot a document\n%%EOF"
    manifest = PrivacyManifest.model_validate_json(json.dumps(preview["manifest"]))
    manifest = manifest.model_copy(
        update={"sanitized_digest": hashlib.sha256(data).hexdigest(), "byte_size": len(data)}
    )
    presenter = Mock()
    channel = sink.bind_confirmation(presenter)
    with pytest.raises(DocumentFault):
        channel.confirm(PrivacyExportPayload(data, manifest.model_dump_json()))
    presenter.confirm.assert_not_called()
    service.ingest.assert_not_called()


def test_callback_failure_retains_committed_receipt_without_automatic_retry(bridge, caplog):
    callback = Mock(side_effect=RuntimeError("SYNTHETIC-PRIVATE-CATALOG-ERROR"))
    service, principal, sink, _ = sink_for(bridge, callback=callback)
    base, _, pdf = exact_preview(bridge)
    response = bridge.client.post(base + "/transfer", json={})
    assert response.status_code == 409
    assert "SYNTHETIC-PRIVATE-CATALOG-ERROR" not in response.text + caplog.text
    assert len(sink.receipts) == 1
    assert service.read(principal, sink.receipts[0].reference).content == pdf
    assert bridge.client.post(base + "/transfer", json={}).status_code == 409
    callback.assert_called_once()
    service.ingest.assert_called_once()


@pytest.mark.parametrize("failure", ["mapping-key", "mapping-store"])
def test_local_mapping_failure_makes_zero_real_c2_calls(bridge, failure):
    service, _, _, callback = sink_for(bridge)
    base, _, _ = exact_preview(bridge)
    if failure == "mapping-key":
        bridge.keys.lock()
    else:
        bridge.maps.close()
    assert bridge.client.post(base + "/transfer", json={}).status_code == 409
    service.ingest.assert_not_called()
    callback.assert_not_called()
    with closing(sqlite3.connect(service.storage.database)) as connection, connection:
        assert connection.execute("SELECT count(*) FROM documents").fetchone()[0] == 0
