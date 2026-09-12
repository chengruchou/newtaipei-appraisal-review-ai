"""Bounded local refill worker: raster base, explicit fields, embedded Unicode text."""

from __future__ import annotations

import base64
import hashlib
import importlib
import json
import math
import sys
from typing import Any, Literal

from pydantic import Field

from appraisal_review.adapters.local.privacy.pdf_worker import ScanLimits
from appraisal_review.adapters.local.privacy.sanitize_worker import encode_pages
from appraisal_review.application.privacy_refill import validate_refill
from appraisal_review.domain.privacy_mapping import LocalMappingRecord
from appraisal_review.domain.privacy_models import (
    Digest,
    PrivacyModel,
    PrivacyPage,
    PrivacyRegion,
    RehydrationPlan,
)
from appraisal_review.domain.privacy_refill import (
    PublishedRefillArtifact,
    PublishedRefillDescriptor,
)
from appraisal_review.domain.privacy_scan import TextObservation


class RefillWorkerRequest(PrivacyModel):
    action: Literal["render", "write"]
    data: str = Field(repr=False)
    pages: tuple[PrivacyPage, ...]
    limits: ScanLimits
    original: str | None = Field(default=None, repr=False)
    mapping: LocalMappingRecord | None = Field(default=None, repr=False)
    plan: RehydrationPlan | None = Field(default=None, repr=False)
    descriptor: PublishedRefillDescriptor | None = None
    font_digest: Digest | None = None
    font_size: float = Field(default=9.0, ge=8, le=18)


def rect(
    region: PrivacyRegion, pages: tuple[PrivacyPage, ...]
) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = region.bbox
    height = pages[region.page - 1].height
    return x0, height - y1, x1, height - y0


def bounds(
    region: PrivacyRegion, page: PrivacyPage, width: int, height: int
) -> tuple[int, int, int, int]:
    left, bottom_left, right, top_left = region.bbox
    top, bottom = page.height - top_left, page.height - bottom_left
    return (
        math.floor(left * width / page.width),
        math.floor(top * height / page.height),
        math.ceil(right * width / page.width),
        math.ceil(bottom * height / page.height),
    )


def plain(text: str) -> str:
    return "".join(text.split())


def execute(request: RefillWorkerRequest) -> dict[str, object]:
    fitz: Any = importlib.import_module("pymupdf")
    fitz.TOOLS.mupdf_display_errors(False)
    fitz.TOOLS.mupdf_display_warnings(False)
    data = base64.b64decode(request.data, validate=True)
    limits = request.limits
    pixels = [
        math.ceil(p.width * limits.dpi / 72) * math.ceil(p.height * limits.dpi / 72)
        for p in request.pages
    ]
    if (
        not data.startswith(b"%PDF-")
        or len(data) > limits.max_bytes
        or not pixels
        or (
            len(pixels) > limits.max_pages
            or max(pixels) > limits.max_pixels
            or sum(pixels) * 3 > limits.memory_mb * 1024 * 1024 // 4
        )
    ):
        raise ValueError("Input resource limit")
    originals = []
    previews = []
    with fitz.open(stream=data, filetype="pdf") as cloud:
        if cloud.is_encrypted or cloud.is_repaired or len(cloud) != len(request.pages):
            raise ValueError("Unsupported input PDF")
        for page, geometry in zip(cloud, request.pages, strict=True):
            if page.rotation != 0 or tuple(page.cropbox) != (
                0.0,
                0.0,
                geometry.width,
                geometry.height,
            ):
                raise ValueError("Unsupported published geometry")
            raster = page.get_pixmap(dpi=limits.dpi, colorspace=fitz.csRGB, alpha=False)
            originals.append(raster)
            if request.action == "render":
                words = page.get_text("words")
                if len(words) > 10000 or sum(len(w[4]) for w in words) > limits.max_text:
                    raise ValueError("Text resource limit")
                observations = tuple(
                    TextObservation(
                        text=w[4],
                        origin="native",
                        region=PrivacyRegion(
                            page=geometry.number,
                            bbox=(w[0], geometry.height - w[3], w[2], geometry.height - w[1]),
                        ),
                    )
                    for w in words
                )
                previews.append(
                    {
                        "width": raster.width,
                        "height": raster.height,
                        "png": base64.b64encode(raster.tobytes("png")).decode("ascii"),
                        "native": [o.model_dump(mode="json") for o in observations],
                    }
                )
        if request.action == "render":
            return {"pages": previews}
    mapping, plan, descriptor = request.mapping, request.plan, request.descriptor
    if mapping is None or plan is None or descriptor is None or request.original is None:
        raise ValueError("Incomplete write request")
    validate_refill(mapping, plan, PublishedRefillArtifact(descriptor, data))
    if request.pages != plan.pages:
        raise ValueError("Plan geometry differs")
    original = base64.b64decode(request.original, validate=True)
    if (
        len(original) > limits.max_bytes
        or len(original) != mapping.command.source.byte_size
        or (hashlib.sha256(original).hexdigest() != mapping.command.source.source_digest)
    ):
        raise ValueError("Original differs")
    selected = [s.candidate for s in mapping.command.selections if s.disposition == "redact"]
    candidates = {
        o.occurrence_id: c for o, c in zip(mapping.manifest.occurrences, selected, strict=True)
    }
    targets = {t.occurrence_id: t for t in descriptor.targets}
    baselines = [(p.width, p.height, p.samples) for p in originals]
    for target in descriptor.targets:
        if target.present:
            pix = originals[target.region.page - 1]
            pix.set_rect(
                fitz.IRect(
                    bounds(
                        target.region, request.pages[target.region.page - 1], pix.width, pix.height
                    )
                ),
                (255, 255, 255),
            )
    clean = encode_pages([(p.width, p.height, p.samples) for p in originals], request.pages)
    font = fitz.Font("cjk")
    expected_text: dict[int, list[str]] = {}
    crop_proofs = []
    with (
        fitz.open(stream=clean, filetype="pdf") as output,
        fitz.open(stream=original, filetype="pdf") as source,
    ):
        if (
            source.is_encrypted
            or source.is_repaired
            or len(source) != len(mapping.command.source.pages)
        ):
            raise ValueError("Unsupported original")
        for p, geometry in zip(source, mapping.command.source.pages, strict=True):
            if p.rotation != geometry.rotation or tuple(p.cropbox) != geometry.crop_box:
                raise ValueError("Original transform differs")
            p.set_rotation(0)
        for field in plan.fields:
            if field.operation == "omit":
                continue
            target = targets[field.occurrence_id]
            page = output[target.region.page - 1]
            destination = fitz.Rect(rect(target.region, plan.pages))
            candidate = candidates[field.occurrence_id]
            if field.operation == "restore_text":
                if (
                    request.font_digest is None
                    or hashlib.sha256(font.buffer).hexdigest() != request.font_digest
                ):
                    raise ValueError("Unapproved embedded font")
                if candidate.raw_text is None:
                    raise ValueError("Missing original text")
                text = candidate.raw_text.rstrip("\r\n")
                if not text.strip() or any(
                    not font.has_glyph(ord(c)) for c in text if not c.isspace()
                ):
                    raise ValueError("Missing font glyph")
                page.insert_font(fontname="LocalCJK", fontbuffer=font.buffer)
                if (
                    page.insert_textbox(
                        destination, text, fontname="LocalCJK", fontsize=request.font_size
                    )
                    < 0
                ):
                    raise ValueError("Text overflow")
                expected_text.setdefault(target.region.page, []).append(text)
            else:
                if candidate.crop_id is None:
                    raise ValueError("Missing approved original crop")
                geometry = mapping.command.source.pages[candidate.region.page - 1]
                clip = fitz.Rect(rect(candidate.region, mapping.command.source.pages))
                if (
                    math.ceil(clip.width * limits.dpi / 72)
                    * math.ceil(clip.height * limits.dpi / 72)
                    > limits.max_pixels
                ):
                    raise ValueError("Crop resource limit")
                crop = source[candidate.region.page - 1].get_pixmap(
                    dpi=limits.dpi, clip=clip, colorspace=fitz.csRGB, alpha=False
                )
                page.insert_image(destination, stream=crop.tobytes("png"), keep_proportion=True)
                crop_proofs.append((target.region, crop.width, crop.height, crop.samples))
        result = output.tobytes(garbage=4, deflate=True)
    if len(result) > limits.max_bytes:
        raise ValueError("Output resource limit")
    with fitz.open(stream=result, filetype="pdf") as final:
        if (
            len(final) != len(request.pages)
            or final.embfile_count()
            or final.is_encrypted
            or final.is_repaired
        ):
            raise ValueError("Unexpected output PDF")
        for index, page in enumerate(final):
            if list(page.annots() or ()) or list(page.widgets() or ()):
                raise ValueError("Unexpected active output objects")
            if plain(page.get_text()) != plain("".join(expected_text.get(index + 1, []))):
                raise ValueError("Restored text differs")
            raster = page.get_pixmap(dpi=limits.dpi, colorspace=fitz.csRGB, alpha=False)
            width, height, before = baselines[index]
            if (raster.width, raster.height) != (width, height):
                raise ValueError("Output raster differs")
            after = raster.samples
            editable = [
                bounds(t.region, request.pages[index], width, height)
                for t in descriptor.targets
                if t.present and t.region.page == index + 1
            ]
            for y in range(height):
                start = y * width * 3
                row = bytearray(before[start : start + width * 3])
                for left, top, right, bottom in editable:
                    if top <= y < bottom:
                        row[left * 3 : right * 3] = after[start + left * 3 : start + right * 3]
                if row != after[start : start + width * 3]:
                    raise ValueError("Protected cloud pixels changed")
        for region, width, height, samples in crop_proofs:
            page = final[region.page - 1]
            destination = fitz.Rect(rect(region, plan.pages))
            found = 0
            for image in page.get_image_info(xrefs=True):
                if (
                    image["xref"]
                    and destination.contains(fitz.Rect(image["bbox"]))
                    and (image["width"], image["height"]) == (width, height)
                ):
                    decoded = fitz.Pixmap(final, image["xref"])
                    if decoded.samples == samples:
                        found += 1
            if found != 1:
                raise ValueError("Original crop differs")
        for field in plan.fields:
            if field.operation == "restore_text" and field.destination is not None:
                actual = final[field.destination.page - 1].get_textbox(
                    fitz.Rect(rect(field.destination, plan.pages))
                )
                if plain(actual) != plain(candidates[field.occurrence_id].raw_text or ""):
                    raise ValueError("Text outside approved field")
    return {"pdf": base64.b64encode(result).decode("ascii")}


def main() -> None:
    try:
        raw = sys.stdin.buffer.read(140_000_001)
        if len(raw) > 140_000_000:
            raise ValueError("Input limit")
        request = RefillWorkerRequest.model_validate_json(raw)
        if sys.platform == "linux":
            import resource

            size = request.limits.memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (size, size))
            cpu = math.ceil(request.limits.timeout) + 1
            resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
        sys.stdout.buffer.write(json.dumps(execute(request), ensure_ascii=True).encode("utf-8"))
    except Exception:
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
