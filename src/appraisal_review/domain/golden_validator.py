"""Independent golden checks: manifests are re-derived from fixtures, never from results.

`verify_manifest` proves the reviewed expectations still follow from the synthetic
material. `compare_case_review` and `compare_service_result` then compare an actual run
against those expectations. Nothing here writes an expected value back.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from uuid import NAMESPACE_URL, UUID, uuid5

from appraisal_review.domain.confidence import confirmation_digest
from appraisal_review.domain.factor_models import (
    AgentReviewRun,
    CaseReviewResult,
    EvaluationStatus,
    FactorRule,
    FactorRuleSet,
    Grade,
    IntervalBand,
    ReviewMaterial,
)
from appraisal_review.domain.golden_contract import (
    ArithmeticDerivation,
    CorrectionDerivation,
    ExpectationBasis,
    ExpectedHumanTask,
    ExpectedSlot,
    GoldenCase,
    GradeDerivation,
    IndependentExpectation,
)
from appraisal_review.domain.review_contracts import (
    ObservedValue,
    ReviewFinding,
    ReviewSlot,
    content_digest,
)
from appraisal_review.domain.service_contracts import (
    FactSideReference,
    HumanTask,
    RevisionReference,
    RunReference,
    ServiceResult,
)


@dataclass(frozen=True)
class GoldenMismatch:
    check: str
    detail: str


@dataclass(frozen=True)
class GoldenReport:
    case_key: str
    mismatches: tuple[GoldenMismatch, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.mismatches

    def describe(self) -> str:
        return "\n".join(f"{self.case_key}: {m.check}: {m.detail}" for m in self.mismatches)


class _Collector:
    def __init__(self, case_key: str) -> None:
        self.case_key = case_key
        self.mismatches: list[GoldenMismatch] = []

    def require(self, condition: bool, check: str, detail: str) -> bool:
        if not condition:
            self.mismatches.append(GoldenMismatch(check=check, detail=detail))
        return condition

    def report(self) -> GoldenReport:
        return GoldenReport(case_key=self.case_key, mismatches=tuple(self.mismatches))


def _decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None


def _same_value(left: str | None, right: str | None) -> bool:
    if left == right:
        return True
    numbers = (_decimal(left), _decimal(right))
    return None not in numbers and numbers[0] == numbers[1]


def _printed(value: str, text: str) -> bool:
    """A golden may only expect a value the synthetic document actually prints."""
    tokens = text.replace(",", " ").split()
    if value in tokens:
        return True
    number = _decimal(value)
    return number is not None and any(_decimal(token) == number for token in tokens)


def _band_contains(band: IntervalBand, value: Decimal) -> bool:
    """Reviewer-side interval reading, implemented separately from the rule engine."""
    minimum = None if band.minimum is None else Decimal(str(band.minimum))
    maximum = None if band.maximum is None else Decimal(str(band.maximum))
    above = minimum is None or value > minimum or (band.minimum_inclusive and value == minimum)
    below = maximum is None or value < maximum or (band.maximum_inclusive and value == maximum)
    return above and below


def _sum(values: list[Decimal], *, operation: str, quantum: Decimal) -> Decimal:
    total = sum(values, start=Decimal("0")) if operation == "sum" else values[0]
    return total.quantize(quantum, rounding=ROUND_HALF_UP)


def _rule(material: ReviewMaterial, rule_set_id: str, rule_id: str) -> FactorRule | None:
    for scoped in material.policy.rule_sets:
        if scoped.rules.rule_set_id != rule_set_id:
            continue
        for rule in scoped.rules.rules:
            if rule.id == rule_id:
                return rule
    return None


def _rule_set(material: ReviewMaterial, rule_set_id: str) -> FactorRuleSet | None:
    return next(
        (s.rules for s in material.policy.rule_sets if s.rules.rule_set_id == rule_set_id), None
    )


def expected_task_contract(
    case: GoldenCase, task: ExpectedHumanTask, material: ReviewMaterial, run: RunReference
) -> HumanTask:
    """Build the frozen service contract so a golden cannot describe an illegal task."""
    side = None
    if task.side is not None:
        pair = next(
            p
            for p in material.facts.pairs
            if p.context == task.side.context and p.pair.factor_id == task.side.factor_id
        )
        side = FactSideReference(
            context=task.side.context,
            factor_id=task.side.factor_id,
            side=task.side.side,
            input_digest=confirmation_digest(pair, task.side.side),
        )
    return HumanTask(
        task_id=uuid5(NAMESPACE_URL, f"golden:{case.case_key}:{task.task_ref}"),
        run=run,
        version=1,
        kind=task.kind,
        required_permission=task.required_permission,
        side=side,
        result_digest=task.result_digest,
        question=task.question,
        finding_ids=task.finding_ids,
        allowed_responses=task.allowed_responses,
    )


def _authoritative(slot: ExpectedSlot) -> str | None:
    return slot.independent.value if slot.independent is not None else slot.observed.value


def _check_classification(
    collector: _Collector,
    slot: ExpectedSlot,
    derivation: GradeDerivation,
    expected: IndependentExpectation,
    material: ReviewMaterial,
) -> None:
    rule = _rule(material, derivation.rule_set_id, derivation.rule_id)
    if not collector.require(
        rule is not None, "classification", f"{slot.slot_id}: unknown rule {derivation.rule_id}"
    ):
        return
    assert rule is not None
    collector.require(
        rule.unit == derivation.unit,
        "classification",
        f"{slot.slot_id}: measurement unit differs from the rule unit",
    )
    pair = next(
        (
            p
            for p in material.facts.pairs
            if p.context == slot.context and p.pair.factor_id == rule.factor_id
        ),
        None,
    )
    if not collector.require(
        pair is not None, "classification", f"{slot.slot_id}: no fixture pair for {rule.factor_id}"
    ):
        return
    assert pair is not None
    observation = getattr(pair.pair, derivation.side)
    measured = None if observation.value is None else _decimal(str(observation.value.value))
    collector.require(
        measured == derivation.measurement
        and (observation.value.unit if observation.value else None) == derivation.unit,
        "classification",
        f"{slot.slot_id}: fixture measurement differs from the reviewed measurement",
    )
    bands = [band for band in rule.intervals if band.grade is derivation.grade]
    collector.require(
        len(bands) == 1 and _band_contains(bands[0], derivation.measurement),
        "classification",
        f"{slot.slot_id}: {derivation.measurement} is not inside the {derivation.grade} band",
    )
    collector.require(
        expected.value == derivation.grade.value,
        "classification",
        f"{slot.slot_id}: expected value differs from the classified grade",
    )


def _check_correction(
    collector: _Collector,
    slot: ExpectedSlot,
    derivation: CorrectionDerivation,
    expected: IndependentExpectation,
    material: ReviewMaterial,
) -> None:
    rule = _rule(material, derivation.rule_set_id, derivation.rule_id)
    if not collector.require(
        rule is not None, "correction", f"{slot.slot_id}: unknown rule {derivation.rule_id}"
    ):
        return
    assert rule is not None
    row = rule.correction_matrix.values.get(derivation.target_grade.value, {})
    cell = row.get(derivation.comparable_grade.value)
    collector.require(
        cell is not None and _same_value(str(cell), expected.value),
        "correction",
        f"{slot.slot_id}: expected rate differs from the fixture correction matrix",
    )


def _check_arithmetic(
    collector: _Collector,
    slot: ExpectedSlot,
    derivation: ArithmeticDerivation,
    expected: IndependentExpectation,
    slots: dict[str, ExpectedSlot],
) -> None:
    inputs: list[Decimal] = []
    for name in derivation.input_slot_ids:
        source = slots.get(name)
        value = _decimal(_authoritative(source)) if source is not None else None
        if not collector.require(
            value is not None, "arithmetic", f"{slot.slot_id}: input {name} has no expected number"
        ):
            return
        assert value is not None
        inputs.append(value)
    recomputed = _sum(inputs, operation=derivation.operation, quantum=derivation.quantum)
    collector.require(
        _same_value(str(recomputed), expected.value),
        "arithmetic",
        f"{slot.slot_id}: {derivation.operation}{derivation.input_slot_ids} is {recomputed}, "
        f"not the reviewed {expected.value}",
    )


def verify_manifest(case: GoldenCase, material: ReviewMaterial) -> GoldenReport:
    """Re-derive every expected value from the fixture the manifest was authored against."""
    collector = _Collector(case.case_key)
    registry = material.policy.registry
    collector.require(
        case.fixture.material_digest == content_digest(material),
        "fixture",
        "Manifest was authored against different material",
    )
    collector.require(
        case.identity == material.policy.identity,
        "fixture",
        "Manifest identity differs from the material identity",
    )
    actual_documents = {
        d.document_id: (d.role, d.version, d.content_hash, len(d.pages)) for d in registry.documents
    }
    declared = {
        d.document_id: (d.role, d.version, d.content_hash, d.page_count)
        for d in case.fixture.documents
    }
    collector.require(declared == actual_documents, "fixture", "Declared documents differ")

    inventory = {slot.id: slot for slot in material.policy.inventory.slots}
    observations = {value.slot_id: value for value in material.facts.observed}
    slots = {slot.slot_id: slot for slot in case.expected_slots}
    for slot in case.expected_slots:
        _verify_slot(collector, case, slot, slots, inventory, observations, material)

    for rule_state in case.expected_rules:
        rule_set = _rule_set(material, rule_state.rule_set_id)
        collector.require(
            rule_set is not None
            and (rule_set.version, rule_set.status)
            == (rule_state.version, rule_state.authored_status),
            "rules",
            f"{rule_state.rule_set_id}: authored version or candidate status differs",
        )
        collector.require(
            rule_state.material_authority == case.material_authority,
            "rules",
            f"{rule_state.rule_set_id}: rule authority differs from the case authority",
        )

    run = RunReference(
        run_id=uuid5(NAMESPACE_URL, f"golden-run:{case.case_key}"),
        revision=_placeholder_revision(case, material),
    )
    for task in case.expected_tasks:
        try:
            expected_task_contract(case, task, material, run)
        except (StopIteration, ValueError) as error:
            collector.require(False, "tasks", f"{task.task_ref}: {error}")
    return collector.report()


def _placeholder_revision(case: GoldenCase, material: ReviewMaterial) -> RevisionReference:
    """A manifest names no run; task shape is checked against a derived placeholder."""
    return RevisionReference(
        case_id=case.identity.case_id,
        revision_id=case.identity.version,
        material_digest=content_digest(material),
    )


def _verify_slot(
    collector: _Collector,
    case: GoldenCase,
    slot: ExpectedSlot,
    slots: dict[str, ExpectedSlot],
    inventory: Mapping[str, ReviewSlot],
    observations: Mapping[str, ObservedValue],
    material: ReviewMaterial,
) -> None:
    registry = material.policy.registry
    reviewed = inventory.get(slot.slot_id)
    if not collector.require(
        reviewed is not None, "slot", f"{slot.slot_id}: not part of the reviewed inventory"
    ):
        return
    observation = observations.get(slot.slot_id)
    if collector.require(
        observation is not None, "slot", f"{slot.slot_id}: the fixture records no observation"
    ):
        assert observation is not None
        collector.require(
            observation.state == slot.observed.state
            and _same_value(
                None if observation.value is None else str(observation.value), slot.observed.value
            )
            and observation.unit == slot.observed.unit,
            "slot",
            f"{slot.slot_id}: the fixture observation differs from the manifest",
        )
    citation = slot.observed.citation
    if citation is not None:
        collector.require(
            registry.resolves(citation),
            "citation",
            f"{slot.slot_id}: the cited region does not resolve in the fixture registry",
        )
        if slot.observed.value is not None:
            collector.require(
                _printed(slot.observed.value, citation.excerpt),
                "citation",
                f"{slot.slot_id}: {slot.observed.value} is not printed in the cited excerpt",
            )
    expected = slot.independent
    if expected is None:
        return
    if expected.basis is ExpectationBasis.CLASSIFICATION and expected.classification is not None:
        _check_classification(collector, slot, expected.classification, expected, material)
    elif expected.basis is ExpectationBasis.CORRECTION and expected.correction is not None:
        _check_correction(collector, slot, expected.correction, expected, material)
    elif expected.basis is ExpectationBasis.ARITHMETIC and expected.arithmetic is not None:
        _check_arithmetic(collector, slot, expected.arithmetic, expected, slots)
    else:
        record = next(
            (r for r in case.adjudications if r.record_id == expected.adjudication_id), None
        )
        collector.require(
            record is not None
            and record.outcome == "agreed"
            and _same_value(record.decision, expected.value),
            "adjudication",
            f"{slot.slot_id}: no agreed adjudication grounds {expected.value}",
        )


def _finding_counts(findings: Sequence[ReviewFinding]) -> Counter[tuple[str, str, str]]:
    return Counter((f.id, f.kind, str(f.status)) for f in findings)


def _reported_candidate(slot: ExpectedSlot) -> str | None:
    """An approved blank reports the value that filled it; every other field reports itself."""
    if slot.observed.state == "present":
        return slot.observed.value
    if slot.observed.state == "blank" and slot.expected_status == "verified":
        return None if slot.independent is None else slot.independent.value
    return None


def _expected_counts(case: GoldenCase) -> Counter[tuple[str, str, str]]:
    return Counter(
        (finding.id, finding.kind, str(finding.status)) for finding in case.expected_findings
    )


def compare_case_review(case: GoldenCase, result: CaseReviewResult) -> GoldenReport:
    """Compare an actual whole-case review against the reviewed expectations."""
    collector = _Collector(case.case_key)
    collector.require(
        result.identity == case.identity, "identity", "Reviewed identity differs from the golden"
    )
    collector.require(
        result.status is case.expected_status,
        "status",
        f"case status is {result.status.value}, expected {case.expected_status.value}",
    )
    actual = _finding_counts(result.findings)
    expected = _expected_counts(case)
    for entry in sorted(set(expected) - set(actual)):
        collector.require(False, "findings", f"missing finding {entry}")
    for entry in sorted(set(actual) - set(expected)):
        collector.require(False, "findings", f"unexpected finding {entry}")
    collector.require(
        tuple(result.coverage.missing) == case.expected_coverage.missing,
        "coverage",
        f"missing coverage {tuple(result.coverage.missing)}",
    )
    collector.require(
        tuple(result.coverage.unsupported) == case.expected_coverage.unsupported,
        "coverage",
        f"unsupported coverage {tuple(result.coverage.unsupported)}",
    )
    findings = {finding.id: finding for finding in result.findings}
    for slot in case.expected_slots:
        finding = findings.get(f"observed/{slot.slot_id}")
        if not collector.require(
            finding is not None, "slot", f"{slot.slot_id}: no observed finding was reported"
        ):
            continue
        assert finding is not None
        reported = _reported_candidate(slot)
        collector.require(
            _same_value(finding.observed, reported),
            "slot",
            f"{slot.slot_id}: reported {finding.observed}, reviewed candidate {reported}",
        )
        independent = None if slot.independent is None else slot.independent.value
        collector.require(
            _same_value(finding.expected, independent),
            "slot",
            f"{slot.slot_id}: reported expectation {finding.expected}, reviewed {independent}",
        )
    _compare_comparisons(collector, case, result)
    return collector.report()


def _compare_comparisons(collector: _Collector, case: GoldenCase, result: CaseReviewResult) -> None:
    """Grades and correction rates are compared as values, not only as finding statuses."""
    for slot in case.expected_slots:
        if slot.factor_id is None or slot.independent is None:
            continue
        entries = [
            item
            for comparison in result.comparisons
            if comparison.context == slot.context
            for item in comparison.results
            if item.factor_id == slot.factor_id
        ]
        if not collector.require(
            len(entries) == 1, "comparison", f"{slot.slot_id}: no single evaluated factor"
        ):
            continue
        value = getattr(entries[0], slot.slot_value)
        reported = None if value is None else (value.value if isinstance(value, Grade) else value)
        collector.require(
            _same_value(None if reported is None else str(reported), slot.independent.value),
            "comparison",
            f"{slot.slot_id}: evaluated {reported}, reviewed {slot.independent.value}",
        )


def compare_service_result(case: GoldenCase, result: ServiceResult) -> GoldenReport:
    """Compare the published service envelope: diagnostics and artifact coverage."""
    collector = _Collector(case.case_key)
    verification = result.verification
    if collector.require(
        verification is not None, "verification", "the service published no verification"
    ):
        assert verification is not None
        collector.require(
            verification.status is case.expected_verification.status,
            "verification",
            f"gate status {verification.status.value}",
        )
        collector.require(
            verification.critical_errors == case.expected_verification.critical,
            "verification",
            f"critical diagnostics {[d.code for d in verification.critical_errors]}",
        )
        collector.require(
            verification.warnings == case.expected_verification.warnings,
            "verification",
            f"warning diagnostics {[d.code for d in verification.warnings]}",
        )
    collector.require(
        result.artifact_status == case.expected_artifact.artifact_status,
        "artifact",
        f"artifact status {result.artifact_status}",
    )
    # Only a written artifact publishes a manifest; a simulated write publishes nothing.
    published = case.expected_artifact.artifact_status == "written"
    expected_fields = case.expected_artifact.field_ids if published else ()
    reviewed = case.expected_artifact.context if published else None
    fields = tuple(id for manifest in result.artifacts for id in manifest.field_ids)
    collector.require(
        fields == expected_fields,
        "artifact",
        f"published fields {fields}, reviewed {expected_fields}",
    )
    contexts = [manifest.context for manifest in result.artifacts]
    collector.require(
        contexts == ([] if reviewed is None else [reviewed]),
        "artifact",
        "artifact context differs from the reviewed context",
    )
    collector.require(
        _finding_counts(result.findings) == _expected_counts(case),
        "findings",
        "published findings differ from the golden",
    )
    return collector.report()


def verification_status(case: GoldenCase) -> EvaluationStatus:
    return case.expected_verification.status


def golden_task_id(case: GoldenCase, task: ExpectedHumanTask) -> UUID:
    return uuid5(NAMESPACE_URL, f"golden:{case.case_key}:{task.task_ref}")


def compare_review_run(case: GoldenCase, run: AgentReviewRun) -> GoldenReport:
    """Compare a whole controller run: findings, the completion gate and the writer boundary."""
    collector = _Collector(case.case_key)
    if collector.require(
        run.case_review is not None, "review", "the run carries no whole-case review"
    ):
        assert run.case_review is not None
        collector.mismatches.extend(compare_case_review(case, run.case_review).mismatches)
    expected = case.expected_verification
    if collector.require(run.verification is not None, "verification", "no completion gate ran"):
        assert run.verification is not None
        collector.require(
            run.verification.status is expected.status,
            "verification",
            f"gate status {run.verification.status.value}, expected {expected.status.value}",
        )
        collector.require(
            len(run.verification.critical_errors) == len(expected.critical),
            "verification",
            f"{len(run.verification.critical_errors)} blockers, expected {len(expected.critical)}",
        )
    collector.require(
        run.artifact_status == case.expected_artifact.artifact_status,
        "artifact",
        f"artifact status {run.artifact_status}, expected {case.expected_artifact.artifact_status}",
    )
    written = () if run.pdf_result is None else tuple(run.pdf_result.written_field_ids)
    collector.require(
        written == case.expected_artifact.field_ids,
        "artifact",
        f"placed fields {written}, reviewed {case.expected_artifact.field_ids}",
    )
    return collector.report()
