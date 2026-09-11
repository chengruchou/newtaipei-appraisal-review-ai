"""Shared publication/download core with injected durable authority and object ports.

This module imports no AWS SDK. Local and cloud compositions use the same byte,
PDF, manifest, permission and expiry checks. Stores own atomic authority checks.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from pypdf import PdfReader

from appraisal_review.application.service_guards import Principal
from appraisal_review.domain.artifact_publication import (
    ArtifactKey,
    CommittedManifest,
    ManifestCandidate,
    PublicationError,
    PublishedArtifact,
)
from appraisal_review.domain.factor_models import WorkflowStatus
from appraisal_review.domain.service_contracts import Permission, RunReference
from appraisal_review.ports.artifact_publication import (
    ArtifactObjectStore,
    ManifestRepository,
    PublicationAttempt,
)


def _verify(data: bytes, artifact: PublishedArtifact) -> None:
    if (
        len(data) != artifact.size_bytes
        or hashlib.sha256(data).hexdigest() != artifact.content_hash
    ):
        raise PublicationError("artifact_mismatch")
    try:
        reader = PdfReader(BytesIO(data), strict=True)
        if reader.is_encrypted or len(reader.pages) != artifact.page_count:
            raise ValueError("PDF structure mismatch")
        metadata = reader.metadata
        if (
            metadata is None
            or metadata.get("/AppraisalReviewWriterVersion") != artifact.writer_version
        ):
            raise ValueError("Writer version mismatch")
        # Force page content decoding, not just a header check.
        for page in reader.pages:
            page.extract_text()
    except Exception:
        raise PublicationError("artifact_mismatch") from None


class AttemptArtifactPublisher:
    def __init__(self, *, objects: ArtifactObjectStore, manifests: ManifestRepository) -> None:
        self.objects, self.manifests = objects, manifests

    def stage(
        self, local: Path, *, run: RunReference, artifact: PublishedArtifact, writer: object
    ) -> PublishedArtifact:
        if getattr(writer, "reveals_placeholders", None) is not False:
            raise PublicationError("revealed_output_forbidden")
        artifact = PublishedArtifact.model_validate_json(artifact.model_dump_json())
        if ArtifactKey.for_run(run, artifact.artifact_id).key() != artifact.key:
            raise PublicationError("artifact_mismatch")
        data = local.read_bytes()
        _verify(data, artifact)
        version = self.objects.create(artifact.key, data)
        if not isinstance(version, str) or not version or version == "null":
            raise PublicationError("artifact_version_missing")
        staged = artifact.model_copy(update={"object_version": version})
        _verify(self.objects.read(staged), staged)
        return staged

    def publish(
        self,
        candidate: ManifestCandidate,
        *,
        fencing_token: int,
        principal: Principal,
        attempt: PublicationAttempt,
    ) -> CommittedManifest:
        candidate = ManifestCandidate.model_validate_json(candidate.model_dump_json())
        self.manifests.authorize(principal, candidate.run.revision.case_id, Permission.PUBLISH)
        if candidate.review_status != WorkflowStatus.COMPLETED:
            raise PublicationError("review_not_publishable")
        for artifact in candidate.artifacts:
            if not artifact.source_versions or artifact.font_hash is None:
                raise PublicationError("artifact_evidence_missing")
            if not artifact.object_version or artifact.object_version == "null":
                raise PublicationError("artifact_version_missing")
            _verify(self.objects.read(artifact), artifact)
        return self.manifests.commit(
            candidate, fencing_token=fencing_token, principal=principal, attempt=attempt
        )


class CommittedResultResolver:
    def __init__(
        self,
        *,
        manifests: ManifestRepository,
        objects: ArtifactObjectStore,
        download_issuer: Callable[[PublishedArtifact, int], str] | None = None,
        work_directory: Path | None = None,
    ) -> None:
        self.manifests, self.objects = manifests, objects
        self.download_issuer, self.work_directory = download_issuer, work_directory

    def result(self, principal: Principal, case_id: str, run_id: UUID) -> CommittedManifest | None:
        self.manifests.authorize(principal, case_id, Permission.REVIEW)
        result = self.manifests.read(case_id, run_id)
        self.manifests.authorize(principal, case_id, Permission.REVIEW)
        return result

    def verified_bytes(
        self,
        principal: Principal,
        case_id: str,
        run_id: UUID,
        artifact_id: UUID,
    ) -> tuple[PublishedArtifact, bytes]:
        result = self.result(principal, case_id, run_id)
        if result is not None:
            for artifact in result.candidate.artifacts:
                if artifact.artifact_id == artifact_id:
                    if not artifact.object_version or artifact.object_version == "null":
                        raise PublicationError("artifact_version_missing")
                    data = self.objects.read(artifact)
                    _verify(data, artifact)
                    self.manifests.authorize(principal, case_id, Permission.REVIEW)
                    return artifact, data
        raise PublicationError("unpublished")

    def download_url(
        self,
        principal: Principal,
        case_id: str,
        run_id: UUID,
        artifact_id: UUID,
        *,
        expires_in: int = 300,
    ) -> str:
        if type(expires_in) is not int or expires_in < 1:
            raise ValueError("Download expiry must be a positive integer")
        artifact, _ = self.verified_bytes(principal, case_id, run_id, artifact_id)
        deadline = self.manifests.authorize(principal, case_id, Permission.REVIEW)
        expires = min(expires_in, 900, deadline - int(self.manifests.clock()))
        if expires < 1:
            raise PublicationError("publication_unauthorized")
        if self.download_issuer is None:
            raise PublicationError("download_issuer_unavailable")
        return self.download_issuer(artifact, expires)

    def fetch_verified(
        self,
        principal: Principal,
        case_id: str,
        run_id: UUID,
        artifact_id: UUID,
        destination: Path,
    ) -> PublishedArtifact:
        artifact, data = self.verified_bytes(principal, case_id, run_id, artifact_id)
        with TemporaryDirectory(
            prefix="artifact-fetch-", dir=self.work_directory or destination.parent
        ) as directory:
            path = Path(directory) / "verified.pdf"
            path.write_bytes(data)
            self.manifests.authorize(principal, case_id, Permission.REVIEW)
            try:
                os.link(path, destination)
            except FileExistsError:
                raise PublicationError("destination_exists") from None
        return artifact
