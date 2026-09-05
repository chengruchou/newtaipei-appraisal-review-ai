"""Local reviewer store using OS identity, private permissions and signed receipts.

The configured store and its owning OS account are trusted. This does not protect
against that account being compromised or a malicious system administrator.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import pwd
import secrets
import stat
from datetime import UTC, datetime
from pathlib import Path

from pydantic import Field

from appraisal_review.domain.confidence import confirmation_digest
from appraisal_review.domain.document_models import DocumentModel
from appraisal_review.domain.factor_models import ReviewMaterial
from appraisal_review.domain.review_contracts import content_digest


class Reviewer(DocumentModel):
    uid: int = Field(ge=0)
    name: str = Field(min_length=1)


class ApprovalReceipt(DocumentModel):
    material_digest: str
    case_id: str
    case_version: str
    reviewer: Reviewer
    approved_at: datetime
    signature: str

    def signed_bytes(self) -> bytes:
        return json.dumps(
            self.model_dump(mode="json", exclude={"signature"}),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()


def current_reviewer() -> Reviewer:
    uid = os.getuid()
    return Reviewer(uid=uid, name=pwd.getpwuid(uid).pw_name)


class LocalApprovalStore:
    def __init__(self, root: Path) -> None:
        self.root = root.absolute()

    @classmethod
    def initialize(cls, root: Path, reviewer: Reviewer) -> LocalApprovalStore:
        if reviewer != current_reviewer():
            raise PermissionError("Initialization requires the actual OS reviewer identity")
        root.mkdir(mode=0o700, parents=True, exist_ok=False)
        store = cls(root)
        store._write_new("reviewer.json", reviewer.model_dump_json().encode())
        store._write_new("signing.key", secrets.token_bytes(32))
        return store

    def _write_new(self, name: str, data: bytes) -> None:
        fd = os.open(self.root / name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())

    def _read_private(self, path: Path, *, directory: bool = False) -> bytes:
        info = path.lstat()
        if (
            stat.S_ISLNK(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077
            or (directory and not stat.S_ISDIR(info.st_mode))
            or (not directory and not stat.S_ISREG(info.st_mode))
        ):
            raise PermissionError(
                "Approval store must be private and owned by the current reviewer"
            )
        return b"" if directory else path.read_bytes()

    def _identity_key(self) -> tuple[Reviewer, bytes]:
        self._read_private(self.root, directory=True)
        reviewer = Reviewer.model_validate_json(self._read_private(self.root / "reviewer.json"))
        if reviewer != current_reviewer():
            raise PermissionError("Unauthorized OS reviewer")
        key = self._read_private(self.root / "signing.key")
        if len(key) != 32:
            raise PermissionError("Invalid signing key")
        return reviewer, key

    def approve(self, material: ReviewMaterial, *, expected_digest: str) -> ApprovalReceipt:
        reviewer, key = self._identity_key()
        if not confirmations_valid(material, reviewer):
            raise ValueError("Material requires current reviewer confirmation")
        digest = content_digest(material)
        if not hmac.compare_digest(digest, expected_digest):
            raise ValueError("Reviewed digest does not match exact material")
        receipt = ApprovalReceipt(
            material_digest=digest,
            case_id=material.policy.identity.case_id,
            case_version=material.policy.identity.version,
            reviewer=reviewer,
            approved_at=datetime.now(UTC),
            signature="",
        )
        receipt.signature = hmac.new(key, receipt.signed_bytes(), hashlib.sha256).hexdigest()
        self._write_new(f"{digest}.json", receipt.model_dump_json(indent=2).encode())
        return receipt

    def permits(self, material: ReviewMaterial) -> bool:
        try:
            reviewer, key = self._identity_key()
            digest = content_digest(material)
            receipt = ApprovalReceipt.model_validate_json(
                self._read_private(self.root / f"{digest}.json")
            )
            signature = hmac.new(key, receipt.signed_bytes(), hashlib.sha256).hexdigest()
            return (
                confirmations_valid(material, reviewer)
                and hmac.compare_digest(signature, receipt.signature)
                and receipt.material_digest == digest
                and receipt.case_id == material.policy.identity.case_id
                and receipt.case_version == material.policy.identity.version
                and receipt.reviewer == reviewer
                and receipt.approved_at.tzinfo is not None
                and receipt.approved_at <= datetime.now(UTC)
            )
        except (OSError, ValueError, PermissionError):
            return False


def confirmations_valid(material: ReviewMaterial, reviewer: Reviewer) -> bool:
    for pair in material.facts.pairs:
        for side in ("target", "comparable"):
            reliability = getattr(pair, f"{side}_reliability")
            if reliability.method == "reviewer_confirmed":
                confirmation = reliability.confirmation
                if (
                    confirmation is None
                    or confirmation.reviewer != f"{reviewer.uid}:{reviewer.name}"
                    or confirmation.input_digest != confirmation_digest(pair, side)
                ):
                    return False
    return True
