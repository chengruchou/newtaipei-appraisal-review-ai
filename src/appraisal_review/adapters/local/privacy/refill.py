"""Bounded refill processing and reuse of the existing atomic local PDF publisher."""

from __future__ import annotations

import base64
import hashlib
import os
import stat
import sys
from pathlib import Path

from pydantic import Field

from appraisal_review.adapters.local.object_access import LocalObjectAccess
from appraisal_review.adapters.local.privacy.pdf_worker import ScanLimits
from appraisal_review.adapters.local.privacy.process import run_bounded
from appraisal_review.adapters.local.privacy.refill_worker import RefillWorkerRequest
from appraisal_review.adapters.local.privacy.source import LocalSnapshotStore, RasterResult
from appraisal_review.application.privacy_guards import PrivacyFault
from appraisal_review.domain.privacy_mapping import LocalMappingRecord
from appraisal_review.domain.privacy_models import (
    LocalSourceSnapshot,
    PrivacyErrorCode,
    PrivacyModel,
    PrivacyPage,
    RehydrationPlan,
)
from appraisal_review.domain.privacy_refill import (
    FinalLocalArtifact,
    FinalLocalManifest,
    PublishedRefillArtifact,
    RefillRenderedPage,
)
from appraisal_review.domain.privacy_review import PrivacyPagePreview
from appraisal_review.domain.privacy_scan import TextObservation


class RenderedRefillPage(RasterResult):
    native: tuple[TextObservation, ...] = Field(repr=False)


class RefillRenderResult(PrivacyModel):
    pages: tuple[RenderedRefillPage, ...] = Field(repr=False)


class RefillWriteResult(PrivacyModel):
    pdf: str = Field(repr=False)


class IsolatedPrivacyRefillProcessor:
    def __init__(
        self,
        work_directory: Path,
        *,
        font_digest: str | None = None,
        font_size: float = 9.0,
        limits: ScanLimits | None = None,
    ) -> None:
        if not work_directory.is_absolute() or not work_directory.is_dir():
            raise ValueError("Explicit existing local work directory required")
        self._work = work_directory.resolve(strict=True)
        self._font_digest, self._font_size = font_digest, font_size
        self._limits = ScanLimits.model_validate(limits) if limits is not None else ScanLimits()

    def _call(self, request: RefillWorkerRequest) -> bytes:
        return run_bounded(
            [sys.executable, "-I", "-m", "appraisal_review.adapters.local.privacy.refill_worker"],
            request.model_dump_json().encode("utf-8"),
            cwd=self._work,
            timeout=self._limits.timeout,
            max_output=self._limits.max_bytes * 8,
        )

    def render(self, data: bytes, pages: tuple[PrivacyPage, ...]) -> tuple[RefillRenderedPage, ...]:
        result = RefillRenderResult.model_validate_json(
            self._call(
                RefillWorkerRequest(
                    action="render",
                    data=base64.b64encode(data).decode("ascii"),
                    pages=pages,
                    limits=self._limits,
                )
            )
        )
        return tuple(
            RefillRenderedPage(
                PrivacyPagePreview(p.width, p.height, base64.b64decode(p.png, validate=True)),
                p.native,
            )
            for p in result.pages
        )

    def write(
        self,
        mapping: LocalMappingRecord,
        plan: RehydrationPlan,
        artifact: PublishedRefillArtifact,
        original: bytes,
    ) -> bytes:
        result = RefillWriteResult.model_validate_json(
            self._call(
                RefillWorkerRequest(
                    action="write",
                    data=base64.b64encode(artifact.pdf).decode("ascii"),
                    pages=plan.pages,
                    limits=self._limits,
                    original=base64.b64encode(original).decode("ascii"),
                    mapping=mapping,
                    plan=plan,
                    descriptor=artifact.descriptor,
                    font_digest=self._font_digest,
                    font_size=self._font_size,
                )
            )
        )
        return base64.b64decode(result.pdf, validate=True)


class LocalRefillFileSink:
    def __init__(
        self, store: LocalSnapshotStore, output_directory: Path, *, workspace: Path
    ) -> None:
        if (
            not output_directory.is_absolute()
            or not output_directory.is_dir()
            or output_directory.resolve(strict=True) != output_directory
            or (
                not workspace.is_absolute()
                or workspace.resolve(strict=True) != workspace
                or not output_directory.is_relative_to(workspace)
            )
        ):
            raise ValueError("Explicit confined output directory required")
        self._store, self._directory = store, output_directory

    def save(self, artifact: FinalLocalArtifact, source: LocalSourceSnapshot) -> None:
        try:
            manifest = FinalLocalManifest.model_validate(artifact.manifest)
            if (
                manifest.case_id != source.case_id
                or manifest.document_id != source.document_id
                or (
                    len(artifact.pdf) != manifest.byte_size
                    or hashlib.sha256(artifact.pdf).hexdigest() != manifest.final_digest
                )
            ):
                raise ValueError("Output identity differs")
            self._store.read(source)
            original = self._store.source_file(source)
            with LocalObjectAccess(overwrite_existing=False).staged_write(
                original.as_uri(), (self._directory / manifest.filename).as_uri(), ()
            ) as session:
                if session._source_sha256.hex() != source.source_digest:
                    raise ValueError("Original file changed since capture")
                # Open without truncation, then verify the already-owned inode before writing.
                with session.temporary_path.open("r+b") as stream:
                    info = os.fstat(stream.fileno())
                    if (
                        not stat.S_ISREG(info.st_mode)
                        or info.st_nlink != 1
                        or ((info.st_dev, info.st_ino) != session._temporary_identity)
                    ):
                        raise ValueError("Temporary output replaced")
                    stream.truncate(0)
                    stream.write(artifact.pdf)
                    stream.flush()
                    os.fsync(stream.fileno())
                    stream.seek(0)
                    if hashlib.sha256(stream.read()).hexdigest() != manifest.final_digest:
                        raise ValueError("Written bytes differ")
                session.publish()
        except Exception:
            raise PrivacyFault(PrivacyErrorCode.VERIFICATION_FAILED) from None
