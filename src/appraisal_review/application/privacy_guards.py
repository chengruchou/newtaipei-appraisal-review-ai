"""Pure privacy admission guards; no state persistence or bundle export operation."""

from collections import Counter
from datetime import datetime

from pydantic import ValidationError

from appraisal_review.domain.privacy_models import (
    LocalPrivacyApproval,
    PrivacyErrorCode,
    PrivacyManifest,
    PrivacyProblem,
    PrivacyReviewCommand,
    privacy_review_digest,
)
from appraisal_review.ports.privacy import PrivacyApprovalAuthority, SanitizedArtifactVerifier


class PrivacyFault(Exception):
    def __init__(self, code: PrivacyErrorCode) -> None:
        self.problem = PrivacyProblem(code=code)
        super().__init__(code.value)


def check_export_admission(
    command: PrivacyReviewCommand,
    approval: LocalPrivacyApproval,
    manifest: PrivacyManifest,
    *,
    now: datetime,
    approval_authority: PrivacyApprovalAuthority,
    artifact_verifier: SanitizedArtifactVerifier,
) -> None:
    """Check prerequisites against trusted injected adapters, never produce a bundle.

    The local export gate hands off the same verified immutable bytes and rechecks
    revocation/current revisions. This pure check is not a grant that
    can be serialized, cached or used against an arbitrary upload path.
    """
    try:
        command = PrivacyReviewCommand.model_validate(command)
        approval = LocalPrivacyApproval.model_validate(approval)
        manifest = PrivacyManifest.model_validate(manifest)
    except (ValidationError, ValueError):
        raise PrivacyFault(PrivacyErrorCode.INVALID_INPUT) from None
    if now.tzinfo is None or now.utcoffset() is None:
        raise PrivacyFault(PrivacyErrorCode.INVALID_INPUT)
    if set(command.reviewed_pages) != set(range(1, len(command.source.pages) + 1)):
        raise PrivacyFault(PrivacyErrorCode.INVALID_INPUT)
    if (
        approval.case_id != command.source.case_id
        or approval.snapshot_id != command.source.snapshot_id
        or approval.review_digest != privacy_review_digest(command)
        or not approval.approved_at <= now < approval.expires_at
    ):
        raise PrivacyFault(PrivacyErrorCode.STALE_CONFIRMATION)
    if (
        manifest.case_id != command.source.case_id
        or manifest.document_id != command.source.document_id
        or manifest.sanitized_digest == command.source.source_digest
        or len(manifest.pages) != len(command.source.pages)
        or Counter(o.entity_id for o in manifest.occurrences)
        != Counter(s.entity_id for s in command.selections if s.disposition == "redact")
    ):
        raise PrivacyFault(PrivacyErrorCode.VERIFICATION_FAILED)
    try:
        approved = approval_authority.permits(approval, command, now=now)
    except Exception:
        raise PrivacyFault(PrivacyErrorCode.UNAUTHORIZED) from None
    if approved is not True:
        raise PrivacyFault(PrivacyErrorCode.UNAUTHORIZED)
    try:
        verified = artifact_verifier.permits(command, manifest)
    except Exception:
        raise PrivacyFault(PrivacyErrorCode.VERIFICATION_FAILED) from None
    if verified is not True:
        raise PrivacyFault(PrivacyErrorCode.VERIFICATION_FAILED)
