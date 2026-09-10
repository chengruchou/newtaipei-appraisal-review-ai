"""Private one-request PDF worker; all source bytes arrive on stdin, never argv."""

from __future__ import annotations

import base64
import json
import math
import sys
from typing import Literal

from pydantic import Field

from appraisal_review.domain.privacy_models import LocalSourcePage, PrivacyModel, PrivacyRegion
from appraisal_review.domain.privacy_scan import TextObservation


class ScanLimits(PrivacyModel):
    max_bytes: int = Field(default=20_000_000, gt=0, le=100_000_000)
    max_pages: int = Field(default=100, gt=0, le=200)
    max_pixels: int = Field(default=16_000_000, gt=0, le=64_000_000)
    max_text: int = Field(default=100_000, gt=0, le=1_000_000)
    max_candidates: int = Field(default=1000, gt=0, le=10_000)
    timeout: float = Field(default=30.0, gt=0, le=300)
    memory_mb: int = Field(default=768, ge=256, le=2048)
    dpi: int = Field(default=144, ge=72, le=300)


class NativePage(PrivacyModel):
    geometry: LocalSourcePage
    observations: tuple[TextObservation, ...]
    has_images: bool


class PDFInspection(PrivacyModel):
    parser_version: str
    pages: tuple[NativePage, ...] = Field(min_length=1)


class WorkerRequest(PrivacyModel):
    action: Literal["inspect", "render"]
    data: str = Field(repr=False)
    limits: ScanLimits
    page: int = Field(default=1, ge=1)


def execute(request: WorkerRequest) -> dict[str, object]:
    import pymupdf

    pymupdf.TOOLS.mupdf_display_errors(False)  # type: ignore[no-untyped-call]
    pymupdf.TOOLS.mupdf_display_warnings(False)  # type: ignore[no-untyped-call]
    data = base64.b64decode(request.data, validate=True)
    limits = request.limits
    if len(data) > limits.max_bytes or not data.startswith(b"%PDF-"):
        raise ValueError("Invalid PDF size or signature")
    with pymupdf.open(stream=data, filetype="pdf") as pdf:  # type: ignore[no-untyped-call]
        if pdf.is_encrypted or pdf.is_repaired or not 0 < len(pdf) <= limits.max_pages:
            raise ValueError("Unsupported PDF")
        if request.page > len(pdf):
            raise ValueError("Page outside source")
        pages = []
        observation_count = 0
        for index, page in enumerate(pdf):
            rotation = page.rotation
            page.set_rotation(0)
            geometry = LocalSourcePage(
                number=index + 1,
                width=page.rect.width,
                height=page.rect.height,
                rotation=rotation,
                crop_box=tuple(page.cropbox),
            )
            width = math.ceil(geometry.width * limits.dpi / 72)
            height = math.ceil(geometry.height * limits.dpi / 72)
            if width * height > limits.max_pixels:
                raise ValueError("Raster resource limit")
            if request.action == "render":
                if index + 1 != request.page:
                    continue
                pix = page.get_pixmap(dpi=limits.dpi, alpha=False)
                if pix.width * pix.height > limits.max_pixels:
                    raise ValueError("Raster resource limit")
                return {
                    "width": pix.width,
                    "height": pix.height,
                    "png": base64.b64encode(pix.tobytes("png")).decode("ascii"),
                }
            observations = []
            text_count = 0
            for block in page.get_text("blocks", flags=3):
                if block[6] != 0 or not block[4].strip():
                    continue
                text_count += len(block[4])
                observation_count += 1
                if observation_count > 10_000:
                    raise ValueError("Observation resource limit")
                if text_count > limits.max_text:
                    raise ValueError("Text resource limit")
                x0, y0, x1, y1 = block[:4]
                # Reject invalid geometry rather than inventing a clipped evidence box.
                if not (0 <= x0 < x1 <= geometry.width and 0 <= y0 < y1 <= geometry.height):
                    raise ValueError("Text outside source page")
                observations.append(
                    TextObservation(
                        text=block[4],
                        region=PrivacyRegion(
                            page=index + 1,
                            bbox=(x0, geometry.height - y1, x1, geometry.height - y0),
                        ),
                        origin="native",
                    )
                )
            pages.append(
                NativePage(
                    geometry=geometry,
                    observations=tuple(observations),
                    has_images=bool(page.get_image_info()),
                )
            )
        return PDFInspection(parser_version=pymupdf.VersionBind, pages=tuple(pages)).model_dump(
            mode="json"
        )


def main() -> None:
    try:
        # Hard ceiling applies before decoding attacker-controlled JSON/base64.
        raw = sys.stdin.buffer.read(140_000_001)
        if len(raw) > 140_000_000:
            raise ValueError("Request size limit")
        request = WorkerRequest.model_validate_json(raw)
        if sys.platform == "linux":
            import resource

            memory = request.limits.memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
            cpu = math.ceil(request.limits.timeout) + 1
            resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
        result = execute(request)
        sys.stdout.buffer.write(json.dumps(result, ensure_ascii=True).encode("utf-8"))
    except Exception:
        # Do not echo parser errors, paths, bytes or document text across error channels.
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
