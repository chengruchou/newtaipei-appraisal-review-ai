"""Configured local Tesseract TSV adapter. Assets must already exist; no downloads."""

from __future__ import annotations

import csv
import hashlib
import io
import math
import re
import time
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from appraisal_review.adapters.local.privacy.process import ScanFailure, run_bounded
from appraisal_review.adapters.local.privacy.source import LocalRaster
from appraisal_review.domain.privacy_models import Digest, LocalSourcePage, PrivacyModel
from appraisal_review.domain.privacy_scan import ScanIssue, TextObservation, pixel_region


class OCRAsset(PrivacyModel):
    language: Literal["eng", "chi_tra"]
    sha256: Digest


class TesseractConfig(PrivacyModel):
    executable: Path
    executable_sha256: Digest
    tessdata: Path
    assets: tuple[OCRAsset, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def explicit_assets(self) -> TesseractConfig:
        if not self.executable.is_absolute() or not self.tessdata.is_absolute():
            raise ValueError("OCR assets require explicit absolute paths")
        names = [a.language for a in self.assets]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate OCR language asset")
        return self


def asset_digest(path: Path) -> str:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 250_000_000:
        raise ScanFailure(ScanIssue.OCR_UNAVAILABLE)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(65536):
            digest.update(chunk)
    return digest.hexdigest()


def parse_tsv(
    data: bytes, raster: LocalRaster, page: LocalSourcePage, *, max_text: int
) -> tuple[TextObservation, ...]:
    """Preserve word-level raw scores (0..100 -> 0..1), reject malformed geometry."""
    try:
        reader = csv.DictReader(
            io.StringIO(data.decode("utf-8")), delimiter="\t", quoting=csv.QUOTE_NONE
        )
        required = {"level", "page_num", "left", "top", "width", "height", "conf", "text"}
        if not required <= set(reader.fieldnames or ()):
            raise ValueError("Invalid OCR columns")
        observations: list[TextObservation] = []
        count = 0
        for row in reader:
            if row["level"] != "5":
                continue
            text = row["text"]
            if text is None or not text.strip():
                continue
            confidence = float(row["conf"])
            if not math.isfinite(confidence) or not 0 <= confidence <= 100:
                raise ValueError("Invalid OCR confidence")
            if row["page_num"] != "1":
                raise ValueError("Unexpected OCR page")
            x, y, width, height = (float(row[key]) for key in ("left", "top", "width", "height"))
            region = pixel_region((x, y, x + width, y + height), raster.width, raster.height, page)
            count += len(text)
            if count > max_text or len(observations) >= 10_000:
                raise ScanFailure(ScanIssue.RESOURCE_LIMIT)
            observations.append(
                TextObservation(text=text, region=region, origin="ocr", confidence=confidence / 100)
            )
        if not observations:
            raise ScanFailure(ScanIssue.OCR_EMPTY)
        return tuple(observations)
    except (ValueError, KeyError, TypeError, csv.Error):
        raise ScanFailure(ScanIssue.OCR_FAILED) from None


class TesseractOCR:
    """Trusted operator configuration, never parameters chosen by document content."""

    def __init__(self, config: TesseractConfig, work_directory: Path) -> None:
        self.config = TesseractConfig.model_validate(config)
        if not work_directory.is_absolute() or not work_directory.is_dir():
            raise ValueError("Existing local OCR work directory required")
        self.work_directory = work_directory.resolve(strict=True)

    def preflight(self, *, timeout: float) -> str:
        try:
            if asset_digest(self.config.executable) != self.config.executable_sha256:
                raise ScanFailure(ScanIssue.OCR_UNAVAILABLE)
            for asset in self.config.assets:
                if (
                    asset_digest(self.config.tessdata / f"{asset.language}.traineddata")
                    != asset.sha256
                ):
                    raise ScanFailure(ScanIssue.OCR_UNAVAILABLE)
            raw = run_bounded(
                [str(self.config.executable), "--version"],
                b"",
                cwd=self.work_directory,
                timeout=min(timeout, 5.0),
                max_output=4096,
            )
            match = re.match(rb"tesseract ([0-9]+\.[0-9]+(?:\.[0-9]+)?)", raw)
            if not match:
                raise ScanFailure(ScanIssue.OCR_UNAVAILABLE)
            return match[1].decode("ascii")
        except OSError:
            raise ScanFailure(ScanIssue.OCR_UNAVAILABLE) from None

    def recognize(
        self,
        raster: LocalRaster,
        page: LocalSourcePage,
        *,
        dpi: int,
        timeout: float,
        max_text: int,
    ) -> tuple[TextObservation, ...]:
        if not raster.png.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ScanFailure(ScanIssue.INVALID_INPUT)
        deadline = time.monotonic() + timeout
        self.preflight(timeout=timeout)
        raw = run_bounded(
            [
                str(self.config.executable),
                "stdin",
                "stdout",
                "--tessdata-dir",
                str(self.config.tessdata),
                "-l",
                "+".join(a.language for a in self.config.assets),
                "--dpi",
                str(dpi),
                "--psm",
                "3",
                "-c",
                "tessedit_create_tsv=1",
            ],
            raster.png,
            cwd=self.work_directory,
            timeout=deadline - time.monotonic(),
            max_output=max_text * 128,
        )
        return parse_tsv(raw, raster, page, max_text=max_text)
