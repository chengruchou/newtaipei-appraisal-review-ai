"""Local privacy ports. Export composition has a separate privacy_export module.

Linux is the runtime acceptance target. Implementations must never fall back to
remote processing or download model assets during execution.
"""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from appraisal_review.domain.privacy_bundle import SanitizedBundle
from appraisal_review.domain.privacy_mapping import LocalMappingRecord
from appraisal_review.domain.privacy_models import (
    EncryptedMappingEnvelope,
    LocalPrivacyApproval,
    LocalSourceSnapshot,
    PrivacyManifest,
    PrivacyPage,
    PrivacyReviewCommand,
    RehydrationPlan,
    SensitiveCandidate,
)
from appraisal_review.domain.privacy_review import PrivacyPagePreview
from appraisal_review.domain.privacy_scan import PrivacyScanReport, TextObservation


class PrivacyRasterProcessor(Protocol):
    def build(self, command: PrivacyReviewCommand, original: bytes) -> SanitizedBundle:
        """Rebuild clean bytes. This draft has no verification authority."""
        ...

    def verify(
        self,
        command: PrivacyReviewCommand,
        original: bytes,
        bundle: SanitizedBundle,
    ) -> tuple[PrivacyPagePreview, ...]:
        """Independently inspect every surface/pixel and re-render for OCR."""
        ...


class PrivacyOutputOCR(Protocol):
    def read(
        self,
        preview: PrivacyPagePreview,
        page: PrivacyPage,
        *,
        timeout: float,
    ) -> tuple[TextObservation, ...]:
        """Trusted local OCR on verified output pixels; missing assets fail closed."""
        ...


class PrivacyHumanConfirmation(Protocol):
    def confirm(self, command: PrivacyReviewCommand) -> str | None:
        """Trusted composition only: present exact review and obtain human OS identity.

        Never derive authority from model output, consumer JSON or an actor field.
        Return None on denial. The operator account and local consumer are trusted.
        """
        ...


class PrivacyPreviewProvider(Protocol):
    def preview(self, source: LocalSourceSnapshot, page: int) -> PrivacyPagePreview:
        """Render owned original bytes locally; never accept a consumer URL/path."""
        ...


class LocalPrivacyScan(Protocol):
    async def scan(self, snapshot: LocalSourceSnapshot) -> PrivacyScanReport:
        """Report every page; no partial or zero-match result establishes safety."""
        ...


class LocalSnapshotReader(Protocol):
    def read(self, snapshot: LocalSourceSnapshot) -> bytes:
        """Read owned immutable bytes; recheck digest/size and case-scoped identity."""
        ...


class LocalPrivacyDetector(Protocol):
    async def detect(self, snapshot: LocalSourceSnapshot) -> tuple[SensitiveCandidate, ...]:
        """Inspect every page locally; unsupported/partial scans fail explicitly."""
        ...


class PrivacyApprovalAuthority(Protocol):
    def permits(
        self, approval: LocalPrivacyApproval, command: PrivacyReviewCommand, *, now: datetime
    ) -> bool:
        """Check authenticated human issuance, revocation and exact current binding.

        Neither a caller's principal_id nor a matching digest proves issuance.
        The local service supplies this adapter, never a request body.
        """
        ...


class SanitizedArtifactVerifier(Protocol):
    def permits(self, command: PrivacyReviewCommand, manifest: PrivacyManifest) -> bool:
        """Independently verify owned sanitized bytes and their exact source lineage.

        Check all privacy content surfaces and every approved region/occurrence;
        reject stale/replaced bytes. A supplied digest or verified flag is no proof.
        """
        ...


class MappingKeyProvider(Protocol):
    def unlock(self, key_reference: UUID) -> bytes:
        """Obtain a local 32-byte key without logging or colocating it with the map."""
        ...


class EncryptedMappingStore(Protocol):
    def create(self, envelope: EncryptedMappingEnvelope) -> None:
        """Atomically publish new ciphertext only; never overwrite an existing map."""
        ...

    def read(self, case_id: UUID, map_id: UUID, *, now: datetime) -> EncryptedMappingEnvelope:
        """Confined, expiry-checked read; authenticated decryption is still required."""
        ...

    def delete(self, case_id: UUID, map_id: UUID, *, expected_digest: str) -> None:
        """Delete exactly the identified ciphertext, including after expiry."""
        ...


class MappingEncryption(Protocol):
    def seal(
        self, record: LocalMappingRecord, reference: UUID, *, now: datetime
    ) -> EncryptedMappingEnvelope:
        """Authenticated encryption of local-only mapping data."""
        ...

    def open(
        self, envelope: EncryptedMappingEnvelope, *, case_id: UUID, map_id: UUID, now: datetime
    ) -> LocalMappingRecord:
        """Authenticated decryption with exact context and retention validation."""
        ...


class RehydrationAuthority(Protocol):
    def permits(self, plan: RehydrationPlan) -> bool:
        """Verify publisher lineage, run/revision and approved output occurrences.

        Digests supplied by the caller never replace authenticated provenance.
        This authority does not grant business completion or final-file upload.
        """
        ...
