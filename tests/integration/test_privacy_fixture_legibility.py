"""Authored raster fixture readability without changing source facts or trust gates."""

import pymupdf
import test_privacy_browser_rehearsal as harness
import test_raster_publication as publication_harness


def test_authored_forms_use_readable_labels_and_preserve_sensitive_canaries(monkeypatch, tmp_path):
    _, helper = harness.load_privacy_scripts(monkeypatch)
    sources = helper.write_privacy_sources(tmp_path)
    forms = next(item for item in sources if item.purpose == "forms")
    with pymupdf.open(forms.path) as pdf:
        for number, page in enumerate(pdf, 1):
            text = page.get_text()
            footer = next(line for line in text.splitlines() if line.startswith("SYNTHETIC SOURCE"))
            assert footer == "SYNTHETIC SOURCE"
            assert f"合成機敏姓名-forms-{number}" in text
            spans = [
                span
                for block in page.get_text("dict")["blocks"]
                for line in block.get("lines", [])
                for span in line["spans"]
            ]
            facts = [
                span for span in spans if "道路寬度" in span["text"] or "填報修正率" in span["text"]
            ]
            assert len(facts) == 3
            assert all(span["size"] >= 16 for span in facts)
            assert "10 公尺" in text and "9 公尺" in text
            assert ("5 百分點" if number == 1 else "-5 百分點") in text


privacy_rehearsal = harness.rehearsal
rehearsal_workspace = harness.rehearsal_workspace
publication_scene = publication_harness.raster_scene


def test_authored_grade_annotations_have_black_ink_and_intact_cjk(publication_scene):
    scene = publication_scene
    output = scene.config.output_directory / "completed.pdf"
    with pymupdf.open(output) as pdf:
        for number, page in enumerate(pdf):
            assert "優" in page.get_text() and "劣" in page.get_text()
            for field in scene.fixture.request.field_map.fields:
                if field.page != number + 1:
                    continue
                x0, y0, x1, y1 = field.bounding_box
                pixels = page.get_pixmap(
                    clip=pymupdf.Rect(x0, page.rect.height - y1, x1, page.rect.height - y0),
                    dpi=144,
                    colorspace=pymupdf.csRGB,
                    alpha=False,
                )
                channels = pixels.samples
                assert min(channels) < 128
                assert channels[0::3] == channels[1::3] == channels[2::3]
