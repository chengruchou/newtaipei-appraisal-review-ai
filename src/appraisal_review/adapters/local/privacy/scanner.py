"""Local scan orchestration with explicit per-page failure and manual coverage."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import time
from contextlib import suppress
from typing import Literal

from appraisal_review.adapters.local.privacy.detector import detect_candidates
from appraisal_review.adapters.local.privacy.ocr import TesseractOCR
from appraisal_review.adapters.local.privacy.process import ScanFailure
from appraisal_review.adapters.local.privacy.source import LocalSnapshotStore
from appraisal_review.domain.privacy_models import LocalSourceSnapshot, SensitiveCandidate
from appraisal_review.domain.privacy_scan import (
    PageScan,
    PrivacyCapabilities,
    PrivacyScanReport,
    ScanIssue,
)


class LocalPrivacyScanner:
    def __init__(self, store: LocalSnapshotStore, ocr: TesseractOCR | None = None) -> None:
        self.store, self.ocr = store, ocr

    def capabilities(self) -> PrivacyCapabilities:
        version = None
        if self.ocr is not None:
            with suppress(ScanFailure):
                version = self.ocr.preflight(timeout=5.0)
        return PrivacyCapabilities(
            native_pdf=importlib.util.find_spec("pymupdf") is not None,
            local_ocr=version is not None
            and self.ocr is not None
            and {"eng", "chi_tra"} <= {a.language for a in self.ocr.config.assets},
            languages=tuple(a.language for a in self.ocr.config.assets)
            if version and self.ocr
            else (),
            engine_version=version,
            runtime_platform="linux" if sys.platform == "linux" else "development",
        )

    async def scan(self, snapshot: LocalSourceSnapshot) -> PrivacyScanReport:
        # Only orchestration/pipe I/O uses a thread. All PDF access occurs in a worker process.
        return await asyncio.to_thread(self._scan, snapshot)

    async def detect(self, snapshot: LocalSourceSnapshot) -> tuple[SensitiveCandidate, ...]:
        """Compatibility with the Phase 1 complete-only detector port."""
        report = await self.scan(snapshot)
        if report.status == "blocked":
            issue = next(
                issue
                for p in report.pages
                for issue in p.issues
                if issue != ScanIssue.LOW_CONFIDENCE
            )
            raise ScanFailure(issue)
        return report.candidates

    def _scan(self, snapshot: LocalSourceSnapshot) -> PrivacyScanReport:
        deadline = time.monotonic() + self.store.pdf.limits.timeout
        data = self.store.read(snapshot)
        inspection = self.store.inspection(snapshot)
        pages: list[PageScan] = []
        candidates: list[SensitiveCandidate] = []
        for native in inspection.pages:
            needs_ocr = native.has_images or not native.observations
            mode: Literal["native", "scanned", "mixed"] = (
                "mixed"
                if native.observations and needs_ocr
                else "scanned"
                if needs_ocr
                else "native"
            )
            status: Literal["processed", "blocked"]
            observations = native.observations
            ocr_version = None
            model_digests: tuple[str, ...] = ()
            issues = []
            try:
                if time.monotonic() >= deadline:
                    raise ScanFailure(ScanIssue.TIMEOUT)
                if needs_ocr:
                    if self.ocr is None:
                        raise ScanFailure(ScanIssue.OCR_UNAVAILABLE)
                    if not {"eng", "chi_tra"} <= {a.language for a in self.ocr.config.assets}:
                        raise ScanFailure(ScanIssue.OCR_UNAVAILABLE)
                    ocr_version = self.ocr.preflight(timeout=deadline - time.monotonic())
                    model_digests = tuple(a.sha256 for a in self.ocr.config.assets)
                    raster = self.store.pdf.render(
                        data, native.geometry.number, timeout=deadline - time.monotonic()
                    )
                    ocr_observations = self.ocr.recognize(
                        raster,
                        native.geometry,
                        dpi=self.store.pdf.limits.dpi,
                        timeout=deadline - time.monotonic(),
                        max_text=self.store.pdf.limits.max_text,
                    )
                    if not ocr_observations:
                        raise ScanFailure(ScanIssue.OCR_EMPTY)
                    observations += ocr_observations
                if any(o.confidence is not None and o.confidence < 0.85 for o in observations):
                    issues.append(ScanIssue.LOW_CONFIDENCE)
                candidates.extend(
                    detect_candidates(
                        observations,
                        max_candidates=self.store.pdf.limits.max_candidates,
                        deadline=deadline,
                    )
                )
                if time.monotonic() >= deadline:
                    raise ScanFailure(ScanIssue.TIMEOUT)
                status = "processed"
            except ScanFailure as error:
                issues.append(error.issue)
                status = "blocked"
            except Exception:
                issues.append(ScanIssue.INVALID_INPUT)
                status = "blocked"
            pages.append(
                PageScan(
                    page=native.geometry.number,
                    mode=mode,
                    status=status,
                    observations=observations,
                    ocr_engine_version=ocr_version,
                    ocr_model_digests=model_digests,
                    issues=tuple(issues),
                )
            )
        return PrivacyScanReport(
            source=snapshot,
            native_engine_version=inspection.parser_version,
            pages=tuple(pages),
            candidates=tuple(candidates),
            status="blocked" if any(p.status == "blocked" for p in pages) else "needs_review",
        )
