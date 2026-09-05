"""Deterministic whole-case review and independent inventory completion gate."""

from contextlib import suppress
from decimal import Decimal, InvalidOperation

from pydantic import ValidationError

from appraisal_review.domain.document_models import SourceCitation, SourceRegistry
from appraisal_review.domain.factor_engine import FactorRuleEngine
from appraisal_review.domain.factor_models import (
    CaseFacts,
    CaseReviewResult,
    EvaluationStatus,
    FactorEvaluationRequest,
    FactorReviewResult,
    ReviewMaterial,
    ReviewPolicy,
)
from appraisal_review.domain.models import CanonicalCase, ExtractedField, RuleDefinition, RuleSet
from appraisal_review.domain.review_contracts import Coverage, ObservedValue, ReviewFinding
from appraisal_review.domain.rule_engine import RuleEngine, calculate
from appraisal_review.domain.verification import ReviewVerifier
from appraisal_review.ports.approval import ReviewAuthorization


def percent(value: ObservedValue) -> Decimal:
    if value.state != "present" or value.unit not in {"percent_points", "ratio"}:
        raise ValueError("A present numeric value with an explicit percent unit is required")
    result = Decimal(str(value.value))
    if not result.is_finite():
        raise ValueError("Nonfinite observed value")
    return result * 100 if value.unit == "ratio" else result


class CaseReviewer:
    def __init__(self, authorization: ReviewAuthorization | None) -> None:
        self.authorization = authorization

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
        if self.authorization is None or not self.authorization.permits(material):
            add("trust", "approval", "needs_review", "Exact material requires trusted approval")
        else:
            add("trust", "approval", "verified", "Exact case, policy and facts authorized")
        if registry != policy.registry:
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
        expected_slots: dict[str, str] = {}

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
                    "applicability",
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
                    "rule_source",
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
                if not reliable:
                    add(
                        factor_key,
                        "evidence_reliability",
                        "needs_review",
                        "Located text or model confidence alone does not prove interpretation",
                        context=context,
                        factor_id=factor,
                    )
                valid_pairs.append(pair.pair)
            # Approval status is established by the injected authority, not by this field.
            approved = rule_set.model_copy(update={"status": "approved"})
            inputs = FactorEvaluationRequest(
                case_id=policy.identity.case_id,
                rule_set_id=rule_set.rule_set_id,
                factors=valid_pairs,
            )
            result = FactorRuleEngine(approved).evaluate(inputs)
            result.context = context
            result.case_version = policy.identity.version
            result.source_hashes = {d.document_id: d.content_hash for d in registry.documents}
            comparisons.append(result)
            validation = ReviewVerifier().verify(result, approved, facts=inputs)
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
            for slot in inventory.slots:
                if slot.context != context:
                    continue
                if slot.factor_id is not None:
                    items = [r for r in result.results if r.factor_id == slot.factor_id]
                    if len(items) == 1 and slot.value in {
                        "target_grade",
                        "comparable_grade",
                        "adjustment_percent",
                    }:
                        value = getattr(items[0], slot.value)
                        if value is not None:
                            expected_slots[slot.id] = str(value)
                elif slot.value == "total" and result.summary.total_adjustment_percent is not None:
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

        # RuleEngine owns legacy sum/equals arithmetic. Observed values remain separate.
        numeric: dict[str, ExtractedField] = {}
        for value in facts.observed:
            with suppress(ValueError, InvalidOperation):
                numeric[value.slot_id] = ExtractedField(
                    raw_value=value.raw_text, value=str(percent(value))
                )
        for check in inventory.checks:
            id = f"arithmetic/{check.id}"
            required.append(id)
            if (
                check.target not in slots
                or not set(check.inputs) <= set(slots)
                or not citations_valid(check.evidence)
            ):
                add(
                    id,
                    "arithmetic_definition",
                    "needs_review",
                    "Unbound arithmetic inputs or evidence",
                )
                continue
            definition = RuleDefinition(
                id=check.id,
                kind=check.kind,
                description="Approved arithmetic",
                inputs=check.inputs,
                target=check.target,
                tolerance=float(check.tolerance),
            )
            legacy = (
                RuleEngine(RuleSet(version=policy.identity.version, rules=[definition]))
                .evaluate(CanonicalCase(case_id=policy.identity.case_id, fields=numeric))
                .findings[0]
            )
            actual_field = numeric.get(check.target)
            input_fields = [numeric.get(name) for name in check.inputs]
            if all(v is not None for v in input_fields) and actual_field is not None:
                expected_number = calculate(
                    check.kind,
                    [Decimal(str(v.value)) for v in input_fields if v is not None],
                    quantum=check.quantum,
                )
                arithmetic_matches = (
                    abs(expected_number - Decimal(str(actual_field.value))) <= check.tolerance
                )
                add(
                    id,
                    "arithmetic",
                    "verified" if arithmetic_matches else "failed",
                    (
                        f"{check.kind}({check.inputs}); ROUND_HALF_UP quantum={check.quantum}; "
                        f"tolerance={check.tolerance}"
                    ),
                    observed=str(actual_field.value),
                    expected=str(expected_number),
                    evidence=check.evidence,
                    rule_id=check.id,
                    rule_version=policy.identity.version,
                )
                if check.target not in expected_slots:
                    expected_slots[check.target] = str(expected_number)
            else:
                add(
                    id,
                    "arithmetic",
                    "needs_review",
                    legacy.message,
                    evidence=check.evidence,
                    rule_id=check.id,
                    rule_version=policy.identity.version,
                )

        for slot in inventory.slots:
            id = f"observed/{slot.id}"
            required.append(id)
            value = observed.get(slot.id)
            expected = expected_slots.get(slot.id)
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
                "Observed and expected compared in grade or percent points",
                observed=actual_text,
                **details,
            )
        return finish()
