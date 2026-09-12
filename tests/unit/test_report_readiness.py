"""Regressions for defects P1-A, R1 and R2: readiness judges values, not key presence.

P1-A (v1 -> v2): placeholder values, blanket not-applicable, a missing table-3 basis
and open human tasks all said ready_to_submit; v2 binds keys to mapping units/kinds,
recomputes printed sums and names open tasks.

R1 (this round): the policy's conventions block carries per-convention confirmation
status, but the evaluator never loaded it. Three reproductions, each wrongly green:

1. the two unconfirmed conventions (abs-sum region inclusion; compared-price
   combination) let a coherent case pass 34/34 with zero blockers;
2. compared_price 140808, 150000 and 180292 all passed - only a range check existed;
3. with region_adjustment_pct=10 both abs-sum candidates (18.96 and 28.96) passed,
   because "either candidate formula matches" was treated as confirmation.

Fixed: a key whose value depends on an unconfirmed convention always gets a
calculation_policy_unconfirmed blocker and never counts toward required_satisfied.
Range checks and candidate matches are diagnostics, never confirmation.

R2 (this round): absences and non-numeric values were taken on faith.

4. all 20 of a comparable's factor component rows not_applicable with trace "x" and
   no origin still verified 34/34 (same with confirmed_zero);
5. required table-3 text fields with value "" and state="present" counted satisfied.

Fixed: an absent factor row needs origin=human_confirmed AND a trace found in a
caller-supplied receipt registry (confirmed_traces; None fails closed under a
v2-shape policy). Text/option kinds need a non-empty stripped value; date kinds need
the mapping's 7-digit ROC-date digit shape (e.g. "1110901").
"""

from __future__ import annotations

import json
from dataclasses import replace
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
ABS_KEYS = frozenset(f"table_4.{sid}.abs_adjustment_sum_pct" for sid in COMPARABLES)
COMPARED_KEY = "table_4.P001.compared_price"

#: A receipt registry as the integrator would supply it from the human-task store.
RECEIPTS = frozenset(
    {
        "receipt:urban-plan-ruling-0007",
        "receipt:park-factor-ruling-0011",
    }
)

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

#: survey_date follows the mapping's existing date convention: 7 ROC digits.
TABLE_3_BASIS = {
    "table_3.case.year_term": "1110901",
    "table_3.case.zone_number": "P001-00",
    "table_3.case.zone_extent": "沿八德街以西之捷運開發區",
    "table_3.case.urban_plan": "都市計畫內",
    "table_3.case.zoning": "第一種住宅區",
    "table_3.case.survey_date": "1110815",
}

#: The pinned weighted-mean compared price for CHAIN under the confirmed variant:
#: (155855*30 + 140808*40 + 180292*30) / 100 = 157167.3 -> 157167 half-up.
WEIGHTED_COMPARED = "157167"


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
    entries[COMPARED_KEY] = num("150000", TWD)
    return entries


def ready_entries() -> dict[str, SnapshotEntry]:
    """complete_entries with the compared price matching the pinned weighted mean."""
    entries = complete_entries()
    entries[COMPARED_KEY] = num(WEIGHTED_COMPARED, TWD)
    return entries


def confirmed_policy() -> ReadinessPolicy:
    """TEST-ONLY variant: both open conventions marked confirmed with pinned formulas.

    Built in memory from the real v2 policy; never written to configs/. The real
    policy keeps abs_adjustment_sum and compared_price_combination unconfirmed until
    the official convention is established.
    """
    base = ReadinessPolicy.default()
    return replace(
        base,
        conventions_confirmed={
            **base.conventions_confirmed,
            "abs_adjustment_sum": True,
            "compared_price_combination": True,
        },
    )


def evaluate(entries: dict[str, SnapshotEntry], **kwargs: object) -> ReportReadiness:
    return evaluate_readiness(
        build_snapshot(entries),
        ReadinessPolicy.default(),
        **kwargs,  # type: ignore[arg-type]
    )


def evaluate_with(
    policy: ReadinessPolicy, entries: dict[str, SnapshotEntry], **kwargs: object
) -> ReportReadiness:
    return evaluate_readiness(build_snapshot(entries), policy, **kwargs)  # type: ignore[arg-type]


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
        assert COMPARED_KEY in na_blocked
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
        # Evaluated under the confirmed variant so the open task is the ONLY reason.
        result = evaluate_with(confirmed_policy(), ready_entries(), open_task_count=2)
        assert result.state == "pending_data"
        assert codes(result) == {"human_task_open"}


class TestR1UnconfirmedConventions:
    """R1 reproductions: unconfirmed calculation conventions must actually block."""

    def test_unconfirmed_conventions_always_block(self) -> None:
        # Repro (a): both conventions are confirmed:false in formal-v2.json, yet
        # the old evaluator returned ready_to_submit, 34/34, zero blockers.
        result = evaluate(complete_entries())
        assert result.state == "pending_data"
        unconfirmed = keys_with(result, "calculation_policy_unconfirmed")
        assert unconfirmed >= ABS_KEYS
        assert COMPARED_KEY in unconfirmed
        # The affected keys never count toward required_satisfied.
        assert result.required_satisfied == result.required_total - 4

    @pytest.mark.parametrize("compared", ["140808", "150000", "180292"])
    def test_in_range_compared_price_is_not_confirmation(self, compared: str) -> None:
        # Repro (b): with the same weights/trial prices, all three passed before -
        # only a range check existed, and a range check is not a combination rule.
        entries = complete_entries()
        entries[COMPARED_KEY] = num(compared, TWD)
        result = evaluate(entries)
        assert COMPARED_KEY in keys_with(result, "calculation_policy_unconfirmed")

    @pytest.mark.parametrize("abs_sum", ["18.96", "28.96"])
    def test_two_candidate_abs_sum_match_is_not_confirmation(self, abs_sum: str) -> None:
        # Repro (c): P002 region 10 -> both candidate formulas (with and without
        # the region factor) passed. Matching either candidate is a diagnostic,
        # never confirmation.
        entries = complete_entries()
        entries["table_4.P002.region_adjustment_pct"] = num("10", PCT)
        entries["table_4.P002.abs_adjustment_sum_pct"] = num(abs_sum, PCT)
        result = evaluate(entries)
        assert "table_4.P002.abs_adjustment_sum_pct" in keys_with(
            result, "calculation_policy_unconfirmed"
        )

    def test_confirmed_variant_with_exact_values_is_ready(self) -> None:
        # Positive regression: convention confirmed + single pinned formula + exact
        # values -> ready. (abs sum: |date| + sum|components|, region excluded;
        # compared: weighted mean of trial prices, half-up to 1 TWD.)
        result = evaluate_with(confirmed_policy(), ready_entries())
        assert result.blockers == ()
        assert result.state == "ready_to_submit"
        assert result.required_satisfied == result.required_total

    def test_confirmed_variant_abs_sum_mismatch_blocks_arithmetic(self) -> None:
        entries = ready_entries()
        entries["table_4.P002.abs_adjustment_sum_pct"] = num("19.96", PCT)
        result = evaluate_with(confirmed_policy(), entries)
        assert "table_4.P002.abs_adjustment_sum_pct" in keys_with(result, "arithmetic_mismatch")

    def test_confirmed_variant_compared_mismatch_blocks_arithmetic(self) -> None:
        entries = ready_entries()
        entries[COMPARED_KEY] = num("150000", TWD)
        result = evaluate_with(confirmed_policy(), entries)
        assert COMPARED_KEY in keys_with(result, "arithmetic_mismatch")


class TestR2FactorAbsences:
    """R2 reproductions: factor-row absences need a lawful state AND a receipt."""

    P003_COMPONENTS = frozenset(f"table_4.P003.{stem}_adjustment_pct" for stem in COMPONENT_STEMS)

    def _blanket(self, state: str, **entry_kwargs: object) -> dict[str, SnapshotEntry]:
        entries = complete_entries()
        for key in self.P003_COMPONENTS:
            entries[key] = SnapshotEntry(state=state, **entry_kwargs)  # type: ignore[arg-type]
        return entries

    def test_blanket_component_na_without_receipt_blocks(self) -> None:
        # Repro (d): all 20 P003 component rows not_applicable, trace "x", no
        # origin -> the old evaluator still verified totals and said 34/34 ready.
        result = evaluate(self._blanket("not_applicable", trace="x"))
        assert result.state == "pending_data"
        assert keys_with(result, "unjustified_not_applicable") >= self.P003_COMPONENTS

    def test_blanket_component_zero_without_receipt_blocks(self) -> None:
        # Repro (d), confirmed_zero flavor.
        result = evaluate(self._blanket("confirmed_zero", trace="x"))
        assert result.state == "pending_data"
        assert keys_with(result, "unjustified_confirmed_zero") >= self.P003_COMPONENTS

    def test_component_absence_fails_closed_without_registry(self) -> None:
        # origin=human_confirmed and a plausible trace, but no receipt registry
        # supplied (confirmed_traces=None): the absence cannot be verified -> block.
        entries = ready_entries()
        entries["table_4.P003.park_adjustment_pct"] = SnapshotEntry(
            state="not_applicable",
            origin="human_confirmed",
            trace="receipt:park-factor-ruling-0011",
        )
        result = evaluate_with(confirmed_policy(), entries)
        assert "table_4.P003.park_adjustment_pct" in keys_with(result, "unjustified_not_applicable")

    def test_component_absence_with_unregistered_trace_blocks(self) -> None:
        # A registry is supplied and the trace is not in it: an arbitrary trace
        # string never suffices.
        entries = ready_entries()
        entries["table_4.P003.park_adjustment_pct"] = SnapshotEntry(
            state="not_applicable", origin="human_confirmed", trace="made-up-receipt"
        )
        result = evaluate_with(confirmed_policy(), entries, confirmed_traces=RECEIPTS)
        assert "table_4.P003.park_adjustment_pct" in keys_with(result, "unjustified_not_applicable")

    def test_component_absence_with_registered_receipt_passes(self) -> None:
        # Lawful + registered: origin human_confirmed, trace present in the
        # caller-supplied registry, policy does not forbid absence on this row.
        entries = ready_entries()
        entries["table_4.P003.park_adjustment_pct"] = SnapshotEntry(
            state="not_applicable",
            origin="human_confirmed",
            trace="receipt:park-factor-ruling-0011",
        )
        result = evaluate_with(confirmed_policy(), entries, confirmed_traces=RECEIPTS)
        assert result.state == "ready_to_submit"

    def test_model_set_origin_never_suffices(self) -> None:
        # computed/given_input origins are not human confirmation, registry or not.
        entries = ready_entries()
        entries["table_4.P003.park_adjustment_pct"] = SnapshotEntry(
            state="not_applicable", origin="computed", trace="receipt:park-factor-ruling-0011"
        )
        result = evaluate_with(confirmed_policy(), entries, confirmed_traces=RECEIPTS)
        assert "table_4.P003.park_adjustment_pct" in keys_with(result, "unjustified_not_applicable")

    def test_required_key_absence_uses_the_same_registry_rule(self) -> None:
        # _check_absence path: urban_plan may lawfully be absent, but only with a
        # registered receipt. No registry -> fail closed; registered -> ready.
        entries = ready_entries()
        entries["table_3.case.urban_plan"] = SnapshotEntry(
            state="not_applicable",
            origin="human_confirmed",
            trace="receipt:urban-plan-ruling-0007",
        )
        closed = evaluate_with(confirmed_policy(), entries)
        assert "table_3.case.urban_plan" in keys_with(closed, "unjustified_not_applicable")
        opened = evaluate_with(confirmed_policy(), entries, confirmed_traces=RECEIPTS)
        assert opened.state == "ready_to_submit"


class TestR2TypedValues:
    """R2 reproductions: required non-numeric keys are judged by value, not state."""

    @pytest.mark.parametrize(
        "key", ["table_3.case.zoning", "table_3.case.urban_plan", "table_3.case.zone_number"]
    )
    def test_empty_text_required_field_blocks(self, key: str) -> None:
        # Repro (e): value "" with state="present" counted as satisfied before.
        entries = complete_entries()
        entries[key] = text("")
        result = evaluate(entries)
        assert key in keys_with(result, "invalid_value")

    def test_whitespace_text_blocks(self) -> None:
        entries = complete_entries()
        entries["table_3.case.zoning"] = text("   ")
        result = evaluate(entries)
        assert "table_3.case.zoning" in keys_with(result, "invalid_value")

    @pytest.mark.parametrize("value", ["", "2022-08-15", "August 15", "111081"])
    def test_survey_date_must_match_roc_digit_shape(self, value: str) -> None:
        # The mapping's existing date convention is 7 ROC digits ("1110901").
        entries = complete_entries()
        entries["table_3.case.survey_date"] = text(value)
        result = evaluate(entries)
        assert "table_3.case.survey_date" in keys_with(result, "invalid_value")

    def test_roc_shaped_survey_date_satisfies(self) -> None:
        result = evaluate_with(confirmed_policy(), ready_entries())
        assert "table_3.case.survey_date" not in {b.source_key for b in result.blockers}
        assert result.state == "ready_to_submit"

    def test_blocked_text_key_does_not_count_satisfied(self) -> None:
        entries = ready_entries()
        entries["table_3.case.zoning"] = text("")
        result = evaluate_with(confirmed_policy(), entries)
        assert result.required_satisfied == result.required_total - 1


class TestLegitimateCases:
    def test_complete_case_blocks_only_on_unconfirmed_conventions(self) -> None:
        # Under the real v2 policy, an arithmetically coherent case is honest about
        # the two conventions nobody has confirmed - and nothing else.
        result = evaluate(complete_entries())
        assert codes(result) == {"calculation_policy_unconfirmed"}
        assert keys_with(result, "calculation_policy_unconfirmed") == ABS_KEYS | {COMPARED_KEY}
        assert result.policy_version == "formal-readiness-v2"

    def test_confirmed_variant_complete_case_is_ready(self) -> None:
        result = evaluate_with(confirmed_policy(), ready_entries())
        assert result.blockers == ()
        assert result.state == "ready_to_submit"
        assert result.required_satisfied == result.required_total

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
        assert "weight_sum_invalid" not in codes(evaluate(entries))


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
        entries[COMPARED_KEY] = num("-150000", TWD)
        result = evaluate(entries)
        assert COMPARED_KEY in keys_with(result, "invalid_value")

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
        entries[COMPARED_KEY] = num("999999", TWD)
        result = evaluate(entries)
        assert COMPARED_KEY in keys_with(result, "calculation_policy_unconfirmed")


class TestPolicyFiles:
    def test_default_policy_is_v2(self) -> None:
        assert ReadinessPolicy.default().policy_version == "formal-readiness-v2"

    def test_v1_policy_file_still_loads_for_history(self) -> None:
        policy = ReadinessPolicy.load(V1_POLICY)
        assert policy.policy_version == "formal-readiness-v1"
        assert policy.conventions_confirmed == {}

    def test_v2_keeps_every_v1_required_key(self) -> None:
        assert set(v1_required()) <= set(ReadinessPolicy.default().required)

    def test_v2_convention_confirmations_load_as_written(self) -> None:
        # Read, never flipped: the two price-chain relations are confirmed, the
        # abs-sum region question and the combination rule are not.
        assert ReadinessPolicy.default().conventions_confirmed == {
            "adjusted_unit_price": True,
            "table_4_total_adjustment": True,
            "abs_adjustment_sum": False,
            "compared_price_combination": False,
        }


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
