"""Real AEAD with generated test keys; Linux filesystem cases remain platform-specific."""

from __future__ import annotations

import json
import os
import runpy
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import jsonschema
import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import ValidationError

from appraisal_review.adapters.local.privacy.mapping_crypto import MappingCipher, SessionMappingKeys
from appraisal_review.adapters.local.privacy.mapping_store import (
    LinuxEncryptedMappingStore,
    envelope_digest,
)
from appraisal_review.application.privacy_mapping import LocalMappingService
from appraisal_review.domain.privacy_mapping import LocalMappingRecord, MappingError, MappingFault
from appraisal_review.domain.privacy_models import (
    LocalPrivacyApproval,
    PrivacyManifest,
    PrivacyReviewCommand,
    privacy_review_digest,
)


@pytest.fixture
def material():
    root = Path(__file__).resolve().parents[2] / "examples/privacy-v1"
    command = PrivacyReviewCommand.model_validate_json(
        (root / "local/review-command.json").read_bytes()
    )
    manifest = PrivacyManifest.model_validate_json((root / "public/manifest.json").read_bytes())
    now = datetime.now(UTC)
    record = LocalMappingRecord(
        map_id=uuid4(),
        created_at=now,
        expires_at=now + timedelta(days=1),
        command=command,
        manifest=manifest,
    )
    keys = SessionMappingKeys()
    reference = uuid4()
    keys.provide(reference, AESGCM.generate_key(bit_length=256))
    return record, keys, reference, MappingCipher(keys), now


def decrypt(cipher, envelope, now):
    return cipher.open(envelope, case_id=envelope.case_id, map_id=envelope.map_id, now=now)


def test_real_aead_round_trip_and_no_plaintext_in_envelope(material):
    record, _, reference, cipher, now = material
    envelope = cipher.seal(record, reference, now=now)
    assert decrypt(cipher, envelope, now) == record
    raw = envelope.model_dump_json()
    assert "SYNTHETIC-LOCAL-ONLY-VALUE" not in raw
    assert record.command.source.source_digest not in raw
    assert "SYNTHETIC-LOCAL-ONLY-VALUE" not in repr(record)


@pytest.mark.parametrize(
    "field",
    [
        "case_id",
        "map_id",
        "key_reference",
        "nonce_hex",
        "ciphertext_hex",
        "expires_at",
        "schema_version",
        "context_version",
        "algorithm",
    ],
)
def test_every_header_ciphertext_and_nonce_tamper_rejected(material, field):
    record, _, reference, cipher, now = material
    envelope = cipher.seal(record, reference, now=now)
    value = {
        "nonce_hex": "00" * 12,
        "ciphertext_hex": "00" * 16,
        "expires_at": now + timedelta(days=2),
        "schema_version": "tampered",
        "context_version": "tampered",
        "algorithm": "tampered",
    }.get(field, uuid4())
    changed = envelope.model_copy(update={field: value})
    with pytest.raises(MappingFault):
        cipher.open(changed, case_id=envelope.case_id, map_id=envelope.map_id, now=now)


def test_wrong_key_and_lock_refuse_plaintext(material):
    record, keys, reference, cipher, now = material
    envelope = cipher.seal(record, reference, now=now)
    owned_buffer = keys._keys[reference][0]
    keys.lock()
    assert not any(owned_buffer)
    with pytest.raises(MappingFault, match="mapping_locked"):
        decrypt(cipher, envelope, now)
    keys.provide(reference, AESGCM.generate_key(bit_length=256))
    with pytest.raises(MappingFault, match="mapping_authentication_failed"):
        decrypt(cipher, envelope, now)


@pytest.mark.parametrize("offset", [-1, 86400, 86401])
def test_retention_boundaries(material, offset):
    record, _, reference, cipher, now = material
    envelope = cipher.seal(record, reference, now=now)
    with pytest.raises(MappingFault):
        decrypt(cipher, envelope, now + timedelta(seconds=offset))


def test_random_nonce_and_map_subkey_domains(material):
    record, _, reference, cipher, now = material
    first = cipher.seal(record, reference, now=now)
    second_record = record.model_copy(update={"map_id": uuid4()})
    second = cipher.seal(second_record, reference, now=now)
    assert first.nonce_hex != second.nonce_hex
    assert cipher._key(first) != cipher._key(second)
    with pytest.raises(MappingFault, match="mapping_conflict"):
        cipher.seal(record, reference, now=now)


def test_session_key_expiry_and_no_replacement(material, monkeypatch):
    _, keys, reference, _, _ = material
    with pytest.raises(MappingFault, match="mapping_conflict"):
        keys.provide(reference, AESGCM.generate_key(bit_length=256))
    deadline = keys._keys[reference][1]
    monkeypatch.setattr(
        "appraisal_review.adapters.local.privacy.mapping_crypto.time.monotonic", lambda: deadline
    )
    with pytest.raises(MappingFault, match="mapping_locked"):
        keys.unlock(reference)
    assert reference not in keys._keys


@pytest.mark.parametrize("length", [0, 16, 24, 31, 33])
def test_only_256_bit_master_keys_are_accepted(length):
    with pytest.raises(MappingFault):
        SessionMappingKeys().provide(uuid4(), b"x" * length)


@pytest.mark.parametrize("change", ["case", "entity", "retention", "unreviewed"])
def test_mapping_contract_rejects_invalid_binding(material, change):
    record, _, _, _, now = material
    update = {}
    if change == "case":
        update["manifest"] = record.manifest.model_copy(update={"case_id": uuid4()})
    elif change == "entity":
        update["manifest"] = record.manifest.model_copy(update={"occurrences": ()})
    elif change == "retention":
        update["expires_at"] = now + timedelta(days=8)
    else:
        update["command"] = record.command.model_copy(update={"reviewed_pages": ()})
    with pytest.raises(ValidationError):
        LocalMappingRecord.model_validate(record.model_copy(update=update))


class CiphertextTestStore:
    """Test-only storage double; no filesystem/security claim."""

    def __init__(self):
        self.entries = {}

    def create(self, envelope):
        key = envelope.case_id, envelope.map_id
        if key in self.entries:
            raise MappingFault(MappingError.CONFLICT)
        self.entries[key] = envelope

    def read(self, case_id, map_id, *, now):
        return self.entries[case_id, map_id]

    def delete(self, case_id, map_id, *, expected_digest):
        if envelope_digest(self.entries[case_id, map_id]) != expected_digest:
            raise MappingFault(MappingError.CONFLICT)
        del self.entries[case_id, map_id]


def service_for(material):
    record, _, _, cipher, now = material
    authority, verifier, clock = Mock(), Mock(), Mock(return_value=now)
    authority.permits.return_value = verifier.permits.return_value = True
    store = CiphertextTestStore()
    service = LocalMappingService(
        store=store, cipher=cipher, authority=authority, verifier=verifier, clock=clock
    )
    approval = LocalPrivacyApproval(
        approval_id=uuid4(),
        case_id=record.command.source.case_id,
        snapshot_id=record.command.source.snapshot_id,
        review_digest=privacy_review_digest(record.command),
        principal_id="synthetic-test-reviewer",
        approved_at=now,
        expires_at=now + timedelta(minutes=15),
    )
    return service, store, approval, authority, verifier, clock


def test_mapping_lifecycle_uses_real_aead_and_exact_delete(material):
    record, keys, reference, _, _ = material
    service, store, approval, _, _, clock = service_for(material)
    handle = service.create(record.command, approval, record.manifest, key_reference=reference)
    assert service.read(handle).command == record.command
    with pytest.raises(MappingFault):
        service.delete(handle.model_copy(update={"envelope_digest": "0" * 64}))
    keys.lock()
    with pytest.raises(MappingFault, match="mapping_locked"):
        service.read(handle)
    clock.return_value = handle.expires_at
    with pytest.raises(MappingFault, match="mapping_expired"):
        service.read(handle)
    service.delete(handle)
    assert not store.entries


@pytest.mark.parametrize("gate", ["authority", "verifier", "revoked"])
def test_unapproved_or_unverified_mapping_never_persists(material, gate):
    record, _, reference, _, _ = material
    service, store, approval, authority, verifier, _ = service_for(material)
    if gate == "revoked":
        authority.permits.side_effect = [True, False]
    else:
        (authority if gate == "authority" else verifier).permits.return_value = False
    with pytest.raises(MappingFault):
        service.create(record.command, approval, record.manifest, key_reference=reference)
    assert not store.entries


def test_windows_storage_is_explicitly_blocked(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "appraisal_review.adapters.local.privacy.mapping_store.sys.platform", "win32"
    )
    with pytest.raises(MappingFault, match="mapping_platform_unavailable"):
        LinuxEncryptedMappingStore(tmp_path / "not-created", workspace=tmp_path)
    assert not (tmp_path / "not-created").exists()


def test_mapping_contract_schema_is_reproducible(tmp_path):
    root = Path(__file__).resolve().parents[2]
    runpy.run_path(str(root / "scripts/export_privacy_mapping_contracts.py"))["export"](tmp_path)
    for relative in (
        "schemas/local-privacy-mapping-v1.json",
        "examples/privacy-mapping-v1/synthetic-record.json",
    ):
        assert (tmp_path / relative).read_bytes() == (root / relative).read_bytes()
    schema = json.loads((tmp_path / "schemas/local-privacy-mapping-v1.json").read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    raw = (tmp_path / "examples/privacy-mapping-v1/synthetic-record.json").read_text()
    jsonschema.validate(json.loads(raw), {**schema, "$ref": "#/$defs/LocalMappingRecord"})
    assert LocalMappingRecord.model_validate_json(raw)


def test_replaced_ciphertext_and_cross_case_handles_fail_closed(material):
    record, _, reference, _, _ = material
    service, store, approval, _, _, _ = service_for(material)
    handle = service.create(record.command, approval, record.manifest, key_reference=reference)
    with pytest.raises(MappingFault):
        service.read(handle.model_copy(update={"case_id": uuid4()}))
    envelope = store.entries[handle.case_id, handle.map_id]
    store.entries[handle.case_id, handle.map_id] = envelope.model_copy(
        update={"nonce_hex": "00" * 12}
    )
    with pytest.raises(MappingFault, match="mapping_authentication_failed"):
        service.read(handle)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux directory confinement acceptance")
def test_linux_rejects_symlink_parent_and_outside_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(mode=0o700)
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    (workspace / "alias").symlink_to(outside, target_is_directory=True)
    for root in (outside, workspace / "alias"):
        with pytest.raises(MappingFault):
            LinuxEncryptedMappingStore(root, workspace=workspace)


linux_only = pytest.mark.skipif(
    sys.platform != "linux", reason="Linux openat/flock/private-mode acceptance"
)


@linux_only
def test_linux_private_atomic_files_conflicts_expiry_and_delete(tmp_path, material):
    record, _, reference, cipher, now = material
    root = tmp_path / "maps"
    root.mkdir(mode=0o700)
    store = LinuxEncryptedMappingStore(root, workspace=tmp_path)
    envelope = cipher.seal(record, reference, now=now)
    try:
        store.create(envelope)
        assert store.read(envelope.case_id, envelope.map_id, now=now) == envelope
        with pytest.raises(MappingFault, match="mapping_conflict"):
            store.create(envelope)
        with pytest.raises(MappingFault, match="mapping_expired"):
            store.read(envelope.case_id, envelope.map_id, now=envelope.expires_at)
        for path in root.iterdir():
            assert path.stat().st_mode & 0o777 == 0o600
            assert b"SYNTHETIC-LOCAL-ONLY-VALUE" not in path.read_bytes()
        store.delete(envelope.case_id, envelope.map_id, expected_digest=envelope_digest(envelope))
        assert list(root.iterdir()) == [root / ".mapping.lock"]
    finally:
        store.close()


@linux_only
@pytest.mark.parametrize("attack", ["symlink", "hardlink", "fifo", "partial", "permissions"])
def test_linux_rejects_unsafe_or_partial_files(tmp_path, material, attack):
    record, _, reference, cipher, now = material
    root = tmp_path / "maps"
    root.mkdir(mode=0o700)
    store = LinuxEncryptedMappingStore(root, workspace=tmp_path)
    envelope = cipher.seal(record, reference, now=now)
    path = root / store._name(envelope.case_id, envelope.map_id)
    target = tmp_path / "ciphertext"
    target.write_text(envelope.model_dump_json())
    target.chmod(0o600)
    if attack == "symlink":
        path.symlink_to(target)
    elif attack == "hardlink":
        os.link(target, path)
    elif attack == "fifo":
        os.mkfifo(path, 0o600)
    else:
        path.write_text("{" if attack == "partial" else envelope.model_dump_json())
        path.chmod(0o600 if attack == "partial" else 0o644)
    try:
        with pytest.raises(MappingFault):
            store.read(envelope.case_id, envelope.map_id, now=now)
    finally:
        store.close()


@linux_only
def test_linux_concurrent_store_lock_and_failed_write_cleanup(tmp_path, material, monkeypatch):
    record, _, reference, cipher, now = material
    root = tmp_path / "maps"
    root.mkdir(mode=0o700)
    first = LinuxEncryptedMappingStore(root, workspace=tmp_path)
    second = LinuxEncryptedMappingStore(root, workspace=tmp_path)
    envelope = cipher.seal(record, reference, now=now)
    try:
        with first._guard(), pytest.raises(MappingFault, match="mapping_conflict"):
            second.create(envelope)

        def interrupted(*args):
            raise OSError("Synthetic interrupted write")

        monkeypatch.setattr(
            "appraisal_review.adapters.local.privacy.mapping_store.os.write", interrupted
        )
        with pytest.raises(MappingFault):
            first.create(envelope)
        assert not list(root.glob("*.map")) and not list(root.glob("*.tmp"))
    finally:
        first.close()
        second.close()
