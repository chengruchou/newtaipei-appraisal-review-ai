"""Single owned-bundle export boundary and shared local free-text preparation."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import UTC, datetime
from threading import RLock
from uuid import UUID

from appraisal_review.application.privacy_bundle import (
    LocalSanitizedBundleBuilder,
    LocalSanitizedVerifier,
)
from appraisal_review.application.privacy_guards import PrivacyFault, check_export_admission
from appraisal_review.application.privacy_mapping import LocalMappingService
from appraisal_review.domain.privacy_export import LocalTextDraft, PrivacyExportPayload
from appraisal_review.domain.privacy_mapping import LocalMappingHandle
from appraisal_review.domain.privacy_models import (
    LocalPrivacyApproval,
    PrivacyErrorCode,
    PrivacyManifest,
    PrivacyReviewCommand,
    placeholder_text,
    public_manifest_json,
)
from appraisal_review.ports.privacy import PrivacyApprovalAuthority
from appraisal_review.ports.privacy_export import PrivacyExportConfirmation, PrivacyExportSink


def sanitize_reviewer_text(text: str, command: PrivacyReviewCommand) -> LocalTextDraft:
    """Prepare local text for #24/#25. This draft always requires human review.

    Replace known redacted values including case/width/whitespace variants.
    Unknown sensitive facts require the final human review; this is not a PII
    detector or an upload grant. Never log this input or the local draft.
    """
    try:
        command = PrivacyReviewCommand.model_validate(command)
        if type(text) is not str or len(text) > 16384:
            raise ValueError("Text limit")
        normalized = unicodedata.normalize("NFKC", text)
        if any(unicodedata.category(c).startswith("C") and c not in "\n\r\t" for c in normalized):
            raise ValueError("Unsupported control character")
        if command.source.source_digest in "".join(normalized.casefold().split()):
            raise ValueError("Original fingerprint is local only")
        replacements: dict[str, str] = {}
        for selection in command.selections:
            if selection.disposition != "redact" or selection.candidate.raw_text is None:
                continue
            key = "".join(unicodedata.normalize("NFKC", selection.candidate.raw_text).split())
            if not key or selection.entity_id is None:
                raise ValueError("Unresolved replacement")
            token = placeholder_text(selection.entity_id)
            previous = replacements.get(key.casefold())
            if previous is not None and previous != token:
                raise ValueError("Ambiguous text entity")
            replacements[key.casefold()] = token
        if sum(map(len, replacements)) > 100000 or len(replacements) > 1000:
            raise ValueError("Replacement limit")
        # One pass prevents replacements from modifying newly inserted tokens.
        keys = sorted(replacements, key=len, reverse=True)
        if keys:
            pattern = "|".join("\\s*".join(map(re.escape, key)) for key in keys)
            normalized = re.sub(
                pattern,
                lambda match: replacements["".join(match.group().casefold().split())],
                normalized,
                flags=re.IGNORECASE,
            )
            compact = "".join(normalized.casefold().split())
            if any(key in compact for key in keys):
                raise ValueError("Known value remains")
        if len(normalized) > 65536:
            raise ValueError("Output text limit")
        return LocalTextDraft(normalized)
    except Exception:
        raise PrivacyFault(PrivacyErrorCode.INVALID_INPUT) from None


class LocalPrivacyExportGate:
    """Persist, read back, confirm and transfer one build; trusted local composition only.

    Use one instance per local session. A failed sink may have transferred bytes;
    retain the mapping handle for reconciliation and never retry automatically.
    """

    def __init__(
        self,
        *,
        builder: LocalSanitizedBundleBuilder,
        verifier: LocalSanitizedVerifier,
        authority: PrivacyApprovalAuthority,
        confirmation: PrivacyExportConfirmation,
        sink: PrivacyExportSink,
        mapping_service: LocalMappingService,
        key_reference: UUID,
    ) -> None:
        self._builder = builder
        self._verifier = verifier
        self._authority = authority
        self._confirmation = confirmation
        self._sink = sink
        self._mapping_service = mapping_service
        self._key_reference = key_reference
        self._mapping_handle: LocalMappingHandle | None = None
        self._lock = RLock()

    @property
    def mapping_handle(self) -> LocalMappingHandle | None:
        """Local-only handle from the latest persistence; never an upload DTO or grant."""
        with self._lock:
            return self._mapping_handle

    def export(
        self,
        command: PrivacyReviewCommand,
        approval: LocalPrivacyApproval,
        *,
        reviewer_text: str | None = None,
    ) -> PrivacyManifest:
        with self._lock:
            self._mapping_handle = None
            try:
                command = PrivacyReviewCommand.model_validate(command)
                approval = LocalPrivacyApproval.model_validate(approval)
                text = (
                    sanitize_reviewer_text(reviewer_text, command).text
                    if reviewer_text is not None
                    else None
                )
                bundle = self._builder.build(command, approval)
                if type(bundle.pdf) is not bytes:
                    raise ValueError("Immutable PDF required")
                manifest_json = public_manifest_json(bundle.manifest)
                manifest = PrivacyManifest.model_validate_json(manifest_json)
                payload = PrivacyExportPayload(bundle.pdf, manifest_json, text)

                def admit() -> None:
                    if (
                        hashlib.sha256(payload.pdf).hexdigest() != manifest.sanitized_digest
                        or len(payload.pdf) != manifest.byte_size
                        or payload.manifest_json != manifest_json
                        or payload.reviewer_text != text
                        or payload.filename != "sanitized.pdf"
                    ):
                        raise PrivacyFault(PrivacyErrorCode.VERIFICATION_FAILED)
                    check_export_admission(
                        command,
                        approval,
                        manifest,
                        now=datetime.now(UTC),
                        approval_authority=self._authority,
                        artifact_verifier=self._verifier,
                    )

                admit()
                handle = self._mapping_service.create(
                    command, approval, manifest, key_reference=self._key_reference
                )
                # Retain the exact handle even if confirmation/transfer fails. A remote
                # outcome may be unknown; deleting this map would prevent reconciliation.
                self._mapping_handle = handle

                def read_mapping() -> None:
                    record = self._mapping_service.read(handle)
                    if record.command != command or record.manifest != manifest:
                        raise PrivacyFault(PrivacyErrorCode.VERIFICATION_FAILED)

                read_mapping()
                if self._confirmation.confirm(payload) is not True:
                    raise PrivacyFault(PrivacyErrorCode.UNAUTHORIZED)
                # Confirmation can outlive the key session, retention or local file.
                read_mapping()
                admit()
                self._sink.accept(payload)
                return manifest
            except PrivacyFault:
                raise
            except Exception:
                raise PrivacyFault(PrivacyErrorCode.VERIFICATION_FAILED) from None
