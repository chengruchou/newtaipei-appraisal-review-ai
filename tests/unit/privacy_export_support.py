"""Synthetic canaries and value-free scan results; never use real case data."""

import json
import unicodedata
from typing import get_args

import pymupdf

from appraisal_review.domain.privacy_export import CanaryHit, LeakCheck, LeakScanReport, LeakSurface

CANARIES = {
    "c01": "CANARY-PRIVATE-1234",
    "c02": "合成隱私測試值",
    "c03": "synthetic@example.invalid",
}


def scan(surface, value):
    if isinstance(value, bytes):
        projections = [value.decode("utf-8", errors="ignore")]
        projections += [
            value.decode(codec, errors="ignore") for codec in ("utf-16-le", "utf-16-be")
        ]
    else:
        projections = [value]
    normalized = [
        "".join(unicodedata.normalize("NFKC", value).casefold().split()) for value in projections
    ]
    hits = []
    for identifier, canary in CANARIES.items():
        needle = "".join(unicodedata.normalize("NFKC", canary).casefold().split())
        count = sum(value.count(needle) for value in normalized)
        count += sum(
            value.count(json.dumps(canary, ensure_ascii=True)[1:-1]) for value in projections
        )
        if count:
            hits.append(CanaryHit(canary_id=identifier, count=count))
    return LeakCheck(surface=surface, inspected=True, hits=tuple(hits))


def canary_raster(identifier):
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=240, height=40)
        page.insert_text((10, 25), CANARIES[identifier], fontname="china-t", fontsize=10)
        return page.get_pixmap(dpi=144, colorspace=pymupdf.csRGB, alpha=False)


def contains_patch(image, patch):
    """Exact rendered template control, not general OCR or semantic recognition."""
    if patch.width > image.width or patch.height > image.height:
        return False
    data, template = image.samples, patch.samples
    stride, patch_stride = image.width * 3, patch.width * 3
    row = next(
        i
        for i in range(patch.height)
        if template[i * patch_stride : (i + 1) * patch_stride] != b"\xff" * patch_stride
    )
    needle = template[row * patch_stride : (row + 1) * patch_stride]
    start = 0
    while (found := data.find(needle, start)) >= 0:
        start = found + 1
        top = found - row * stride
        if top < 0 or found % stride + patch_stride > stride or found % 3:
            continue
        if all(
            data[top + y * stride : top + y * stride + patch_stride]
            == template[y * patch_stride : (y + 1) * patch_stride]
            for y in range(patch.height)
        ):
            return True
    return False


def inspect_pdf(data):
    streams = bytearray()
    text = []
    image_hits = {identifier: 0 for identifier in CANARIES}
    with pymupdf.open(stream=data, filetype="pdf") as pdf:
        for xref in range(1, pdf.xref_length()):
            streams.extend(pdf.xref_object(xref).encode())
            if pdf.xref_is_stream(xref):
                streams.extend(pdf.xref_stream(xref))
        for page in pdf:
            text.append(page.get_text())
            raster = page.get_pixmap(dpi=144, colorspace=pymupdf.csRGB, alpha=False)
            for identifier in CANARIES:
                image_hits[identifier] += contains_patch(raster, canary_raster(identifier))
    return (
        scan("bundle_bytes", data),
        scan("pdf_streams", bytes(streams)),
        scan("pdf_text", "\n".join(text)),
        LeakCheck(
            surface="pdf_images",
            inspected=True,
            hits=tuple(
                CanaryHit(canary_id=key, count=count) for key, count in image_hits.items() if count
            ),
        ),
    )


def report(checks):
    checked = {check.surface: check for check in checks}
    ordered = tuple(
        checked.get(surface, LeakCheck(surface=surface, inspected=False))
        for surface in get_args(LeakSurface)
    )
    status = (
        "failed"
        if any(c.hits for c in ordered)
        else ("passed" if all(c.inspected for c in ordered) else "blocked")
    )
    return LeakScanReport(scope="python_hooks", checks=ordered, status=status)
