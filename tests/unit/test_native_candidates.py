"""Synthetic grammar fixtures exercise native classification, not source policy."""

import pytest

from appraisal_review.adapters.local.native_candidates import _bands
from appraisal_review.domain.factor_models import Grade


def test_native_candidate_thresholds_and_absence_are_separate():
    intervals, categories = _bands(
        {
            "優": "20m以上",
            "稍優": "15m以上未滿20m",
            "普通": "8m以上未滿15m",
            "稍劣": "4m以上未滿8m",
            "劣": "未滿4m或無",
        }
    )
    assert intervals[0].maximum == 4 and intervals[-1].minimum == 20
    assert categories[0].values == ["absent"]
    assert categories[0].grade is Grade.INFERIOR
    intervals, categories = _bands(
        {
            "優": "區段內有",
            "稍優": "未滿200m",
            "普通": "200m以上未滿400m",
            "稍劣": "400m以上未滿700m",
            "劣": "700m以上",
        }
    )
    assert categories[0].values == ["within_section"]
    assert intervals[0].grade is Grade.SLIGHTLY_SUPERIOR


def test_contradictory_unit_range_and_unsupported_endpoint_are_not_repaired():
    with pytest.raises(ValueError):
        _bands({"普通": "2km以上未滿40m"})
    with pytest.raises(ValueError):
        _bands({"普通": "約40m"})
    with pytest.raises(ValueError):
        _bands({"優": "區段內未知", "普通": "200m以上"})


def test_rendered_synthetic_matrix_candidates_and_header_orientation(tmp_path):
    import asyncio

    import pymupdf

    from appraisal_review.adapters.local.native_candidates import native_candidates
    from appraisal_review.adapters.local.pdf_parser import DocumentInput, LocalPDFParser

    path = tmp_path / "synthetic-matrix.pdf"
    labels = ["優", "稍優", "普通", "稍劣", "劣"]
    descriptions = [
        "優\uff1a40m以上",
        "稍優\uff1a25m以上未滿40m",
        "普通\uff1a12m以上未滿25m",
        "稍劣\uff1a3m以上未滿12m",
        "劣\uff1a未滿3m",
    ]
    with pymupdf.open() as pdf:
        for swapped in [False, True]:
            page = pdf.new_page(width=800, height=420)
            page.insert_text((20, 40), "合成區域因素", fontname="china-t", fontsize=12)
            xs = [20, 120, 180, 240, 300, 360, 420, 480, 780]
            ys = [90 + row * 40 for row in range(7)]
            for x in xs:
                page.draw_line((x, ys[0]), (x, ys[-1]))
            for y in ys:
                page.draw_line((xs[0], y), (xs[-1], y))
            headers = labels[::-1] if swapped else labels
            for col, label in enumerate(headers):
                page.insert_text((xs[col + 2] + 8, 115), label, fontname="china-t", fontsize=10)
            for row, label in enumerate(labels):
                y = 155 + row * 40
                page.insert_text((30, y), "合成道路條件", fontname="china-t", fontsize=10)
                page.insert_text((130, y), label, fontname="china-t", fontsize=10)
                for col in range(5):
                    page.insert_text((xs[col + 2] + 8, y), str(col - row), fontsize=10)
                page.insert_text((490, y), descriptions[row], fontname="china-t", fontsize=10)
        pdf.save(path)
    parser = LocalPDFParser([DocumentInput(path, "synthetic", "1", "criteria")])
    source = asyncio.run(parser.parse_document(path.as_uri())).source
    result = native_candidates(source)
    assert len(result) == 2 and result[0].candidate is not None and result[1].candidate is None
    assert result[0].candidate.rule.correction_matrix.values["excellent"]["inferior"] == 4
    assert result[0].candidate.rule.intervals[2].minimum == 12
    assert result[1].unresolved
