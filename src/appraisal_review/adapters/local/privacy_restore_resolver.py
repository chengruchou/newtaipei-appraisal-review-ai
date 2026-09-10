"""Local restoration plans from current authorized publication and exact encrypted maps."""

from __future__ import annotations

import hashlib
import math
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from uuid import UUID, uuid4

import pymupdf

from appraisal_review.adapters.local.privacy_bridge import (
    PrivacyBridgeRestore,
    PrivacyBridgeSession,
)
from appraisal_review.application.document_transfer import DocumentTransferService
from appraisal_review.application.privacy_refill import validate_refill
from appraisal_review.application.service_guards import Principal
from appraisal_review.domain.artifact_publication import ArtifactKey, PublishedArtifact
from appraisal_review.domain.document_transfer import DocumentMetadata
from appraisal_review.domain.privacy_mapping import LocalMappingRecord
from appraisal_review.domain.privacy_models import RehydrationField, RehydrationPlan
from appraisal_review.domain.privacy_refill import (
    PublishedRefillArtifact,
    PublishedRefillDescriptor,
    RefillTarget,
)
from appraisal_review.domain.service_contracts import Permission, RunReference
from appraisal_review.ports.privacy import PrivacyOutputOCR
from appraisal_review.ports.privacy_refill import PrivacyRefillProcessor


@dataclass(frozen=True, repr=False)
class RestorePublication:
    """Trusted callback result, never accepted from HTTP or a model.

    The callback must reauthorize the principal, require the job's CURRENT
    completed run/result, and fetch exact bytes through CommittedResultResolver
    on every call. A historical published object alone is insufficient.
    """

    principal: Principal
    job_id: UUID
    run: RunReference
    artifact: PublishedArtifact
    pdf: bytes = field(repr=False)
    forms: DocumentMetadata


class _CurrentPublication:
    def __init__(
        self,
        owner: LocalRestoreCoordinator,
        principal: str,
        result: UUID,
        expected: RestorePublication,
        mapping: LocalMappingRecord,
        plan: RehydrationPlan,
        artifact: PublishedRefillArtifact,
    ) -> None:
        self.owner, self.principal, self.result = owner, principal, result
        self.expected, self.mapping, self.plan, self.artifact = expected, mapping, plan, artifact

    def current(self, plan: RehydrationPlan) -> PublishedRefillArtifact:
        if not self.permits(plan, self.artifact):
            raise ValueError("Current publication authority is unavailable")
        return self.artifact

    def permits(
        self,
        plan: RehydrationPlan,
        artifact: PublishedRefillArtifact | None = None,
    ) -> bool:
        try:
            actual, mapping = self.owner._checked(self.principal, self.result)
            return (
                actual == self.expected
                and mapping == self.mapping
                and plan == self.plan
                and (artifact is None or artifact == self.artifact)
            )
        except Exception:
            return False


class LocalRestoreCoordinator:
    """PrivacyBridgeResultResolver using the existing real local refill executor.

    Owns no backend result authority. All callback I/O is synchronous; a caller
    on the bridge event loop must use fresh synchronous storage reads, never
    wait for a coroutine scheduled back onto the same loop.
    """

    def __init__(
        self,
        *,
        workspace: Path,
        session: PrivacyBridgeSession,
        documents: DocumentTransferService,
        publication: Callable[[str, UUID], RestorePublication],
        processor: PrivacyRefillProcessor,
        ocr: PrivacyOutputOCR,
    ) -> None:
        if not workspace.is_absolute() or workspace.is_symlink() or not workspace.is_dir():
            raise ValueError("Existing private local workspace required")
        info = workspace.stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("Owned private local workspace required")
        self.workspace, self.session, self.documents = workspace, session, documents
        self.publication, self.processor, self.ocr = publication, processor, ocr
        self._lock = RLock()
        self._cache: dict[UUID, tuple[RestorePublication, PrivacyBridgeRestore]] = {}

    def _checked(
        self, principal_id: str, result_id: UUID
    ) -> tuple[RestorePublication, LocalMappingRecord]:
        if principal_id != self.session.principal_id:
            raise ValueError("Local session differs")
        value = self.publication(principal_id, result_id)
        if type(value) is not RestorePublication or type(value.pdf) is not bytes:
            raise ValueError("Current publication evidence required")
        run = RunReference.model_validate_json(value.run.model_dump_json())
        artifact = PublishedArtifact.model_validate_json(value.artifact.model_dump_json())
        forms = DocumentMetadata.model_validate_json(value.forms.model_dump_json())
        value.principal.require(run.revision.case_id, Permission.REVIEW)
        manifest = forms.attestation.claims.manifest
        key = ArtifactKey.parse(artifact.key)
        if (
            value.principal.actor.actor_id != principal_id
            or value.principal.actor.kind != "human"
            or forms.reference.purpose != "forms"
            or forms.reference.case_id != run.revision.case_id
            or str(manifest.case_id) != run.revision.case_id
            or artifact.template_hash != manifest.sanitized_digest
            or forms.reference.content_hash != manifest.sanitized_digest
            or artifact.page_count != len(manifest.pages)
            or hashlib.sha256(value.pdf).hexdigest() != artifact.content_hash
            or len(value.pdf) != artifact.size_bytes
            or not artifact.object_version
            or artifact.object_version == "null"
            or (key.case_id, key.run_id, key.attempt_id)
            != (run.revision.case_id, run.run_id, run.attempt_id)
            or sum(
                (source.document_id, source.version, source.content_hash)
                == (
                    forms.reference.document_id,
                    forms.reference.version,
                    forms.reference.content_hash,
                )
                for source in artifact.source_versions
            )
            != 1
        ):
            raise ValueError("Publication and exact sanitized forms differ")
        admitted = self.documents.read(value.principal, forms.reference)
        if (
            admitted.metadata != forms
            or hashlib.sha256(admitted.content).hexdigest() != artifact.template_hash
        ):
            raise ValueError("Authorized forms changed")
        records = []
        for handle in tuple(self.session._maps.values()):
            if handle.case_id != manifest.case_id:
                continue
            record = self.session.mappings.read(handle)
            if (
                record.manifest == manifest
                and record.command.source.document_id == manifest.document_id
            ):
                records.append(record)
        if len(records) != 1:
            raise ValueError("One exact session-owned mapping required")
        mapping = records[0]
        if (
            mapping.command.source.case_id != manifest.case_id
            or self.session.sources.read(mapping.command.source) == admitted.content
            or not manifest.occurrences
        ):
            raise ValueError("Original and sanitized identities must remain separate")
        return RestorePublication(
            value.principal, value.job_id, run, artifact, value.pdf, forms
        ), mapping

    def _prove_placeholder_pixels(
        self, value: RestorePublication, mapping: LocalMappingRecord
    ) -> None:
        template = self.documents.read(value.principal, value.forms.reference).content
        pages = mapping.manifest.pages
        before = self.processor.render(template, pages)
        after = self.processor.render(value.pdf, pages)
        if len(before) != len(pages) or len(after) != len(pages):
            raise ValueError("Published page coverage differs")
        for geometry, left, right in zip(pages, before, after, strict=True):
            a = pymupdf.Pixmap(left.preview.png)  # type: ignore[no-untyped-call]
            b = pymupdf.Pixmap(right.preview.png)  # type: ignore[no-untyped-call]
            if (a.width, a.height, a.n, a.alpha) != (b.width, b.height, b.n, b.alpha) or a.n != 3:
                raise ValueError("Published raster geometry differs")
            original, published = a.samples, b.samples
            for occurrence in mapping.manifest.occurrences:
                if occurrence.region.page != geometry.number:
                    continue
                x0, y0, x1, y1 = occurrence.region.bbox
                left_x = max(0, math.floor(x0 * a.width / geometry.width))
                right_x = min(a.width, math.ceil(x1 * a.width / geometry.width))
                top = max(0, math.floor((geometry.height - y1) * a.height / geometry.height))
                bottom = min(
                    a.height, math.ceil((geometry.height - y0) * a.height / geometry.height)
                )
                for row in range(top, bottom):
                    start, end = (row * a.width + left_x) * 3, (row * a.width + right_x) * 3
                    if original[start:end] != published[start:end]:
                        raise ValueError("Published placeholder pixels changed")

    def resolve(self, principal_id: str, result_id: UUID) -> PrivacyBridgeRestore:
        with self._lock:
            value, mapping = self._checked(principal_id, result_id)
            cached = self._cache.get(result_id)
            if cached is not None:
                if cached[0] != value or not cached[1].authority.permits(cached[1].plan):
                    raise ValueError("Current publication changed")
                return cached[1]
            self._prove_placeholder_pixels(value, mapping)
            manifest = mapping.manifest
            # Existing occurrence IDs identify local restoration fields; no regenerated
            # export, entity, occurrence or map identity enters this coordinator.
            targets = tuple(
                RefillTarget(
                    occurrence_id=item.occurrence_id,
                    entity_id=item.entity_id,
                    output_field_id=item.occurrence_id,
                    region=item.region,
                )
                for item in manifest.occurrences
            )
            descriptor = PublishedRefillDescriptor(
                case_id=manifest.case_id,
                document_id=manifest.document_id,
                run_id=value.run.run_id,
                revision_id=UUID(value.run.revision.revision_id),
                artifact_digest=value.artifact.content_hash,
                base_sanitized_digest=manifest.sanitized_digest,
                template_digest=value.artifact.template_hash,
                pages=manifest.pages,
                targets=targets,
            )
            plan = RehydrationPlan(
                **descriptor.model_dump(exclude={"targets", "base_sanitized_digest"}),
                map_id=mapping.map_id,
                fields=tuple(
                    RehydrationField(
                        occurrence_id=item.occurrence_id,
                        entity_id=item.entity_id,
                        output_field_id=item.output_field_id,
                        destination=item.region,
                        operation="restore_original_crop",
                    )
                    for item in targets
                ),
            )
            artifact = PublishedRefillArtifact(descriptor, value.pdf)
            validate_refill(mapping, plan, artifact)
            authority = _CurrentPublication(
                self, principal_id, result_id, value, mapping, plan, artifact
            )
            if not authority.permits(plan):
                raise ValueError("Current publication changed")
            # A fresh private directory and exclusive file protect original/download
            # aliases. The bridge independently confines and hashes this download.
            directory = self.workspace / f"authorized-download-{uuid4()}"
            directory.mkdir(mode=0o700)
            path = directory / "published.pdf"
            descriptor_fd = os.open(
                path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
            with os.fdopen(descriptor_fd, "wb") as stream:
                stream.write(value.pdf)
                stream.flush()
                os.fsync(stream.fileno())
            if not authority.permits(plan):
                raise ValueError("Current publication changed")
            result = PrivacyBridgeRestore(
                plan, authority, authority, self.processor, self.ocr, path
            )
            self._cache[result_id] = value, result
            return result
