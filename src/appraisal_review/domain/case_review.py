"""Deterministic whole-case review and independent inventory completion gate."""

from contextlib import suppress
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from graphlib import CycleError, TopologicalSorter

from pydantic import ValidationError

from appraisal_review.domain.confidence import confirmation_digest
from appraisal_review.domain.document_models import SourceCitation, SourceRegistry
from appraisal_review.domain.factor_engine import FactorRuleEngine, validate_minimum_confidence
from appraisal_review.domain.factor_models import (
    CaseFacts,
    CaseReviewResult,
    EvaluationStatus,
    FactorEvaluationRequest,
    FactorReviewResult,
    ReviewMaterial,
    ReviewPolicy,
)
from appraisal_review.domain.review_contracts import (
    ArithmeticCheck,
    Coverage,
    ObservedValue,
    ReviewFinding,
    ReviewSlot,
)
from appraisal_review.domain.rule_engine import calculate
from appraisal_review.domain.verification import ReviewVerifier
from appraisal_review.ports.approval import ReviewAuthorization


def source_cell(ref: SourceCitation) -> tuple[object, ...]:
    """All current citation entries are value anchors; excerpt is not cell identity."""
    return (ref.document_id, ref.content_hash, ref.version, ref.page, ref.region_id, ref.bbox)


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


class CaseReviewer:
    def __init__(
        self, authorization: ReviewAuthorization | None, *, minimum_confidence: float = 0.85
    ) -> None:
        self.authorization = authorization
        self.minimum_confidence = validate_minimum_confidence(minimum_confidence)

    def review(
        self,
        policy: ReviewPolicy,
        facts: CaseFacts,
        registry: SourceRegistry,
        *,
        claimed: list[FactorReviewResult] | None = None,
    ) -> CaseReviewResult:
        findings: list[ReviewFinding] = []
        comparisons: list[FactorReviewResult] = []
        required: list[str] = []

        def add(id: str, kind: str, status: str, trace: str, **details: object) -> None:
            findings.append(
                ReviewFinding.model_validate(
                    dict(id=id, kind=kind, status=status, trace=trace, **details)
                )
            )

        def citations_valid(refs: list[SourceCitation]) -> bool:
            return bool(refs) and all(registry.resolves(ref) for ref in refs)

        def finish() -> CaseReviewResult:
            verified = {f.id for f in findings if f.status == "verified"}
            blocked = {f.id for f in findings if f.status != "verified"}
            status = (
                EvaluationStatus.FAILED
                if any(f.status == "failed" for f in findings)
                else EvaluationStatus.NEEDS_REVIEW
                if blocked or set(required) - verified
                else EvaluationStatus.VERIFIED
            )
            return CaseReviewResult(
                identity=policy.identity,
                comparisons=comparisons,
                findings=findings,
                coverage=Coverage(
                    required=required,
                    verified=sorted(verified - blocked),
                    missing=sorted(set(required) - (verified - blocked)),
                    unsupported=policy.inventory.unsupported,
                ),
                status=status,
            )

        required.extend(["trust", "sources", "inventory"])
        try:
            material = ReviewMaterial.model_validate({"policy": policy, "facts": facts})
            registry = SourceRegistry.model_validate(registry.model_dump())
        except ValidationError:
            add("trust", "invalid_input", "failed", "Invalid or nonfinite typed material")
            return finish()
        if facts.identity != policy.identity:
            add("trust", "case_identity", "failed", "Case identity/version does not match policy")
            return finish()
        authorized = self.authorization is not None and self.authorization.permits(material)
        if not authorized:
            add("trust", "approval", "needs_review", "Exact material requires trusted approval")
        else:
            add("trust", "approval", "verified", "Exact case, policy and facts authorized")
        if len(registry.documents) != len(policy.registry.documents) or any(
            not any(current.same_identity_and_content(reviewed) for current in registry.documents)
            for reviewed in policy.registry.documents
        ):
            add(
                "sources",
                "source_identity",
                "failed",
                "Current parser registry differs from policy",
            )
        else:
            add(
                "sources", "source_identity", "verified", "Current source versions and hashes match"
            )

        inventory = policy.inventory
        contexts = [c.context.key() for c in inventory.contexts]
        pair_keys = [(p.context.key(), p.pair.factor_id) for p in facts.pairs]
        slots = [s.id for s in inventory.slots]
        check_ids = [c.id for c in inventory.checks]
        observed_ids = [v.slot_id for v in facts.observed]
        duplicate = any(
            len(values) != len(set(values))
            for values in (
                contexts,
                pair_keys,
                slots,
                check_ids,
                observed_ids,
                [c.id for c in inventory.empty_columns],
            )
        )
        if duplicate:
            add("inventory", "duplicate_identity", "failed", "Duplicate composite identities")
            return finish()
        if not set(observed_ids) <= set(slots):
            add("inventory", "unknown_observed", "failed", "Observation is outside inventory")
        if any(key not in contexts for key, _ in pair_keys):
            add("inventory", "unknown_context", "failed", "Facts include an uninventoried context")
        contexts_by_key = {entry.context.key(): entry for entry in inventory.contexts}
        valid_slots: dict[str, ReviewSlot] = {}
        for slot in inventory.slots:
            id = f"observed/{slot.id}"
            required.append(id)
            entry = contexts_by_key.get(slot.context.key())
            factor_bound = slot.value in {"target_grade", "comparable_grade", "adjustment_percent"}
            if entry is None or (
                slot.factor_id not in entry.factor_ids
                if factor_bound
                else slot.factor_id is not None
            ):
                add(
                    id,
                    "slot_binding",
                    "failed",
                    "Slot requires a registered context and a value-compatible factor binding",
                    context=slot.context,
                    factor_id=slot.factor_id,
                    evidence=slot.evidence,
                )
            else:
                valid_slots[slot.id] = slot
        for source in registry.documents:
            if source.role in {"forms", "criteria"} and inventory.inspected_pages.get(
                source.document_id
            ) != [p.number for p in source.pages]:
                add(
                    "inventory",
                    "page_coverage",
                    "needs_review",
                    "Entire source must be inventoried",
                )
        for source in registry.documents:
            if source.role in {"criteria", "forms"}:
                actual_tables = {r.table_id for p in source.pages for r in p.regions if r.table_id}
                declared_tables = inventory.inspected_tables.get(source.document_id, [])
                if set(declared_tables) != actual_tables or len(declared_tables) != len(
                    set(declared_tables)
                ):
                    add(
                        "inventory",
                        "table_coverage",
                        "needs_review",
                        "Every parser-discovered table needs explicit inventory accounting",
                    )
        for empty in inventory.empty_columns:
            if not citations_valid(empty.evidence):
                add(
                    "inventory",
                    "empty_column_evidence",
                    "needs_review",
                    "Blank column needs evidence",
                )
        for unresolved in [*inventory.unresolved, *facts.unresolved, *inventory.unsupported]:
            add("inventory", "unresolved", "needs_review", unresolved)
        add(
            "inventory",
            "inventory",
            "verified",
            "Explicit inventory examined independently of results",
        )
        observed = {v.slot_id: v for v in facts.observed}
        bound_slots: dict[str, ReviewSlot] = {}
        for slot in valid_slots.values():
            observation = observed.get(slot.id)
            if (
                observation is None
                or not citations_valid(slot.evidence)
                or not citations_valid(observation.evidence)
                or {source_cell(ref) for ref in slot.evidence}
                != {source_cell(ref) for ref in observation.evidence}
            ):
                add(
                    f"observed/{slot.id}",
                    "observed_source_binding",
                    "needs_review",
                    "Every value-source cell must match the slot's complete anchor set",
                    context=slot.context,
                    factor_id=slot.factor_id,
                    evidence=slot.evidence,
                )
            else:
                bound_slots[slot.id] = slot
        expected_slots: dict[str, str] = {}
        arithmetic_targets: dict[str, list[ArithmeticComparison]] = {}

        for entry in inventory.contexts:
            context = entry.context
            key = context.key()
            required.append(key)
            required.extend(f"{key}/factor/{factor}" for factor in entry.factor_ids)
            matches = [
                s
                for s in policy.rule_sets
                if s.context == context
                and s.zone == policy.identity.zone
                and s.rules.applicability.jurisdiction == policy.identity.district
                and s.rules.applicability.land_use_category == policy.identity.land_use_category
                and (
                    s.rules.applicability.effective_from is None
                    or s.rules.applicability.effective_from <= policy.identity.effective_date
                )
                and (
                    s.rules.applicability.effective_to is None
                    or policy.identity.effective_date <= s.rules.applicability.effective_to
                )
            ]
            if len(matches) != 1:
                add(
                    key,
                    "not_applicable" if not matches else "selection_conflict",
                    "needs_review",
                    "Exactly one applicable rule set required",
                    context=context,
                )
                continue
            scoped = matches[0]
            rule_set = scoped.rules
            rule_source = rule_set.source_document
            source_matches = [
                d
                for d in registry.documents
                if d.document_id == rule_source.document_id
                and d.content_hash == rule_source.content_hash
                and d.version == scoped.source_version
            ]
            if (
                not source_matches
                or not rule_source.pages
                or not citations_valid(scoped.evidence)
                or not citations_valid(entry.evidence)
                or rule_set.status == "rejected"
                or any(
                    e.document_id != rule_source.document_id or e.page not in rule_source.pages
                    for e in scoped.evidence
                )
            ):
                add(
                    key,
                    "rule_rejected" if rule_set.status == "rejected" else "rule_source",
                    "needs_review",
                    "Rule source/version/evidence is not valid",
                    context=context,
                )
                continue
            if len(entry.factor_ids) != len(set(entry.factor_ids)):
                add(
                    key,
                    "duplicate_factor",
                    "failed",
                    "Duplicate inventory factors",
                    context=context,
                )
                continue
            rules_by_factor = {r.factor_id: r for r in rule_set.rules}
            if not {r.factor_id for r in rule_set.rules if r.critical} <= set(entry.factor_ids):
                add(
                    key,
                    "rule_coverage",
                    "needs_review",
                    "Inventory omitted required rules",
                    context=context,
                )
            pairs = [p for p in facts.pairs if p.context == context]
            missing = set(entry.factor_ids) - {p.pair.factor_id for p in pairs}
            unknown = {p.pair.factor_id for p in pairs} - set(entry.factor_ids)
            if unknown:
                add(
                    key,
                    "unknown_factor",
                    "failed",
                    "Factors outside approved inventory",
                    context=context,
                )
            valid_pairs = []
            reliable_factors: set[str] = set()
            confirmed_sides: set[tuple[str, str]] = set()
            for factor in entry.factor_ids:
                factor_key = f"{key}/factor/{factor}"
                if factor in missing or factor not in rules_by_factor:
                    add(
                        factor_key,
                        "missing_factor",
                        "needs_review",
                        "Required factor unavailable",
                        context=context,
                        factor_id=factor,
                    )
                    continue
                pair = next(p for p in pairs if p.pair.factor_id == factor)
                reliable = all(
                    citations_valid(refs)
                    and reliability.method != "model_proposed"
                    and not reliability.unresolved
                    and reliability.selection != "ambiguous"
                    and observation.raw_text is not None
                    and bool(observation.evidence)
                    and all(
                        any(
                            evidence.document_id == ref.document_id
                            and evidence.page == ref.page
                            and evidence.bounding_box == ref.bbox
                            and evidence.coordinate_system == "pdf_bottom_left"
                            and evidence.source_file
                            == next(
                                d.uri
                                for d in registry.documents
                                if d.document_id == ref.document_id
                            )
                            for ref in refs
                        )
                        for evidence in observation.evidence
                    )
                    and all(
                        ref.excerpt in observation.raw_text or observation.raw_text in ref.excerpt
                        for ref in refs
                        if ref.excerpt
                    )
                    for refs, reliability, observation in (
                        (pair.target_sources, pair.target_reliability, pair.pair.target),
                        (
                            pair.comparable_sources,
                            pair.comparable_reliability,
                            pair.pair.comparable,
                        ),
                    )
                )
                effective_pair = pair.pair.model_copy(deep=True)
                for side in ("target", "comparable"):
                    reliability = getattr(pair, f"{side}_reliability")
                    observation = getattr(pair.pair, side)
                    refs = getattr(pair, f"{side}_sources")
                    # Every used citation needs an evidence entry, not just the reverse mapping.
                    reliable = reliable and all(
                        any(
                            e.document_id == ref.document_id
                            and e.page == ref.page
                            and e.bounding_box == ref.bbox
                            for e in observation.evidence
                        )
                        for ref in refs
                    )
                    if reliability.method == "reviewer_confirmed":
                        confirmation = reliability.confirmation
                        confirmed = (
                            authorized
                            and confirmation is not None
                            and confirmation.input_digest == confirmation_digest(pair, side)
                            and reliability.confidence_kind != "unknown"
                            and reliability.provenance != "unknown"
                        )
                        reliable = reliable and confirmed
                        if confirmed:
                            confirmed_sides.add((factor, side))
                    elif reliability.method == "native_numeric":
                        reliable = (
                            reliable
                            and reliability.confidence_kind == "measured"
                            and reliability.provenance == "native_extraction"
                            and reliability.producer is not None
                        )
                        if observation.evidence:
                            getattr(effective_pair, side).confidence = min(
                                observation.confidence,
                                *(e.confidence for e in observation.evidence),
                            )
                    else:
                        reliable = False
                if not reliable:
                    add(
                        factor_key,
                        "evidence_reliability",
                        "needs_review",
                        "Located text or model confidence alone does not prove interpretation",
                        context=context,
                        factor_id=factor,
                    )
                else:
                    reliable_factors.add(factor)
                    valid_pairs.append(effective_pair)
            # Approval status is established by the injected authority, not by this field.
            approved = rule_set.model_copy(update={"status": "approved"})
            inputs = FactorEvaluationRequest(
                case_id=policy.identity.case_id,
                rule_set_id=rule_set.rule_set_id,
                factors=valid_pairs,
            )
            result = FactorRuleEngine(
                approved,
                minimum_confidence=self.minimum_confidence,
                confirmed_sides=frozenset(confirmed_sides),
            ).evaluate(inputs)
            result.context = context
            result.case_version = policy.identity.version
            result.source_hashes = {d.document_id: d.content_hash for d in registry.documents}
            comparisons.append(result)
            validation = ReviewVerifier().verify(
                result,
                approved,
                facts=inputs,
                minimum_confidence=self.minimum_confidence,
                confirmed_sides=frozenset(confirmed_sides),
            )
            add(
                key,
                "calculation",
                validation.status.value,
                "; ".join(validation.critical_errors)
                or "Recalculated from source facts and rule matrix",
                context=context,
                rule_version=rule_set.version,
            )
            for item in result.results:
                add(
                    f"{key}/factor/{item.factor_id}",
                    "factor",
                    item.status.value,
                    item.calculation_trace,
                    context=context,
                    factor_id=item.factor_id,
                    rule_id=item.rule_id,
                    rule_version=rule_set.version,
                    expected=str(item.adjustment_percent),
                    evidence=[
                        ref
                        for pair in pairs
                        if pair.pair.factor_id == item.factor_id
                        for ref in [*pair.target_sources, *pair.comparable_sources]
                    ],
                )
            for slot in valid_slots.values():
                if slot.context != context:
                    continue
                if slot.factor_id is not None:
                    items = [r for r in result.results if r.factor_id == slot.factor_id]
                    if (
                        len(items) == 1
                        and items[0].status is EvaluationStatus.VERIFIED
                        and slot.factor_id in reliable_factors
                    ) and slot.value in {
                        "target_grade",
                        "comparable_grade",
                        "adjustment_percent",
                    }:
                        value = getattr(items[0], slot.value)
                        if value is not None:
                            expected_slots[slot.id] = str(value)
                elif (
                    slot.value == "total"
                    and result.summary.total_adjustment_percent is not None
                    and set(entry.factor_ids) <= reliable_factors
                    and validation.status is EvaluationStatus.VERIFIED
                ):
                    expected_slots[slot.id] = str(result.summary.total_adjustment_percent)

        if claimed is not None:
            required.append("claimed_results")
            try:
                supplied = [FactorReviewResult.model_validate(c.model_dump()) for c in claimed]
                equal = supplied == comparisons
            except ValidationError:
                equal = False
            add(
                "claimed_results",
                "independent_result",
                "verified" if equal else "failed",
                "Complete comparison claims checked against independent recomputation",
            )

        # Only passed, source-bound observations can seed the derivation DAG.
        numeric: dict[str, Decimal] = {}
        for value in facts.observed:
            if value.slot_id in bound_slots:
                with suppress(ValueError, InvalidOperation):
                    numeric[value.slot_id] = percent(value)
        by_target: dict[str, list[ArithmeticCheck]] = {}
        graph: dict[str, set[str]] = {}
        for check in inventory.checks:
            required.append(f"arithmetic/{check.id}")
            by_target.setdefault(check.target, []).append(check)
            graph.setdefault(check.target, set()).update(check.inputs)
        try:
            order = list(TopologicalSorter(graph).static_order())
        except CycleError:
            order = []
            for check in inventory.checks:
                add(
                    f"arithmetic/{check.id}",
                    "arithmetic_dependency",
                    "needs_review",
                    "Cyclic derivation graph; no aggregate expected value established",
                    rule_id=check.id,
                    evidence=check.evidence,
                )
                add(
                    f"observed/{check.target}",
                    "arithmetic_dependency",
                    "needs_review",
                    "Aggregate participates in an invalid derivation graph",
                    evidence=check.evidence,
                )
        trusted: dict[str, Decimal] = {}
        for name, value in numeric.items():
            if name not in by_target and name in expected_slots:
                with suppress(InvalidOperation):
                    if value == Decimal(expected_slots[name]):
                        trusted[name] = value
        for target in order:
            checks = by_target.get(target, [])
            if not checks:
                continue
            passed = True
            for check in checks:
                id = f"arithmetic/{check.id}"
                if (
                    check.target not in valid_slots
                    or not set(check.inputs) <= valid_slots.keys()
                    or not citations_valid(check.evidence)
                ):
                    add(
                        id,
                        "arithmetic_definition",
                        "needs_review",
                        "Unbound arithmetic inputs or evidence",
                        rule_id=check.id,
                        evidence=check.evidence,
                    )
                    passed = False
                    continue
                if not set(check.inputs) <= trusted.keys() or target not in bound_slots:
                    add(
                        id,
                        "arithmetic_dependency",
                        "needs_review",
                        f"Inputs must be independently grounded and passed: {check.inputs}",
                        rule_id=check.id,
                        evidence=check.evidence,
                    )
                    passed = False
                    continue
                expected_number = calculate(
                    check.kind, [trusted[name] for name in check.inputs], quantum=check.quantum
                )
                comparison = ArithmeticComparison(check, expected_number)
                arithmetic_targets.setdefault(target, []).append(comparison)
                actual = numeric.get(target)
                blank = observed[target].state == "blank" and bound_slots[target].derivable_blank
                blank = blank and not any(r.excerpt.strip() for r in observed[target].evidence)
                matched = comparison.matches(actual) if actual is not None else blank
                passed = passed and matched
                add(
                    id,
                    "arithmetic",
                    "verified" if matched else "failed" if actual is not None else "needs_review",
                    (
                        f"{check.kind}({check.inputs}); grounded DAG; "
                        f"ROUND_HALF_UP quantum={check.quantum}; tolerance={check.tolerance}"
                    ),
                    observed=str(actual) if actual is not None else None,
                    expected=str(expected_number),
                    evidence=check.evidence,
                    rule_id=check.id,
                    rule_version=policy.identity.version,
                )
            constraints = arithmetic_targets.get(target, [])
            actual = numeric.get(target)
            independent = expected_slots.get(target)
            if passed and actual is not None and independent is not None:
                passed = all(
                    ArithmeticComparison(
                        c.check,
                        calculate("equals", [Decimal(independent)], quantum=c.check.quantum),
                    ).matches(actual)
                    for c in constraints
                )
            if passed and actual is not None:
                trusted[target] = actual
            elif passed and len({c.expected for c in constraints}) == 1:
                trusted[target] = constraints[0].expected
            elif target in valid_slots and len(constraints) != len(checks):
                add(
                    f"observed/{target}",
                    "arithmetic_dependency",
                    "needs_review",
                    "Not all derivation constraints have passed; target cannot seed another check",
                    evidence=valid_slots[target].evidence if target in valid_slots else [],
                )

        for slot in bound_slots.values():
            id = f"observed/{slot.id}"
            value = observed.get(slot.id)
            deterministic_expected = expected_slots.get(slot.id)
            constraints = arithmetic_targets.get(slot.id, [])
            expected = deterministic_expected or (
                "; ".join(f"{c.check.id}: {c.expected}" for c in constraints) or None
            )
            details = dict(
                context=slot.context,
                factor_id=slot.factor_id,
                evidence=slot.evidence,
                expected=expected,
            )
            if (
                value is None
                or expected is None
                or not citations_valid(slot.evidence)
                or not citations_valid(value.evidence)
            ):
                add(
                    id,
                    "observed_missing",
                    "needs_review",
                    "Required observed/expected evidence missing",
                    **details,
                )
                continue
            if value.state == "blank" and any(ref.excerpt.strip() for ref in value.evidence):
                add(
                    id,
                    "blank_contradiction",
                    "needs_review",
                    "Claimed blank references nonblank source text",
                    **details,
                )
                continue
            if value.state == "blank" and slot.derivable_blank:
                add(
                    id,
                    "derivable_blank",
                    "verified",
                    "Approved blank can be filled from expected value",
                    **details,
                )
                continue
            try:
                actual_text = (
                    str(value.value)
                    if value.unit == "grade" and value.state == "present"
                    else str(percent(value))
                )
                if slot.factor_id is None and constraints:
                    actual_number = percent(value)
                    equal = all(c.matches(actual_number) for c in constraints)
                    if deterministic_expected is not None:
                        # Preserve the independent factor total under every approved constraint.
                        equal = equal and all(
                            ArithmeticComparison(
                                c.check,
                                calculate(
                                    "equals",
                                    [Decimal(deterministic_expected)],
                                    quantum=c.check.quantum,
                                ),
                            ).matches(actual_number)
                            for c in constraints
                        )
                else:
                    equal = (
                        actual_text == expected
                        if value.unit == "grade"
                        else Decimal(actual_text) == Decimal(expected)
                    )
            except (ValueError, InvalidOperation):
                add(
                    id,
                    "observed_unresolved",
                    "needs_review",
                    f"Observed state: {value.state}",
                    **details,
                )
                continue
            add(
                id,
                "observed_comparison",
                "verified" if equal else "failed",
                (
                    "Observed target must satisfy every arithmetic constraint: "
                    + ", ".join(c.check.id for c in constraints)
                    if slot.factor_id is None and constraints
                    else "Observed and expected compared exactly in grade or percent points"
                ),
                observed=actual_text,
                **details,
            )
        return finish()
