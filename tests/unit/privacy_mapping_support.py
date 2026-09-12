"""Explicit in-memory ciphertext store for export unit tests; no durability claim."""

from unittest.mock import Mock
from uuid import uuid4

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from appraisal_review.adapters.local.privacy.mapping_crypto import MappingCipher, SessionMappingKeys
from appraisal_review.application.privacy_mapping import LocalMappingService
from appraisal_review.domain.privacy_models import EncryptedMappingEnvelope


def mapping_service(authority, verifier):
    keys, key = SessionMappingKeys(), uuid4()
    keys.provide(key, AESGCM.generate_key(bit_length=256))
    records = {}
    store = Mock()

    def create(envelope):
        assert envelope.map_id not in records
        records[envelope.map_id] = envelope.model_dump_json()

    def read(case_id, map_id, *, now):
        return EncryptedMappingEnvelope.model_validate_json(records[map_id])

    store.create.side_effect, store.read.side_effect = create, read
    return LocalMappingService(
        store=store, cipher=MappingCipher(keys), authority=authority, verifier=verifier
    ), key
