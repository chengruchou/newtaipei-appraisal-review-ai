"""Readiness for a formal report: a reproducible policy check, never an empty-gaps test.

The evaluator answers one question: does the registered calculation snapshot carry the
complete pricing chain the formal tables must print? It reads a versioned policy - a
fixed list of required source keys plus which absence states count as justified - and
returns structured blockers a person can act on. Deleting a gap entry, clearing the
gaps dict or hiding a binding changes nothing here, because the policy demands the
values themselves.

A lawful blank never blocks by itself: a required key recorded as not_applicable or
confirmed_zero passes exactly when its justification trace is recorded, and an unknown
value can never be laundered into either state without one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from appraisal_review.domain.calculation_snapshot import CalculationSnapshot
from appraisal_review.domain.official_table_mapping import OfficialTable
from appraisal_review.domain.report_approval import (
    ReadinessBlocker,
    ReportReadiness,
)

_ZH = {
    "required_value_missing": "正式報表必要數值尚未提供",
    "unjustified_not_applicable": "標記為不適用但缺少可追溯依據",
    "unjustified_confirmed_zero": "標記為確認為零但缺少可追溯依據",
}
_NEEDED = {
    "required_value_missing": "supply the value through the existing correction flow",
    "unjustified_not_applicable": "record the basis for the not-applicable ruling",
    "unjustified_confirmed_zero": "record the basis for the confirmed-zero ruling",
}


@dataclass(frozen=True)
class ReadinessPolicy:
    policy_version: str
    required: tuple[str, ...]
    justified_absence_states: frozenset[str]

    @classmethod
    def load(cls, path: Path) -> ReadinessPolicy:
        data = json.loads(path.read_text())
        required = tuple(sorted(set(data["required"])))
        if not required:
            raise ValueError("A readiness policy with no required keys approves anything")
        return cls(
            policy_version=data["policy_version"],
            required=required,
            justified_absence_states=frozenset(data["justified_absence_states"]),
        )

    @classmethod
    def default(cls) -> ReadinessPolicy:
        return cls.load(Path("configs/readiness/formal-v1.json"))


def _split(key: str) -> tuple[OfficialTable | None, str | None]:
    parts = key.split(".")
    table = parts[0] if parts and parts[0] in {"table_3", "table_4", "table_5"} else None
    subject = parts[1] if len(parts) > 1 and parts[1].startswith("P") else None
    return table, subject  # type: ignore[return-value]


def evaluate_readiness(snapshot: CalculationSnapshot, policy: ReadinessPolicy) -> ReportReadiness:
    blockers: list[ReadinessBlocker] = []
    satisfied = 0
    for key in policy.required:
        table, subject = _split(key)
        entry = snapshot.entries.get(key)
        if entry is None or entry.state == "missing":
            reason = snapshot.gaps.get(key, "")
            blockers.append(
                ReadinessBlocker(
                    code="required_value_missing",
                    message=_ZH["required_value_missing"] + (f"（{reason}）" if reason else ""),  # noqa: RUF001
                    source_key=key,
                    table=table,
                    subject_id=subject,
                    current_state=None if entry is None else entry.state,
                    needed=_NEEDED["required_value_missing"],
                    action=f"provide {key} via case correction, then re-evaluate",
                )
            )
            continue
        if entry.state == "present":
            satisfied += 1
            continue
        if entry.state in policy.justified_absence_states:
            if entry.trace.strip():
                satisfied += 1
                continue
            code: Literal["unjustified_not_applicable", "unjustified_confirmed_zero"] = (
                "unjustified_not_applicable"
                if entry.state == "not_applicable"
                else "unjustified_confirmed_zero"
            )
            blockers.append(
                ReadinessBlocker(
                    code=code,
                    message=_ZH[code],
                    source_key=key,
                    table=table,
                    subject_id=subject,
                    current_state=entry.state,
                    needed=_NEEDED[code],
                    action=f"record the ruling basis for {key} through human confirmation",
                )
            )
            continue
        blockers.append(
            ReadinessBlocker(
                code="required_value_missing",
                message=_ZH["required_value_missing"],
                source_key=key,
                table=table,
                subject_id=subject,
                current_state=entry.state,
                needed=_NEEDED["required_value_missing"],
                action=f"provide {key} via case correction, then re-evaluate",
            )
        )
    return ReportReadiness(
        policy_version=policy.policy_version,
        state="ready_to_submit" if not blockers else "pending_data",
        blockers=tuple(blockers),
        required_total=len(policy.required),
        required_satisfied=satisfied,
    )
