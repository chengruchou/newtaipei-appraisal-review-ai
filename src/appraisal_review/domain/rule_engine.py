"""Deterministic validation over canonical appraisal fields."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml

from appraisal_review.domain.models import (
    CanonicalCase,
    CheckStatus,
    EvidenceRef,
    ExtractedField,
    Finding,
    ReviewResult,
    RuleDefinition,
    RuleSet,
)


class RuleEngine:
    """Evaluate versioned rules without calling external services."""

    def __init__(self, rule_set: RuleSet) -> None:
        self.rule_set = rule_set

    @classmethod
    def from_yaml(cls, path: str | Path) -> RuleEngine:
        with Path(path).open(encoding="utf-8") as stream:
            payload = yaml.safe_load(stream)
        return cls(RuleSet.model_validate(payload))

    def evaluate(self, case: CanonicalCase) -> ReviewResult:
        findings = [self._evaluate_rule(case, rule) for rule in self.rule_set.rules]
        return ReviewResult(
            case_id=case.case_id,
            schema_version=case.schema_version,
            rule_version=self.rule_set.version,
            findings=findings,
        )

    def _evaluate_rule(self, case: CanonicalCase, rule: RuleDefinition) -> Finding:
        required_names = [*rule.inputs, rule.target]
        missing = [name for name in required_names if not self._has_value(case.fields.get(name))]
        evidence = self._collect_evidence(case, required_names)
        if missing:
            return Finding(
                rule_id=rule.id,
                rule_version=self.rule_set.version,
                status=CheckStatus.NEEDS_REVIEW,
                severity=rule.severity,
                message=f"Missing required field(s): {', '.join(missing)}",
                evidence=evidence,
            )

        try:
            inputs = [self._to_decimal(case.fields[name]) for name in rule.inputs]
            actual = self._to_decimal(case.fields[rule.target])
        except (InvalidOperation, TypeError, ValueError) as error:
            return Finding(
                rule_id=rule.id,
                rule_version=self.rule_set.version,
                status=CheckStatus.NEEDS_REVIEW,
                severity=rule.severity,
                message=f"A required field is not unambiguously numeric: {error}",
                evidence=evidence,
            )

        expected = sum(inputs, start=Decimal("0")) if rule.kind == "sum" else inputs[0]
        tolerance = Decimal(str(rule.tolerance))
        status = CheckStatus.PASS if abs(expected - actual) <= tolerance else CheckStatus.FAIL
        message = (
            f"Rule {rule.id} passed within tolerance {tolerance}."
            if status is CheckStatus.PASS
            else f"Rule {rule.id} failed: expected {expected}, observed {actual}."
        )
        return Finding(
            rule_id=rule.id,
            rule_version=self.rule_set.version,
            status=status,
            severity=rule.severity,
            message=message,
            expected=str(expected),
            actual=str(actual),
            evidence=evidence,
        )

    @staticmethod
    def _has_value(field: ExtractedField | None) -> bool:
        return field is not None and field.value is not None and field.value != ""

    @staticmethod
    def _to_decimal(field: ExtractedField) -> Decimal:
        value = field.value
        if isinstance(value, bool) or value is None:
            raise TypeError(f"unsupported numeric value: {value!r}")
        if isinstance(value, str):
            normalized = value.strip().replace(",", "")
            if normalized.endswith("%"):
                normalized = normalized[:-1].strip()
            return Decimal(normalized)
        return Decimal(str(value))

    @staticmethod
    def _collect_evidence(case: CanonicalCase, field_names: list[str]) -> list[EvidenceRef]:
        return [
            evidence
            for name in field_names
            if (field := case.fields.get(name)) is not None
            for evidence in field.evidence
        ]
