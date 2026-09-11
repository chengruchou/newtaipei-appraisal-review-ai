"""Deterministic whole-case review and independent inventory completion gate."""

import json
from decimal import Decimal
from graphlib import CycleError, TopologicalSorter

from pydantic import ValidationError

from appraisal_review.domain.confidence import confirmation_digest
from appraisal_review.domain.document_models import SourceCitation, SourceDocument, SourceRegistry
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
from appraisal_review.domain.fill_candidates import ArithmeticComparison, validate_slot
from appraisal_review.domain.pdf_types import document_identity
from appraisal_review.domain.review_contracts import (
    ArithmeticCheck,
    Coverage,
    ReviewFinding,
    ReviewSlot,
)
from appraisal_review.domain.rule_engine import calculate
from appraisal_review.domain.source_purpose import SourcePurposes
from appraisal_review.domain.verification import ReviewVerifier
from appraisal_review.ports.approval import ReviewAuthorization


def source_cell(ref: SourceCitation) -> tuple[object, ...]:
    """All current citation entries are value anchors; excerpt is not cell identity."""
    return (ref.document_id, ref.content_hash, ref.version, ref.page, ref.region_id, ref.bbox)


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
        forms_source: SourceDocument | None = None,
        criteria_source: SourceDocument | None = None,
    ) -> CaseReviewResult:
        findings: list[ReviewFinding] = []
        comparisons: list[FactorReviewResult] = []
        required: list[str] = []
        origin_by_finding_id: dict[str, str] = {}
        source_origin_ids: list[str] = []

        def add(id: str, kind: str, status: str, trace: str, **details: object) -> None:
            findings.append(
                ReviewFinding.model_validate(
                    dict(id=id, kind=kind, status=status, trace=trace, **details)
                )
            )

        def citations_valid(refs: list[SourceCitation]) -> bool:
            return bool(refs) and all(registry.resolves(ref) for ref in refs)

        def finish() -> CaseReviewResult:
            # Diagnostics are derived anew from this reviewed inventory. They
            # never grant authority or copy observation text into public output.
            for finding in findings:
                if finding.status == "verified":
                    continue
                origins = (
                    source_origin_ids
                    if finding.id in origin_by_finding_id or finding.id.startswith("arithmetic/")
                    else []
                )
                if not origins and finding.kind == "observed_source_binding":
                    origin = origin_by_finding_id.get(finding.id)
                    origins = [origin] if origin is not None else []
                if origins:
                    finding.originating_field_ids = list(origins)
                    finding.trace += " Review source evidence for field IDs: " + ", ".join(
                        json.dumps(origin, ensure_ascii=False) for origin in origins
                    )
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
        origin_by_finding_id = {f"observed/{slot.id}": slot.id for slot in policy.inventory.slots}
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

        try:
            purposes = SourcePurposes.selected(
                registry, forms=forms_source, criteria=criteria_source
            )
            violations = purposes.violations(policy, facts)
        except ValueError:
            violations = [("sources", [])]
        source_origin_ids = sorted(
            {origin_by_finding_id[id] for id, _ in violations if id in origin_by_finding_id}
        )
        if violations:
            for id, refs in violations:
                required.append(id)
                add(
                    id,
                    "source_purpose",
                    "needs_review",
                    "Source does not support this use in the selected case documents",
                    evidence=refs,
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
                            and evidence.source_file is not None
                            and document_identity(evidence.source_file)
                            == document_identity(
                                next(
                                    d.uri
                                    for d in registry.documents
                                    if d.document_id == ref.document_id
                                )
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

        by_target: dict[str, list[ArithmeticCheck]] = {}
        graph: dict[str, set[str]] = {name: set() for name in valid_slots}
        for check in inventory.checks:
            required.append(f"arithmetic/{check.id}")
            by_target.setdefault(check.target, []).append(check)
            graph.setdefault(check.target, set()).update(check.inputs)
        cyclic = False
        try:
            order = list(TopologicalSorter(graph).static_order())
        except CycleError:
            cyclic = True
            order = list(graph)
        trusted: dict[str, Decimal] = {}
        source_trust = (
            authorized
            and not violations
            and not any(f.id in {"sources", "trust"} and f.status != "verified" for f in findings)
        )
        for target in order:
            checks = by_target.get(target, [])
            comparisons_for_slot: list[ArithmeticComparison] = []
            ready = not (cyclic and target in by_target)
            for check in checks:
                if (
                    check.target not in valid_slots
                    or not set(check.inputs) <= valid_slots.keys()
                    or not citations_valid(check.evidence)
                ):
                    reason = "arithmetic_definition"
                elif cyclic or not set(check.inputs) <= trusted.keys() or target not in bound_slots:
                    reason = "arithmetic_dependency"
                else:
                    comparisons_for_slot.append(
                        ArithmeticComparison(
                            check,
                            calculate(
                                check.kind,
                                [trusted[name] for name in check.inputs],
                                quantum=check.quantum,
                            ),
                        )
                    )
                    continue
                ready = False
                add(
                    f"arithmetic/{check.id}",
                    reason,
                    "needs_review",
                    f"Unvalidated or cyclic derivation inputs: {check.inputs}",
                    rule_id=check.id,
                    evidence=check.evidence,
                )
            if target not in bound_slots:
                continue
            slot = bound_slots[target]
            candidate = validate_slot(
                slot,
                observed[target],
                expected_slots.get(target),
                comparisons_for_slot,
                ready=ready,
                authorized=source_trust,
            )
            for constraint in comparisons_for_slot:
                check = constraint.check
                constraint_matches = isinstance(
                    candidate.candidate, Decimal
                ) and constraint.matches(candidate.candidate)
                add(
                    f"arithmetic/{check.id}",
                    "arithmetic",
                    "verified"
                    if constraint_matches
                    else "needs_review"
                    if candidate.candidate is None
                    else "failed",
                    (
                        f"{check.kind}({check.inputs}); one grounded candidate; "
                        f"ROUND_HALF_UP quantum={check.quantum}; tolerance={check.tolerance}"
                    ),
                    observed=str(candidate.candidate) if candidate.candidate is not None else None,
                    expected=str(constraint.expected),
                    evidence=check.evidence,
                    rule_id=check.id,
                    rule_version=policy.identity.version,
                )
            add(
                f"observed/{target}",
                candidate.kind,
                candidate.status,
                candidate.trace,
                observed=str(candidate.candidate) if candidate.candidate is not None else None,
                expected=str(candidate.expected) if candidate.expected is not None else None,
                context=slot.context,
                factor_id=slot.factor_id,
                evidence=slot.evidence,
            )
            if candidate.trusted_number is not None:
                trusted[target] = candidate.trusted_number
        return finish()
