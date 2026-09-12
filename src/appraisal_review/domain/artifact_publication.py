"""Attempt-scoped artifact keys and fenced publication manifests.

A worker writes artifacts under its own attempt-scoped keys and proposes a
manifest candidate. Only a commit carrying the current fencing token becomes
the run's result; object existence, worker logs and queue acknowledgements
never do. These models are provider-neutral wire/state shapes; conditional
storage semantics live in adapters and DTO validity grants no authority.
"""

from __future__ import annotations

import re
from typing import Literal, cast
from uuid import UUID

from pydantic import Field, model_validator

from appraisal_review.domain.document_models import Digest, DocumentModel
from appraisal_review.domain.factor_models import WorkflowStatus
from appraisal_review.domain.review_contracts import ComparisonContext, content_digest
from appraisal_review.domain.service_contracts import OpaqueID, RunReference, ServiceModel

_SEGMENT = r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}"
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_ARTIFACT_KEY = re.compile(
    rf"^cases/(?P<case_id>{_SEGMENT})"
    rf"/runs/(?P<run_id>{_UUID})"
    rf"/attempts/(?P<attempt_id>{_UUID})"
    rf"/artifacts/(?P<artifact_id>{_UUID})\.(?P<suffix>pdf|xlsx)$"
)


#: Formats this system actually writes. The official workbook and the PDF converted from
#: it are the only two, and a key must say which one it addresses.
ArtifactSuffix = Literal["pdf", "xlsx"]


class PublicationError(Exception):
    """Stable code only; storage details never cross this boundary."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ArtifactKey(DocumentModel):
    """Parsed attempt-scoped object key; segments never contain separators.

    The suffix is part of the key because a run may publish the official workbook and the
    PDF converted from it, and the two must not collide on one object. It defaults to pdf so
    every existing caller and stored key keeps its exact spelling.
    """

    case_id: OpaqueID
    run_id: UUID
    attempt_id: UUID
    artifact_id: UUID
    suffix: ArtifactSuffix = "pdf"

    def key(self) -> str:
        return (
            f"cases/{self.case_id}/runs/{self.run_id}"
            f"/attempts/{self.attempt_id}/artifacts/{self.artifact_id}.{self.suffix}"
        )

    @classmethod
    def for_run(
        cls, run: RunReference, artifact_id: UUID, *, suffix: ArtifactSuffix = "pdf"
    ) -> ArtifactKey:
        if run.attempt_id is None:
            raise ValueError("Artifact keys are attempt-scoped; the run must bind an attempt")
        return cls(
            case_id=run.revision.case_id,
            run_id=run.run_id,
            attempt_id=run.attempt_id,
            artifact_id=artifact_id,
            suffix=suffix,
        )

    @classmethod
    def parse(cls, key: str) -> ArtifactKey:
        match = _ARTIFACT_KEY.fullmatch(key)
        if match is None:
            raise ValueError("Object key is outside the attempt-scoped artifact layout")
        return cls(
            case_id=match["case_id"],
            run_id=UUID(match["run_id"]),
            attempt_id=UUID(match["attempt_id"]),
            artifact_id=UUID(match["artifact_id"]),
            suffix=cast(ArtifactSuffix, match["suffix"]),
        )


class SourceVersion(ServiceModel):
    """Pinned reviewed input identity recorded with the published output."""

    document_id: OpaqueID
    version: OpaqueID
    content_hash: Digest


class PublishedArtifact(ServiceModel):
    """One attempt-scoped object with independently verifiable facts."""

    artifact_id: UUID
    key: str
    content_hash: Digest
    # Optional for legacy decoding; publishing requires an immutable S3 version.
    object_version: str | None = Field(default=None, min_length=1)
    font_hash: Digest | None = None
    size_bytes: int = Field(ge=1, strict=True)
    content_type: Literal["application/pdf"] = "application/pdf"
    writer_version: str = Field(min_length=1)
    template_id: OpaqueID
    template_version: OpaqueID
    template_hash: Digest
    field_map_hash: Digest
    contexts: tuple[ComparisonContext, ...] = ()
    field_ids: tuple[str, ...] = Field(min_length=1)
    page_count: int = Field(ge=1, strict=True)
    source_versions: tuple[SourceVersion, ...] = ()
    # Cloud artifacts carry opaque placeholder tokens only; a locally revealed
    # copy is never eligible for publication through this contract.
    placeholder_only: Literal[True] = True
    verification: Literal["local_writer_reopened"] = "local_writer_reopened"

    @model_validator(mode="after")
    def coherent_key(self) -> PublishedArtifact:
        parsed = ArtifactKey.parse(self.key)
        if parsed.artifact_id != self.artifact_id:
            raise ValueError("Artifact key and artifact identity must match")
        if len(self.field_ids) != len(set(self.field_ids)):
            raise ValueError("Published field IDs must be unique")
        return self


class ManifestCandidate(ServiceModel):
    """A worker's proposed result; publication requires the current fencing token."""

    run: RunReference
    result_version: int = Field(ge=1, strict=True)
    review_status: WorkflowStatus
    artifacts: tuple[PublishedArtifact, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def attempt_scoped(self) -> ManifestCandidate:
        if self.run.attempt_id is None:
            raise ValueError("A manifest candidate must identify its attempt")
        keys = [artifact.key for artifact in self.artifacts]
        ids = [artifact.artifact_id for artifact in self.artifacts]
        if len(keys) != len(set(keys)) or len(ids) != len(set(ids)):
            raise ValueError("Candidate artifacts must be unique")
        for artifact in self.artifacts:
            parsed = ArtifactKey.parse(artifact.key)
            if (
                parsed.case_id != self.run.revision.case_id
                or parsed.run_id != self.run.run_id
                or parsed.attempt_id != self.run.attempt_id
            ):
                raise ValueError("Candidate artifacts must belong to this exact attempt")
        return self

    def digest(self) -> str:
        return content_digest(self)


class CommittedManifest(ServiceModel):
    """The run's current published result; only committed manifests are exposed."""

    candidate: ManifestCandidate
    fencing_token: int = Field(ge=0, strict=True)
    manifest_digest: Digest

    @model_validator(mode="after")
    def bound_digest(self) -> CommittedManifest:
        if self.manifest_digest != self.candidate.digest():
            raise ValueError("Committed manifest digest must match its candidate")
        return self
