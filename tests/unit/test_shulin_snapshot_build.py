"""Focused checks on the Shulin snapshot bridge (skipped without local artifacts).

The artifacts under ``artifacts/shulin-case`` are gitignored case data, so this
module only runs where they exist. It asserts the honesty properties the
snapshot promises: validation, structural keys, points-not-fractions, no
sample-workbook (範本) answers, and the entries/gaps partition.
"""

from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
ARTIFACTS = REPO / "artifacts" / "shulin-case"
SCRIPT = REPO / "scripts" / "build_shulin_calculation_snapshot.py"

pytestmark = pytest.mark.skipif(
    not (ARTIFACTS / "extraction.json").exists()
    or not (ARTIFACTS / "computed.json").exists()
    or not (REPO / "configs" / "mappings" / "table3-v1.json").exists(),
    reason="Shulin case artifacts are local-only (gitignored)",
)

# Figures that exist ONLY in the organizer's pre-filled 金山 sample workbook
# (documented in artifacts/shulin-case/crosscheck.md as acceptance answers).
# By construction the snapshot never reads the 範本, so none may appear.
SAMPLE_ONLY_FIGURES = {Decimal("188459"), Decimal("212958"), Decimal("1.13")}


@pytest.fixture(scope="module")
def built():
    spec = importlib.util.spec_from_file_location("build_shulin_snapshot", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    builder = module.Builder()
    snapshot = builder.build("shulin-1110901-99-XXX", "rev-test", "0" * 64)
    return module, builder, snapshot


def test_snapshot_validates_and_partitions(built):
    module, builder, snapshot = built
    from appraisal_review.domain.calculation_snapshot import CalculationSnapshot

    # round-trips through JSON-mode validation
    CalculationSnapshot.model_validate(snapshot.model_dump(mode="json"))
    # every demanded mapping source is either provided or an explained gap
    provided = set(snapshot.entries) | set(snapshot.gaps)
    assert provided == builder.demand
    assert not (set(snapshot.entries) & set(snapshot.gaps))
    assert all(reason.strip() for reason in snapshot.gaps.values())


def test_structural_case_keys_present(built):
    _, _, snapshot = built
    for key, expected in {
        "table_4.case.case_number": "1110901-99-XXX",
        "table_5.case.case_number": "1110901-99-XXX",
        "table_4.case.valuation_date": "1110901",
        "table_3.case.zone_number": "P001-00",
        "table_4.P001.serial_number": "0003",
        "table_4.P002.zone_number": "P002-00",
    }.items():
        entry = snapshot.entries[key]
        assert entry.state == "present"
        assert entry.value == expected
        assert entry.origin == "given_input"


def test_percentages_are_points_not_fractions(built):
    _, _, snapshot = built
    # a given percentage: 5.96 points, never pre-divided to 0.0596
    given = snapshot.entries["table_4.P002.date_adjustment_pct"]
    assert given.value == Decimal("5.96")
    assert given.unit == "percent_points"
    # a computed correction with a non-zero value stays in points too
    computed = snapshot.entries["table_5.P003.avg_road_width_adjustment_pct"]
    assert computed.origin == "computed"
    assert computed.trace.strip()
    assert computed.unit == "percent_points"
    assert abs(Decimal(computed.value)) >= Decimal("1")  # 6 points, not 0.06


def test_no_sample_workbook_answers_and_no_unearned_origins(built):
    _, _, snapshot = built
    for key, entry in snapshot.entries.items():
        assert entry.origin != "human_confirmed", key  # nobody has confirmed anything yet
        if entry.origin == "computed":
            assert entry.trace.strip(), key
        if entry.state == "present" and isinstance(entry.value, Decimal):
            assert entry.value not in SAMPLE_ONLY_FIGURES, key


def test_blockers_stay_gaps(built):
    _, _, snapshot = built
    # P001 zoning ambiguity and the FAR method question await human confirmation
    assert "awaiting human confirmation" in snapshot.gaps["table_4.P001.zoning"]
    assert "awaiting human confirmation" in snapshot.gaps["table_4.P002.zoning_adjustment_pct"]
    assert "awaiting human confirmation" in snapshot.gaps["table_4.P002.floor_area_ratio_adjustment_pct"]
    # partial regional totals must not pose as the 區域因素調整百分率
    assert "PARTIAL" in snapshot.gaps["table_5.P002.total_adjustment_pct"]
    assert "table_4.P002.region_adjustment_pct" in snapshot.gaps
    # blank parcel attributes are gaps, never zeros
    assert "table_4.P002.area_adjustment_pct" in snapshot.gaps
    assert "table_4.P002.area" in snapshot.gaps
