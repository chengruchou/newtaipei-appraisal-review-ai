"""Isolated raster processor and configured local OCR verification adapter."""

from __future__ import annotations

import base64
import hashlib
import sys
from pathlib import Path
from uuid import uuid4

from pydantic import Field

from appraisal_review.adapters.local.privacy.ocr import TesseractOCR
from appraisal_review.adapters.local.privacy.pdf_worker import ScanLimits
from appraisal_review.adapters.local.privacy.process import run_bounded
from appraisal_review.adapters.local.privacy.sanitize_worker import (
    SanitizeRequest,
    layout,
    token_box,
)
from appraisal_review.adapters.local.privacy.source import LocalRaster, RasterResult
from appraisal_review.domain.privacy_bundle import SanitizedBundle
from appraisal_review.domain.privacy_models import (
    LocalSourcePage,
    PlaceholderOccurrence,
    PrivacyManifest,
    PrivacyModel,
    PrivacyPage,
    PrivacyRegion,
    PrivacyReviewCommand,
)
from appraisal_review.domain.privacy_review import PrivacyPagePreview
from appraisal_review.domain.privacy_scan import TextObservation


class BuildResult(PrivacyModel):
    artifact: str = Field(repr=False)


class VerifyResult(PrivacyModel):
    previews: tuple[RasterResult, ...] = Field(repr=False)


class IsolatedPrivacyRasterProcessor:
    def __init__(self, work_directory: Path, limits: ScanLimits | None = None) -> None:
        if not work_directory.is_absolute() or not work_directory.is_dir():
            raise ValueError("Explicit existing local work directory required")
        self._work = work_directory.resolve(strict=True)
        self.limits = ScanLimits.model_validate(limits) if limits is not None else ScanLimits()

    def _call(self, request: SanitizeRequest) -> bytes:
        return run_bounded(
            [sys.executable, "-I", "-m", "appraisal_review.adapters.local.privacy.sanitize_worker"],
            request.model_dump_json().encode("utf-8"),
            cwd=self._work,
            timeout=self.limits.timeout,
            max_output=self.limits.max_bytes * 8,
        )

    def build(self, command: PrivacyReviewCommand, original: bytes) -> SanitizedBundle:
        command = PrivacyReviewCommand.model_validate(command)
        pages = layout(command, self.limits.dpi)
        occurrences = []
        positions: dict[int, int] = {}
        for selection in command.selections:
            if selection.disposition == "dismiss":
                continue
            if selection.entity_id is None:
                raise ValueError("Missing entity")
            page = selection.candidate.region.page
            index = positions.get(page, 0)
            occurrences.append(
                PlaceholderOccurrence(
                    occurrence_id=uuid4(),
                    entity_id=selection.entity_id,
                    region=PrivacyRegion(page=page, bbox=token_box(pages[page - 1], index)),
                )
            )
            positions[page] = index + 1
        draft = PrivacyManifest(
            case_id=command.source.case_id,
            document_id=command.source.document_id,
            sanitized_digest="0" * 64,
            byte_size=1,
            pages=pages,
            occurrences=tuple(occurrences),
        )
        result = BuildResult.model_validate_json(
            self._call(
                SanitizeRequest(
                    action="build",
                    original=base64.b64encode(original).decode("ascii"),
                    command=command,
                    manifest=draft,
                    limits=self.limits,
                )
            )
        )
        pdf = base64.b64decode(result.artifact, validate=True)
        manifest = PrivacyManifest(
            **draft.model_dump(exclude={"sanitized_digest", "byte_size"}),
            sanitized_digest=hashlib.sha256(pdf).hexdigest(),
            byte_size=len(pdf),
        )
        return SanitizedBundle(pdf, manifest)

    def verify(
        self,
        command: PrivacyReviewCommand,
        original: bytes,
        bundle: SanitizedBundle,
    ) -> tuple[PrivacyPagePreview, ...]:
        result = VerifyResult.model_validate_json(
            self._call(
                SanitizeRequest(
                    action="verify",
                    original=base64.b64encode(original).decode("ascii"),
                    command=command,
                    manifest=bundle.manifest,
                    limits=self.limits,
                    artifact=base64.b64encode(bundle.pdf).decode("ascii"),
                )
            )
        )
        return tuple(
            PrivacyPagePreview(p.width, p.height, base64.b64decode(p.png, validate=True))
            for p in result.previews
        )


class TesseractPrivacyOutputOCR:
    def __init__(self, ocr: TesseractOCR, *, dpi: int = 144) -> None:
        self._ocr = ocr
        self._dpi = dpi

    def identity(self) -> str:
        """Revalidate the pinned engine/assets before issuing a local review binding."""
        version = self._ocr.preflight(timeout=5)
        return hashlib.sha256(
            f"{version}\n{self._dpi}\n{self._ocr.config.model_dump_json()}".encode()
        ).hexdigest()

    def read(
        self,
        preview: PrivacyPagePreview,
        page: PrivacyPage,
        *,
        timeout: float,
    ) -> tuple[TextObservation, ...]:
        if {a.language for a in self._ocr.config.assets} != {"eng", "chi_tra"}:
            raise ValueError("Required OCR languages unavailable")
        geometry = LocalSourcePage(
            number=page.number,
            width=page.width,
            height=page.height,
            rotation=0,
            crop_box=(0.0, 0.0, page.width, page.height),
        )
        return self._ocr.recognize(
            LocalRaster(preview.width, preview.height, preview.png),
            geometry,
            dpi=self._dpi,
            timeout=timeout,
            max_text=100000,
        )
