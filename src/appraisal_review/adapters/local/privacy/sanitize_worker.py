"""Bounded raster rebuild and independent pypdf/pixel profile verification worker."""

from __future__ import annotations

import base64
import hashlib
import importlib
import io
import json
import math
import sys
from typing import Any, Literal

from pydantic import Field

from appraisal_review.adapters.local.privacy.pdf_worker import ScanLimits
from appraisal_review.domain.privacy_models import (
    PrivacyManifest,
    PrivacyModel,
    PrivacyPage,
    PrivacyReviewCommand,
    placeholder_text,
)


class SanitizeRequest(PrivacyModel):
    action: Literal["build", "verify"]
    original: str = Field(repr=False)
    command: PrivacyReviewCommand = Field(repr=False)
    manifest: PrivacyManifest
    limits: ScanLimits
    artifact: str | None = Field(default=None, repr=False)


def layout(command: PrivacyReviewCommand, dpi: int) -> tuple[PrivacyPage, ...]:
    scale = dpi / 72
    return tuple(
        PrivacyPage(
            number=p.number,
            width=math.ceil(p.width * scale) / scale + 220,
            height=math.ceil(p.height * scale) / scale,
        )
        for p in command.source.pages
    )


def token_box(page: PrivacyPage, index: int) -> tuple[float, float, float, float]:
    top = 8.0 + index * 20
    if top + 16 > page.height:
        raise ValueError("Token column capacity exceeded")
    return page.width - 210, page.height - top - 16, page.width - 10, page.height - top


def encode_pages(images: list[tuple[int, int, bytes]], pages: tuple[PrivacyPage, ...]) -> bytes:
    """Canonical image-only PDF profile; no source PDF objects ever enter this writer."""
    from pypdf import PdfWriter
    from pypdf.generic import (
        ArrayObject,
        DecodedStreamObject,
        DictionaryObject,
        NameObject,
        NumberObject,
    )

    writer = PdfWriter()
    writer.metadata = None
    for (width, height, rgb), geometry in zip(images, pages, strict=True):
        page = writer.add_blank_page(geometry.width, geometry.height)
        image = DecodedStreamObject()
        image.set_data(rgb)
        image.update(
            {
                NameObject("/Type"): NameObject("/XObject"),
                NameObject("/Subtype"): NameObject("/Image"),
                NameObject("/Width"): NumberObject(width),
                NameObject("/Height"): NumberObject(height),
                NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
                NameObject("/BitsPerComponent"): NumberObject(8),
            }
        )
        image_ref = writer._add_object(image.flate_encode())
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/XObject"): DictionaryObject({NameObject("/Raster"): image_ref})}
        )
        content = DecodedStreamObject()
        # Use the serialized MediaBox numbers for identical rendering geometry.
        width_pt, height_pt = page.mediabox.width, page.mediabox.height
        content.set_data(f"q {width_pt} 0 0 {height_pt} 0 0 cm /Raster Do Q\n".encode("ascii"))
        page[NameObject("/Contents")] = ArrayObject([writer._add_object(content)])
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


def template(page: PrivacyPage, tokens: list[str], dpi: int) -> Any:
    pymupdf: Any = importlib.import_module("pymupdf")

    with pymupdf.open() as pdf:
        canvas = pdf.new_page(width=page.width, height=page.height)
        for index, token in enumerate(tokens):
            token_box(page, index)
            if pymupdf.get_text_length(token, fontsize=9) > 200:
                raise ValueError("Token overflow")
            canvas.insert_text((page.width - 210, 20 + index * 20), token, fontsize=9)
        return canvas.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)


def execute(request: SanitizeRequest) -> dict[str, object]:
    pymupdf: Any = importlib.import_module("pymupdf")
    from pypdf import PdfReader

    pymupdf.TOOLS.mupdf_display_errors(False)
    pymupdf.TOOLS.mupdf_display_warnings(False)
    limits, command, manifest = request.limits, request.command, request.manifest
    original = base64.b64decode(request.original, validate=True)
    if (
        len(original) > limits.max_bytes
        or len(original) != command.source.byte_size
        or (hashlib.sha256(original).hexdigest() != command.source.source_digest)
    ):
        raise ValueError("Source differs")
    if (
        manifest.pages != layout(command, limits.dpi)
        or manifest.case_id != command.source.case_id
        or (manifest.document_id != command.source.document_id)
    ):
        raise ValueError("Manifest lineage differs")
    redactions = [s for s in command.selections if s.disposition == "redact"]
    if len(redactions) != len(manifest.occurrences):
        raise ValueError("Occurrence count differs")
    positions: dict[int, int] = {}
    for selection, occurrence in zip(redactions, manifest.occurrences, strict=True):
        number = selection.candidate.region.page
        index = positions.get(number, 0)
        if (
            occurrence.entity_id != selection.entity_id
            or occurrence.region.page != number
            or (occurrence.region.bbox != token_box(manifest.pages[number - 1], index))
        ):
            raise ValueError("Occurrence placement differs")
        positions[number] = index + 1
    # Bound retained uncompressed images before parsing/rendering any document.
    if any(
        math.ceil(p.width * limits.dpi / 72) * math.ceil(p.height * limits.dpi / 72)
        > limits.max_pixels
        for p in manifest.pages
    ):
        raise ValueError("Raster resource limit")
    if (
        sum(
            math.ceil(p.width * limits.dpi / 72) * math.ceil(p.height * limits.dpi / 72) * 3
            for p in manifest.pages
        )
        > limits.memory_mb * 1024 * 1024 // 4
    ):
        raise ValueError("Aggregate raster limit")
    artifact = base64.b64decode(request.artifact, validate=True) if request.artifact else b""
    parsed_images: list[tuple[int, int, bytes]] = []
    if request.action == "verify":
        if (
            not artifact
            or len(artifact) > limits.max_bytes
            or len(artifact) != manifest.byte_size
            or (hashlib.sha256(artifact).hexdigest() != manifest.sanitized_digest)
        ):
            raise ValueError("Artifact identity differs")
        reader = PdfReader(io.BytesIO(artifact), strict=True)
        if reader.is_encrypted or len(reader.pages) != len(manifest.pages):
            raise ValueError("Artifact page count differs")
        for pdf_page in reader.pages:
            resources: Any = pdf_page["/Resources"]
            image = resources["/XObject"]["/Raster"]
            width, height = int(image["/Width"]), int(image["/Height"])
            if width * height > limits.max_pixels or width < 1 or height < 1:
                raise ValueError("Image resource limit")
            if image["/ColorSpace"] != "/DeviceRGB" or image["/BitsPerComponent"] != 8:
                raise ValueError("Unsupported image format")
            rgb = image.get_data()
            if len(rgb) != width * height * 3 or pdf_page.extract_text():
                raise ValueError("Unexpected image or text layer")
            parsed_images.append((width, height, rgb))
        # Exact profile rejects extra objects, attachments, metadata, forms, OCGs,
        # alpha masks, alternate streams, incremental history and trailing payloads.
        if encode_pages(parsed_images, manifest.pages) != artifact:
            raise ValueError("Noncanonical PDF surface")
    images: list[tuple[int, int, bytes]] = []
    previews: list[dict[str, object]] = []
    with pymupdf.open(stream=original, filetype="pdf") as source:
        if source.is_encrypted or source.is_repaired or len(source) != len(command.source.pages):
            raise ValueError("Unsupported source")
        if len(source) > limits.max_pages:
            raise ValueError("Page resource limit")
        for index, source_page in enumerate(source):
            expected_source = command.source.pages[index]
            if source_page.rotation != expected_source.rotation or tuple(source_page.cropbox) != (
                expected_source.crop_box
            ):
                raise ValueError("Source transform differs")
            source_page.set_rotation(0)
            page = manifest.pages[index]
            canvas = template(
                page,
                [
                    placeholder_text(o.entity_id)
                    for o in manifest.occurrences
                    if o.region.page == index + 1
                ],
                limits.dpi,
            )
            if canvas.width * canvas.height > limits.max_pixels:
                raise ValueError("Raster limit")
            native = source_page.get_pixmap(dpi=limits.dpi, colorspace=pymupdf.csRGB, alpha=False)
            regions = [
                s.candidate.region for s in redactions if s.candidate.region.page == index + 1
            ]
            if request.action == "build":
                for region in regions:
                    x0, y0, x1, y1 = region.bbox
                    native.set_rect(
                        pymupdf.IRect(
                            math.floor(x0 * native.width / expected_source.width),
                            math.floor(
                                (expected_source.height - y1)
                                * native.height
                                / expected_source.height
                            ),
                            math.ceil(x1 * native.width / expected_source.width),
                            math.ceil(
                                (expected_source.height - y0)
                                * native.height
                                / expected_source.height
                            ),
                        ),
                        (255, 255, 255),
                    )
                canvas.copy(native, native.irect)
                images.append((canvas.width, canvas.height, canvas.samples))
            else:
                width, height, rgb = parsed_images[index]
                if (width, height) != (canvas.width, canvas.height):
                    raise ValueError("Raster dimensions differ")
                # Independent byte-level test; does not call the builder's set_rect/copy.
                original_rgb, column_rgb = native.samples, canvas.samples
                for y in range(height):
                    row = bytearray(original_rgb[y * native.width * 3 : (y + 1) * native.width * 3])
                    for region in regions:
                        left, bottom, right, top = region.bbox
                        lo = math.floor(
                            (expected_source.height - top) / expected_source.height * height
                        )
                        hi = math.ceil(
                            (expected_source.height - bottom) / expected_source.height * height
                        )
                        if lo <= y < hi:
                            start = math.floor(left / expected_source.width * native.width) * 3
                            stop = math.ceil(right / expected_source.width * native.width) * 3
                            row[start:stop] = b"\xff" * (stop - start)
                    row.extend(column_rgb[(y * width + native.width) * 3 : (y + 1) * width * 3])
                    if row != rgb[y * width * 3 : (y + 1) * width * 3]:
                        raise ValueError("Protected or redacted pixels differ")
    if request.action == "build":
        pdf = encode_pages(images, manifest.pages)
        if len(pdf) > limits.max_bytes:
            raise ValueError("Output size limit")
        return {"artifact": base64.b64encode(pdf).decode("ascii")}
    with pymupdf.open(stream=artifact, filetype="pdf") as reopened:
        for page, (width, height, rgb) in zip(reopened, parsed_images, strict=True):
            raster = page.get_pixmap(dpi=limits.dpi, colorspace=pymupdf.csRGB, alpha=False)
            if (raster.width, raster.height, raster.samples) != (width, height, rgb):
                raise ValueError("Re-render differs from verified pixels")
            previews.append(
                {
                    "width": width,
                    "height": height,
                    "png": base64.b64encode(raster.tobytes("png")).decode("ascii"),
                }
            )
    return {"previews": previews}


def main() -> None:
    try:
        raw = sys.stdin.buffer.read(140_000_001)
        if len(raw) > 140_000_000:
            raise ValueError("Input limit")
        request = SanitizeRequest.model_validate_json(raw)
        if sys.platform == "linux":
            import resource

            size = request.limits.memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (size, size))
            cpu = math.ceil(request.limits.timeout) + 1
            resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
        sys.stdout.buffer.write(json.dumps(execute(request)).encode("utf-8"))
    except Exception:
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
