"""Exact mapping lifecycle at the export boundary; no network or production keys."""

from unittest.mock import Mock
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from test_privacy_bundle import approval
from test_privacy_export import gate_fixture

from appraisal_review.adapters.local.privacy.mapping_crypto import MappingCipher, SessionMappingKeys
from appraisal_review.application.privacy_guards import PrivacyFault
from appraisal_review.application.privacy_mapping import LocalMappingService
from appraisal_review.domain.privacy_mapping import MappingError, MappingFault


def coordinator(tmp_path):
    gate, command, authority, confirmation, sink, verifier = gate_fixture(tmp_path)
    events = []
    keys, key = SessionMappingKeys(), uuid4()
    keys.provide(key, AESGCM.generate_key(bit_length=256))
    # Explicit persistence double; actual filesystem persistence has separate adapter tests.
    store = Mock()
    records = {}

    def create(envelope):
        events.append("create")
        records[envelope.map_id] = envelope.model_dump_json()

    def read(case_id, map_id, *, now):
        from appraisal_review.domain.privacy_models import EncryptedMappingEnvelope

        events.append("read")
        return EncryptedMappingEnvelope.model_validate_json(records[map_id])

    store.create.side_effect, store.read.side_effect = create, read
    mappings = LocalMappingService(
        store=store, cipher=MappingCipher(keys), authority=authority, verifier=verifier
    )
    gate._mapping_service, gate._key_reference = mappings, key
    confirmation.confirm.side_effect = lambda payload: events.append("confirm") or True
    sink.accept.side_effect = lambda payload: events.append("transfer")
    return gate, command, store, keys, mappings, confirmation, sink, events


def test_mapping_is_read_back_before_confirmation_and_exact_transfer(tmp_path):
    gate, command, store, _, mappings, confirmation, sink, events = coordinator(tmp_path)
    original_build = gate._builder.build
    gate._builder.build = Mock(wraps=original_build)
    manifest = gate.export(command, approval(command))
    assert events == ["create", "read", "confirm", "read", "transfer"]
    gate._builder.build.assert_called_once()
    handle = gate.mapping_handle
    assert handle is not None
    record = mappings.read(handle)
    assert record.command == command
    assert record.manifest == manifest
    payload = sink.accept.call_args.args[0]
    assert confirmation.confirm.call_args.args[0] is payload
    assert record.command.source.source_digest != manifest.sanitized_digest
    assert command.source.source_digest not in payload.manifest_json
    assert str(command.source.snapshot_id) not in payload.manifest_json
    assert str(handle.map_id) not in payload.manifest_json
    assert "CANARY-PRIVATE-1234" not in store.create.call_args.args[0].model_dump_json()
    assert record.manifest.occurrences == manifest.occurrences


@pytest.mark.parametrize("failure", ["key", "write", "read", "confirm-key", "confirm-map"])
def test_mapping_failure_causes_zero_transfers(tmp_path, failure):
    gate, command, store, keys, _, confirmation, sink, _ = coordinator(tmp_path)
    if failure == "key":
        keys.lock()
    elif failure == "write":
        store.create.side_effect = MappingFault(MappingError.IO)
    elif failure == "read":
        store.read.side_effect = MappingFault(MappingError.AUTHENTICATION)
    elif failure == "confirm-key":
        confirmation.confirm.side_effect = lambda payload: keys.lock() or True
    else:

        def corrupt(payload):
            store.read.side_effect = MappingFault(MappingError.AUTHENTICATION)
            return True

        confirmation.confirm.side_effect = corrupt
    with pytest.raises(PrivacyFault):
        gate.export(command, approval(command))
    sink.accept.assert_not_called()


@pytest.mark.parametrize("mismatch", ["manifest", "command"])
def test_authenticated_readback_must_equal_this_exact_export(tmp_path, mismatch):
    gate, command, _, _, mappings, _, sink, _ = coordinator(tmp_path)
    read = mappings.read

    def substituted(handle):
        record = read(handle)
        if mismatch == "manifest":
            occurrence = record.manifest.occurrences[0].model_copy(
                update={"occurrence_id": uuid4()}
            )
            return record.model_copy(
                update={
                    "manifest": record.manifest.model_copy(update={"occurrences": (occurrence,)})
                }
            )
        return record.model_copy(
            update={
                "command": command.model_copy(
                    update={"selection_revision": command.selection_revision + 1}
                )
            }
        )

    mappings.read = substituted
    with pytest.raises(PrivacyFault):
        gate.export(command, approval(command))
    sink.accept.assert_not_called()
