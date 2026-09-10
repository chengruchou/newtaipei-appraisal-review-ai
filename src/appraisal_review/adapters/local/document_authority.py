"""Explicit case/purpose grants and pinned public keys for controlled document admission."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from appraisal_review.application.service_guards import Principal
from appraisal_review.domain.document_transfer import (
    DocumentErrorCode,
    DocumentFault,
    DocumentOperation,
    PrivacyAttestation,
    Purpose,
    canonical_bytes,
)
from appraisal_review.domain.service_contracts import Permission


@dataclass(frozen=True)
class DocumentGrant:
    actor_id: UUID
    case_id: UUID
    purposes: frozenset[Purpose]
    operations: frozenset[DocumentOperation]


class ConfiguredDocumentAuthorization:
    """Additional document grants do not replace authenticated service case membership."""

    def __init__(self, grants: tuple[DocumentGrant, ...]) -> None:
        self.grants = grants

    def require(
        self, principal: Principal, case_id: str, purpose: Purpose, operation: DocumentOperation
    ) -> None:
        if (
            case_id not in principal.case_ids
            or Permission.REVIEW not in principal.permissions
            or principal.actor.kind == "model"
            or not any(
                str(g.actor_id) == principal.actor.actor_id
                and str(g.case_id) == case_id
                and purpose in g.purposes
                and operation in g.operations
                for g in self.grants
            )
        ):
            raise DocumentFault(DocumentErrorCode.UNAUTHORIZED)


@dataclass(frozen=True)
class TrustedExportKey:
    key_id: UUID
    public_key: Ed25519PublicKey
    actor_id: UUID
    case_ids: frozenset[UUID]


class Ed25519ExportVerifier:
    """Private keys stay in the local export process; no self-supplied public keys."""

    def __init__(
        self,
        keys: tuple[TrustedExportKey, ...],
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        max_age: timedelta = timedelta(minutes=10),
    ) -> None:
        if max_age <= timedelta(0) or len({k.key_id for k in keys}) != len(keys):
            raise ValueError("Invalid export verification configuration")
        self.keys = {k.key_id: k for k in keys}
        self.clock = clock
        self.max_age = max_age

    def verify(self, attestation: PrivacyAttestation, principal: Principal) -> None:
        claims = attestation.claims
        key = self.keys.get(claims.key_id)
        now = self.clock()
        if (
            key is None
            or principal.actor.kind != "human"
            or str(claims.principal_id) != principal.actor.actor_id
            or key.actor_id != claims.principal_id
            or claims.manifest.case_id not in key.case_ids
            or not claims.confirmed_at <= now < claims.expires_at
            or claims.expires_at - claims.confirmed_at > self.max_age
            or now - claims.confirmed_at > self.max_age
        ):
            raise DocumentFault(DocumentErrorCode.PRIVACY)
        try:
            key.public_key.verify(bytes.fromhex(attestation.signature_hex), canonical_bytes(claims))
        except (ValueError, InvalidSignature):
            raise DocumentFault(DocumentErrorCode.PRIVACY) from None
