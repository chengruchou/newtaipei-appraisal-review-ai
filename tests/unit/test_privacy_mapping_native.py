"""Real local ciphertext files on the executing POSIX host, with generated test keys."""

import sys
from datetime import UTC, datetime

import pytest
from test_privacy_bundle import approval
from test_privacy_export_mapping import coordinator

from appraisal_review.adapters.local.privacy import mapping_store
from appraisal_review.domain.privacy_mapping import MappingFault


def native_store(root, workspace):
    cls = (
        mapping_store.MacOSEncryptedMappingStore
        if sys.platform == "darwin"
        else mapping_store.LinuxEncryptedMappingStore
    )
    return cls(root, workspace=workspace)


def test_exact_export_ciphertext_survives_store_reopen_before_transfer(tmp_path):
    root = tmp_path / "maps"
    root.mkdir(mode=0o700)
    store = native_store(root, tmp_path)
    gate, command, _, _, mappings, _, sink, _ = coordinator(tmp_path)
    mappings._store = store

    def accept(payload):
        handle = gate.mapping_handle
        reopened = native_store(root, tmp_path)
        try:
            mappings._store = reopened
            record = mappings.read(handle)
            assert record.manifest.model_dump_json() == payload.manifest_json
            assert record.command.source == command.source
            assert record.command.selections[0].candidate.raw_text == "CANARY-PRIVATE-1234\n"
        finally:
            mappings._store = store
            reopened.close()

    sink.accept.side_effect = accept
    try:
        gate.export(command, approval(command))
        sink.accept.assert_called_once()
        assert len(list(root.glob("*.map"))) == 1
        raw = next(root.glob("*.map")).read_bytes()
        assert b"CANARY-PRIVATE-1234" not in raw
        assert command.source.source_digest.encode() not in raw
    finally:
        store.close()


def test_native_store_rejects_world_readable_directory_and_symlink(tmp_path):
    root = tmp_path / "maps"
    root.mkdir(mode=0o755)
    with pytest.raises(MappingFault):
        native_store(root, tmp_path)
    root.chmod(0o700)
    link = tmp_path / "link"
    link.symlink_to(root)
    with pytest.raises(MappingFault):
        native_store(link, tmp_path)


def test_native_store_rejects_changed_ciphertext_file_identity(tmp_path):
    root = tmp_path / "maps"
    root.mkdir(mode=0o700)
    store = native_store(root, tmp_path)
    gate, command, _, _, mappings, _, _, _ = coordinator(tmp_path)
    mappings._store = store
    try:
        gate.export(command, approval(command))
        path = next(root.glob("*.map"))
        path.chmod(0o644)
        handle = gate.mapping_handle
        with pytest.raises(MappingFault):
            store.read(handle.case_id, handle.map_id, now=datetime.now(UTC))
    finally:
        store.close()


def test_ciphertext_is_authenticated_in_a_fresh_process(tmp_path):
    import json
    import subprocess

    root = tmp_path / "maps"
    root.mkdir(mode=0o700)
    store = native_store(root, tmp_path)
    gate, command, _, keys, mappings, _, _, _ = coordinator(tmp_path)
    mappings._store = store
    try:
        manifest = gate.export(command, approval(command))
        handle = gate.mapping_handle
        # Test-only key provision over stdin; never argv, environment or disk.
        request = json.dumps(
            {
                "handle": handle.model_dump(mode="json"),
                "key": keys.unlock(handle.key_reference).hex(),
            }
        )
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                """
import json, sys
from pathlib import Path
from appraisal_review.adapters.local.privacy.mapping_store import (
    LinuxEncryptedMappingStore, MacOSEncryptedMappingStore,
)
from appraisal_review.adapters.local.privacy.mapping_crypto import MappingCipher, SessionMappingKeys
from appraisal_review.domain.privacy_mapping import LocalMappingHandle
from datetime import UTC, datetime
request = json.loads(sys.stdin.read())
handle = LocalMappingHandle.model_validate_json(json.dumps(request["handle"]))
keys = SessionMappingKeys()
keys.provide(handle.key_reference, bytes.fromhex(request["key"]))
cls = MacOSEncryptedMappingStore if sys.platform == "darwin" else LinuxEncryptedMappingStore
store = cls(Path(sys.argv[1]), workspace=Path(sys.argv[2]))
try:
    envelope = store.read(handle.case_id, handle.map_id, now=datetime.now(UTC))
    record = MappingCipher(keys).open(envelope, case_id=handle.case_id,
                                     map_id=handle.map_id, now=datetime.now(UTC))
    assert record.command.selections[0].candidate.raw_text == "CANARY-PRIVATE-1234\\n"
    print(record.manifest.sanitized_digest)
finally:
    keys.lock()
    store.close()
""",
                str(root),
                str(tmp_path),
            ],
            input=request,
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        assert result.stdout.strip() == manifest.sanitized_digest
        assert "CANARY-PRIVATE-1234" not in result.stdout + result.stderr
    finally:
        store.close()


@pytest.mark.parametrize("change", ["permissions", "hardlink", "removed"])
def test_real_store_mutation_during_confirmation_blocks_transfer(tmp_path, change):
    root = tmp_path / "maps"
    root.mkdir(mode=0o700)
    store = native_store(root, tmp_path)
    gate, command, _, _, mappings, confirmation, sink, _ = coordinator(tmp_path)
    mappings._store = store

    def confirm(payload):
        path = next(root.glob("*.map"))
        if change == "permissions":
            path.chmod(0o644)
        elif change == "hardlink":
            (root / "alias").hardlink_to(path)
        else:
            path.unlink()
        return True

    from appraisal_review.application.privacy_guards import PrivacyFault

    confirmation.confirm.side_effect = confirm
    try:
        with pytest.raises(PrivacyFault):
            gate.export(command, approval(command))
        sink.accept.assert_not_called()
    finally:
        store.close()
