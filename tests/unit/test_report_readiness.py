"""Regressions for defect P1-A: readiness must judge values, not key presence.

Four reproductions drove this file, each a way the old evaluator said
ready_to_submit for a snapshot no reviewer would sign:

1. every required key present with the placeholder Decimal("1") (weights summing
   to 3 percent points, prices of 1 TWD) passed 28/28;
2. every required key marked not_applicable with the trace "x" passed, including
   the comparison-base compared price - the one value the report exists to print;
3. a snapshot with no table-3 basis at all passed, because v1 required nothing
   from table 3;
4. open human-confirmation tasks were invisible to the evaluator even though the
   blocker schema always carried a human_task_open code.

The fix is general: policy v2 binds each required key to its mapping-declared
unit and kind, forbids the not-applicable escape on the price chain, recomputes
the printed totals from the snapshot's own component entries (Decimal only), and
names any relation whose official convention is still unconfirmed instead of
silently passing it.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from appraisal_review.application.report_readiness import ReadinessPolicy, evaluate_readiness
from appraisal_review.domain.calculation_snapshot import (
    CalculationSnapshot,
    SnapshotEntry,
    SnapshotSubject,
)
from appraisal_review.domain.report_approval import ReportReadiness
from appraisal_review.domain.service_contracts import RevisionReference

WORKTREE = Path(__file__).resolve().parents[2]
V1_POLICY = WORKTREE / "configs" / "readiness" / "formal-v1.json"
V2_POLICY = WORKTREE / "configs" / "readiness" / "formal-v2.json"
SHULIN_SNAPSHOT = WORKTREE.parents[1] / "shulin-case" / "snapshot.json"

PCT = "percent_points"
TWD = "TWD_per_m2"
COMPARABLES = ("P002", "P003", "P004")

#: The table-4 individual-factor rows (mapping sources ending _adjustment_pct,
#: minus the date/region chain stages and the two printed sums). The sample form
#: pins 合計 = the sum of exactly these rows (1+2+5+3+2 = 13).
COMPONENT_STEMS = (
    "area",
    "width",
    "depth",
    "shape",
    "frontage",
    "terrain",
    "road_type",
    "frontage_road",
    "school",
    "market",
    "park",
    "station",
    "business_district",
    "nuisance_facility",
    "parking",
    "zoning",
    "building_coverage",
    "floor_area_ratio",
    "building_restriction",
    "other",
)

#: Price chain verified against artifacts/shulin-case/computed.json
#: price_date_adjustment_checks: adjusted = normal x (1 + date%/100), half-up to 1.
CHAIN = {
    "P002": ("130167", "5.96", "137925", "13", "18.96", "155855", "30"),
    "P003": ("135275", "4.09", "140808", "0", "4.09", "140808", "40"),
    "P004": ("170909", "5.49", "180292", "0", "5.49", "180292", "30"),
}
P002_NONZERO = {
    "depth": "1",
    "road_type": "2",
    "frontage_road": "5",
    "nuisance_facility": "3",
    "parking": "2",
}

TABLE_3_BASIS = {
    "table_3.case.year_term": "1110901",
    "table_3.case.zone_number": "P001-00",
    "table_3.case.zone_extent": "沿八德街以西之捷運開發區",
    "table_3.case.urban_plan": "都市計畫內",
    "table_3.case.zoning": "第一種住宅區",
    "table_3.case.survey_date": "2022-08-15",
}


def v1_required() -> list[str]:
    return list(json.loads(V1_POLICY.read_text())["required"])


def num(value: str, unit: str | None) -> SnapshotEntry:
    return SnapshotEntry(state="present", value=Decimal(value), unit=unit, origin="given_input")


def text(value: str) -> SnapshotEntry:
    return SnapshotEntry(state="present", value=value, unit=None, origin="given_input")


def build_snapshot(
    entries: dict[str, SnapshotEntry], gaps: dict[str, str] | None = None
) -> CalculationSnapshot:
    return CalculationSnapshot(
        revision=RevisionReference(case_id="case-1", revision_id="rev-1", material_digest="a" * 64),
        district="Shulin",
        valuation_date=date(2022, 9, 1),
        rule_bundle_id="shulin-2022",
        rule_bundle_version="v1",
        subjects=(
            SnapshotSubject(subject_id="P001", role="comparison_base", label="base"),
            SnapshotSubject(subject_id="P002", role="comparable", label="c1"),
            SnapshotSubject(subject_id="P003", role="comparable", label="c2"),
            SnapshotSubject(subject_id="P004", role="comparable", label="c3"),
        ),
        entries=entries,
        gaps=gaps or {},
    )


def complete_entries() -> dict[str, SnapshotEntry]:
    """An arithmetically coherent case: chain, components, weights, table-3 basis."""
    entries = {key: text(value) for key, value in TABLE_3_BASIS.items()}
    for sid in COMPARABLES:
        normal, date_pct, adjusted, total, abs_sum, trial, weight = CHAIN[sid]
        entries[f"table_4.{sid}.normal_unit_price"] = num(normal, TWD)
        entries[f"table_4.{sid}.date_adjustment_pct"] = num(date_pct, PCT)
        entries[f"table_4.{sid}.adjusted_unit_price"] = num(adjusted, TWD)
        entries[f"table_4.{sid}.region_adjustment_pct"] = num("0", PCT)
        entries[f"table_4.{sid}.total_adjustment_pct"] = num(total, PCT)
        entries[f"table_4.{sid}.abs_adjustment_sum_pct"] = num(abs_sum, PCT)
        entries[f"table_4.{sid}.trial_price"] = num(trial, TWD)
        entries[f"table_4.{sid}.weight_pct"] = num(weight, PCT)
        entries[f"table_5.{sid}.total_adjustment_pct"] = num("2.5", PCT)
        for stem in COMPONENT_STEMS:
            value = P002_NONZERO.get(stem, "0") if sid == "P002" else "0"
            entries[f"table_4.{sid}.{stem}_adjustment_pct"] = num(value, PCT)
    entries["table_4.P001.compared_price"] = num("150000", TWD)
    return entries


def evaluate(entries: dict[str, SnapshotEntry], **kwargs: object) -> ReportReadiness:
    return evaluate_readiness(
        build_snapshot(entries),
        ReadinessPolicy.default(),
        **kwargs,  # type: ignore[arg-type]
    )


def codes(result: ReportReadiness) -> set[str]:
    return {blocker.code for blocker in result.blockers}


def keys_with(result: ReportReadiness, code: str) -> set[str | None]:
    return {b.source_key for b in result.blockers if b.code == code}


class TestDefectReproductions:
    """The four P1-A reproductions. Each said ready_to_submit before the fix."""

    def test_uniform_placeholder_values_do_not_pass(self) -> None:
        # Every v1-required key present with Decimal("1"): weights sum to 3
        # percent points, every price is 1 TWD. The old evaluator: 28/28 ready.
        entries = {key: num("1", TWD if key.endswith("price") else PCT) for key in v1_required()}
        result = evaluate(entries)
        assert result.state == "pending_data"
        assert "weight_sum_invalid" in codes(result)
        missing = keys_with(result, "required_value_missing")
        # The printed total cannot be verified without its component rows.
        assert "table_4.P002.area_adjustment_pct" in missing
        # And the table-3 basis is required now.
        assert "table_3.case.zone_number" in missing

    def test_blanket_not_applicable_does_not_pass(self) -> None:
        # Every required key not_applicable with trace "x": the old evaluator
        # accepted any non-blank trace, even on the compared price itself.
        entries = {key: SnapshotEntry(state="not_applicable", trace="x") for key in v1_required()}
        result = evaluate(entries)
        assert result.state == "pending_data"
        na_blocked = keys_with(result, "na_not_permitted")
        assert "table_4.P001.compared_price" in na_blocked
        assert {f"table_4.{sid}.trial_price" for sid in COMPARABLES} <= na_blocked
        assert {f"table_4.{sid}.weight_pct" for sid in COMPARABLES} <= na_blocked

    def test_missing_table_3_basis_blocks(self) -> None:
        # A coherent price chain with no table-3 basis at all was "ready" in v1.
        entries = {
            key: entry
            for key, entry in complete_entries().items()
            if not key.startswith("table_3.")
        }
        result = evaluate(entries)
        assert result.state == "pending_data"
        missing = keys_with(result, "required_value_missing")
        assert "table_3.case.zone_number" in missing
        assert "table_3.case.survey_date" in missing
        assert all(
            blocker.table == "table_3"
            for blocker in result.blockers
            if blocker.code == "required_value_missing"
        )

    def test_open_human_tasks_block(self) -> None:
        # The schema always had human_task_open; the evaluator never emitted it.
        result = evaluate(complete_entries(), open_task_count=2)
        assert result.state == "pending_data"
        assert codes(result) == {"human_task_open"}


class TestLegitimateCases:
    def test_consistent_complete_case_is_ready(self) -> None:
        result = evaluate(complete_entries())
        assert result.blockers == ()
        assert result.state == "ready_to_submit"
        assert result.required_satisfied == result.required_total
        assert result.policy_version == "formal-readiness-v2"

    def test_justified_human_confirmed_na_passes_where_allowed(self) -> None:
        entries = complete_entries()
        entries["table_3.case.urban_plan"] = SnapshotEntry(
            state="not_applicable",
            origin="human_confirmed",
            trace="reviewer ruled the zone outside any urban plan; criteria p.7",
        )
        result = evaluate(entries)
        assert result.state == "ready_to_submit"

    def test_na_without_human_confirmation_blocks_even_where_allowed(self) -> None:
        entries = complete_entries()
        entries["table_3.case.urban_plan"] = SnapshotEntry(
            state="not_applicable", trace="looks inapplicable"
        )
        result = evaluate(entries)
        assert "unjustified_not_applicable" in codes(result)
        assert result.state == "pending_data"

    def test_weight_sum_within_tolerance_passes(self) -> None:
        entries = complete_entries()
        entries["table_4.P002.weight_pct"] = num("33.33", PCT)
        entries["table_4.P003.weight_pct"] = num("33.33", PCT)
        entries["table_4.P004.weight_pct"] = num("33.34", PCT)
        assert evaluate(entries).state == "ready_to_submit"


class TestValueValidation:
    def test_unparseable_value_blocks(self) -> None:
        entries = complete_entries()
        entries["table_4.P002.normal_unit_price"] = SnapshotEntry(
            state="present", value="not-a-number", unit=TWD, origin="given_input"
        )
        result = evaluate(entries)
        assert "table_4.P002.normal_unit_price" in keys_with(result, "invalid_value")

    def test_non_positive_price_blocks(self) -> None:
        entries = complete_entries()
        entries["table_4.P001.compared_price"] = num("-150000", TWD)
        result = evaluate(entries)
        assert "table_4.P001.compared_price" in keys_with(result, "invalid_value")

    def test_adjustment_beyond_100_points_blocks(self) -> None:
        entries = complete_entries()
        entries["table_4.P003.date_adjustment_pct"] = num("250", PCT)
        result = evaluate(entries)
        assert "table_4.P003.date_adjustment_pct" in keys_with(result, "invalid_value")

    def test_unit_mismatch_blocks(self) -> None:
        entries = complete_entries()
        entries["table_4.P002.weight_pct"] = num("30", "percent")
        result = evaluate(entries)
        assert "table_4.P002.weight_pct" in keys_with(result, "unit_mismatch")


class TestArithmeticConsistency:
    def test_wrong_adjusted_unit_price_blocks(self) -> None:
        entries = complete_entries()
        entries["table_4.P002.adjusted_unit_price"] = num("137930", TWD)
        result = evaluate(entries)
        assert "table_4.P002.adjusted_unit_price" in keys_with(result, "arithmetic_mismatch")

    def test_unrounded_adjusted_price_names_the_convention(self) -> None:
        # Matches the exact product but not the pinned half-up rounding: the
        # evaluator names the convention question instead of pass/fail silence.
        entries = complete_entries()
        entries["table_4.P002.adjusted_unit_price"] = num("137924.9532", TWD)
        result = evaluate(entries)
        assert "table_4.P002.adjusted_unit_price" in keys_with(
            result, "calculation_policy_unconfirmed"
        )

    def test_total_adjustment_must_match_component_sum(self) -> None:
        entries = complete_entries()
        entries["table_4.P002.total_adjustment_pct"] = num("14", PCT)
        result = evaluate(entries)
        assert "table_4.P002.total_adjustment_pct" in keys_with(result, "arithmetic_mismatch")

    def test_abs_sum_mismatch_names_the_convention(self) -> None:
        entries = complete_entries()
        entries["table_4.P002.abs_adjustment_sum_pct"] = num("99", PCT)
        result = evaluate(entries)
        assert "table_4.P002.abs_adjustment_sum_pct" in keys_with(
            result, "calculation_policy_unconfirmed"
        )

    def test_weight_sum_off_by_one_percent_blocks(self) -> None:
        entries = complete_entries()
        entries["table_4.P002.weight_pct"] = num("33", PCT)
        entries["table_4.P003.weight_pct"] = num("33", PCT)
        entries["table_4.P004.weight_pct"] = num("33", PCT)
        result = evaluate(entries)
        assert "weight_sum_invalid" in codes(result)

    def test_compared_price_outside_trial_range_blocks(self) -> None:
        entries = complete_entries()
        entries["table_4.P001.compared_price"] = num("999999", TWD)
        result = evaluate(entries)
        assert "table_4.P001.compared_price" in keys_with(result, "calculation_policy_unconfirmed")


class TestPolicyFiles:
    def test_default_policy_is_v2(self) -> None:
        assert ReadinessPolicy.default().policy_version == "formal-readiness-v2"

    def test_v1_policy_file_still_loads_for_history(self) -> None:
        assert ReadinessPolicy.load(V1_POLICY).policy_version == "formal-readiness-v1"

    def test_v2_keeps_every_v1_required_key(self) -> None:
        assert set(v1_required()) <= set(ReadinessPolicy.default().required)


@pytest.mark.skipif(not SHULIN_SNAPSHOT.exists(), reason="shulin artifacts not present")
class TestShulinSnapshot:
    def test_real_snapshot_stays_pending_with_named_reasons(self) -> None:
        snapshot = CalculationSnapshot.model_validate(json.loads(SHULIN_SNAPSHOT.read_text()))
        result = evaluate_readiness(snapshot, ReadinessPolicy.default())
        assert result.state == "pending_data"
        # The withheld price pipeline is named, with the recorded gap reason.
        missing = keys_with(result, "required_value_missing")
        assert "table_4.P001.compared_price" in missing
        # The zoning question awaiting human confirmation surfaces by name.
        zoning = [b for b in result.blockers if b.source_key == "table_3.case.zoning"]
        assert zoning and "human confirmation" in zoning[0].message
        # The aligned snapshot is honest data: nothing in it is malformed.
        assert "invalid_value" not in codes(result)
        assert "unit_mismatch" not in codes(result)
        assert all(blocker.needed and blocker.action for blocker in result.blockers)
