"""Local bundle construction and live verification registry used by the export gate."""

from __future__ import annotations

import hashlib
import time
import unicodedata
from datetime import UTC, datetime
from threading import RLock

from appraisal_review.application.privacy_guards import PrivacyFault, check_export_admission
from appraisal_review.domain.privacy_bundle import SanitizedBundle
from appraisal_review.domain.privacy_models import (
    LocalPrivacyApproval,
    PrivacyErrorCode,
    PrivacyManifest,
    PrivacyReviewCommand,
    placeholder_text,
    privacy_review_digest,
    validate_region,
)
from appraisal_review.domain.privacy_scan import TextObservation
from appraisal_review.ports.privacy import (
    LocalSnapshotReader,
    PrivacyApprovalAuthority,
    PrivacyOutputOCR,
    PrivacyRasterProcessor,
)


def _normalized(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).casefold().split())


class LocalSanitizedVerifier:
    """One owned verified artifact, never a supplied digest or boolean credential."""

    def __init__(self, processor: PrivacyRasterProcessor, ocr: PrivacyOutputOCR) -> None:
        self._processor = processor
        self._ocr = ocr
        self._entry: tuple[str, SanitizedBundle] | None = None
        self._lock = RLock()

    def verify(
        self, command: PrivacyReviewCommand, original: bytes, bundle: SanitizedBundle
    ) -> None:
        with self._lock:
            self._entry = None
            try:
                command = PrivacyReviewCommand.model_validate(command)
                manifest = PrivacyManifest.model_validate(bundle.manifest)
                if hashlib.sha256(bundle.pdf).hexdigest() != manifest.sanitized_digest or (
                    len(bundle.pdf) != manifest.byte_size
                ):
                    raise ValueError("Artifact differs")
                previews = self._processor.verify(command, original, bundle)
                if len(previews) != len(manifest.pages):
                    raise ValueError("Missing page")
                deadline = time.monotonic() + 30
                canaries = [
                    _normalized(s.candidate.raw_text)
                    for s in command.selections
                    if s.disposition == "redact" and s.candidate.raw_text
                ]
                for preview, page in zip(previews, manifest.pages, strict=True):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ValueError("OCR deadline")
                    observations = tuple(
                        TextObservation.model_validate(o)
                        for o in self._ocr.read(preview, page, timeout=remaining)
                    )
                    if (
                        time.monotonic() > deadline
                        or len(observations) > 10000
                        or (sum(len(o.text) for o in observations) > 100000)
                    ):
                        raise ValueError("Output OCR resource limit")
                    for observation in observations:
                        validate_region(observation.region, manifest.pages)
                    if not observations or any(
                        o.origin != "ocr"
                        or o.confidence is None
                        or o.confidence < 0.85
                        or o.region.page != page.number
                        for o in observations
                    ):
                        raise ValueError("Uncertain output OCR")
                    text = _normalized(" ".join(o.text for o in observations))
                    if any(canary in text for canary in canaries):
                        raise ValueError("Source canary remains")
                    if any(
                        _normalized(placeholder_text(o.entity_id)) not in text
                        for o in manifest.occurrences
                        if o.region.page == page.number
                    ):
                        raise ValueError("Unreadable placeholder")
                self._entry = (privacy_review_digest(command), bundle)
            except Exception:
                raise PrivacyFault(PrivacyErrorCode.VERIFICATION_FAILED) from None

    def permits(self, command: PrivacyReviewCommand, manifest: PrivacyManifest) -> bool:
        with self._lock:
            try:
                if self._entry is None:
                    return False
                digest, bundle = self._entry
                return (
                    digest == privacy_review_digest(command)
                    and bundle.manifest == manifest
                    and hashlib.sha256(bundle.pdf).hexdigest() == manifest.sanitized_digest
                    and len(bundle.pdf) == manifest.byte_size
                )
            except Exception:
                return False


class LocalSanitizedBundleBuilder:
    def __init__(
        self,
        *,
        sources: LocalSnapshotReader,
        authority: PrivacyApprovalAuthority,
        processor: PrivacyRasterProcessor,
        verifier: LocalSanitizedVerifier,
    ) -> None:
        self._sources = sources
        self._authority = authority
        self._processor = processor
        self._verifier = verifier

    def build(
        self, command: PrivacyReviewCommand, approval: LocalPrivacyApproval
    ) -> SanitizedBundle:
        try:
            command = PrivacyReviewCommand.model_validate(command)
            approval = LocalPrivacyApproval.model_validate(approval)
            if self._authority.permits(approval, command, now=datetime.now(UTC)) is not True:
                raise PrivacyFault(PrivacyErrorCode.UNAUTHORIZED)
            original = self._sources.read(command.source)
            bundle = self._processor.build(command, original)
            self._verifier.verify(command, original, bundle)
            check_export_admission(
                command,
                approval,
                bundle.manifest,
                now=datetime.now(UTC),
                approval_authority=self._authority,
                artifact_verifier=self._verifier,
            )
            return bundle
        except PrivacyFault:
            raise
        except Exception:
            raise PrivacyFault(PrivacyErrorCode.VERIFICATION_FAILED) from None
