#!/usr/bin/env python
"""Prepare a structured, independently recomputed snapshot of the Shulin case.

Sources (read-only, native-text PDFs, no OCR):
  - artifacts/official-templates/題目.pdf 的副本.pdf          (this round's assignment)
  - artifacts/official-templates/評價基準明細表.pdf 的副本.pdf  (this round's criteria)

Outputs (created under artifacts/shulin-case/, mode 0700; plus docs/shulin-case-data.md):
  - extraction.json  : every stated attribute per subject with page/quote/confidence
  - criteria.json    : factor tables, bands, grade matrices with explicit orientation
  - computed.json    : independent Decimal recomputation of every computable correction
  - crosscheck.md    : method-shape cross-check against the organizer's Jinshan sample
  - docs/shulin-case-data.md : classification of every data element + open items

Every quote is verified against the cited page's native text layer (whitespace-
normalized); confidence 1.0 is only asserted for verified quotes.

Run:  PYTHONPATH=src .venv/bin/python scripts/prepare_shulin_snapshot.py
"""
from __future__ import annotations

import json
import os
import re
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

try:
    import pymupdf
except ImportError:  # pragma: no cover
    import fitz as pymupdf  # type: ignore

ROOT = Path(__file__).resolve().parents[1]
TPL_DIR = ROOT / "artifacts" / "official-templates"
ASSIGNMENT_PDF = TPL_DIR / "題目.pdf 的副本.pdf"
CRITERIA_PDF = TPL_DIR / "評價基準明細表.pdf 的副本.pdf"
OUT_DIR = ROOT / "artifacts" / "shulin-case"
DOC_PATH = ROOT / "docs" / "shulin-case-data.md"

GRADES5 = ["優", "稍優", "普通", "稍劣", "劣"]
GRADES3 = ["優", "普通", "劣"]
GRADES2 = ["優", "劣"]
GRADES7 = ["極優", "優", "稍優", "普通", "稍劣", "劣", "極劣"]

# --------------------------------------------------------------------------
# text access + quote verification
# --------------------------------------------------------------------------

_WS = re.compile(r"[\s　]+")


def norm(s: str) -> str:
    """Whitespace-insensitive normal form (incl. full-width spaces)."""
    return _WS.sub("", s).replace("＋", "+").replace("％", "%")


class Doc:
    def __init__(self, path: Path):
        self.path = path
        d = pymupdf.open(path)
        self.n_pages = len(d)
        self.raw = [p.get_text() for p in d]
        self.pages = [norm(t) for t in self.raw]
        d.close()

    def has(self, page_1based: int, quote: str) -> bool:
        return norm(quote) in self.pages[page_1based - 1]


VERIFY_FAILURES: list[str] = []


def verified(doc: Doc, page: int, quote: str, label: str) -> float:
    """Return confidence 1.0 iff the quote is present in the page's native text."""
    if doc.has(page, quote):
        return 1.0
    VERIFY_FAILURES.append(f"{doc.path.name} p.{page}: {label}: quote not found: {quote!r}")
    return 0.0


# --------------------------------------------------------------------------
# matrix builders (values are percentage POINTS, kept as Decimal strings)
# --------------------------------------------------------------------------

def sym_matrix(step: str, n: int) -> list[list[str]]:
    """matrix[row][col] = (col - row) * step, grades ordered best -> worst.

    row axis = the entity whose value is being derived (regional: 目標區段;
    individual as used in 表4: 比準地). col axis = the price source (regional:
    基準區段; individual: 比較標的). Positive = adjust the source price upward.
    """
    s = Decimal(step)
    return [[str((Decimal(c) - Decimal(r)) * s) for c in range(n)] for r in range(n)]


# 其他影響因素 (regional, 7x7) is NOT uniform-step; literal ladder from the document.
_OTHER7_LADDER = ["-20", "-16.67", "-13.33", "-10", "-6.67", "-3.33", "0",
                  "3.33", "6.67", "10", "13.33", "16.67", "20"]
OTHER7_MATRIX = [[_OTHER7_LADDER[(c - r) + 6] for c in range(7)] for r in range(7)]


# --------------------------------------------------------------------------
# criteria definitions  (evaluated against 評價基準明細表.pdf 的副本.pdf)
# Pairings matrix<->factor were verified by y-coordinate overlap of the matrix
# anchors and the vertical sub-item labels; axis orientation was verified by
# rendering the diagonal header cells (regional p.1: top-right=基準區段 columns,
# bottom-left=目標區段 rows; individual p.6: top-right=比凖地(比較標的) columns,
# bottom-left=宗地(比準地) rows).
# --------------------------------------------------------------------------

DIST_NEAR_2000 = [  # nuisance-style, farther is better
    ("優", "2000m以上或無"), ("稍優", "1500m以上未滿2000m"), ("普通", "1000m以上未滿1500m"),
    ("稍劣", "500m以上未滿1000m"), ("劣", "區段內有或距離未滿500m"),
]

REGIONAL_FACTORS = [
    # ---- page 1 (printed 4-26): 土地使用管制 (1)
    dict(id="urban_plan_status", zh="都市計畫（內、外）", group="土地使用管制(1)", page=1,
         printed_page="4-26", grades=GRADES2, matrix=sym_matrix("20", 2),
         bands=[("優", "都市計畫內"), ("劣", "都市計畫外")],
         rule="以都市計畫內外來制定其優劣", step_token="+20"),
    dict(id="zoning_district", zh="使用分區（使用地類別）", group="土地使用管制(1)", page=1,
         printed_page="4-26", grades=GRADES5, matrix=sym_matrix("5", 5),
         bands=[("優", "商業區、捷運用地(聯開)"), ("稍優", "住宅區、市場用地"),
                ("普通", "甲建、乙建、特定專用區、多目標使用之其他公共設施用地"),
                ("稍劣", "工業區、丙建、丁建"), ("劣", "其他可建築用地")],
         rule="以都市計畫使用分區及使用地類別來制定其優劣", step_token="+5"),
    dict(id="coverage_ratio", zh="建蔽率", group="土地使用管制(1)", page=1,
         printed_page="4-26", grades=GRADES5, matrix=sym_matrix("2.5", 5),
         bands=[("優", "80%以上"), ("稍優", "70%以上未滿80%"), ("普通", "60%以上未滿70%"),
                ("稍劣", "50%以上未滿60%"), ("劣", "未滿50%")],
         rule="以建蔽率高低來制定其優劣等級", step_token="+2.5"),
    dict(id="floor_area_ratio", zh="容積率", group="土地使用管制(1)", page=1,
         printed_page="4-26", grades=GRADES5, matrix=sym_matrix("6.25", 5),
         bands=[("優", "460%以上"), ("稍優", "360%以上未滿460%"), ("普通", "260%以上未滿360%"),
                ("稍劣", "180%以上未滿260%"), ("劣", "未滿180%")],
         rule="以容積率高低來制定其優劣等級", step_token="+6.25"),
    dict(id="building_prohibition", zh="有無禁止建築", group="土地使用管制(1)", page=1,
         printed_page="4-26", grades=GRADES2, matrix=sym_matrix("50", 2),
         bands=[("優", "無禁止建築"), ("劣", "有禁止建築")],
         rule="以區段內有無禁止建築來衡量其優劣等級", step_token="+50"),
    dict(id="building_restriction", zh="有無限制建築（整體開發、面積限制、高度限制……等）",
         group="土地使用管制(1)", page=1, printed_page="4-26", grades=GRADES3,
         matrix=sym_matrix("25", 3),
         bands=[("優", "無限制建築"), ("普通", "部分限制建築(如高度限制或面積限制)"),
                ("劣", "限制整體開發")],
         rule="以該地區有無限制建築及開發來衡量其優劣等級", step_token="+25"),
    # ---- page 2 (4-27): 交通運輸 (2)
    dict(id="main_road_width", zh="主要道路寬度", group="交通運輸(2)", page=2,
         printed_page="4-27", grades=GRADES5, matrix=sym_matrix("3.75", 5),
         bands=[("優", "28m以上"), ("稍優", "20m以上未滿28m"), ("普通", "12m以上未滿20m"),
                ("稍劣", "8m以上未滿12m"), ("劣", "未滿8m")],
         rule="以區段內主要道路寬度來衡量", step_token="+3.75"),
    dict(id="avg_road_width_in_section", zh="區段內道路平均寬度", group="交通運輸(2)", page=2,
         printed_page="4-27", grades=GRADES5, matrix=sym_matrix("3", 5),
         bands=[("優", "20m以上"), ("稍優", "15m以上未滿20m"), ("普通", "10m以上未滿15m"),
                ("稍劣", "8m以上未滿10m"), ("劣", "未滿8m")],
         rule="以區段內已開闢道路平均寬度來衡量", step_token="+12"),
    dict(id="large_station_proximity", zh="接近大型車站之程度", group="交通運輸(2)", page=2,
         printed_page="4-27", grades=GRADES5, matrix=sym_matrix("2.5", 5),
         bands=[("優", "區段內有大型車站或距離未滿500m"), ("稍優", "500m以上未滿1000m"),
                ("普通", "1000m以上未滿1500m"), ("稍劣", "1500m以上未滿2000m"),
                ("劣", "2000m以上或無")],
         rule="以接近大型車站距離程度來衡量其優劣等級", step_token="+2.5"),
    dict(id="bus_stop_proximity", zh="站牌之接近程度或密集程度", group="交通運輸(2)", page=2,
         printed_page="4-27", grades=GRADES5, matrix=sym_matrix("1", 5),
         bands=[("優", "區段內有站牌或距離未滿200m"), ("稍優", "200m以上未滿400m"),
                ("普通", "400m以上未滿600m"), ("稍劣", "600m以上未滿800m"),
                ("劣", "800m以上或無")],
         rule="以接近站牌距離程度來衡量其優劣等級", step_token="+4"),
    dict(id="interchange_proximity", zh="交流道之有無及接近交流道之程度", group="交通運輸(2)",
         page=2, printed_page="4-27", grades=GRADES5, matrix=sym_matrix("1", 5),
         bands=[("優", "區段內有交流道或距離未滿1000m"), ("稍優", "1000m以上未滿2000m"),
                ("普通", "2000m以上未滿3000m"), ("稍劣", "3000m以上未滿4000m"),
                ("劣", "4000m以上或無")],
         rule="以各地價區段至交流道直線距離計算", step_token="+4"),
    dict(id="road_planning_development", zh="區段內道路規劃及闢建程度", group="交通運輸(2)",
         page=2, printed_page="4-27", grades=GRADES5, matrix=sym_matrix("2.5", 5),
         bands=[("優", "全部規劃及闢建"), ("稍優", "大部分規劃及闢建"), ("普通", "部分規劃及闢建"),
                ("稍劣", "砂石路"), ("劣", "全無規劃及闢建")],
         rule="以區段內道路規劃及闢建程度來衡量", step_token="+2.5"),
    # ---- page 3 (4-28): 自然條件 (3) + 土地改良 (4)
    dict(id="sunlight", zh="日照", group="自然條件(3)", page=3, printed_page="4-28",
         grades=GRADES5, matrix=sym_matrix("2.5", 5),
         bands=[("優", "充分"), ("稍優", "少許有陰雨"), ("普通", "有部分陰雨"),
                ("稍劣", "有相當陰雨"), ("劣", "大部分陰雨")],
         rule="以陽光照射時間長短及溫溼度來衡量其優劣", step_token="+2.5"),
    dict(id="landscape", zh="景觀", group="自然條件(3)", page=3, printed_page="4-28",
         grades=GRADES5, matrix=sym_matrix("1.25", 5),
         bands=[("優", "視野極寬廣、景觀極優美"), ("稍優", "視野寬廣、景觀優美"),
                ("普通", "視野、景觀尚可"), ("稍劣", "視野、景觀差"), ("劣", "視野、景觀極差")],
         rule="以景觀良好程度來衡量其優劣等級", step_token="+1.25"),
    dict(id="slope", zh="傾斜度", group="自然條件(3)", page=3, printed_page="4-28",
         grades=GRADES5, matrix=sym_matrix("3.75", 5),
         bands=[("優", "平均坡度未滿5度"), ("稍優", "平均坡度5度以上未滿10度"),
                ("普通", "平均坡度10度以上未滿15度"), ("稍劣", "平均坡度15度以上未滿20度"),
                ("劣", "平均坡度20度以上")],
         rule="以該地價區段坡度高低來衡量", step_token="+3.75"),
    dict(id="drainage", zh="排水之良否", group="自然條件(3)", page=3, printed_page="4-28",
         grades=GRADES5, matrix=sym_matrix("2.5", 5),
         bands=[("優", "極完善"), ("稍優", "非常完善"), ("普通", "普通完善"),
                ("稍劣", "不良"), ("劣", "極不良")],
         rule="以排水系統是否良好來衡量", step_token="+2.5"),
    dict(id="terrain", zh="地勢", group="自然條件(3)", page=3, printed_page="4-28",
         grades=GRADES5, matrix=sym_matrix("2.5", 5),
         bands=[("優", "極平坦堅硬"), ("稍優", "平坦地"), ("普通", "緩傾斜地"),
                ("稍劣", "低地、溼地"), ("劣", "地勢孤劣地")],
         rule="以地勢平坦完整來衡量", step_token="+2.5"),
    dict(id="site_improvement", zh="建築基地改良（整平或填挖基地、開挖水溝、水土保持、舖築道路、埋設管道、修築駁嵌等）或其他改良",
         group="土地改良(4)", page=3, printed_page="4-28", grades=GRADES5,
         matrix=sym_matrix("2.5", 5),
         bands=[("優", "四項以上"), ("稍優", "三項"), ("普通", "二項"), ("稍劣", "一項"),
                ("劣", "無")],
         rule="以建築基地有無建築或其他改良來判定其優劣", step_token="+2.5"),
    # ---- page 4 (4-29): 公共建設 (5)
    dict(id="school_proximity", zh="接近學校之程度（國小、國中、高中、大專院校）",
         group="公共建設(5)", page=4, printed_page="4-29", grades=GRADES5,
         matrix=sym_matrix("2", 5),
         bands=[("優", "區段內有學校者或距離未滿300m"), ("稍優", "300m以上未滿500m"),
                ("普通", "500m以上未滿800m"), ("稍劣", "800m以上未滿1000m"),
                ("劣", "1000m以上或無")],
         rule="以接近國小、國中、高中或大專院校之距離衡量", step_token="+2"),
    dict(id="market_proximity", zh="接近市場之程度（傳統市場、超級市場、超大型購物中心）",
         group="公共建設(5)", page=4, printed_page="4-29", grades=GRADES5,
         matrix=sym_matrix("2", 5),
         bands=[("優", "區段內有市場者或距離未滿300m"), ("稍優", "300m以上未滿500m"),
                ("普通", "500m以上未滿800m"), ("稍劣", "800m以上未滿1000m"),
                ("劣", "1000m以上或無")],
         rule="以接近傳統市場、超級市場或超大型購物中心之距離來衡量", step_token="+2"),
    dict(id="park_plaza_proximity", zh="接近公園（里鄰公園、一般公園）、廣場、徒步區之程度",
         group="公共建設(5)", page=4, printed_page="4-29", grades=GRADES5,
         matrix=sym_matrix("2", 5),
         bands=[("優", "區段內有公園者或距離未滿300m"), ("稍優", "300m以上未滿500m"),
                ("普通", "500m以上未滿800m"), ("稍劣", "800m以上未滿1000m"),
                ("劣", "1000m以上或無")],
         rule="以接近鄰里公園、一般公園、廣場、徒步區之距離來衡量", step_token="+2"),
    dict(id="tourism_recreation_proximity", zh="接近觀光遊憩設施之程度", group="公共建設(5)",
         page=4, printed_page="4-29", grades=GRADES5, matrix=sym_matrix("1.5", 5),
         bands=[("優", "區段內有或距離未滿500m"), ("稍優", "500m以上未滿1000m"),
                ("普通", "1000m以上未滿1500m"), ("稍劣", "1500m以上未滿2000m"),
                ("劣", "2000m以上或無")],
         rule="以接近觀光遊憩設施程度來衡量其優劣等級", step_token="+1.5"),
    dict(id="parking_availability", zh="停車場地之便利程度", group="公共建設(5)", page=4,
         printed_page="4-29", grades=GRADES5, matrix=sym_matrix("1.5", 5),
         bands=[("優", "區段內有停車位者或距離未滿200m"), ("稍優", "200m以上未滿400m"),
                ("普通", "400m以上未滿600m"), ("稍劣", "600m以上未滿1000m"),
                ("劣", "1000m以上或無")],
         rule="以接近停車位的距離衡量其優劣等級", step_token="+1.5"),
    dict(id="service_facility_proximity", zh="接近服務性設施的程度（郵局、銀行、醫院、機關等設施）",
         group="公共建設(5)", page=4, printed_page="4-29", grades=GRADES5,
         matrix=sym_matrix("1.5", 5),
         bands=[("優", "區段內有或距離未滿500m"), ("稍優", "500m以上未滿1000m"),
                ("普通", "1000m以上未滿1500m"), ("稍劣", "1500m以上未滿2000m"),
                ("劣", "2000m以上或無")],
         rule="以區段內有服務性設施或接近服務性設施（郵局、醫院、機關等服務性設施）來衡量",
         step_token="+1.5"),
    # ---- page 5 (4-30): 特殊設施 (6) / 環境污染 (7) / 其他影響因素 (8)
    dict(id="utility_gas_facility", zh="電業設施及公用氣體燃料設施之有無及接近程度（變電所或高壓鐵塔、瓦斯槽）",
         group="特殊設施(6)", page=5, printed_page="4-30", grades=GRADES5,
         matrix=sym_matrix("2.5", 5), bands=DIST_NEAR_2000,
         rule="以區段內是否有此特殊設施及接近該等設施距離之遠近來衡量", step_token="+2.5",
         direction="reversed_distance"),
    dict(id="funeral_facility", zh="殯葬設施之有無及接近程度（墓地、殯儀館、火葬場）",
         group="特殊設施(6)", page=5, printed_page="4-30", grades=GRADES5,
         matrix=sym_matrix("2.5", 5), bands=DIST_NEAR_2000,
         rule="以該地區有無殯葬設施及接近程度來衡量其優劣等級", step_token="+2.5",
         direction="reversed_distance"),
    dict(id="waste_facility", zh="廢棄物處理設施之有無及接近程度（垃圾場或掩埋場、焚化爐）",
         group="特殊設施(6)", page=5, printed_page="4-30", grades=GRADES5,
         matrix=sym_matrix("3.75", 5), bands=DIST_NEAR_2000,
         rule="以該地區有無廢棄物處理設施及接近程度來衡量其優劣等級", step_token="+3.75",
         direction="reversed_distance"),
    dict(id="environmental_pollution", zh="水污染、噪音污染、廢氣污染、廢棄物污染等之有無及接近程度",
         group="環境污染(7)", page=5, printed_page="4-30", grades=GRADES5,
         matrix=sym_matrix("5", 5), bands=DIST_NEAR_2000,
         rule="以區段內是否有污染設施及接近程度來衡量其優劣等級", step_token="+5",
         direction="reversed_distance"),
    dict(id="other_factors", zh="其他影響因素", group="其他影響因素(8)", page=5,
         printed_page="4-30", grades=GRADES7, matrix=OTHER7_MATRIX,
         bands=[("極優", "其他影響因素極優"), ("優", "其他影響因素優"), ("稍優", "其他影響因素稍優"),
                ("普通", "其他影響因素普通"), ("稍劣", "其他影響因素稍差"),
                ("劣", "其他影響因素差"), ("極劣", "其他影響因素極差")],
         rule="以其他足以影響地價因素之程度(如:寧適度、人文素質、明星學區、淹水程度、地震帶、重大工程規劃、聯外動線等)衡量",
         step_token="3.33",
         matrix_note="Non-uniform ladder taken literally from the document "
                     "(0, 3.33, 6.67, 10, 13.33, 16.67, 20)."),
]

DIST_250 = [("優", "未滿250m"), ("稍優", "250m以上未滿500m"), ("普通", "500m以上未滿1000m"),
            ("稍劣", "1000m以上未滿2000m"), ("劣", "2000m以上或無")]

INDIVIDUAL_FACTORS = [
    # ---- page 6 (4-31): 宗地條件
    dict(id="parcel_area", zh="面積", group="宗地條件(1)", table4_row="7面積(M2)", page=6,
         printed_page="4-31", grades=GRADES5, matrix=sym_matrix("2.5", 5),
         bands=[("優", "600m2以上"), ("稍優", "400m2以上未滿600m2"), ("普通", "200m2以上未滿400m2"),
                ("稍劣", "50m2以上未滿200m2"), ("劣", "50m2以下")],
         rule="以宗地面積是否達最適開發規模衡量其優劣", step_token="2.50",
         # the superscript 2 of 600m² is a separate glyph in the text layer
         verify_tokens=["600m", "400m2以上未滿600m2", "200m2以上未滿400m2", "2.50"]),
    dict(id="parcel_width", zh="寬度", group="宗地條件(1)", table4_row="8寬度(M)", page=6,
         printed_page="4-31", grades=GRADES5, matrix=sym_matrix("1.25", 5),
         bands=[("優", "20m以上"), ("稍優", "15m以上未滿20m"), ("普通", "8m以上未滿15m"),
                ("稍劣", "4m以上未滿8m"), ("劣", "未滿4m")],
         rule="以宗地寬度衡量其優劣", step_token="1.25"),
    dict(id="parcel_depth", zh="深度", group="宗地條件(1)", table4_row="9深度(M)", page=6,
         printed_page="4-31", grades=GRADES5, matrix=sym_matrix("1.25", 5),
         bands=[("優", "14m以上未滿30m"), ("稍優", "30m以上未滿40m"),
                ("普通", "7m以上未滿14m 或 40m以上未滿50m"), ("稍劣", "50m以上未滿60m"),
                ("劣", "未滿7m 或 60m以上")],
         rule="以宗地深度衡量其優劣", step_token="1.25"),
    dict(id="parcel_shape", zh="形狀", group="宗地條件(1)", table4_row="10形狀", page=6,
         printed_page="4-31", grades=GRADES2, matrix=sym_matrix("5", 2),
         bands=[("優", "方形、梯形"), ("劣", "不規則形、長條形")],
         rule="以宗地形狀是否易於規劃利用衡量其優劣", step_token="5.00"),
    dict(id="street_frontage", zh="臨路情形", group="宗地條件(1)", table4_row="11臨街情形",
         page=6, printed_page="4-31", grades=GRADES5, matrix=sym_matrix("2.5", 5),
         bands=[("優", "3面以上臨街"), ("稍優", "路角地"), ("普通", "雙面臨街"),
                ("稍劣", "單面臨街"), ("劣", "未臨街地")],
         rule="以宗地實際臨街情況衡量其優劣", step_token="+2.5"),
    dict(id="parcel_terrain", zh="地勢", group="宗地條件(1)", table4_row="12地勢", page=6,
         printed_page="4-31", grades=GRADES2, matrix=sym_matrix("10", 2),
         bands=[("優", "平坦"), ("劣", "高亢或低窪")],
         rule="以宗地地勢高低衡量其優劣。", step_token="10.00"),
    # ---- page 7 (4-32): 道路條件 + 接近條件
    dict(id="road_type", zh="道路種類", group="道路條件(2)", table4_row="13道路種類", page=7,
         printed_page="4-32", grades=GRADES5, matrix=sym_matrix("1.25", 5),
         bands=[("優", "主要道路"), ("稍優", "次要道路"), ("普通", "巷道"), ("稍劣", "農路"),
                ("劣", "無")],
         rule="以道路種類衡量其優劣", step_token="1.25"),
    dict(id="frontage_road_width", zh="面前道路寬度", group="道路條件(2)",
         table4_row="14面前道路寬度", page=7, printed_page="4-32", grades=GRADES5,
         matrix=sym_matrix("3", 5),
         bands=[("優", "20m以上"), ("稍優", "12m以上未滿20m"), ("普通", "8m以上未滿12m"),
                ("稍劣", "5m以上未滿8m"), ("劣", "未滿5m或無")],
         rule="以道路面前道路寬度量其優劣", step_token="+12"),
    dict(id="school_proximity_parcel", zh="接近學校程度", group="接近條件(3)",
         table4_row="15接近學校之程度", page=7, printed_page="4-32", grades=GRADES5,
         matrix=sym_matrix("1.25", 5), bands=DIST_250,
         rule="以宗地接近學校程度衡量其優劣", step_token="1.25"),
    dict(id="market_proximity_parcel", zh="接近市場程度", group="接近條件(3)",
         table4_row="16接近市場之程度", page=7, printed_page="4-32", grades=GRADES5,
         matrix=sym_matrix("1.25", 5), bands=DIST_250,
         rule="以宗地接近市場程度衡量其優劣", step_token="1.25"),
    dict(id="park_plaza_proximity_parcel", zh="接近公園、廣場程度", group="接近條件(3)",
         table4_row="17接近公園、廣場之程度", page=7, printed_page="4-32", grades=GRADES5,
         matrix=sym_matrix("1.25", 5), bands=DIST_250,
         rule="以宗地接近公園、廣場程度衡量其優劣", step_token="1.25"),
    dict(id="station_proximity_parcel", zh="接近車站程度", group="接近條件(3)",
         table4_row="18接近車站之程度", page=7, printed_page="4-32", grades=GRADES5,
         matrix=sym_matrix("1.25", 5), bands=DIST_250,
         rule="以宗地接近車站程度衡量其優劣", step_token="1.25"),
    # ---- page 8 (4-33): 周邊環境條件 + 行政條件
    dict(id="commercial_district_proximity", zh="接近商圈程度", group="周邊環境條件(4)",
         table4_row="19接近商圈之程度", page=8, printed_page="4-33", grades=GRADES5,
         matrix=sym_matrix("2.5", 5), bands=DIST_250,
         rule="以宗地接近商圈之程度來衡量其優劣", step_token="+2.5"),
    dict(id="nuisance_facility", zh="嫌惡設施之有無", group="周邊環境條件(4)",
         table4_row="20嫌惡設施(類型)", page=8, printed_page="4-33", grades=GRADES5,
         matrix=sym_matrix("2", 5),
         bands=[("優", "2000m以上或無"), ("稍優", "1000m以上未滿2000m"),
                ("普通", "500m以上未滿1000m"), ("稍劣", "200m以上未滿500m"), ("劣", "未滿200m")],
         rule="以宗地周圍環境是否有嫌惡設施衡量其優劣", step_token="+2",
         direction="reversed_distance"),
    dict(id="parking_convenience", zh="停車方便性", group="周邊環境條件(4)",
         table4_row="21停車方便性", page=8, printed_page="4-33", grades=GRADES3,
         matrix=sym_matrix("2.5", 3),
         bands=[("優", "停車方便性優"), ("普通", "停車方便性普通"), ("劣", "停車方便性劣")],
         rule="以宗地周圍環境停車方便性衡量其優劣", step_token="2.50"),
    dict(id="zoning_district_parcel", zh="使用分區或編定", group="行政條件(5)",
         table4_row="22使用分區或編定用地", page=8, printed_page="4-33", grades=GRADES5,
         matrix=sym_matrix("3.75", 5),
         bands=[("優", "商業區、捷運用地(聯開)"), ("稍優", "住宅區、市場用地"),
                ("普通", "甲建、乙建、特定專用區、多目標使用之其他公共設施用地"),
                ("稍劣", "工業區、丙建、丁建"), ("劣", "其他可建築用地")],
         rule="以使用分區或編定衡量其優劣", step_token="3.75"),
    dict(id="coverage_ratio_parcel", zh="建蔽率", group="行政條件(5)", table4_row="23建蔽率(%)",
         page=8, printed_page="4-33", grades=GRADES5, matrix=sym_matrix("2.5", 5),
         bands=[("優", "80%以上"), ("稍優", "70%以上未滿80%"), ("普通", "60%以上未滿70%"),
                ("稍劣", "50%以上未滿60%"), ("劣", "未滿50%")],
         rule="以建蔽率衡量其優劣", step_token="2.50"),
    dict(id="far_parcel", zh="容積率", group="行政條件(5)", table4_row="24容積率(%)", page=8,
         printed_page="4-33", grades=None, matrix=None, bands=None,
         rule="詳備註", step_token="容積率差異以土地開發分析法",
         matrix_note="NO matrix. 備註: 1.容積率差異以土地開發分析法進行試算調整 "
                     "2.本項需與區域因素容積率併同考量，調整不足者另於區域因素補充調整"),
    # ---- page 9 (4-34): 行政條件 (cont.) + 其他
    dict(id="no_build_restriction_parcel", zh="有無禁限建", group="行政條件(5)",
         table4_row="25有無禁限建", page=9, printed_page="4-34", grades=GRADES5,
         matrix=sym_matrix("12.5", 5),
         bands=[("優", "無禁止或限制建築"), ("稍優", "限制建築高度"), ("普通", "限制建築高度及面積"),
                ("稍劣", "限制整體開發"), ("劣", "禁止建築")],
         rule="以有無禁限建衡量其優劣", step_token="12.50"),
    dict(id="dead_end_alley", zh="無尾巷", group="其他(6)", table4_row="6其他", page=9,
         printed_page="4-34", grades=GRADES2, matrix=sym_matrix("5", 2),
         bands=[("優", "無"), ("劣", "無尾巷")],
         rule="以其他足以影響地價因素之程度衡量", step_token="5.00"),
]


# --------------------------------------------------------------------------
# assignment extraction (題目.pdf 的副本.pdf)
# Physical page order is P002, P003, P004, P001 -- roles are taken from the
# printed 區段編號 on each sheet, never from page order.
# --------------------------------------------------------------------------

def A(value, zh, page, quote, unit=None, tokens=None):
    """Attribute record; quote(s) will be verified against the page text."""
    return dict(value=value, zh_label=zh, unit=unit, page=page, quote=quote,
                _tokens=tokens or [quote])


SITE_IMP_Q1 = "■整平或填挖基地　■開挖水溝　□水土保持　■鋪築道路"
SITE_IMP_Q2 = "■埋設管道"
SITE_IMP_VALUE = ["整平或填挖基地", "開挖水溝", "鋪築道路", "埋設管道"]

SUBJECTS = {
    "P001": dict(
        role="comparison_base",  # 比準地
        source_page=4,
        attributes=dict(
            section_code=A("P001-00", "區段編號", 4, "P001-00"),
            survey_period=A("1110901", "年期", 4, "1110901"),
            building_type=A("透天厝、公寓", "建築型態", 4, "透天厝、公寓"),
            land_use_current=A("商業用+住宅用（兩者皆標記）", "土地利用現況", 4,
                               "●商業用       　   　●住宅用",
                               tokens=["●商業用", "●住宅用"]),
            building_density=A("60%", "建築密度", 4, "60%", unit="percent"),
            urban_plan_status=A("都市計畫內", "都市計畫(內外)", 4, "都市計畫內"),
            zoning_district=A("第一種住宅區", "使用分區(使用地類別)", 4, "第一種住宅區"),
            coverage_ratio=A("50", "建蔽率", 4, "50%", unit="percent"),
            floor_area_ratio=A("260", "容積率", 4, "260%", unit="percent"),
            building_prohibition=A("無", "有無禁止建築", 4, "有無禁止建築",
                                   tokens=["有無禁止建築", "無"]),
            building_restriction=A("無", "有無限制建築(整體開發、面積限制、高度限制)", 4,
                                   "有無限制建築", tokens=["有無限制建築", "無"]),
            main_road_name=A("八德街", "主要道路名稱", 4, "名稱：八德街"),
            main_road_width=A("28", "主要道路寬度", 4, "寬度：28M", unit="m"),
            avg_road_width_in_section=A("12", "區段內道路平均寬度", 4,
                                        "區段內道路平均寬度    12M", unit="m"),
            road_planning_development=A("大部分規劃及闢建", "區段內道路規劃及闢建程度", 4,
                                        "大部分規劃及闢建"),
            sunlight=A("充分", "日照", 4, "充分"),
            landscape=A("視野、景觀尚可", "景觀", 4, "視野、景觀尚可"),
            slope=A("平均坡度未滿5度", "傾斜度", 4, "平均坡度未滿5度"),
            drainage=A("普通完善", "保（排）水之良否", 4, "普通完善"),
            terrain=A("極平坦堅硬", "地勢", 4, "極平坦堅硬"),
            site_improvement=A(SITE_IMP_VALUE, "建築基地改良（勾選4項）", 4, SITE_IMP_Q1,
                               tokens=[SITE_IMP_Q1, SITE_IMP_Q2]),
            section_range=A("沿八德街以西、啟智街及未開闢計畫道路以南、啟智街187巷以東、"
                            "啟智街187巷24弄以北之捷運開發區(變更前為第一種住宅區)",
                            "區段範圍", 4,
                            "啟智街187巷24弄以北之捷運開發區(變更\n前為第一種住宅區)"),
        ),
    ),
    "P002": dict(
        role="comparable_1",
        source_page=1,
        attributes=dict(
            section_code=A("P002-00", "區段編號", 1, "P002-00"),
            survey_period=A("1110901", "年期", 1, "1110901"),
            building_type=A("公寓、透天", "建築型態", 1, "公寓、透天"),
            land_use_current=A("住宅用", "土地利用現況", 1, "●住宅用"),
            building_density=A("70%", "建築密度", 1, "70%", unit="percent"),
            urban_plan_status=A("都市計畫內", "都市計畫(內外)", 1, "都市計畫內"),
            zoning_district=A("第一種住宅區", "使用分區(使用地類別)", 1, "第一種住宅區"),
            coverage_ratio=A("50", "建蔽率", 1, "50%", unit="percent"),
            floor_area_ratio=A("200", "容積率", 1, "200%", unit="percent"),
            building_prohibition=A("無", "有無禁止建築", 1, "有無禁止建築",
                                   tokens=["有無禁止建築", "無"]),
            building_restriction=A("無", "有無限制建築(整體開發、面積限制、高度限制)", 1,
                                   "有無限制建築", tokens=["有無限制建築", "無"]),
            main_road_name=A("樹人街", "主要道路名稱", 1, "名稱：樹人街"),
            main_road_width=A("7", "主要道路寬度", 1, "寬度：7M", unit="m"),
            avg_road_width_in_section=A("6", "區段內道路平均寬度", 1,
                                        "區段內道路平均寬度    6M", unit="m"),
            road_planning_development=A("部分規劃及闢建", "區段內道路規劃及闢建程度", 1,
                                        "部分規劃及闢建"),
            sunlight=A("充分", "日照", 1, "充分"),
            landscape=A("視野、景觀尚可", "景觀", 1, "視野、景觀尚可"),
            slope=A("平均坡度未滿5度", "傾斜度", 1, "平均坡度未滿5度"),
            drainage=A("普通完善", "保（排）水之良否", 1, "普通完善"),
            terrain=A("極平坦堅硬", "地勢", 1, "極平坦堅硬"),
            site_improvement=A(SITE_IMP_VALUE, "建築基地改良（勾選4項）", 1, SITE_IMP_Q1,
                               tokens=[SITE_IMP_Q1, SITE_IMP_Q2]),
            section_range=A("沿樹人街以北、長壽街21巷以西、啟智街14巷以南及樹德街136巷以東之第一種住宅區",
                            "區段範圍", 1,
                            "沿樹人街以北、長壽街21巷以西、啟智街14巷以南及樹德街136巷以東之第一種住宅區"),
        ),
    ),
    "P003": dict(
        role="comparable_2",
        source_page=2,
        attributes=dict(
            section_code=A("P003-00", "區段編號", 2, "P003-00"),
            survey_period=A("1110901", "年期", 2, "1110901"),
            building_type=A("公寓、透天", "建築型態", 2, "公寓、透天"),
            land_use_current=A("住宅用", "土地利用現況", 2, "●住宅用"),
            building_density=A("70%", "建築密度", 2, "70%", unit="percent"),
            urban_plan_status=A("都市計畫內", "都市計畫(內外)", 2, "都市計畫內"),
            zoning_district=A("第一種住宅區", "使用分區(使用地類別)", 2, "第一種住宅區"),
            coverage_ratio=A("50", "建蔽率", 2, "50%", unit="percent"),
            floor_area_ratio=A("260", "容積率", 2, "260%", unit="percent"),
            building_prohibition=A("無", "有無禁止建築", 2, "有無禁止建築",
                                   tokens=["有無禁止建築", "無"]),
            building_restriction=A("無", "有無限制建築(整體開發、面積限制、高度限制)", 2,
                                   "有無限制建築", tokens=["有無限制建築", "無"]),
            main_road_name=A("東榮街", "主要道路名稱", 2, "東榮街"),
            main_road_width=A("10", "主要道路寬度", 2, "寬度：10M", unit="m"),
            avg_road_width_in_section=A("7", "區段內道路平均寬度", 2,
                                        "區段內道路平均寬度    7M", unit="m"),
            road_planning_development=A("全部規劃及闢建", "區段內道路規劃及闢建程度", 2,
                                        "全部規劃及闢建"),
            sunlight=A("充分", "日照", 2, "充分"),
            landscape=A("視野、景觀尚可", "景觀", 2, "視野、景觀尚可"),
            slope=A("平均坡度未滿5度", "傾斜度", 2, "平均坡度未滿5度"),
            drainage=A("普通完善", "保（排）水之良否", 2, "普通完善"),
            terrain=A("極平坦堅硬", "地勢", 2, "極平坦堅硬"),
            site_improvement=A(SITE_IMP_VALUE, "建築基地改良（勾選4項）", 2, SITE_IMP_Q1,
                               tokens=[SITE_IMP_Q1, SITE_IMP_Q2]),
            section_range=A("沿東榮街以北、鎮前街411巷1弄以南、東榮街88巷以東、鎮前街367巷以西之第一種住宅區",
                            "區段範圍", 2,
                            "沿東榮街以北、鎮前街411巷1弄以南、東榮街88巷以東、鎮前街367巷以西之第一種住宅區"),
        ),
    ),
    "P004": dict(
        role="comparable_3",
        source_page=3,
        attributes=dict(
            section_code=A("P004-00", "區段編號", 3, "P004-00"),
            survey_period=A("1110901", "年期", 3, "1110901"),
            building_type=A("公寓", "建築型態", 3, "公寓"),
            land_use_current=A("住宅用", "土地利用現況", 3, "●住宅用"),
            building_density=A("50%", "建築密度", 3, "50%", unit="percent"),
            urban_plan_status=A("都市計畫內", "都市計畫(內外)", 3, "都市計畫內"),
            zoning_district=A("第一種住宅區", "使用分區(使用地類別)", 3, "第一種住宅區"),
            coverage_ratio=A("50", "建蔽率", 3, "50%", unit="percent"),
            floor_area_ratio=A("260", "容積率", 3, "260%", unit="percent"),
            building_prohibition=A("無", "有無禁止建築", 3, "有無禁止建築",
                                   tokens=["有無禁止建築", "無"]),
            building_restriction=A("無", "有無限制建築(整體開發、面積限制、高度限制)", 3,
                                   "有無限制建築", tokens=["有無限制建築", "無"]),
            main_road_name=A("潭興街", "主要道路名稱", 3, "名稱：潭興街"),
            main_road_width=A("10", "主要道路寬度", 3, "寬度：10M", unit="m"),
            avg_road_width_in_section=A("7", "區段內道路平均寬度", 3,
                                        "區段內道路平均寬度    7M", unit="m"),
            road_planning_development=A("全部規劃及闢建", "區段內道路規劃及闢建程度", 3,
                                        "全部規劃及闢建"),
            sunlight=A("充分", "日照", 3, "充分"),
            landscape=A("視野、景觀尚可", "景觀", 3, "視野、景觀尚可"),
            slope=A("平均坡度未滿5度", "傾斜度", 3, "平均坡度未滿5度"),
            drainage=A("普通完善", "保（排）水之良否", 3, "普通完善"),
            terrain=A("極平坦堅硬", "地勢", 3, "極平坦堅硬"),
            site_improvement=A(SITE_IMP_VALUE, "建築基地改良（勾選4項）", 3, SITE_IMP_Q1,
                               tokens=[SITE_IMP_Q1, SITE_IMP_Q2]),
            section_range=A("沿潭興街以西、潭興街107巷21弄以東及以北、潭興街91巷以南之第一種住宅區",
                            "區段範圍", 3,
                            "沿潭興街以西、潭興街107巷21弄以東及以北、潭興街91巷以南之第一種住宅區"),
        ),
    ),
}

# 表4 (assignment p.6) given values -- column mapping verified by x-coordinates
# (P001 x~250-320, P002 x~365-455, P003 x~510-600, P004 x~648-740).
TABLE4 = {
    "P001": dict(
        address=A("新北市樹林區樹德段1415地號", "土地標示", 6, "新北市樹林區樹德段1415地號"),
        transaction_date=A("111年9月1日", "交易日期", 6, "111年9月1日"),
    ),
    "P002": dict(
        address=A("新北市樹林區樹德段284地號", "土地標示", 6, "新北市樹林區樹德段284地號"),
        transaction_date=A("110年9月14日", "交易日期", 6, "110年9月14日"),
        normal_unit_price=A("130167", "土地正常單價", 6, "130,167", unit="TWD/m2"),
        price_date_adjustment_rate=A("5.96", "調整百分率", 6, "5.96%", unit="percent"),
        adjusted_unit_price=A("137925", "調整至估價基準日單價(元/M2)", 6, "137,925",
                              unit="TWD/m2"),
    ),
    "P003": dict(
        address=A("新北市樹林區太平段367、917地號", "土地標示", 6, "新北市樹林區太平段367、917地號"),
        transaction_date=A("111年1月11日", "交易日期", 6, "111年1月11日"),
        normal_unit_price=A("135275", "土地正常單價", 6, "135,275", unit="TWD/m2"),
        price_date_adjustment_rate=A("4.09", "調整百分率", 6, "4.09%", unit="percent"),
        adjusted_unit_price=A("140808", "調整至估價基準日單價(元/M2)", 6, "140,808",
                              unit="TWD/m2"),
    ),
    "P004": dict(
        address=A("新北市樹林區文林段317地號", "土地標示", 6, "新北市樹林區文林段317地號"),
        transaction_date=A("110年10月29日", "交易日期", 6, "110年10月29日"),
        normal_unit_price=A("170909", "土地正常單價", 6, "170,909", unit="TWD/m2"),
        price_date_adjustment_rate=A("5.49", "調整百分率", 6, "5.49%", unit="percent"),
        adjusted_unit_price=A("180292", "調整至估價基準日單價(元/M2)", 6, "180,292",
                              unit="TWD/m2"),
    ),
}

ASSIGNMENT_REMARK_T51 = ("使用分區、建蔽率、容積率修正併同於比較法調查估價表宗地個別因素"
                         "考量調整修正")
FAR_PARCEL_REMARK_1 = "1.容積率差異以土地開發分析法進行試算調整"
FAR_PARCEL_REMARK_2 = "2.本項需與區域因素容積率併同考量，調整不足者另於區域因素補充調整"

# Regional factors with NO stated inputs on any 表3 sheet (facility rows all
# have unmarked circles and blank distances). Blank is recorded as missing --
# never as 0, never as "none exists".
REGIONAL_MISSING = [
    "large_station_proximity", "bus_stop_proximity", "interchange_proximity",
    "school_proximity", "market_proximity", "park_plaza_proximity",
    "tourism_recreation_proximity", "parking_availability", "service_facility_proximity",
    "utility_gas_facility", "funeral_facility", "waste_facility",
    "environmental_pollution",
]
# Regional factors the assignment's 表5-1 remark moves into 表4 individual factors.
REGIONAL_EXCLUDED = ["zoning_district", "coverage_ratio", "floor_area_ratio"]

INDIVIDUAL_MISSING = [
    "parcel_area", "parcel_width", "parcel_depth", "parcel_shape", "street_frontage",
    "parcel_terrain", "road_type", "frontage_road_width", "school_proximity_parcel",
    "market_proximity_parcel", "park_plaza_proximity_parcel", "station_proximity_parcel",
    "commercial_district_proximity", "nuisance_facility", "parking_convenience",
    "dead_end_alley",
]
MISSING_REASON_INDIVIDUAL = (
    "表4 rows for this factor are blank in the assignment (unit placeholders only). "
    "表3 is section-level data and provides no parcel attribute; section main-road "
    "data must NOT be substituted for parcel frontage, and blank must NOT be read "
    "as 0 or as 'none'.")
MISSING_REASON_REGIONAL = (
    "表3 facility row is unmarked (empty circles, blank name/distance) on every "
    "sheet. Cannot distinguish 'facility does not exist' from 'not surveyed'; "
    "blank is not read as 0 and not read as 無.")

COMPARABLES = ["P002", "P003", "P004"]


# --------------------------------------------------------------------------
# grading
# --------------------------------------------------------------------------

def grade_by_width(factor_id: str, w: Decimal) -> str:
    if factor_id == "main_road_width":
        cuts = [("優", 28), ("稍優", 20), ("普通", 12), ("稍劣", 8)]
    elif factor_id == "avg_road_width_in_section":
        cuts = [("優", 20), ("稍優", 15), ("普通", 10), ("稍劣", 8)]
    else:  # pragma: no cover
        raise ValueError(factor_id)
    for g, lo in cuts:
        if w >= lo:
            return g
    return "劣"


QUALITATIVE_GRADE = {
    "urban_plan_status": {"都市計畫內": "優", "都市計畫外": "劣"},
    "building_prohibition": {"無": "優", "有": "劣"},
    "building_restriction": {"無": "優"},
    "road_planning_development": {"全部規劃及闢建": "優", "大部分規劃及闢建": "稍優",
                                  "部分規劃及闢建": "普通", "砂石路": "稍劣",
                                  "全無規劃及闢建": "劣"},
    "sunlight": {"充分": "優", "少許有陰雨": "稍優", "有部分陰雨": "普通",
                 "有相當陰雨": "稍劣", "大部分陰雨": "劣"},
    "landscape": {"視野極寬廣、景觀極優美": "優", "視野寬廣、景觀優美": "稍優",
                  "視野、景觀尚可": "普通", "視野、景觀差": "稍劣", "視野、景觀極差": "劣"},
    "slope": {"平均坡度未滿5度": "優", "平均坡度5度以上未滿10度": "稍優",
              "平均坡度10度以上未滿15度": "普通", "平均坡度15度以上未滿20度": "稍劣",
              "平均坡度20度以上": "劣"},
    "drainage": {"極完善": "優", "非常完善": "稍優", "普通完善": "普通", "不良": "稍劣",
                 "極不良": "劣"},
    "terrain": {"極平坦堅硬": "優", "平坦地": "稍優", "緩傾斜地": "普通", "低地、溼地": "稍劣",
                "地勢孤劣地": "劣"},
}


def grade_coverage(pct: Decimal) -> str:
    for g, lo in [("優", 80), ("稍優", 70), ("普通", 60), ("稍劣", 50)]:
        if pct >= lo:
            return g
    return "劣"


def subject_input(pid: str, attr: str) -> str:
    return SUBJECTS[pid]["attributes"][attr]["value"]


def regional_grade(factor_id: str, pid: str) -> tuple[str, str, str]:
    """Return (grade, input_value, band_text)."""
    fac = next(f for f in REGIONAL_FACTORS if f["id"] == factor_id)
    if factor_id in ("main_road_width", "avg_road_width_in_section"):
        raw = subject_input(pid, factor_id)
        g = grade_by_width(factor_id, Decimal(raw))
        return g, f"{raw}m", dict(fac["bands"])[g]
    if factor_id == "site_improvement":
        items = subject_input(pid, "site_improvement")
        n = len(items)
        g = "優" if n >= 4 else {3: "稍優", 2: "普通", 1: "稍劣"}.get(n, "劣")
        return g, f"{n}項({'、'.join(items)})", dict(fac["bands"])[g]
    raw = subject_input(pid, factor_id)
    g = QUALITATIVE_GRADE[factor_id][raw]
    return g, raw, dict(fac["bands"])[g]


def matrix_lookup(fac: dict, target_grade: str, base_grade: str) -> Decimal:
    gi = fac["grades"].index(target_grade)
    gj = fac["grades"].index(base_grade)
    return Decimal(fac["matrix"][gi][gj])


def dstr(d: Decimal) -> str:
    return str(d.normalize()) if d != 0 else "0"


# --------------------------------------------------------------------------
# build outputs
# --------------------------------------------------------------------------

def clean_attr(doc: Doc, page_offset_doc: Doc | None, item: dict, label: str) -> dict:
    tokens = item.pop("_tokens")
    conf = 1.0
    for t in tokens:
        conf = min(conf, verified(doc, item["page"], t, label))
    out = {k: v for k, v in item.items() if v is not None}
    out["confidence"] = conf
    return out


def build_extraction(assignment: Doc) -> dict:
    subjects = {}
    for pid, s in SUBJECTS.items():
        attrs = {k: clean_attr(assignment, None, dict(v), f"{pid}.{k}")
                 for k, v in s["attributes"].items()}
        attrs["facilities_not_stated"] = dict(
            value=[REGIONAL_FACTORS[i]["zh"] for i in range(len(REGIONAL_FACTORS))
                   if REGIONAL_FACTORS[i]["id"] in REGIONAL_MISSING],
            zh_label="表3設施勾選欄（全部未勾選、距離空白）",
            page=s["source_page"],
            quote="○本區段內　○本區段外(距               M)",
            confidence=verified(assignment, s["source_page"],
                                "○本區段內", f"{pid}.facilities"),
            note=MISSING_REASON_REGIONAL,
        )
        subjects[pid] = dict(role=s["role"], source_page=s["source_page"],
                             zh_role={"comparison_base": "比準地",
                                      "comparable_1": "比較標的1",
                                      "comparable_2": "比較標的2",
                                      "comparable_3": "比較標的3"}[s["role"]],
                             attributes=attrs)

    table4 = {}
    for pid, cols in TABLE4.items():
        table4[pid] = {k: clean_attr(assignment, None, dict(v), f"table4.{pid}.{k}")
                       for k, v in cols.items()}

    open_conditions = [
        dict(
            id="p001_zoning_mrt_development_zone",
            needs_human_confirmation=True,
            subject="P001",
            summary=("P001's 使用分區 field states 第一種住宅區, but the 區段範圍 on the "
                     "same sheet describes the section as 捷運開發區(變更前為第一種住宅區). "
                     "The individual/regional 使用分區 criteria grade 商業區、捷運用地(聯開) "
                     "as 優 and 住宅區、市場用地 as 稍優, so the two readings differ by one "
                     "grade (+3.75 points per comparable in 表4 row 22). NOT resolved here."),
            quote_field="第一種住宅區",
            quote_range=("沿八德街以西、啟智街及未開闢計畫道路以南、啟智街187巷以東、"
                         "啟智街187巷24弄以北之捷運開發區(變更前為第一種住宅區)"),
            page=4,
            note_on_wording=("Even if 捷運開發區 is confirmed as current zoning, whether it "
                             "maps to the criteria category 捷運用地(聯開) is a second, "
                             "separate confirmation."),
            evidence_that_would_settle=("都市計畫變更書圖 / zoning publication effective as of "
                                        "1110901 for the P001 block, plus organizer guidance on "
                                        "whether 捷運開發區 counts as 捷運用地(聯開)."),
        ),
        dict(
            id="p001_dual_land_use_mark",
            needs_human_confirmation=True,
            subject="P001",
            summary=("P001's 土地利用現況 has BOTH ●商業用 and ●住宅用 marked (all other "
                     "sheets mark only ●住宅用). Corroborates mixed character of the P001 "
                     "block; confirm intended reading."),
            quote="●商業用       　   　●住宅用",
            page=4,
            evidence_that_would_settle="Organizer clarification or the official filled 表3.",
        ),
        dict(
            id="far_correction_method",
            needs_human_confirmation=True,
            subject="P002 vs P001",
            summary=("P002 容積率 200% differs from P001 260%. The assignment's 表5-1 remark "
                     "moves 容積率 out of the regional sheet; the individual criteria row 24 "
                     "has NO matrix and instead requires 土地開發分析法 and coordination with "
                     "the regional 容積率 to avoid double counting. No FAR correction is "
                     "computed here; do NOT derive one from the 200/260 band difference."),
            quotes=[ASSIGNMENT_REMARK_T51, FAR_PARCEL_REMARK_1, FAR_PARCEL_REMARK_2],
            pages={"assignment": 5, "criteria": 8},
            evidence_that_would_settle=("Organizer-accepted 土地開發分析法 inputs/procedure for "
                                        "the FAR difference, or instruction that the regional "
                                        "容積率 matrix applies instead."),
        ),
        dict(
            id="blank_facility_rows",
            needs_human_confirmation=True,
            subject="all",
            summary=("All 表3 facility rows (stations, bus stops, interchange, schools, "
                     "markets, parks, tourism, parking, service facilities, "
                     "utility/funeral/waste facilities, pollution) are unmarked with blank "
                     "distances on every sheet. If 'blank means none/identical' were "
                     "confirmed, these factors would grade equally for P001 and each "
                     "comparable and contribute 0; this snapshot does NOT assume that."),
            page="1-4",
            evidence_that_would_settle=("Completed 表3 rows, or organizer confirmation that "
                                        "unmarked rows are to be treated as 無/identical."),
        ),
        dict(
            id="parcel_attributes_absent",
            needs_human_confirmation=True,
            subject="all",
            summary=("表4 rows 7-21 and 6其他 (宗地面積/寬度/深度/形狀/臨街情形/地勢/道路種類/"
                     "面前道路寬度/接近距離5項/嫌惡設施/停車方便性/無尾巷) are blank for all "
                     "four subjects. Parcel-level survey or cadastral data is required; "
                     "section-level 表3 values are not substitutes."),
            page=6,
            evidence_that_would_settle="Parcel survey sheets or cadastral/GIS measurements.",
        ),
    ]

    return dict(
        source=dict(file=ASSIGNMENT_PDF.name, pages=assignment.n_pages,
                    text_layer="native", ocr_used=False),
        case=dict(
            case_number=dict(value="1110901-99-XXX", page=5, quote="1110901-99-XXX",
                             confidence=verified(assignment, 5, "1110901-99-XXX",
                                                 "case_number")),
            valuation_date=dict(roc="1110901", iso="2022-09-01",
                                zh_label="估價基準日/年期", page=1, quote="1110901",
                                confidence=verified(assignment, 1, "1110901",
                                                    "valuation_date")),
            district="新北市樹林區",
            land_use_class="普通住宅用地",
            base_parcel_serial=dict(value="0003", zh_label="比準地 宗地流水號", page=6,
                                    quote="0003",
                                    confidence=verified(assignment, 6, "0003",
                                                        "base_parcel_serial")),
            page_order_note=("Physical page order in the assignment PDF is "
                             "P002, P003, P004, P001; roles come from the printed 區段編號 "
                             "and 表4/表5-1 headers, never from page order."),
            case_collection_note=dict(
                quote=("比準地所在區段於案例蒐集期間(111年3月2日至111年9月1日間)無適當成交案例，"
                       "故依土地徵收補償市價查估辦法第17條第3項規定，擴大選取範圍及案例蒐集期間"
                       "至估價基準日前一年內"),
                page=6,
                confidence=verified(assignment, 6, "無適當成交案例", "collection_note")),
            price_date_note=dict(
                quote="1.價格日期調整係參酌新北市樹林區土地平均區段地價表(住宅區)進行調整。",
                page=6,
                confidence=verified(assignment, 6,
                                    "價格日期調整係參酌新北市樹林區土地平均區段地價表",
                                    "price_date_note")),
        ),
        table4=table4,
        table5_1=dict(
            regional_move_remark=dict(
                quote=ASSIGNMENT_REMARK_T51, page=5,
                confidence=verified(assignment, 5, ASSIGNMENT_REMARK_T51, "t51_remark"),
                effect=("使用分區, 建蔽率, 容積率 are corrected in 表4 individual factors, "
                        "NOT in the regional sheet, for this case.")),
            prefilled_other_factors_row=dict(
                zh_label="其他影響因素(8)",
                cells={"P001": "－ / 無", "P002": "－ / 無 / 0.00",
                       "P003": "－ / 無 / 0.00", "P004": "－ / 無 / 0.00"},
                page=5,
                quote="其他影響",
                confidence=verified(assignment, 5, "其他影響", "t51_other_row"),
                note=("Verified by cell coordinates (y~668; P001 x~196-221, P002 x~252-314, "
                      "P003 x~355-417, P004 x~459-521). The 環境污染(7) row is BLANK -- only "
                      "其他影響因素(8) is pre-filled with 無/0.00.")),
        ),
        open_conditions=open_conditions,
        subjects=subjects,
    )


def build_criteria(criteria: Doc) -> dict:
    def pack(factors, axes, table_zh):
        out = []
        for f in factors:
            conf = 1.0
            if f.get("verify_tokens"):
                checked = list(f["verify_tokens"])
            else:
                checked = [text for _, text in (f["bands"] or [])[:3]]
                checked.append(f["step_token"])
            for t in checked:
                conf = min(conf, verified(criteria, f["page"], t, f"criteria.{f['id']}"))
            rec = dict(
                id=f["id"], zh_label=f["zh"], group=f["group"],
                criteria_pdf_page=f["page"], printed_page_label=f["printed_page"],
                rule_zh=f["rule"],
                bands=[dict(grade=g, condition_zh=t) for g, t in (f["bands"] or [])] or None,
                matrix=None if f["matrix"] is None else dict(
                    axes=axes,
                    grade_order=f["grades"],
                    unit="percentage_points",
                    values=f["matrix"],
                ),
                confidence=conf,
            )
            if f.get("direction"):
                rec["direction"] = f["direction"]
            if f.get("matrix_note"):
                rec["matrix_note"] = f["matrix_note"]
            if f.get("table4_row"):
                rec["table4_row"] = f["table4_row"]
            out.append(rec)
        return out

    regional_axes = dict(
        rows="目標區段 grade (the section whose value is being derived; in 表5-1 use, "
             "the 比準地 section P001)",
        columns="基準區段 grade (the section supplying the price; in 表5-1 use, the "
                "comparable's section P00x)",
        evidence=("Diagonal header cell on criteria p.1 rendered visually: top-right "
                  "label = 基準區段 over the column heads (優/劣), bottom-left label = "
                  "目標區段 beside the row heads. Sign check: row 優 x col 劣 = +20 "
                  "(target better than base -> upward adjustment of the base price)."),
    )
    individual_axes = dict(
        rows="宗地(比準地) grade (in 表4 use, the 比準地 P001)",
        columns="比凖地(比較標的) grade (in 表4 use, the comparable P00x)",
        evidence=("Diagonal header cell on criteria p.6 rendered visually: top-right = "
                  "比凖地(比較標的) over columns, bottom-left = 宗地(比準地) beside rows. "
                  "Row 優 x col 劣 = +10 for 面積: positive when the 比準地 is better, "
                  "i.e. the comparable's price is adjusted upward."),
    )

    return dict(
        source=dict(file=CRITERIA_PDF.name, pages=criteria.n_pages,
                    text_layer="native", ocr_used=False,
                    printed_page_labels="4-26 through 4-34"),
        orientation_note=("matrix[row][col]: row = entity whose value is being derived "
                          "(目標區段 / 比準地), col = entity providing the price (基準區段 / "
                          "比較標的). Values are percentage POINTS as Decimal strings; "
                          "positive means the comparable/base price is adjusted upward."),
        double_count_guard=dict(
            statement=("使用分區/建蔽率/容積率 appear in BOTH the regional table (p.1) and "
                       "the individual table (p.8). The assignment's 表5-1 remark moves all "
                       "three to 表4 for this case; the individual 容積率 row additionally "
                       "requires 土地開發分析法 and coordination with the regional 容積率. "
                       "Whether any residual regional supplement applies is ambiguous in the "
                       "documents and is flagged, not decided."),
            quotes=[ASSIGNMENT_REMARK_T51, FAR_PARCEL_REMARK_1, FAR_PARCEL_REMARK_2]),
        regional_factors=pack(REGIONAL_FACTORS, regional_axes,
                              "新北市樹林區普通住宅用地影響地價區域因素評價基準明細表"),
        individual_factors=pack(INDIVIDUAL_FACTORS, individual_axes,
                                "新北市樹林區住宅用地影響地價個別因素評價基準明細表"),
    )


def build_computed() -> dict:
    computed_count = 0
    missing_entries = 0

    regional = []
    computable = [f for f in REGIONAL_FACTORS
                  if f["id"] not in REGIONAL_MISSING
                  and f["id"] not in REGIONAL_EXCLUDED
                  and f["id"] != "other_factors"]

    per_comp_totals = {c: Decimal(0) for c in COMPARABLES}
    group_partials: dict[str, dict[str, Decimal]] = {}

    for fac in REGIONAL_FACTORS:
        fid = fac["id"]
        for comp in COMPARABLES:
            entry = dict(factor_id=fid, zh_label=fac["zh"], group=fac["group"],
                         comparable=comp, criteria_page=fac["page"],
                         printed_page_label=fac["printed_page"])
            if fid in REGIONAL_EXCLUDED:
                entry.update(state="excluded_by_assignment_remark",
                             reason=("表5-1 remark: " + ASSIGNMENT_REMARK_T51 +
                                     " -- corrected in 表4 individual factors instead."))
            elif fid in REGIONAL_MISSING:
                entry.update(state="missing", reason=MISSING_REASON_REGIONAL)
                missing_entries += 1
            elif fid == "other_factors":
                entry.update(state="given", correction_points="0",
                             source=("Assignment 表5-1 pre-filled row 其他影響因素(8): "
                                     "'－ / 無 / 0.00' for every comparable (p.5)."))
            else:
                g1, in1, band1 = regional_grade(fid, "P001")
                g2, in2, band2 = regional_grade(fid, comp)
                pts = matrix_lookup(fac, g1, g2)
                trace = (f"{comp} {fid} {in2} -> grade {g2} (band {band2}) vs "
                         f"P001 {in1} -> grade {g1} (band {band1}); "
                         f"matrix[目標={g1}][基準={g2}] = "
                         f"{'+' if pts > 0 else ''}{dstr(pts)} points "
                         f"(criteria p.{fac['page']}, {fac['printed_page']})")
                entry.update(state="computed", p001_input=in1, comparable_input=in2,
                             p001_grade=g1, comparable_grade=g2,
                             correction_points=dstr(pts), trace=trace)
                computed_count += 1
                per_comp_totals[comp] += pts
                group_partials.setdefault(fac["group"], {}).setdefault(comp, Decimal(0))
                group_partials[fac["group"]][comp] += pts
            regional.append(entry)

    group_subtotals = []
    missing_by_group: dict[str, list[str]] = {}
    for f in REGIONAL_FACTORS:
        if f["id"] in REGIONAL_MISSING:
            missing_by_group.setdefault(f["group"], []).append(f["id"])
    all_groups = []
    for f in REGIONAL_FACTORS:
        if f["group"] not in all_groups:
            all_groups.append(f["group"])
    for grp in all_groups:
        row = dict(group=grp)
        excl = [f["id"] for f in REGIONAL_FACTORS
                if f["group"] == grp and f["id"] in REGIONAL_EXCLUDED]
        miss = missing_by_group.get(grp, [])
        vals = group_partials.get(grp, {})
        if grp == "其他影響因素(8)":
            row.update(status="given", subtotal_points={c: "0" for c in COMPARABLES})
        elif miss:
            row.update(status="partial" if vals else "missing",
                       missing_factors=miss,
                       subtotal_points=({c: dstr(vals.get(c, Decimal(0)))
                                         for c in COMPARABLES} if vals else None))
        else:
            row.update(status="complete",
                       subtotal_points={c: dstr(vals.get(c, Decimal(0)))
                                        for c in COMPARABLES})
        if excl:
            row["excluded_factors_moved_to_table4"] = excl
        group_subtotals.append(row)

    regional_partial_totals = dict(
        caveat=("PARTIAL sums over the 12 computable + 1 given factors only. 13 factors "
                "are missing inputs; these totals are NOT the 區域因素調整百分率 and must "
                "not be used as such."),
        points={c: dstr(per_comp_totals[c]) for c in COMPARABLES},
        computable_factor_count=len(computable) ,
        given_factor_count=1,
        missing_factor_count=len(REGIONAL_MISSING),
        excluded_factor_count=len(REGIONAL_EXCLUDED),
    )

    # ---- individual factors
    individual = []
    ind_by_id = {f["id"]: f for f in INDIVIDUAL_FACTORS}
    for fac in INDIVIDUAL_FACTORS:
        fid = fac["id"]
        for comp in COMPARABLES:
            entry = dict(factor_id=fid, zh_label=fac["zh"], group=fac["group"],
                         table4_row=fac.get("table4_row"), comparable=comp,
                         criteria_page=fac["page"],
                         printed_page_label=fac["printed_page"])
            if fid in INDIVIDUAL_MISSING:
                entry.update(state="missing", reason=MISSING_REASON_INDIVIDUAL)
                missing_entries += 1
            elif fid == "zoning_district_parcel":
                m = fac["matrix"]
                entry.update(
                    state="blocked_by_open_condition",
                    open_condition_id="p001_zoning_mrt_development_zone",
                    comparable_input=subject_input(comp, "zoning_district"),
                    comparable_grade="稍優",
                    reason=("P001 zoning ambiguous (第一種住宅區 field vs 捷運開發區 range "
                            "text). Not resolved here."),
                    conditional_scenarios=[
                        dict(condition="P001 confirmed 第一種住宅區 (稍優)",
                             correction_points="0",
                             trace=f"matrix[稍優][稍優] = 0 (criteria p.8, 4-33)"),
                        dict(condition=("P001 confirmed to grade 優 (i.e. 捷運開發區 accepted "
                                        "as 商業區/捷運用地(聯開) category)"),
                             correction_points="3.75",
                             trace=f"matrix[優][稍優] = +3.75 (criteria p.8, 4-33)"),
                    ])
            elif fid == "coverage_ratio_parcel":
                g1 = grade_coverage(Decimal(subject_input("P001", "coverage_ratio")))
                g2 = grade_coverage(Decimal(subject_input(comp, "coverage_ratio")))
                pts = matrix_lookup(fac, g1, g2)
                entry.update(
                    state="computed", p001_input="50%", comparable_input="50%",
                    p001_grade=g1, comparable_grade=g2, correction_points=dstr(pts),
                    trace=(f"{comp} 建蔽率 50% -> grade {g2} (band 50%以上未滿60%) vs P001 "
                           f"50% -> grade {g1}; matrix[{g1}][{g2}] = {dstr(pts)} points "
                           f"(criteria p.8, 4-33)"),
                    note=("Inputs are the 表3 zone-wide 建蔽率 control values; the "
                          "assignment's 表5-1 remark directs these to 表4."))
                computed_count += 1
            elif fid == "far_parcel":
                far1 = subject_input("P001", "floor_area_ratio")
                far2 = subject_input(comp, "floor_area_ratio")
                if far1 == far2:
                    entry.update(
                        state="computed", p001_input=f"{far1}%",
                        comparable_input=f"{far2}%", correction_points="0",
                        trace=(f"{comp} 容積率 {far2}% = P001 {far1}%; identical inputs -> "
                               "no difference to adjust (row has no matrix; 土地開發分析法 "
                               "applies only to differences)."))
                    computed_count += 1
                else:
                    entry.update(
                        state="manual_method_required",
                        open_condition_id="far_correction_method",
                        p001_input=f"{far1}%", comparable_input=f"{far2}%",
                        reason=(f"{comp} 容積率 {far2}% vs P001 {far1}%: the individual row "
                                "has NO matrix -- " + FAR_PARCEL_REMARK_1 + "; " +
                                FAR_PARCEL_REMARK_2 + ". A correction must NOT be guessed "
                                "from the 200/260 band difference."))
            elif fid == "no_build_restriction_parcel":
                pts = matrix_lookup(fac, "優", "優")
                entry.update(
                    state="computed",
                    p001_input="禁止建築:無 / 限制建築:無",
                    comparable_input="禁止建築:無 / 限制建築:無",
                    p001_grade="優", comparable_grade="優", correction_points=dstr(pts),
                    trace=(f"{comp} 無禁止且無限制建築 -> grade 優 (band 無禁止或限制建築) vs "
                           f"P001 same -> 優; matrix[優][優] = 0 points (criteria p.9, 4-34)"))
                computed_count += 1
            individual.append(entry)

    # ---- price-date arithmetic verification (given values, independently recomputed)
    price_checks = []
    for comp in COMPARABLES:
        normal = Decimal(TABLE4[comp]["normal_unit_price"]["value"])
        rate = Decimal(TABLE4[comp]["price_date_adjustment_rate"]["value"]) / Decimal(100)
        given_adj = Decimal(TABLE4[comp]["adjusted_unit_price"]["value"])
        raw = normal * (1 + rate)
        rounded = raw.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        price_checks.append(dict(
            comparable=comp,
            normal_unit_price=str(normal),
            given_rate_percent=str(Decimal(TABLE4[comp]["price_date_adjustment_rate"]["value"])),
            recomputed_exact=str(raw),
            recomputed_rounded=str(rounded),
            given_adjusted_price=str(given_adj),
            match=bool(rounded == given_adj),
            trace=(f"{comp}: {normal} x (1 + {dstr(rate*100)}%) = {raw} -> "
                   f"round-half-up {rounded} vs given {given_adj} "
                   f"({'MATCH' if rounded == given_adj else 'MISMATCH'})"),
        ))

    return dict(
        method=dict(
            arithmetic="decimal.Decimal only; values carried as strings",
            regional_direction=("correction adjusts the comparable's price toward the "
                                "比準地 section: matrix[目標=P001 grade][基準=P00x grade]"),
            individual_direction=("matrix[宗地(比準地)=P001 grade][比凖地(比較標的)=P00x "
                                  "grade]; positive = comparable adjusted upward"),
            rules=["blank inputs are 'missing', never 0 and never 無",
                   "no equal-weight assumptions",
                   "section road data never substituted for parcel frontage",
                   "no FAR correction guessed from the 200/260 difference",
                   "使用分區/建蔽率/容積率 not double-counted: regional rows excluded per "
                   "the assignment's 表5-1 remark, corrected in 表4 only"],
        ),
        regional_corrections=regional,
        regional_group_subtotals=group_subtotals,
        regional_partial_totals=regional_partial_totals,
        individual_corrections=individual,
        price_date_adjustment_checks=price_checks,
        counts=dict(
            computed_corrections=computed_count,
            given_values_verified=len(price_checks) + len(COMPARABLES),  # + other_factors rows
            missing_input_entries=missing_entries,
            missing_unique_factors=len(REGIONAL_MISSING) + len(INDIVIDUAL_MISSING),
        ),
    )


CROSSCHECK_MD = """# Cross-check: method shape vs the organizer's Jinshan sample (structure only)

The organizer's pre-filled workbook (查估書表範本.pdf) and criteria example
(評價基準明細表範例.pdf) describe the **金山 commercial** example case. Its case
facts (2025/民國114 dates, 金山 sections, 2.00% price-date rate, 第二種商業區,
prices 184,763 / 188,459 / 212,958, etc.) are acceptance references for METHOD
SHAPE only and appear nowhere in `extraction.json` / `computed.json`.

## Structural agreements (our method matches the sample's shape)

1. **Matrix orientation and sign.** Sample 表4 row 14 面前道路寬度: 比準地 中山路
   18M vs comparable 金包里街 6M gives a **positive** correction (5.00%), and row
   13 道路種類 主要道路 vs 次要道路 gives +2.00%: positive when the 比準地 is
   better, i.e. matrix[比準地 grade][比較標的 grade] with the comparable's price
   adjusted upward. This matches the orientation we verified visually on the
   Shulin criteria headers (rows = 宗地(比準地)/目標區段, columns =
   比凖地(比較標的)/基準區段).
2. **Regional sheet structure.** Sample 表5-2 fills, per factor and subject, a
   rank number + grade text (1=優 … 5=劣) and a per-comparable 修正百分比, then
   group 百分比小計 and 總修正數 = (1)+…+(8). Our `computed.json` mirrors this:
   per-factor grade pair -> matrix points -> group subtotal -> total.
3. **Price build-up.** Sample 表4: 調整至估價基準日單價 188,459 with regional
   0.00% and individual 合計 13.00% yields 試算價格 212,958
   (188,459 x 1.13 = 212,958.67 -> 212,958). Structure: price-date-adjusted unit
   price x (1 + regional % + individual 合計). With regional = 0 in the sample,
   additive vs multiplicative combination of the two blocks cannot be
   distinguished from this sample alone -- flagged as a method detail to confirm
   before final price computation (not needed for this snapshot).
4. **Weights.** Sample uses 差異百分率絕對值加總 (15.00% vs signed 合計 13.00%,
   confirming per-row signs), a qualitative 價格形成因素之相近程度 (普通), and
   比較標的權重 100% for its single comparable. Shulin has three comparables, so
   the weight rule for multiple comparables must come from the 作業手冊 -- left
   open; no equal weights assumed.
5. **Price-date adjustment.** Sample derives its 2% from a price-index ratio; the
   Shulin assignment GIVES the rates (5.96% / 4.09% / 5.49%) and our independent
   Decimal recomputation of the adjusted prices from the given normal prices
   matches all three to the rounded NT$ (see `price_date_adjustment_checks`).

## Structural differences (not errors -- case-specific)

1. **使用分區/建蔽率/容積率 placement.** The 金山 表5-2 keeps these rows in the
   regional sheet (grades filled, 0.00). The Shulin assignment's 表5-1 carries an
   explicit remark moving all three into 表4 individual factors. We follow the
   Shulin remark and exclude them from the regional computation (double-count
   guard), flagging the 容積率 method as an open item.
2. **Header wording of the criteria matrices.** The 金山 criteria example uses
   宗地(比準地) / 比凖地(比較標的) headers even for regional factors; the Shulin
   regional pages use 基準區段 / 目標區段. Numeric patterns are identical
   (row 優 = 0, +step, ..., +max), so the same reading applies.
3. **金山's 表5-2 rows** include commercial-only 工商活動 factors (百貨公司,
   金融機構, ...) that do not exist in the Shulin residential criteria; the
   Shulin sheet instead has 其他影響因素(8), pre-filled 無/0.00 by the assignment.

## Quarantine statement

No 金山 value (grade, percentage, price, date, or address) was copied into the
Shulin extraction or computation. The sample was used solely to confirm the
direction of matrix lookups, the sheet arithmetic pipeline, and rounding shape.
"""


def build_doc_md(computed: dict) -> str:
    pt = computed["regional_partial_totals"]["points"]
    return f"""# Shulin case data snapshot (case 1110901-99-XXX)

Generated by `scripts/prepare_shulin_snapshot.py` from the two official PDFs in
`artifacts/official-templates/` (native text, no OCR). Machine-readable outputs
live in `artifacts/shulin-case/` (gitignored). Valuation date 1110901
(2022-09-01). One case, four subjects: P001 比準地 (comparison base), P002/P003/
P004 comparables. Assignment page order is P002, P003, P004, P001 -- roles are
taken from printed 區段編號, never from page order.

## Classification of every data element

| Element | Where | Class | Notes |
|---|---|---|---|
| 案號 1110901-99-XXX, 年期/估價基準日 1110901 | 題目 p.1-6 | known_input | verified native text |
| 表3 attributes per subject (建築型態, 土地利用現況, 建築密度, 都市計畫, 使用分區, 建蔽率, 容積率, 禁止/限制建築, 主要道路名稱/寬度, 區段內道路平均寬度, 道路規劃闢建, 日照, 景觀, 傾斜度, 排水, 地勢, 建築基地改良勾選, 區段範圍) | 題目 p.1-4 | known_input | quotes + pages in `extraction.json` |
| 表3 facility rows (大型車站/站牌/交流道/學校/市場/公園/觀光遊憩/停車場地/服務性設施/變電所瓦斯槽/殯葬/廢棄物/環境污染) | 題目 p.1-4 | known_input (blank) | unmarked on every sheet; recorded as missing, NOT as 0/無 |
| 表4 addresses (樹德段1415 / 樹德段284 / 太平段367、917 / 文林段317) | 題目 p.6 | known_input | column mapping verified by x-coordinates |
| 表4 交易日期 (111-09-01 / 110-09-14 / 111-01-11 / 110-10-29) | 題目 p.6 | known_input | |
| 表4 土地正常單價 130,167 / 135,275 / 170,909 | 題目 p.6 | known_input | |
| 表4 調整百分率 5.96% / 4.09% / 5.49% | 題目 p.6 | known_input | price-date adjustment, given |
| 表4 調整至估價基準日單價 137,925 / 140,808 / 180,292 | 題目 p.6 | known_input + verified | independent Decimal recomputation matches all three (round half-up) |
| 表5-1 其他影響因素(8) row "－/無/0.00" | 題目 p.5 | known_input | only pre-filled 表5-1 row; 環境污染(7) row is blank |
| 表5-1 remark: 使用分區/建蔽率/容積率 併同表4 individual | 題目 p.5 | known_input | governs double-count guard |
| Criteria: 29 regional factors, bands + matrices (4-26..4-30) | 評價基準明細表 p.1-5 | known_input | orientation: rows=目標區段, cols=基準區段 (visually verified) |
| Criteria: 20 individual factors, bands + matrices (4-31..4-34) | 評價基準明細表 p.6-9 | known_input | rows=宗地(比準地), cols=比凖地(比較標的); 容積率 row has NO matrix (詳備註) |
| 表5-1 grades, 修正百分比, 小計, 總修正數, 區域因素調整百分率 | -- | to_recompute | 12 factors computed, 13 blocked by blank 表3 facility rows; partial point sums: P002 {pt['P002']}, P003 {pt['P003']}, P004 {pt['P004']} |
| 表4 rows 7-21, 6其他 差異率 | -- | to_recompute | blocked: parcel attributes absent for all subjects |
| 表4 rows 22-25 差異率 | -- | to_recompute | 建蔽率=0, 禁限建=0 computed; 使用分區 blocked by P001 zoning ambiguity; 容積率 blocked by manual method (P002) / 0 by identity (P003, P004) |
| 表4 合計, 絕對值加總, 相近程度, 試算價格, 權重, 比準地比較價格 | -- | to_recompute | downstream of the blocked inputs; multi-comparable weight rule to be taken from 作業手冊, never assumed equal |
| 金山 criteria example (評價基準明細表範例.pdf) | organizer example | reference_example | commercial case; structure only, values quarantined |
| 金山 pre-filled workbook (查估書表範本.pdf): 188,459 x 1.13 = 212,958 pipeline, 表5-2 layout, weights | organizer example | acceptance_answer | used only to validate method shape (see `artifacts/shulin-case/crosscheck.md`) |
| 土地徵收補償市價查估作業手冊.pdf | organizer manual | reference_example | criteria pages carry the manual's 4-2x printed numbering; consult targeted pages for weight rule when needed |

## Open items needing human confirmation

1. **P001 zoning ambiguity (捷運開發區 vs 第一種住宅區).** The 使用分區 field
   says 第一種住宅區; the 區段範圍 on the same sheet reads verbatim:
   「沿八德街以西、啟智街及未開闢計畫道路以南、啟智街187巷以東、啟智街187巷24弄
   以北之捷運開發區(變更前為第一種住宅區)」. Criteria grade 商業區、捷運用地(聯開)
   = 優 vs 住宅區 = 稍優, i.e. +3.75 points per comparable in 表4 row 22 if 優 is
   confirmed. Also P001's 土地利用現況 marks BOTH ●商業用 and ●住宅用. Settling
   evidence: 都市計畫變更書圖 effective 1110901 + organizer ruling on whether
   捷運開發區 maps to 捷運用地(聯開). NOT resolved in this snapshot.
2. **容積率 correction method (P002 200% vs P001 260%).** Individual row 24 has
   no matrix: 「容積率差異以土地開發分析法進行試算調整」 and 「本項需與區域因素
   容積率併同考量，調整不足者另於區域因素補充調整」, while the assignment moved
   容積率 out of 表5-1. No correction was derived from the 200/260 bands.
   Settling evidence: organizer-accepted 土地開發分析法 inputs or an instruction
   to apply a matrix.
3. **Blank 表3 facility rows (13 regional factors).** Cannot distinguish "does
   not exist" from "not surveyed"; recorded as missing. Settling evidence:
   completed 表3 or organizer confirmation on blank semantics.
4. **Parcel attributes absent (表4 rows 7-21, 6其他).** 宗地面積/寬度/深度/形狀/
   臨街情形/地勢/道路種類/面前道路寬度/5 proximity distances/嫌惡設施/停車方便性/
   無尾巷 are blank for all four subjects. Section 表3 data is not a substitute.
5. **Boundary-condition clauses.** The only conditional boundary text found in
   the assignment is the P001 區段範圍 clause quoted in item 1 (including
   未開闢計畫道路 as a boundary). No "8m MRT-boundary"-style numeric conditions
   were found in either PDF's text layer.
"""


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main() -> int:
    for p in (ASSIGNMENT_PDF, CRITERIA_PDF):
        if not p.exists():
            print(f"ERROR: missing source PDF: {p}", file=sys.stderr)
            return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(OUT_DIR, 0o700)

    assignment = Doc(ASSIGNMENT_PDF)
    criteria = Doc(CRITERIA_PDF)

    extraction = build_extraction(assignment)
    criteria_json = build_criteria(criteria)
    computed = build_computed()

    files_written = []

    def write(path: Path, payload) -> None:
        if isinstance(payload, str):
            path.write_text(payload, encoding="utf-8")
        else:
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        files_written.append(str(path.relative_to(ROOT)))

    write(OUT_DIR / "extraction.json", extraction)
    write(OUT_DIR / "criteria.json", criteria_json)
    write(OUT_DIR / "computed.json", computed)
    write(OUT_DIR / "crosscheck.md", CROSSCHECK_MD)
    DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    write(DOC_PATH, build_doc_md(computed))

    n_subjects = len(extraction["subjects"])
    n_factors = (len(criteria_json["regional_factors"])
                 + len(criteria_json["individual_factors"]))
    counts = computed["counts"]
    pt = computed["regional_partial_totals"]["points"]

    print("== Shulin snapshot ==")
    print("files written:")
    for f in files_written:
        print(f"  - {f}")
    print(f"subjects found: {n_subjects} (P001 比準地; P002/P003/P004 comparables)")
    print(f"factors parsed: {n_factors} "
          f"({len(criteria_json['regional_factors'])} regional + "
          f"{len(criteria_json['individual_factors'])} individual)")
    print(f"computed corrections: {counts['computed_corrections']} "
          f"(regional partial point sums: P002 {pt['P002']}, "
          f"P003 {pt['P003']}, P004 {pt['P004']})")
    print(f"given values independently verified: {counts['given_values_verified']} "
          f"(3 price-date adjustments all MATCH; 表5-1 其他影響因素 row 0.00 x3)")
    print(f"missing-input entries: {counts['missing_input_entries']} "
          f"({counts['missing_unique_factors']} unique factors x 3 comparables)")
    print(f"open human-confirmation items: {len(extraction['open_conditions'])}")
    print("3 most consequential gaps:")
    print("  1. Parcel-level 表4 inputs (rows 7-21 + 6其他) absent for ALL subjects --")
    print("     the entire individual-factor column is blocked except rows 22-25.")
    print("  2. P001 zoning ambiguity 捷運開發區 vs 第一種住宅區 (+3.75 pts/comparable")
    print("     swing on 表4 row 22) plus the P002 容積率 200% vs 260% manual method.")
    print("  3. Blank 表3 facility rows block 13 of 29 regional factors; regional")
    print("     totals are partial (P002 +23.5 / P003 +14.75 / P004 +14.75 points).")

    if VERIFY_FAILURES:
        print(f"\nQUOTE VERIFICATION FAILURES ({len(VERIFY_FAILURES)}):", file=sys.stderr)
        for v in VERIFY_FAILURES:
            print(f"  ! {v}", file=sys.stderr)
        return 2
    print("\nall quotes verified against native text (confidence 1.0)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
