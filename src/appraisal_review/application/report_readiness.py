"""Readiness for a formal report: a reproducible policy check, never an empty-gaps test.

The evaluator answers one question: does the registered calculation snapshot carry the
complete, internally consistent pricing chain the formal tables must print? It reads a
versioned policy - the required source keys, each key's mapping-declared unit and kind,
which keys may lawfully be absent, and the case-level arithmetic the tables assert - and
returns structured blockers a person can act on.

What v2 refuses to be fooled by (defect P1-A):

- Values, not keys. A required key present with a placeholder value is judged: numeric
  kinds must parse to a finite Decimal, units must equal the mapping binding's declared
  unit, adjustments stay within policy bounds, prices are positive.
- Arithmetic, not trust. The printed table-4 sums are recomputed from the snapshot's own
  component entries with Decimal arithmetic: the total against its individual-factor
  rows, the adjusted unit price against the pinned date-adjustment rounding convention,
  the weights against 100. A relation whose official convention the policy marks
  unconfirmed blocks by name (calculation_policy_unconfirmed) ALWAYS - a value matching
  some candidate formula, or lying inside the trial-price range, is a diagnostic and
  never confirmation. Only a confirmed convention's pinned formula can verify a figure.
- No blanket absences. not_applicable / confirmed_zero pass only where the policy allows
  them for that key AND origin is human_confirmed AND the trace is found in the
  caller-supplied receipt registry (confirmed_traces). No registry -> fail closed. This
  applies to required keys and to the factor component rows feeding a printed sum. The
  price chain allows no absence at all.
- Values, typed. A required text/option key needs a non-empty stripped value; a date key
  follows the mapping's existing 7-digit ROC-date convention (e.g. "1110901"). An empty
  string never satisfies anything.
- Open human tasks block. A case with unanswered confirmation tasks is not submittable,
  whatever the snapshot says.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Literal, cast

from appraisal_review.domain.calculation_snapshot import CalculationSnapshot, SnapshotEntry
from appraisal_review.domain.official_table_mapping import OfficialTable
from appraisal_review.domain.report_approval import (
    ReadinessBlocker,
    ReportReadiness,
)

_BlockerCode = Literal[
    "required_value_missing",
    "unjustified_not_applicable",
    "unjustified_confirmed_zero",
    "na_not_permitted",
    "invalid_value",
    "unit_mismatch",
    "weight_sum_invalid",
    "arithmetic_mismatch",
    "calculation_policy_unconfirmed",
    "human_task_open",
]

_ZH = {
    "required_value_missing": "正式報表必要數值尚未提供",
    "unjustified_not_applicable": "標記為不適用但缺少可追溯依據",
    "unjustified_confirmed_zero": "標記為確認為零但缺少可追溯依據",
    "na_not_permitted": "價格鏈必要數值不得以不適用或確認為零替代",
    "invalid_value": "數值無法解析或超出政策允許範圍",
    "unit_mismatch": "單位與對照表宣告的單位不一致",
    "weight_sum_invalid": "比較標的權重合計不等於100",
    "arithmetic_mismatch": "表列數值與快照自身元素重算結果不一致",
    "calculation_policy_unconfirmed": "官方計算慣例尚未確認，此關係無法核驗",  # noqa: RUF001
    "human_task_open": "仍有未結案的人工確認事項",
}
_NEEDED = {
    "required_value_missing": "supply the value through the existing correction flow",
    "unjustified_not_applicable": "record the basis for the not-applicable ruling",
    "unjustified_confirmed_zero": "record the basis for the confirmed-zero ruling",
    "na_not_permitted": "provide the actual value; this key admits no justified absence",
    "invalid_value": "correct the value so it parses and satisfies the policy bounds",
    "unit_mismatch": "restate the value in the unit the table mapping declares",
    "weight_sum_invalid": "make the comparable weights sum to 100 percent",
    "arithmetic_mismatch": "reconcile the printed figure with its component entries",
    "calculation_policy_unconfirmed": "confirm the official calculation convention",
    "human_task_open": "close every open human-confirmation task before submitting",
}

#: Mapping value kinds whose entries must parse as finite Decimals.
_NUMERIC_KINDS = frozenset({"decimal", "integer", "percentage_fraction", "percentage_points"})
#: Spec kinds with the same requirement (policy vocabulary).
_NUMERIC_SPEC_KINDS = frozenset({"price", "percent_points", "weight_percent", "decimal", "integer"})
#: Table-4 chain stages that are not individual-factor rows of the printed 合計.
_NON_COMPONENT_LEAVES = frozenset(
    {
        "total_adjustment_pct",
        "abs_adjustment_sum_pct",
        "date_adjustment_pct",
        "region_adjustment_pct",
    }
)
#: The mapping's existing date convention: 7-digit ROC date strings like "1110901".
_ROC_DATE = re.compile(r"\d{7}")


@dataclass(frozen=True)
class KeySpec:
    """Per-required-key policy: what the value is and which absences are lawful."""

    kind: str = "value"
    unit: str | None = None
    allow_not_applicable: bool = False
    na_requires: str = "human_confirmed"


@dataclass(frozen=True)
class ReadinessPolicy:
    policy_version: str
    required: tuple[str, ...]
    justified_absence_states: frozenset[str]
    specs: dict[str, KeySpec] = field(default_factory=dict)
    #: source key -> unit the mapping binding declares (None = no unit declared).
    declared_units: dict[str, str | None] = field(default_factory=dict)
    #: source key -> the mapping binding's value_kind.
    value_kinds: dict[str, str] = field(default_factory=dict)
    #: comparable subject -> its table-4 individual-factor adjustment keys.
    table4_components: dict[str, tuple[str, ...]] = field(default_factory=dict)
    comparison_base: str | None = None
    comparables: tuple[str, ...] = ()
    adjustment_abs_max_pct: Decimal = Decimal("100")
    weight_sum_expected: Decimal = Decimal("100")
    weight_sum_tolerance: Decimal = Decimal("0.01")
    #: convention name -> confirmed flag, straight from the policy's conventions block.
    conventions_confirmed: dict[str, bool] = field(default_factory=dict)
    #: pinned parameter of the abs-sum convention; meaningful only once confirmed.
    abs_sum_includes_region: bool = False

    def convention_confirmed(self, name: str) -> bool:
        """False for anything the policy does not explicitly mark confirmed."""
        return self.conventions_confirmed.get(name, False)

    @classmethod
    def load(cls, path: Path) -> ReadinessPolicy:
        data = json.loads(path.read_text())
        raw_entries = data.get("required_entries")
        specs: dict[str, KeySpec] = {}
        if raw_entries is not None:
            for item in raw_entries:
                specs[item["key"]] = KeySpec(
                    kind=item.get("kind", "value"),
                    unit=item.get("unit"),
                    allow_not_applicable=bool(item.get("allow_not_applicable", False)),
                    na_requires=item.get("na_requires", "human_confirmed"),
                )
            required = tuple(sorted(specs))
        else:
            required = tuple(sorted(set(data["required"])))
        if not required:
            raise ValueError("A readiness policy with no required keys approves anything")

        declared_units: dict[str, str | None] = {}
        value_kinds: dict[str, str] = {}
        for relative in data.get("mapping_files", ()):
            mapping = json.loads((path.parent / relative).resolve().read_text())
            for binding in mapping["bindings"]:
                declared_units[binding["source"]] = binding.get("unit")
                value_kinds[binding["source"]] = binding.get("value_kind") or "text"

        sections = data.get("case_sections", {})
        comparables = tuple(sections.get("comparables", ()))
        components: dict[str, tuple[str, ...]] = {}
        for subject in comparables:
            prefix = f"table_4.{subject}."
            components[subject] = tuple(
                sorted(
                    key
                    for key in declared_units
                    if key.startswith(prefix)
                    and key.endswith("_adjustment_pct")
                    and key.rsplit(".", 1)[1] not in _NON_COMPONENT_LEAVES
                )
            )

        conventions = data.get("conventions", {})
        bounds = data.get("bounds", {})
        weight_sum = data.get("weight_sum", {})
        return cls(
            policy_version=data["policy_version"],
            required=required,
            justified_absence_states=frozenset(data["justified_absence_states"]),
            specs=specs,
            declared_units=declared_units,
            value_kinds=value_kinds,
            table4_components=components,
            comparison_base=sections.get("comparison_base"),
            comparables=comparables,
            adjustment_abs_max_pct=Decimal(bounds.get("adjustment_abs_max_pct", "100")),
            weight_sum_expected=Decimal(weight_sum.get("expected", "100")),
            weight_sum_tolerance=Decimal(weight_sum.get("tolerance", "0.01")),
            conventions_confirmed={
                name: bool(spec.get("confirmed", False)) for name, spec in conventions.items()
            },
            abs_sum_includes_region=bool(
                conventions.get("abs_adjustment_sum", {}).get("include_region", False)
            ),
        )

    @classmethod
    def default(cls) -> ReadinessPolicy:
        return cls.load(Path("configs/readiness/formal-v2.json"))


def _split(key: str) -> tuple[OfficialTable | None, str | None]:
    parts = key.split(".")
    table = parts[0] if parts and parts[0] in {"table_3", "table_4", "table_5"} else None
    subject = parts[1] if len(parts) > 1 and parts[1].startswith("P") else None
    return table, subject  # type: ignore[return-value]


def _parse_decimal(entry: SnapshotEntry) -> Decimal | None:
    if entry.value is None:
        return None
    try:
        value = Decimal(str(entry.value))
    except (InvalidOperation, ValueError):
        return None
    return value if value.is_finite() else None


class _Collector:
    """Accumulates deduplicated blockers and tracks which keys they touch."""

    def __init__(self) -> None:
        self.blockers: list[ReadinessBlocker] = []
        self._seen: set[tuple[str, str | None]] = set()
        self.blocked_keys: set[str] = set()

    def add(
        self,
        code: _BlockerCode,
        source_key: str | None,
        current_state: str | None,
        action: str,
        detail: str = "",
    ) -> None:
        if (code, source_key) in self._seen:
            return
        self._seen.add((code, source_key))
        if source_key is not None:
            self.blocked_keys.add(source_key)
        table, subject = _split(source_key) if source_key else (None, None)
        message = _ZH[code] + (f"（{detail}）" if detail else "")  # noqa: RUF001
        self.blockers.append(
            ReadinessBlocker(
                code=code,
                message=message[:512],
                source_key=source_key,
                table=table,
                subject_id=subject,
                current_state=current_state,
                needed=_NEEDED[code],
                action=action[:512],
            )
        )


def _absence_confirmed(
    entry: SnapshotEntry, policy: ReadinessPolicy, confirmed_traces: frozenset[str] | None
) -> bool:
    """A trustworthy absence: human origin plus, under a v2 policy, a registered receipt.

    confirmed_traces is the caller-supplied registry of valid human-task receipt
    references. None means no registry is available, and under a spec-carrying (v2)
    policy the absence then CANNOT be considered confirmed - fail closed. An arbitrary
    trace string, or a model-set origin, never suffices. A v1-shape policy (no specs)
    predates the receipt contract and keeps the origin-plus-trace rule.
    """
    if entry.origin != "human_confirmed":
        return False
    trace = entry.trace.strip()
    if not trace:
        return False
    if not policy.specs:
        return True
    return confirmed_traces is not None and trace in confirmed_traces


def _check_absence(
    out: _Collector,
    key: str,
    entry: SnapshotEntry,
    policy: ReadinessPolicy,
    confirmed_traces: frozenset[str] | None,
) -> None:
    """Rule: a lawful blank needs policy permission, human confirmation and a receipt."""
    spec = policy.specs.get(key)
    if entry.state not in policy.justified_absence_states:
        out.add(
            "required_value_missing",
            key,
            entry.state,
            f"provide {key} via case correction, then re-evaluate",
        )
        return
    allowed = spec.allow_not_applicable if spec is not None else True
    if not allowed:
        out.add(
            "na_not_permitted",
            key,
            entry.state,
            f"provide the actual value for {key}; the policy admits no absence here",
        )
        return
    if _absence_confirmed(entry, policy, confirmed_traces):
        return
    code: _BlockerCode = (
        "unjustified_not_applicable"
        if entry.state == "not_applicable"
        else "unjustified_confirmed_zero"
    )
    out.add(
        code,
        key,
        entry.state,
        f"record the ruling basis for {key} through human confirmation with a registered receipt",
    )


def _check_value(out: _Collector, key: str, entry: SnapshotEntry, policy: ReadinessPolicy) -> None:
    """Unit equality against the mapping, parseability and policy bounds."""
    spec = policy.specs.get(key)
    if key in policy.declared_units:
        expected_unit = policy.declared_units[key]
        if expected_unit is not None and entry.unit != expected_unit:
            out.add(
                "unit_mismatch",
                key,
                entry.state,
                f"restate {key} in {expected_unit}",
                detail=f"mapping declares {expected_unit}, snapshot has {entry.unit}",
            )
    kind = policy.value_kinds.get(key, "")
    spec_kind = spec.kind if spec is not None else ""
    numeric = kind in _NUMERIC_KINDS or spec_kind in _NUMERIC_SPEC_KINDS
    if not numeric:
        if spec is None:
            return  # typed checks apply to the policy's required keys
        stripped = ("" if entry.value is None else str(entry.value)).strip()
        if spec_kind == "date" or kind == "date":
            if _ROC_DATE.fullmatch(stripped) is None:
                out.add(
                    "invalid_value",
                    key,
                    entry.state,
                    f"supply {key} as a 7-digit ROC date string such as 1110901",
                    detail=f"got {entry.value!r}; the mapping's date convention is 7 ROC digits",
                )
        elif not stripped:
            out.add(
                "invalid_value",
                key,
                entry.state,
                f"supply a non-empty {spec_kind or 'text'} value for {key}",
                detail="an empty string satisfies nothing",
            )
        return
    value = _parse_decimal(entry)
    if value is None:
        out.add(
            "invalid_value",
            key,
            entry.state,
            f"correct {key} to a finite numeric value",
            detail=f"cannot parse {entry.value!r} as a finite Decimal",
        )
        return
    leaf = key.rsplit(".", 1)[1]
    if spec_kind == "price" and value <= 0:
        out.add(
            "invalid_value",
            key,
            entry.state,
            f"correct {key}: a unit price must be greater than zero",
            detail=f"got {value}",
        )
    elif leaf == "weight_pct" and not (Decimal("0") <= value <= Decimal("100")):
        out.add(
            "invalid_value",
            key,
            entry.state,
            f"correct {key}: a weight lies between 0 and 100 percent",
            detail=f"got {value}",
        )
    elif leaf.endswith("_adjustment_pct") and abs(value) > policy.adjustment_abs_max_pct:
        out.add(
            "invalid_value",
            key,
            entry.state,
            f"correct {key}: adjustments stay within "
            f"±{policy.adjustment_abs_max_pct} percent points",
            detail=f"got {value}",
        )


def _usable(snapshot: CalculationSnapshot, key: str) -> Decimal | None:
    entry = snapshot.entries.get(key)
    if entry is None or entry.state != "present":
        return None
    return _parse_decimal(entry)


def _check_component_absences(
    out: _Collector,
    snapshot: CalculationSnapshot,
    policy: ReadinessPolicy,
    components: tuple[str, ...],
    confirmed_traces: frozenset[str] | None,
) -> None:
    """Factor rows feeding a printed sum are dependencies, not decoration.

    A not_applicable or confirmed_zero row passes only where the policy leaves the
    state lawful for that factor AND a human confirmed it with a registered receipt
    (origin human_confirmed, trace found in confirmed_traces). No registry -> block.
    """
    for key in components:
        entry = snapshot.entries.get(key)
        if entry is None or entry.state not in {"not_applicable", "confirmed_zero"}:
            continue
        spec = policy.specs.get(key)
        if spec is not None and not spec.allow_not_applicable:
            out.add(
                "na_not_permitted",
                key,
                entry.state,
                f"provide the actual value for {key}; the policy admits no absence here",
            )
            continue
        if _absence_confirmed(entry, policy, confirmed_traces):
            continue
        code: _BlockerCode = (
            "unjustified_not_applicable"
            if entry.state == "not_applicable"
            else "unjustified_confirmed_zero"
        )
        out.add(
            code,
            key,
            entry.state,
            f"record the ruling basis for {key} through human confirmation "
            "with a registered receipt; the printed sums depend on this row",
        )


def _component_values(
    out: _Collector, snapshot: CalculationSnapshot, keys: tuple[str, ...], target: str
) -> list[Decimal] | None:
    """Resolve the component rows a printed sum claims to summarize.

    present -> its value; confirmed (receipted) zero -> 0; confirmed (receipted)
    not_applicable -> excluded; an unconfirmed absence, a missing or an unparseable
    row -> the sum is unverifiable (never a silent pass), with each missing row
    named through the value-missing path.
    """
    values: list[Decimal] = []
    verifiable = True
    for key in keys:
        entry = snapshot.entries.get(key)
        if entry is None or entry.state == "missing":
            out.add(
                "required_value_missing",
                key,
                None if entry is None else entry.state,
                f"provide {key}; it is needed to verify the printed {target}",
                detail=f"required to recompute {target}",
            )
            verifiable = False
            continue
        if entry.state in {"confirmed_zero", "not_applicable"}:
            if key in out.blocked_keys:
                verifiable = False  # the absence itself is unjustified; no arithmetic on it
            elif entry.state == "confirmed_zero":
                values.append(Decimal("0"))
            continue
        value = _parse_decimal(entry)
        if value is None:
            verifiable = False  # invalid_value already reported by _check_value
            continue
        values.append(value)
    return values if verifiable else None


def _check_comparable_arithmetic(
    out: _Collector,
    snapshot: CalculationSnapshot,
    policy: ReadinessPolicy,
    subject: str,
    confirmed_traces: frozenset[str] | None,
) -> None:
    prefix = f"table_4.{subject}."
    components = policy.table4_components.get(subject, ())
    _check_component_absences(out, snapshot, policy, components, confirmed_traces)

    total = _usable(snapshot, prefix + "total_adjustment_pct")
    if total is not None:
        if not policy.convention_confirmed("table_4_total_adjustment"):
            out.add(
                "calculation_policy_unconfirmed",
                prefix + "total_adjustment_pct",
                "present",
                "confirm the individual-factor total convention; until then this "
                "printed figure cannot be verified",
            )
        elif components:
            values = _component_values(out, snapshot, components, prefix + "total_adjustment_pct")
            if values is not None and sum(values, Decimal("0")) != total:
                out.add(
                    "arithmetic_mismatch",
                    prefix + "total_adjustment_pct",
                    "present",
                    "reconcile the total with the individual-factor adjustment rows",
                    detail=f"components sum to {sum(values, Decimal('0'))}, table prints {total}",
                )

    abs_sum = _usable(snapshot, prefix + "abs_adjustment_sum_pct")
    date_pct = _usable(snapshot, prefix + "date_adjustment_pct")
    if abs_sum is not None:
        if not policy.convention_confirmed("abs_adjustment_sum"):
            # ALWAYS while unconfirmed: matching either candidate formula (with or
            # without the region factor) is a diagnostic, never confirmation.
            out.add(
                "calculation_policy_unconfirmed",
                prefix + "abs_adjustment_sum_pct",
                "present",
                "confirm the absolute-sum convention (region-factor inclusion); "
                "no candidate-formula match substitutes for confirmation",
            )
        elif components and date_pct is not None:
            values = _component_values(out, snapshot, components, prefix + "abs_adjustment_sum_pct")
            region = _usable(snapshot, prefix + "region_adjustment_pct")
            if values is not None and (region is not None or not policy.abs_sum_includes_region):
                expected = abs(date_pct) + sum((abs(v) for v in values), Decimal("0"))
                if policy.abs_sum_includes_region and region is not None:
                    expected += abs(region)
                if abs_sum != expected:
                    out.add(
                        "arithmetic_mismatch",
                        prefix + "abs_adjustment_sum_pct",
                        "present",
                        "reconcile the absolute-sum with the confirmed convention",
                        detail=f"pinned formula yields {expected}, table prints {abs_sum}",
                    )

    normal = _usable(snapshot, prefix + "normal_unit_price")
    adjusted = _usable(snapshot, prefix + "adjusted_unit_price")
    if normal is not None and adjusted is not None and date_pct is not None:
        if not policy.convention_confirmed("adjusted_unit_price"):
            out.add(
                "calculation_policy_unconfirmed",
                prefix + "adjusted_unit_price",
                "present",
                "confirm the date-adjustment rounding convention for the adjusted unit price",
            )
            return
        exact = normal * (Decimal("1") + date_pct / Decimal("100"))
        expected = exact.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        if adjusted == expected:
            pass
        elif adjusted == exact:
            out.add(
                "calculation_policy_unconfirmed",
                prefix + "adjusted_unit_price",
                "present",
                "confirm the price rounding convention; the pinned convention "
                "rounds half-up to 1 TWD",
                detail=f"matches the exact product {exact} but not the rounded {expected}",
            )
        else:
            out.add(
                "arithmetic_mismatch",
                prefix + "adjusted_unit_price",
                "present",
                "reconcile the adjusted unit price with "
                "normal_unit_price x (1 + date_adjustment_pct/100), half-up to 1 TWD",
                detail=f"expected {expected}, table prints {adjusted}",
            )


def _check_case_arithmetic(
    out: _Collector,
    snapshot: CalculationSnapshot,
    policy: ReadinessPolicy,
    confirmed_traces: frozenset[str] | None,
) -> None:
    for subject in policy.comparables:
        _check_comparable_arithmetic(out, snapshot, policy, subject, confirmed_traces)

    weights = [_usable(snapshot, f"table_4.{subject}.weight_pct") for subject in policy.comparables]
    if policy.comparables and all(w is not None for w in weights):
        total = sum(cast("list[Decimal]", weights), Decimal("0"))
        if abs(total - policy.weight_sum_expected) > policy.weight_sum_tolerance:
            out.add(
                "weight_sum_invalid",
                None,
                "present",
                f"adjust the comparable weights so they sum to "
                f"{policy.weight_sum_expected} percent",
                detail=f"weights sum to {total}",
            )

    if policy.comparison_base is None or not policy.comparables:
        return
    compared_key = f"table_4.{policy.comparison_base}.compared_price"
    compared = _usable(snapshot, compared_key)
    if compared is None:
        return
    trials = [_usable(snapshot, f"table_4.{subject}.trial_price") for subject in policy.comparables]
    if not policy.convention_confirmed("compared_price_combination"):
        # ALWAYS while unconfirmed: a value inside the trial range proves nothing
        # about the combination rule; the range is at most a diagnostic detail.
        detail = ""
        if all(t is not None for t in trials):
            trial_values = cast("list[Decimal]", trials)
            low, high = min(trial_values), max(trial_values)
            inside = low <= compared <= high
            detail = (
                f"compared {compared} {'inside' if inside else 'outside'} trial "
                f"range [{low}, {high}]; the range check is diagnostic only"
            )
        out.add(
            "calculation_policy_unconfirmed",
            compared_key,
            "present",
            "confirm the official trial-price combination rule; a compared price "
            "inside the trial range is not confirmation",
            detail=detail,
        )
        return
    weights = [_usable(snapshot, f"table_4.{subject}.weight_pct") for subject in policy.comparables]
    if all(t is not None for t in trials) and all(w is not None for w in weights):
        trial_values = cast("list[Decimal]", trials)
        weight_values = cast("list[Decimal]", weights)
        weight_total = sum(weight_values, Decimal("0"))
        if weight_total > 0:
            mean = (
                sum((w * t for w, t in zip(weight_values, trial_values, strict=True)), Decimal("0"))
                / weight_total
            )
            expected = mean.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            if compared != expected:
                out.add(
                    "arithmetic_mismatch",
                    compared_key,
                    "present",
                    "reconcile the compared price with the confirmed combination "
                    "rule: weight-averaged trial prices, half-up to 1 TWD",
                    detail=f"pinned formula yields {expected}, table prints {compared}",
                )


def evaluate_readiness(
    snapshot: CalculationSnapshot,
    policy: ReadinessPolicy,
    open_task_count: int = 0,
    open_task_ids: tuple[str, ...] = (),
    confirmed_traces: frozenset[str] | None = None,
) -> ReportReadiness:
    """Judge one snapshot against one policy version.

    confirmed_traces is the registry of valid human-confirmation receipt references
    the caller (the approval service) resolved from the human-task store. A justified
    absence is accepted only when its trace is found here; None means no registry is
    available and every claimed absence fails closed under a v2-shape policy.
    """
    out = _Collector()
    present: set[str] = set()

    for key in policy.required:
        entry = snapshot.entries.get(key)
        if entry is None or entry.state == "missing":
            reason = snapshot.gaps.get(key, "")
            out.add(
                "required_value_missing",
                key,
                None if entry is None else entry.state,
                f"provide {key} via case correction, then re-evaluate",
                detail=reason,
            )
            continue
        if entry.state == "present":
            present.add(key)
            continue
        _check_absence(out, key, entry, policy, confirmed_traces)
        if key not in out.blocked_keys:
            present.add(key)  # a lawfully justified absence satisfies the key

    for key, entry in snapshot.entries.items():
        if entry.state == "present" and (key in policy.declared_units or key in policy.specs):
            _check_value(out, key, entry, policy)

    _check_case_arithmetic(out, snapshot, policy, confirmed_traces)

    open_tasks = max(open_task_count, len(open_task_ids))
    if open_tasks > 0:
        named = ", ".join(open_task_ids[:8])
        out.add(
            "human_task_open",
            None,
            None,
            "close every open human-confirmation task, then re-evaluate",
            detail=f"{open_tasks} open task(s)" + (f": {named}" if named else ""),
        )

    satisfied = sum(1 for key in policy.required if key in present - out.blocked_keys)
    return ReportReadiness(
        policy_version=policy.policy_version,
        state="ready_to_submit" if not out.blockers else "pending_data",
        blockers=tuple(out.blockers),
        required_total=len(policy.required),
        required_satisfied=satisfied,
    )
