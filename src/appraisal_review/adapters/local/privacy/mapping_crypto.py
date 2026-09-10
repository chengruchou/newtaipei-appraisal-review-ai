"""AEAD via cryptography; keys are provisioned only by trusted local composition."""

from __future__ import annotations

import json
import secrets
import time
from datetime import datetime
from threading import RLock
from uuid import UUID

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from appraisal_review.domain.privacy_mapping import LocalMappingRecord, MappingError, MappingFault
from appraisal_review.domain.privacy_models import EncryptedMappingEnvelope
from appraisal_review.ports.privacy import MappingKeyProvider


class SessionMappingKeys:
    """Ephemeral unlocked buffers; no file/env/argv/keystore input or persistence.

    A trusted external provisioner must supply keys. This is not production key
    provisioning. Lock wipes owned buffers, not copies or Python/OS memory history.
    """

    def __init__(self) -> None:
        self._keys: dict[UUID, tuple[bytearray, float]] = {}
        self._lock = RLock()

    def provide(self, reference: UUID, key: bytes, *, lifetime: float = 900) -> None:
        with self._lock:
            if (
                type(reference) is not UUID
                or reference.version != 4
                or type(key) is not bytes
                or (len(key) != 32 or not 0 < lifetime <= 3600 or len(self._keys) >= 8)
            ):
                raise MappingFault(MappingError.INVALID)
            if reference in self._keys:
                raise MappingFault(MappingError.CONFLICT)
            self._keys[reference] = (bytearray(key), time.monotonic() + lifetime)

    def unlock(self, key_reference: UUID) -> bytes:
        with self._lock:
            entry = self._keys.get(key_reference)
            if entry is None:
                raise MappingFault(MappingError.LOCKED)
            key, deadline = entry
            if time.monotonic() >= deadline:
                self.lock(key_reference)
                raise MappingFault(MappingError.LOCKED)
            return bytes(key)

    def lock(self, reference: UUID | None = None) -> None:
        with self._lock:
            for selected in list(self._keys) if reference is None else [reference]:
                entry = self._keys.pop(selected, None)
                if entry is not None:
                    entry[0][:] = b"\x00" * len(entry[0])


def mapping_aad(envelope: EncryptedMappingEnvelope) -> bytes:
    return json.dumps(
        envelope.model_dump(mode="json", exclude={"ciphertext_hex"}),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


class MappingCipher:
    def __init__(self, keys: MappingKeyProvider) -> None:
        self._keys = keys
        self._sealed: set[UUID] = set()
        self._lock = RLock()

    def _key(self, envelope: EncryptedMappingEnvelope) -> bytes:
        key = self._keys.unlock(envelope.key_reference)
        if type(key) is not bytes or len(key) != 32:
            raise MappingFault(MappingError.LOCKED)
        # Map-specific subkeys isolate nonce domains across independent immutable maps.
        return HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=envelope.map_id.bytes,
            info=b"privacy-map-key-v1",
        ).derive(key)

    def seal(
        self, record: LocalMappingRecord, reference: UUID, *, now: datetime
    ) -> EncryptedMappingEnvelope:
        with self._lock:
            try:
                record = LocalMappingRecord.model_validate(record)
                if not record.created_at <= now < record.expires_at:
                    raise MappingFault(MappingError.EXPIRED)
                if record.map_id in self._sealed or len(self._sealed) >= 10000:
                    raise MappingFault(MappingError.CONFLICT)
                self._sealed.add(record.map_id)
                raw = record.model_dump_json().encode("utf-8")
                if len(raw) > 8_000_000:
                    raise MappingFault(MappingError.INVALID)
                envelope = EncryptedMappingEnvelope(
                    case_id=record.command.source.case_id,
                    map_id=record.map_id,
                    key_reference=reference,
                    nonce_hex=secrets.token_bytes(12).hex(),
                    ciphertext_hex="00" * 16,
                    expires_at=record.expires_at,
                )
                ciphertext = AESGCM(self._key(envelope)).encrypt(
                    bytes.fromhex(envelope.nonce_hex), raw, mapping_aad(envelope)
                )
                return EncryptedMappingEnvelope(
                    **envelope.model_dump(exclude={"ciphertext_hex"}),
                    ciphertext_hex=ciphertext.hex(),
                )
            except MappingFault:
                raise
            except Exception:
                raise MappingFault(MappingError.INVALID) from None

    def open(
        self, envelope: EncryptedMappingEnvelope, *, case_id: UUID, map_id: UUID, now: datetime
    ) -> LocalMappingRecord:
        try:
            envelope = EncryptedMappingEnvelope.model_validate(envelope)
            if envelope.case_id != case_id or envelope.map_id != map_id:
                raise MappingFault(MappingError.AUTHENTICATION)
            if now.tzinfo is None or now.utcoffset() is None:
                raise MappingFault(MappingError.INVALID)
            if now >= envelope.expires_at:
                raise MappingFault(MappingError.EXPIRED)
            if len(envelope.ciphertext_hex) > 16_000_032:
                raise MappingFault(MappingError.INVALID)
            raw = AESGCM(self._key(envelope)).decrypt(
                bytes.fromhex(envelope.nonce_hex),
                bytes.fromhex(envelope.ciphertext_hex),
                mapping_aad(envelope),
            )
            record = LocalMappingRecord.model_validate_json(raw)
            if (
                record.map_id != map_id
                or record.command.source.case_id != case_id
                or (
                    record.expires_at != envelope.expires_at
                    or not record.created_at <= now < record.expires_at
                )
            ):
                raise MappingFault(MappingError.AUTHENTICATION)
            return record
        except MappingFault:
            raise
        except Exception:
            raise MappingFault(MappingError.AUTHENTICATION) from None
