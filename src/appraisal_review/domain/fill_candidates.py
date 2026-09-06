"""One immutable candidate, validated against every constraint before reuse."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from appraisal_review.domain.review_contracts import ArithmeticCheck, ObservedValue, ReviewSlot
from appraisal_review.domain.rule_engine import calculate


def percent(value: ObservedValue) -> Decimal:
    if value.state != "present" or value.unit not in {"percent_points", "ratio"}:
        raise ValueError("A present numeric value with an explicit percent unit is required")
    result = Decimal(str(value.value))
    if not result.is_finite():
        raise ValueError("Nonfinite observed value")
    return result * 100 if value.unit == "ratio" else result


@dataclass(frozen=True)
class ArithmeticComparison:
    check: ArithmeticCheck
    expected: Decimal

    def matches(self, actual: Decimal) -> bool:
        return abs(self.expected - actual) <= self.check.tolerance


@dataclass(frozen=True)
class ValidatedSlot:
    original: ObservedValue
    candidate: Decimal | str | None
    expected: Decimal | str | None
    status: str
    kind: str
    trace: str

    @property
    def trusted_number(self) -> Decimal | None:
        return (
            self.candidate
            if self.status == "verified" and isinstance(self.candidate, Decimal)
            else None
        )


def validate_slot(
    slot: ReviewSlot,
    original: ObservedValue,
    independent: str | None,
    constraints: list[ArithmeticComparison],
    *,
    ready: bool,
    authorized: bool,
) -> ValidatedSlot:
    """Observations are never edited. Different non-independent fills remain ambiguous."""
    grade = slot.value in {"target_grade", "comparable_grade"}
    expected: Decimal | str | None = (
        independent if grade else Decimal(independent) if independent is not None else None
    )
    unique = {c.expected for c in constraints}
    if expected is None and len(unique) == 1:
        expected = next(iter(unique))
    candidate: Decimal | str | None = None

    def result(status: str, kind: str, trace: str) -> ValidatedSlot:
        return ValidatedSlot(original, candidate, expected, status, kind, trace)

    if original.state == "blank":
        if original.raw_text.strip() or any(r.excerpt.strip() for r in original.evidence):
            return result(
                "needs_review", "blank_contradiction", "Claimed blank references nonblank text"
            )
        if not slot.derivable_blank:
            return result(
                "needs_review", "observed_unresolved", "Blank is not approved for derivation"
            )
        candidate = expected
    elif original.state == "present":
        try:
            candidate = (
                str(original.value) if grade and original.unit == "grade" else percent(original)
            )
        except (ValueError, InvalidOperation):
            return result(
                "needs_review", "observed_unresolved", "Observed value/unit is unresolved"
            )
    else:
        return result("needs_review", "observed_unresolved", f"Observed state: {original.state}")
    if not ready:
        return result(
            "needs_review", "arithmetic_dependency", "Not every derivation dependency is validated"
        )
    if not authorized:
        return result(
            "needs_review",
            "observed_unresolved",
            "Current source and exact-material authority required",
        )
    if candidate is None or (expected is None and not constraints):
        return result(
            "needs_review", "observed_missing", "No unique independently grounded fill candidate"
        )
    equal = all(isinstance(candidate, Decimal) and c.matches(candidate) for c in constraints)
    if independent is not None:
        if slot.factor_id is None and constraints and original.state == "present":
            equal = equal and all(
                isinstance(candidate, Decimal)
                and ArithmeticComparison(
                    c.check, calculate("equals", [Decimal(independent)], quantum=c.check.quantum)
                ).matches(candidate)
                for c in constraints
            )
        else:
            equal = equal and candidate == expected
    trace = (
        "One candidate checked against every constraint: "
        + ", ".join(c.check.id for c in constraints)
        if constraints
        else "Observed or proposed value checked against independent calculation"
    )
    return result(
        "verified" if equal else "failed",
        "derivable_blank" if original.state == "blank" else "observed_comparison",
        trace,
    )
