"""Local immutable mapping lifecycle; mapping possession never authorizes refill/export."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from appraisal_review.application.privacy_guards import check_export_admission
from appraisal_review.domain.privacy_mapping import (
    LocalMappingHandle,
    LocalMappingRecord,
    MappingError,
    MappingFault,
)
from appraisal_review.domain.privacy_models import (
    LocalPrivacyApproval,
    PrivacyManifest,
    PrivacyReviewCommand,
)
from appraisal_review.ports.privacy import (
    EncryptedMappingStore,
    MappingEncryption,
    PrivacyApprovalAuthority,
    SanitizedArtifactVerifier,
)


class LocalMappingService:
    def __init__(
        self,
        *,
        store: EncryptedMappingStore,
        cipher: MappingEncryption,
        authority: PrivacyApprovalAuthority,
        verifier: SanitizedArtifactVerifier,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store, self._cipher = store, cipher
        self._authority, self._verifier, self._clock = authority, verifier, clock

    def create(
        self,
        command: PrivacyReviewCommand,
        approval: LocalPrivacyApproval,
        manifest: PrivacyManifest,
        *,
        key_reference: UUID,
        retention: timedelta = timedelta(days=1),
    ) -> LocalMappingHandle:
        try:
            now = self._clock()
            check_export_admission(
                command,
                approval,
                manifest,
                now=now,
                approval_authority=self._authority,
                artifact_verifier=self._verifier,
            )
            record = LocalMappingRecord(
                map_id=uuid4(),
                created_at=now,
                expires_at=now + retention,
                command=command,
                manifest=manifest,
            )
            envelope = self._cipher.seal(record, key_reference, now=now)
            check_export_admission(
                command,
                approval,
                manifest,
                now=self._clock(),
                approval_authority=self._authority,
                artifact_verifier=self._verifier,
            )
            if self._clock() >= envelope.expires_at:
                raise MappingFault(MappingError.EXPIRED)
            self._store.create(envelope)
            return LocalMappingHandle(
                case_id=envelope.case_id,
                map_id=envelope.map_id,
                key_reference=envelope.key_reference,
                expires_at=envelope.expires_at,
                envelope_digest=hashlib.sha256(
                    envelope.model_dump_json().encode("utf-8")
                ).hexdigest(),
            )
        except MappingFault:
            raise
        except Exception:
            raise MappingFault(MappingError.INVALID) from None

    def read(self, handle: LocalMappingHandle) -> LocalMappingRecord:
        try:
            handle = LocalMappingHandle.model_validate(handle)
            now = self._clock()
            if now >= handle.expires_at:
                raise MappingFault(MappingError.EXPIRED)
            envelope = self._store.read(handle.case_id, handle.map_id, now=now)
            if (
                envelope.key_reference != handle.key_reference
                or envelope.expires_at != handle.expires_at
                or (
                    hashlib.sha256(envelope.model_dump_json().encode("utf-8")).hexdigest()
                    != handle.envelope_digest
                )
            ):
                raise MappingFault(MappingError.AUTHENTICATION)
            return self._cipher.open(
                envelope, case_id=handle.case_id, map_id=handle.map_id, now=self._clock()
            )
        except MappingFault:
            raise
        except Exception:
            raise MappingFault(MappingError.AUTHENTICATION) from None

    def delete(self, handle: LocalMappingHandle) -> None:
        try:
            handle = LocalMappingHandle.model_validate(handle)
            self._store.delete(
                handle.case_id, handle.map_id, expected_digest=handle.envelope_digest
            )
        except MappingFault:
            raise
        except Exception:
            raise MappingFault(MappingError.IO) from None
