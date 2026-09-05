"""Self-authored synthetic pages; no competition documents are test fixtures."""

import asyncio
import hashlib
from pathlib import Path

import pymupdf
import pytest

from appraisal_review.adapters.local.pdf_parser import (
    DocumentInput,
    LocalPDFParser,
    pixels_to_pdf,
    selection_state,
    top_left_to_pdf,
)
from appraisal_review.domain.document_models import SourceCitation, SourceRegistry


def make_pdf(path: Path):
    with pymupdf.open() as document:
        for width, height, rotation in [(300, 400, 0), (500, 300, 90)]:
            page = document.new_page(width=width, height=height)
            page.insert_text((40, 60), "合成測試 道路 寬度 18 m", fontname="china-t", fontsize=12)
            for x in [30, 120, 230]:
                page.draw_line((x, 90), (x, 180))
            for y in [90, 120, 150, 180]:
                page.draw_line((30, y), (230, y))
            page.insert_text((35, 110), "Target", fontsize=10)
            page.insert_text((125, 110), "Comparable", fontsize=10)
            page.insert_text((35, 140), "18", fontsize=10)
            page.insert_text((125, 140), "6", fontsize=10)
            page.insert_text((35, 170), "0", fontsize=10)
            page.insert_text(
                (35, 80), "Synthetic rule: threshold 10 m; grade difference 5%", fontsize=7
            )
            page.insert_text((35, 220), "■ □", fontname="china-t", fontsize=12)
            if rotation:
                page.set_cropbox(pymupdf.Rect(10, 20, width - 10, height - 20))
            page.set_rotation(rotation)
        document.set_metadata({"title": "Synthetic parser fixture"})
        document.save(path)


def test_real_pdf_text_tables_mixed_sizes_crop_rotation_and_render(tmp_path):
    path = tmp_path / "synthetic.pdf"
    make_pdf(path)
    original = path.read_bytes()
    parser = LocalPDFParser([DocumentInput(path, "synthetic", "v1", "forms")])
    parsed = asyncio.run(parser.parse_document(path.as_uri()))
    source = parsed.source
    assert source.content_hash == hashlib.sha256(original).hexdigest()
    assert parsed.page_count == 2
    assert source.pages[0].width == 300 and source.pages[1].width == 480
    assert source.pages[1].rotation == 90 and source.pages[1].crop_box == (10, 20, 490, 280)
    for page in source.pages:
        assert page.has_text
        assert {r.selection for r in page.regions if r.kind == "selection"} == {
            "checked",
            "unchecked",
        }
        assert any("合成測試" in r.text for r in page.regions)
        cells = [r for r in page.regions if r.kind == "cell"]
        assert [r.text for r in cells] == ["Target", "Comparable", "18", "6", "0", ""]
        image = asyncio.run(parser.render(path.as_uri(), page.number, scale=1))
        pixmap = pymupdf.Pixmap(image)
        assert (pixmap.width, pixmap.height) == (page.width, page.height)
        region = next(r for r in cells if r.text == "18")
        x0, y0, x1, y1 = top_left_to_pdf(region.bbox, page.height)
        # Rendered ink must occupy the exact parsed value cell, including CropBox offset.
        dark = 0
        for y in range(int(y0) + 2, int(y1) - 2):
            for x in range(int(x0) + 2, int(x1) - 2):
                if min(pixmap.pixel(x, y)) < 100:
                    dark += 1
        assert dark > 10
        assert pixels_to_pdf((x0, y0, x1, y1), pixmap.width, pixmap.height, page) == region.bbox
    assert path.read_bytes() == original
    registry = SourceRegistry(documents=[source])
    region = source.pages[0].regions[0]
    ref = SourceCitation(
        document_id=source.document_id,
        content_hash=source.content_hash,
        version=source.version,
        page=1,
        region_id=region.id,
        bbox=region.bbox,
        excerpt="合成測試",
    )
    assert registry.resolves(ref)
    ref.page = 3
    assert not registry.resolves(ref)


@pytest.mark.parametrize(
    "text,state", [("☑", "checked"), ("□", "unchecked"), ("☑ □", "ambiguous"), ("", None)]
)
def test_selection_marks_never_infer_absence_from_blank(text, state):
    assert selection_state(text) == state


def test_no_arbitrary_uri_source_hash_change_or_symlink_target(tmp_path):
    path = tmp_path / "synthetic.pdf"
    make_pdf(path)
    parser = LocalPDFParser([DocumentInput(path, "synthetic", "1", "forms", "0" * 64)])
    with pytest.raises(ValueError, match="hash changed"):
        asyncio.run(parser.parse_document(path.as_uri()))
    for uri in [(tmp_path / "other.pdf").as_uri(), "s3://unapproved/document.pdf"]:
        with pytest.raises(ValueError, match="allowlist"):
            asyncio.run(parser.parse_document(uri))
    parser = LocalPDFParser([DocumentInput(path, "synthetic", "1", "forms")])
    moved = tmp_path / "changed.pdf"
    path.rename(moved)
    path.symlink_to(moved)
    with pytest.raises(ValueError, match="path changed"):
        asyncio.run(parser.parse_document(path.as_uri()))


def test_concurrent_requests_use_isolated_pdf_worker(tmp_path):
    path = tmp_path / "synthetic.pdf"
    make_pdf(path)
    parser = LocalPDFParser([DocumentInput(path, "synthetic", "1", "forms")])

    async def run():
        first, second, rendered = await asyncio.gather(
            parser.parse_document(path.as_uri()),
            parser.parse_document(path.as_uri()),
            parser.render(path.as_uri(), 1),
        )
        assert first == second
        assert rendered.startswith(b"\x89PNG")

    asyncio.run(run())
