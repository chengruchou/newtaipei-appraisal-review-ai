"""Synthetic full-case fixtures and the reviewed golden manifests authored against them.

Every document, value and rule here is invented for acceptance testing. No real appraisal
case, competition document or identifying value is used, and no fixture is derived from
one. Expected values are authored from the fixture text, the rule bands and the arithmetic
procedure; they are never captured from a review result.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal

from appraisal_review.adapters.local.fake_pdf import FakePDFWriter
from appraisal_review.application.bootstrap import ReviewAdapters
from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.domain.confidence import confirm_side
from appraisal_review.domain.document_models import (
    SourceCitation,
    SourceDocument,
    SourcePage,
    SourceRegion,
    SourceRegistry,
)
from appraisal_review.domain.factor_models import (
    AgentReviewRequest,
    CaseFacts,
    CategoryBand,
    CorrectionMatrix,
    EvaluationStatus,
    EvidencedPair,
    FactorObservation,
    FactorPair,
    FactorRule,
    FactorRuleSet,
    Grade,
    IntervalBand,
    NormalizedValue,
    ReviewMaterial,
    ReviewPolicy,
    RuleApplicability,
    RuleSource,
    ScopedRules,
)
from appraisal_review.domain.golden_contract import (
    AdjudicationRecord,
    ArithmeticDerivation,
    CorrectionDerivation,
    DataUseStatement,
    DocumentProvenance,
    ExpectationBasis,
    ExpectedArtifact,
    ExpectedCoverage,
    ExpectedFinding,
    ExpectedHumanTask,
    ExpectedRuleState,
    ExpectedSlot,
    ExpectedTaskSide,
    ExpectedVerification,
    FindingStatus,
    FixtureProvenance,
    GoldenCase,
    GoldenDimension,
    GoldenSuite,
    GradeDerivation,
    IndependentExpectation,
    ObservedExpectation,
    SlotValue,
    UnresolvedItem,
)
from appraisal_review.domain.models import EvidenceRef
from appraisal_review.domain.pdf_models import PDFField, PDFFieldMap, PDFValueRef
from appraisal_review.domain.review_contracts import (
    ArithmeticCheck,
    CaseIdentity,
    ComparisonContext,
    EmptyColumn,
    InventoryContext,
    ObservedValue,
    Reliability,
    ReviewInventory,
    ReviewSlot,
    content_digest,
)
from appraisal_review.domain.service_contracts import (
    Permission,
    ResponseAction,
    TaskKind,
    VerificationDiagnostic,
)
from appraisal_review.ports.pdf import PDFWriter
from appraisal_review.ports.workflow import ParsedDocument

GENERATOR_VERSION = "golden-cases-v1"

CRITERIA_URI = "file:///golden/criteria.pdf"
FORMS_URI = "file:///golden/forms.pdf"
TEMPLATE_URI = "file:///golden/template.pdf"
OUTPUT_URI = "file:///golden/output/review.pdf"

CASE_ID = "golden-case"
DISTRICT = "golden-district"
ZONE = "golden-zone"
CATEGORY = "golden-commercial"
EFFECTIVE_DATE = date(2026, 9, 1)
GOLDEN_REVIEWER = "golden-reviewer-1"

FACTOR_ID = "golden.road_width"
UNSUPPORTED_FACTOR_ID = "golden.frontage_shape"
RULE_SET_ID = "golden-road-v1"
RULE_ID = "golden-road.v1"
UNSUPPORTED_RULE_ID = "golden-frontage.v1"
RULE_VERSION = "1.0.0"

TARGET_WIDTH = Decimal("12")
COMPARABLE_WIDTH = Decimal("8")
BAND_THRESHOLD = 10.0
CORRECTION_RATE = "5"

REGIONAL = ComparisonContext(
    scope="regional", target_id="golden-target", comparable_id="golden-comparable-a"
)
INDIVIDUAL = ComparisonContext(
    scope="individual", target_id="golden-target", comparable_id="golden-comparable-b"
)

DATA_USE = DataUseStatement(
    generator=GENERATOR_VERSION,
    statement=(
        "Fully synthetic acceptance fixture. Documents, districts, measurements and rules "
        "are invented for testing and are not derived from any real appraisal case."
    ),
)


@dataclass(frozen=True)
class _Region:
    id: str
    text: str
    kind: Literal["text", "cell"] = "text"
    table_id: str | None = None


@dataclass(frozen=True)
class FixtureSpec:
    """Everything a case varies. Text changes the document hash, as a real edit would."""

    contexts: tuple[ComparisonContext, ...] = (REGIONAL,)
    identity_version: str = "r1"
    copied_total: str = CORRECTION_RATE
    copied_state: Literal["present", "blank", "missing"] = "present"
    inspect_second_page: bool = True
    measured: bool = True
    unsupported: tuple[str, ...] = ()
    unsupported_rule: bool = False


CRITERIA_REGIONS = (
    _Region("band-inferior", "Road width below 10 m is graded inferior"),
    _Region("band-excellent", "Road width of 10 m or more is graded excellent"),
    _Region(
        "matrix",
        "An excellent target against an inferior comparable corrects by 5 percent points",
    ),
    _Region(
        "procedure",
        "The subtotal sums the factor rates, the total sums the subtotals, "
        "and the copied total repeats the total",
    ),
    _Region(
        "unsupported",
        "Frontage shape is graded by a narrative table the deterministic engine cannot express",
    ),
)


def prefix(context: ComparisonContext) -> str:
    return context.scope


def _factor_regions(name: str) -> tuple[_Region, ...]:
    return (
        _Region(f"{name}-target-width", f"{name} target road width 12 m", "cell", "factor-table"),
        _Region(
            f"{name}-comparable-width", f"{name} comparable road width 8 m", "cell", "factor-table"
        ),
        _Region(f"{name}-target-grade", f"{name} target grade excellent", "cell", "factor-table"),
        _Region(
            f"{name}-comparable-grade",
            f"{name} comparable grade inferior",
            "cell",
            "factor-table",
        ),
        _Region(f"{name}-rate", f"{name} road rate 5", "cell", "factor-table"),
    )


def _summary_regions(name: str, spec: FixtureSpec) -> tuple[_Region, ...]:
    blank = spec.copied_state == "blank"
    return (
        _Region(f"{name}-subtotal", f"{name} subtotal 5"),
        _Region(f"{name}-total", f"{name} total 5"),
        _Region(
            f"{name}-copied-total",
            "" if blank else f"{name} copied total {spec.copied_total}",
            "cell" if blank else "text",
        ),
    )


def _pages(pages: list[tuple[_Region, ...]], *, width: float = 460.0) -> list[SourcePage]:
    height = 60.0 + 40.0 * max(len(regions) for regions in pages)
    built = []
    for number, regions in enumerate(pages, start=1):
        rows = [
            SourceRegion(
                id=region.id,
                kind=region.kind,
                bbox=(
                    20.0,
                    height - 45.0 - 40.0 * index,
                    width - 20.0,
                    height - 20.0 - 40.0 * index,
                ),
                text=region.text,
                table_id=region.table_id,
            )
            for index, region in enumerate(regions)
        ]
        built.append(
            SourcePage(
                number=number,
                width=width,
                height=height,
                crop_box=(0.0, 0.0, width, height),
                has_text=True,
                regions=rows,
            )
        )
    return built


def _document(
    document_id: str,
    uri: str,
    role: Literal["criteria", "forms"],
    pages: list[SourcePage],
) -> SourceDocument:
    payload = json.dumps(
        [[[r.id, r.kind, r.text, list(r.bbox)] for r in page.regions] for page in pages],
        sort_keys=True,
    )
    return SourceDocument(
        document_id=document_id,
        uri=uri,
        content_hash=hashlib.sha256(payload.encode()).hexdigest(),
        version="1",
        role=role,
        pages=pages,
    )


def cite(document: SourceDocument, page: int, region_id: str) -> SourceCitation:
    region = next(r for r in document.pages[page - 1].regions if r.id == region_id)
    return SourceCitation(
        document_id=document.document_id,
        content_hash=document.content_hash,
        version=document.version,
        page=page,
        region_id=region_id,
        bbox=region.bbox,
        excerpt=region.text,
    )


def _road_rule() -> FactorRule:
    return FactorRule(
        id=RULE_ID,
        factor_id=FACTOR_ID,
        kind="numeric_interval",
        unit="m",
        intervals=[
            IntervalBand(grade=Grade.INFERIOR, maximum=BAND_THRESHOLD, maximum_inclusive=False),
            IntervalBand(grade=Grade.EXCELLENT, minimum=BAND_THRESHOLD, minimum_inclusive=True),
        ],
        correction_matrix=CorrectionMatrix(
            values={
                "inferior": {"inferior": 0.0, "excellent": -5.0},
                "excellent": {"inferior": 5.0, "excellent": 0.0},
            }
        ),
    )


def _unsupported_rule() -> FactorRule:
    """A second critical factor the reviewed inventory cannot evaluate deterministically."""
    return FactorRule(
        id=UNSUPPORTED_RULE_ID,
        factor_id=UNSUPPORTED_FACTOR_ID,
        kind="category",
        categories=[
            CategoryBand(grade=Grade.EXCELLENT, values=["regular"]),
            CategoryBand(grade=Grade.INFERIOR, values=["irregular"]),
        ],
        correction_matrix=CorrectionMatrix(
            values={
                "excellent": {"excellent": 0.0, "inferior": 3.0},
                "inferior": {"excellent": -3.0, "inferior": 0.0},
            }
        ),
    )


def _rule_set(criteria: SourceDocument, spec: FixtureSpec) -> FactorRuleSet:
    rules = [_road_rule()]
    if spec.unsupported_rule:
        rules.append(_unsupported_rule())
    return FactorRuleSet(
        rule_set_id=RULE_SET_ID,
        version=RULE_VERSION,
        status="candidate",
        applicability=RuleApplicability(jurisdiction=DISTRICT, land_use_category=CATEGORY),
        source_document=RuleSource(
            document_id=criteria.document_id, content_hash=criteria.content_hash, pages=[1]
        ),
        rules=rules,
    )


def _observation(
    forms: SourceDocument, region_id: str, measurement: Decimal, *, measured: bool
) -> FactorObservation:
    citation = cite(forms, 1, region_id)
    confidence = 0.99 if measured else 0.0
    return FactorObservation(
        raw_text=citation.excerpt,
        value=NormalizedValue(type="number", value=float(measurement), unit="m"),
        confidence=confidence,
        evidence=[
            EvidenceRef(
                document_id=forms.document_id,
                source_file=forms.uri,
                page=1,
                confidence=confidence,
                bounding_box=citation.bbox,
                coordinate_system="pdf_bottom_left",
            )
        ],
    )


def _reliability(*, measured: bool) -> Reliability:
    if measured:
        return Reliability(
            method="native_numeric",
            selection="not_applicable",
            confidence_kind="measured",
            provenance="native_extraction",
            producer="golden-native-v1",
        )
    return Reliability(
        method="model_proposed",
        selection="not_applicable",
        confidence_kind="localization_only",
        provenance="parser_registry",
        producer="golden-locator-v1",
    )


def _observed_value(
    forms: SourceDocument, page: int, region_id: str, value: str, unit: str
) -> ObservedValue:
    citation = cite(forms, page, region_id)
    return ObservedValue.model_validate(
        {
            "slot_id": region_id,
            "state": "present",
            "value": value,
            "unit": unit,
            "raw_text": citation.excerpt,
            "evidence": [citation],
        }
    )


def build_material(spec: FixtureSpec) -> ReviewMaterial:
    """Compose one complete synthetic case: sources, policy, inventory and facts."""
    criteria = _document("golden-criteria", CRITERIA_URI, "criteria", _pages([CRITERIA_REGIONS]))
    first = tuple(region for c in spec.contexts for region in _factor_regions(prefix(c)))
    second = tuple(region for c in spec.contexts for region in _summary_regions(prefix(c), spec))
    forms = _document(
        "golden-forms",
        FORMS_URI,
        "forms",
        _pages([first, (*second, _Region("remarks", "", "cell"))]),
    )
    registry = SourceRegistry(documents=[criteria, forms])
    identity = CaseIdentity(
        case_id=CASE_ID,
        version=spec.identity_version,
        district=DISTRICT,
        zone=ZONE,
        land_use_category=CATEGORY,
        effective_date=EFFECTIVE_DATE,
    )
    rules = _rule_set(criteria, spec)
    scoped = [
        ScopedRules(
            context=context,
            rules=rules,
            source_version=criteria.version,
            zone=ZONE,
            evidence=[cite(criteria, 1, "matrix")],
        )
        for context in spec.contexts
    ]
    contexts = [
        InventoryContext(
            context=context,
            factor_ids=[FACTOR_ID],
            evidence=[cite(forms, 1, f"{prefix(context)}-rate")],
        )
        for context in spec.contexts
    ]
    slots: list[ReviewSlot] = []
    checks: list[ArithmeticCheck] = []
    observed: list[ObservedValue] = []
    pairs: list[EvidencedPair] = []
    for context in spec.contexts:
        name = prefix(context)
        for region_id, value, factor in (
            (f"{name}-target-grade", "target_grade", FACTOR_ID),
            (f"{name}-comparable-grade", "comparable_grade", FACTOR_ID),
            (f"{name}-rate", "adjustment_percent", FACTOR_ID),
        ):
            slots.append(
                ReviewSlot.model_validate(
                    {
                        "id": region_id,
                        "context": context,
                        "factor_id": factor,
                        "value": value,
                        "evidence": [cite(forms, 1, region_id)],
                    }
                )
            )
        grades = {"target": "excellent", "comparable": "inferior"}
        for side, grade in grades.items():
            observed.append(_observed_value(forms, 1, f"{name}-{side}-grade", grade, "grade"))
        observed.append(
            _observed_value(forms, 1, f"{name}-rate", CORRECTION_RATE, "percent_points")
        )
        for region_id, value in (
            (f"{name}-subtotal", "subtotal"),
            (f"{name}-total", "total"),
            (f"{name}-copied-total", "total"),
        ):
            slots.append(
                ReviewSlot.model_validate(
                    {
                        "id": region_id,
                        "context": context,
                        "value": value,
                        "derivable_blank": region_id.endswith("copied-total")
                        and spec.copied_state == "blank",
                        "evidence": [cite(forms, 2, region_id)],
                    }
                )
            )
        observed.append(_observed_value(forms, 2, f"{name}-subtotal", "5", "percent_points"))
        observed.append(_observed_value(forms, 2, f"{name}-total", "5", "percent_points"))
        observed.append(_copied_observation(forms, name, spec))
        checks.extend(
            ArithmeticCheck.model_validate(
                {
                    "id": check_id,
                    "kind": kind,
                    "inputs": inputs,
                    "target": target,
                    "evidence": [cite(criteria, 1, "procedure")],
                }
            )
            for check_id, kind, inputs, target in (
                (f"{name}-subtotal-sum", "sum", [f"{name}-rate"], f"{name}-subtotal"),
                (f"{name}-total-sum", "sum", [f"{name}-subtotal"], f"{name}-total"),
                (f"{name}-copy", "equals", [f"{name}-total"], f"{name}-copied-total"),
            )
        )
        pairs.append(
            EvidencedPair(
                context=context,
                pair=FactorPair(
                    factor_id=FACTOR_ID,
                    target=_observation(
                        forms, f"{name}-target-width", TARGET_WIDTH, measured=spec.measured
                    ),
                    comparable=_observation(
                        forms, f"{name}-comparable-width", COMPARABLE_WIDTH, measured=spec.measured
                    ),
                ),
                target_sources=[cite(forms, 1, f"{name}-target-width")],
                comparable_sources=[cite(forms, 1, f"{name}-comparable-width")],
                target_reliability=_reliability(measured=spec.measured),
                comparable_reliability=_reliability(measured=spec.measured),
            )
        )
    inventory = ReviewInventory(
        contexts=contexts,
        slots=slots,
        checks=checks,
        empty_columns=[EmptyColumn(id="remarks", evidence=[cite(forms, 2, "remarks")])],
        inspected_pages={
            criteria.document_id: [1],
            forms.document_id: [1, 2] if spec.inspect_second_page else [1],
        },
        inspected_tables={forms.document_id: ["factor-table"]},
        unsupported=list(spec.unsupported),
    )
    policy = ReviewPolicy(
        identity=identity, registry=registry, rule_sets=scoped, inventory=inventory
    )
    facts = CaseFacts(identity=identity, pairs=pairs, observed=observed)
    return ReviewMaterial(policy=policy, facts=facts)


def _copied_observation(forms: SourceDocument, name: str, spec: FixtureSpec) -> ObservedValue:
    slot_id = f"{name}-copied-total"
    citation = cite(forms, 2, slot_id)
    if spec.copied_state == "missing":
        return ObservedValue.model_validate(
            {"slot_id": slot_id, "state": "missing", "raw_text": "", "evidence": []}
        )
    if spec.copied_state == "blank":
        return ObservedValue.model_validate(
            {"slot_id": slot_id, "state": "blank", "raw_text": "", "evidence": [citation]}
        )
    return ObservedValue.model_validate(
        {
            "slot_id": slot_id,
            "state": "present",
            "value": spec.copied_total,
            "unit": "percent_points",
            "raw_text": citation.excerpt,
            "evidence": [citation],
        }
    )


# --- Reviewed expectations ------------------------------------------------------------
#
# Slot and finding expectations below are authored from the fixture text, the rule bands
# and the arithmetic procedure. `golden_validator.verify_manifest` re-derives each of them
# from the material, so an expected value cannot be quietly replaced by an observed result.


def forms_document(material: ReviewMaterial) -> SourceDocument:
    return next(d for d in material.policy.registry.documents if d.role == "forms")


def _grade_slot(
    material: ReviewMaterial,
    context: ComparisonContext,
    side: Literal["target", "comparable"],
    grade: Grade,
    measurement: Decimal,
) -> ExpectedSlot:
    forms = forms_document(material)
    slot_id = f"{prefix(context)}-{side}-grade"
    citation = cite(forms, 1, slot_id)
    value: SlotValue = "target_grade" if side == "target" else "comparable_grade"
    return ExpectedSlot(
        slot_id=slot_id,
        slot_value=value,
        context=context,
        factor_id=FACTOR_ID,
        observed=ObservedExpectation(
            state="present",
            value=grade.value,
            unit="grade",
            raw_text=citation.excerpt,
            citation=citation,
        ),
        independent=IndependentExpectation(
            basis=ExpectationBasis.CLASSIFICATION,
            value=grade.value,
            classification=GradeDerivation(
                rule_set_id=RULE_SET_ID,
                rule_id=RULE_ID,
                side=side,
                measurement=measurement,
                unit="m",
                grade=grade,
            ),
            rationale=(
                f"The criteria band the {side} width of {measurement} m falls into is "
                f"{grade.value}."
            ),
        ),
        expected_status="verified",
        expected_kind="observed_comparison",
        rationale=f"The printed {side} grade must equal the band the measurement falls in.",
    )


def _numeric_slot(
    material: ReviewMaterial,
    context: ComparisonContext,
    suffix: str,
    slot_value: SlotValue,
    page: int,
    independent: IndependentExpectation,
    rationale: str,
) -> ExpectedSlot:
    forms = forms_document(material)
    slot_id = f"{prefix(context)}-{suffix}"
    citation = cite(forms, page, slot_id)
    return ExpectedSlot(
        slot_id=slot_id,
        slot_value=slot_value,
        context=context,
        factor_id=FACTOR_ID if slot_value == "adjustment_percent" else None,
        observed=ObservedExpectation(
            state="present",
            value=CORRECTION_RATE,
            unit="percent_points",
            raw_text=citation.excerpt,
            citation=citation,
        ),
        independent=independent,
        expected_status="verified",
        expected_kind="observed_comparison",
        rationale=rationale,
    )


def _baseline_slots(spec: FixtureSpec, material: ReviewMaterial) -> dict[str, ExpectedSlot]:
    slots: dict[str, ExpectedSlot] = {}
    for context in spec.contexts:
        name = prefix(context)
        for slot in (
            _grade_slot(material, context, "target", Grade.EXCELLENT, TARGET_WIDTH),
            _grade_slot(material, context, "comparable", Grade.INFERIOR, COMPARABLE_WIDTH),
            _numeric_slot(
                material,
                context,
                "rate",
                "adjustment_percent",
                1,
                IndependentExpectation(
                    basis=ExpectationBasis.CORRECTION,
                    value=CORRECTION_RATE,
                    correction=CorrectionDerivation(
                        rule_set_id=RULE_SET_ID,
                        rule_id=RULE_ID,
                        target_grade=Grade.EXCELLENT,
                        comparable_grade=Grade.INFERIOR,
                    ),
                    rationale="The excellent/inferior cell of the correction matrix is 5.",
                ),
                "The printed rate must equal the correction matrix cell for the two grades.",
            ),
            _numeric_slot(
                material,
                context,
                "subtotal",
                "subtotal",
                2,
                IndependentExpectation(
                    basis=ExpectationBasis.ARITHMETIC,
                    value=CORRECTION_RATE,
                    arithmetic=ArithmeticDerivation(
                        operation="sum", input_slot_ids=(f"{name}-rate",)
                    ),
                    rationale="The criteria procedure sums the factor rates into the subtotal.",
                ),
                "The subtotal must equal the sum of this context's factor rates.",
            ),
            _numeric_slot(
                material,
                context,
                "total",
                "total",
                2,
                IndependentExpectation(
                    basis=ExpectationBasis.ARITHMETIC,
                    value=CORRECTION_RATE,
                    arithmetic=ArithmeticDerivation(
                        operation="sum", input_slot_ids=(f"{name}-subtotal",)
                    ),
                    rationale="The criteria procedure sums the subtotals into the total.",
                ),
                "The total must equal the sum of this context's subtotals.",
            ),
            _numeric_slot(
                material,
                context,
                "copied-total",
                "total",
                2,
                IndependentExpectation(
                    basis=ExpectationBasis.ARITHMETIC,
                    value=CORRECTION_RATE,
                    arithmetic=ArithmeticDerivation(
                        operation="equals", input_slot_ids=(f"{name}-total",)
                    ),
                    rationale="The copied field repeats the total of the same context.",
                ),
                "A value copied across pages must equal the total it was copied from.",
            ),
        ):
            slots[slot.slot_id] = slot
    return slots


def _restate(
    slot: ExpectedSlot,
    *,
    status: FindingStatus,
    kind: str,
    rationale: str,
    observed: ObservedExpectation | None = None,
    independent: IndependentExpectation | None = None,
    keep_independent: bool = True,
    derivable_blank: bool | None = None,
) -> ExpectedSlot:
    """Reviewers restate one field's expectation; other fields keep their derivation."""
    return ExpectedSlot(
        slot_id=slot.slot_id,
        slot_value=slot.slot_value,
        context=slot.context,
        factor_id=slot.factor_id,
        derivable_blank=slot.derivable_blank if derivable_blank is None else derivable_blank,
        observed=observed or slot.observed,
        independent=independent
        if independent is not None
        else (slot.independent if keep_independent else None),
        expected_status=status,
        expected_kind=kind,
        rationale=rationale,
    )


def _structural(
    spec: FixtureSpec,
    *,
    approval: FindingStatus = "verified",
    calculation: FindingStatus = "verified",
    factor_kind: str = "factor",
    factor_status: FindingStatus = "verified",
    arithmetic_kind: str = "arithmetic",
    arithmetic_status: FindingStatus = "verified",
    arithmetic_overrides: dict[str, tuple[str, FindingStatus]] | None = None,
    extra: tuple[tuple[str, str, FindingStatus, str], ...] = (),
) -> list[ExpectedFinding]:
    overrides = arithmetic_overrides or {}
    findings = [
        ExpectedFinding(
            id="trust",
            kind="approval",
            status=approval,
            rationale="Reviewing the exact material requires separately injected authority.",
        ),
        ExpectedFinding(
            id="sources",
            kind="source_identity",
            status="verified",
            rationale="The reviewed registry is the current parser registry.",
        ),
        ExpectedFinding(
            id="inventory",
            kind="inventory",
            status="verified",
            rationale="The inventory was examined independently of any result.",
        ),
    ]
    for context in spec.contexts:
        key = context.key()
        findings.append(
            ExpectedFinding(
                id=key,
                kind="calculation",
                status=calculation,
                rationale="Every comparison is recalculated from facts and the rule matrix.",
            )
        )
        findings.append(
            ExpectedFinding(
                id=f"{key}/factor/{FACTOR_ID}",
                kind=factor_kind,
                status=factor_status,
                rationale="Located text and model confidence alone cannot prove a reading.",
            )
        )
        for check in ("subtotal-sum", "total-sum", "copy"):
            check_id = f"{prefix(context)}-{check}"
            kind, status = overrides.get(check_id, (arithmetic_kind, arithmetic_status))
            findings.append(
                ExpectedFinding(
                    id=f"arithmetic/{check_id}",
                    kind=kind,
                    status=status,
                    rationale="Each declared arithmetic constraint is checked on one candidate.",
                )
            )
    findings.extend(
        ExpectedFinding(id=id, kind=kind, status=status, rationale=rationale)
        for id, kind, status, rationale in extra
    )
    return findings


def _verification(findings: list[ExpectedFinding], rationale: str) -> ExpectedVerification:
    """The controller reports the case status and one public diagnostic per open finding."""
    blocked = [f for f in findings if f.status != "verified"]
    status = (
        EvaluationStatus.FAILED
        if any(f.status == "failed" for f in findings)
        else EvaluationStatus.NEEDS_REVIEW
        if blocked
        else EvaluationStatus.VERIFIED
    )
    blocker = VerificationDiagnostic(
        code="verification_blocker",
        message="Verification could not pass; inspect review findings or request human review.",
    )
    return ExpectedVerification(
        status=status, critical=tuple(blocker for _ in blocked), rationale=rationale
    )


def _coverage(
    findings: list[ExpectedFinding], unsupported: tuple[str, ...] = ()
) -> ExpectedCoverage:
    """Coverage repeats the engine rule: an identity blocked anywhere is not covered."""
    blocked = {f.id for f in findings if f.status != "verified"}
    return ExpectedCoverage(unsupported=unsupported, missing=tuple(sorted(blocked)))


def _rule_state(spec: FixtureSpec, *, authorized: bool) -> tuple[ExpectedRuleState, ...]:
    return (
        ExpectedRuleState(
            rule_set_id=RULE_SET_ID,
            version=RULE_VERSION,
            authored_status="candidate",
            material_authority="exact_material_approved" if authorized else "not_approved",
            business_approval="pending",
            rationale=(
                "The rule set is a candidate extracted from the synthetic criteria. Exact-material "
                "authority is a test grant and is not a formal business approval of the bands."
            ),
        ),
    )


def _fixture_provenance(material: ReviewMaterial) -> FixtureProvenance:
    return FixtureProvenance(
        generator_version=GENERATOR_VERSION,
        material_digest=content_digest(material),
        documents=tuple(
            DocumentProvenance(
                document_id=d.document_id,
                role=d.role,
                version=d.version,
                content_hash=d.content_hash,
                page_count=len(d.pages),
            )
            for d in material.policy.registry.documents
        ),
    )


def _case_status(findings: list[ExpectedFinding]) -> EvaluationStatus:
    if any(f.status == "failed" for f in findings):
        return EvaluationStatus.FAILED
    if any(f.status != "verified" for f in findings):
        return EvaluationStatus.NEEDS_REVIEW
    return EvaluationStatus.VERIFIED


def _slot_findings(slots: dict[str, ExpectedSlot]) -> list[ExpectedFinding]:
    return [
        ExpectedFinding(
            id=f"observed/{slot.slot_id}",
            kind=slot.expected_kind,
            status=slot.expected_status,
            rationale=slot.rationale,
        )
        for slot in slots.values()
    ]


def _correction_task(
    task_ref: str, finding_ids: tuple[str, ...], question: str, rationale: str
) -> ExpectedHumanTask:
    return ExpectedHumanTask(
        task_ref=task_ref,
        kind=TaskKind.CORRECTION,
        required_permission=Permission.CORRECT,
        allowed_responses=(ResponseAction.CORRECT, ResponseAction.REJECT),
        finding_ids=finding_ids,
        question=question,
        rationale=rationale,
    )


def _confirmation_tasks(context: ComparisonContext) -> tuple[ExpectedHumanTask, ...]:
    sides: tuple[Literal["target", "comparable"], ...] = ("target", "comparable")
    return tuple(
        ExpectedHumanTask(
            task_ref=f"confirm-{side}",
            kind=TaskKind.FACT,
            required_permission=Permission.CONFIRM,
            allowed_responses=(ResponseAction.CONFIRM, ResponseAction.REJECT),
            finding_ids=(f"{context.key()}/factor/{FACTOR_ID}",),
            side=ExpectedTaskSide(context=context, factor_id=FACTOR_ID, side=side),
            question=f"Confirm the located {side} road width against the source page.",
            rationale=(
                "A located value with no measured confidence stays a proposal until a "
                "reviewer confirms that exact side."
            ),
        )
        for side in sides
    )


def _assemble(
    *,
    case_key: str,
    dimension: GoldenDimension,
    title: str,
    summary: str,
    spec: FixtureSpec,
    material: ReviewMaterial,
    authorized: bool,
    slots: dict[str, ExpectedSlot],
    structural: list[ExpectedFinding],
    artifact: ExpectedArtifact,
    tasks: tuple[ExpectedHumanTask, ...] = (),
    adjudications: tuple[AdjudicationRecord, ...] = (),
    unresolved: tuple[UnresolvedItem, ...] = (),
    verification_rationale: str,
    parent_case_key: str | None = None,
) -> GoldenCase:
    findings = [*structural, *_slot_findings(slots)]
    return GoldenCase(
        case_key=case_key,
        dimension=dimension,
        title=title,
        summary=summary,
        data_use=DATA_USE,
        fixture=_fixture_provenance(material),
        identity=material.policy.identity,
        parent_case_key=parent_case_key,
        material_authority="exact_material_approved" if authorized else "not_approved",
        expected_status=_case_status(findings),
        expected_slots=tuple(slots.values()),
        expected_findings=tuple(findings),
        expected_coverage=_coverage(findings, spec.unsupported),
        expected_verification=_verification(findings, verification_rationale),
        expected_tasks=tasks,
        expected_artifact=artifact,
        expected_rules=_rule_state(spec, authorized=authorized),
        adjudications=adjudications,
        unresolved=unresolved,
    )


PDF_FIELD_IDS = ("form-target-grade", "form-comparable-grade", "form-rate", "form-total")


def golden_field_map() -> PDFFieldMap:
    """One context per written artifact; the writer never merges comparisons."""
    context = REGIONAL
    refs = (
        ("form-target-grade", "target_grade", FACTOR_ID),
        ("form-comparable-grade", "comparable_grade", FACTOR_ID),
        ("form-rate", "adjustment_percent", FACTOR_ID),
        ("form-total", "total_adjustment_percent", None),
    )
    return PDFFieldMap(
        template_id="golden-form",
        fields=[
            PDFField(
                field_id=field_id,
                page=1,
                bounding_box=(40.0, 40.0 + 30.0 * index, 240.0, 60.0 + 30.0 * index),
                value_ref=PDFValueRef(
                    scope=context.scope,
                    target_id=context.target_id,
                    comparable_id=context.comparable_id,
                    value=value,  # type: ignore[arg-type]
                    factor_id=factor,
                ),
            )
            for index, (field_id, value, factor) in enumerate(refs)
        ],
    )


def golden_request(*, write_pdf: bool) -> AgentReviewRequest:
    return AgentReviewRequest(
        case_id=CASE_ID,
        criteria_document_uri=CRITERIA_URI,
        case_document_uri=FORMS_URI,
        pdf_template_uri=TEMPLATE_URI if write_pdf else None,
        output_pdf_uri=OUTPUT_URI if write_pdf else None,
        field_map=golden_field_map() if write_pdf else None,
    )


@dataclass(frozen=True)
class GoldenFixture:
    """A reviewed manifest with the exact synthetic material it was authored against."""

    case: GoldenCase
    material: ReviewMaterial
    request: AgentReviewRequest
    authorized: bool

    @property
    def case_key(self) -> str:
        return self.case.case_key


def _normal_case() -> GoldenFixture:
    spec = FixtureSpec()
    material = build_material(spec)
    slots = _baseline_slots(spec, material)
    return GoldenFixture(
        case=_assemble(
            case_key="normal-complete",
            dimension=GoldenDimension.NORMAL,
            title="Complete case with cross-page evidence",
            summary=(
                "Grades, rate, subtotal, total and the copied total are all printed, evidenced "
                "and consistent. The case is completable without a human decision."
            ),
            spec=spec,
            material=material,
            authorized=True,
            slots=slots,
            structural=_structural(spec),
            tasks=(),
            artifact=ExpectedArtifact(
                artifact_status="simulated",
                field_ids=PDF_FIELD_IDS,
                context=REGIONAL,
                rationale=(
                    "The contract test double reports the mapped fields without creating a "
                    "file, so the artifact is simulated rather than written."
                ),
            ),
            verification_rationale="Every required identity is verified, so the gate can pass.",
        ),
        material=material,
        request=golden_request(write_pdf=True),
        authorized=True,
    )


def _blank_derived_case() -> GoldenFixture:
    spec = FixtureSpec(copied_state="blank")
    material = build_material(spec)
    slots = _baseline_slots(spec, material)
    name = prefix(REGIONAL)
    forms = forms_document(material)
    slot_id = f"{name}-copied-total"
    slots[slot_id] = _restate(
        slots[slot_id],
        status="verified",
        kind="derivable_blank",
        derivable_blank=True,
        observed=ObservedExpectation(state="blank", raw_text="", citation=cite(forms, 2, slot_id)),
        rationale=(
            "The copy cell is empty and approved for derivation, so the grounded total may "
            "fill it. An unapproved blank would stay unresolved."
        ),
    )
    return GoldenFixture(
        case=_assemble(
            case_key="blank-derived",
            dimension=GoldenDimension.MISSING_DATA,
            title="Approved blank filled from a grounded total",
            summary=(
                "The copied total is blank on the form. Because the inventory approves that "
                "blank for derivation, the independently grounded total fills it."
            ),
            spec=spec,
            material=material,
            authorized=True,
            slots=slots,
            structural=_structural(spec),
            artifact=ExpectedArtifact(
                artifact_status="not_requested",
                rationale="This case exercises derivation, not the writer boundary.",
            ),
            verification_rationale="A derived blank is grounded, so the gate can still pass.",
        ),
        material=material,
        request=golden_request(write_pdf=False),
        authorized=True,
    )


def _missing_page_case() -> GoldenFixture:
    spec = FixtureSpec(inspect_second_page=False)
    material = build_material(spec)
    slots = _baseline_slots(spec, material)
    structural = _structural(
        spec,
        extra=(
            (
                "inventory",
                "page_coverage",
                "needs_review",
                "The second form page was never inventoried, so the case is not fully examined.",
            ),
        ),
    )
    return GoldenFixture(
        case=_assemble(
            case_key="missing-page",
            dimension=GoldenDimension.MISSING_PAGE,
            title="Incomplete page inventory blocks completion",
            summary=(
                "Every printed value still checks out, but the reviewed inventory covers only "
                "the first form page. Partial page coverage alone must prevent completion."
            ),
            spec=spec,
            material=material,
            authorized=True,
            slots=slots,
            structural=structural,
            tasks=(
                _correction_task(
                    "inventory-page-coverage",
                    ("inventory",),
                    "Inventory the remaining form pages or explain why they carry no field.",
                    "Only a human can extend the reviewed page inventory of a case.",
                ),
            ),
            artifact=ExpectedArtifact(
                artifact_status="not_requested",
                rationale="An incompletely inventoried case must not produce an artifact.",
            ),
            verification_rationale=(
                "One open coverage finding is enough to keep the gate from passing."
            ),
        ),
        material=material,
        request=golden_request(write_pdf=False),
        authorized=True,
    )


def _missing_observation_case() -> GoldenFixture:
    spec = FixtureSpec(copied_state="missing")
    material = build_material(spec)
    slots = _baseline_slots(spec, material)
    name = prefix(REGIONAL)
    for suffix in ("target-grade", "comparable-grade", "rate"):
        slot_id = f"{name}-{suffix}"
        slots[slot_id] = _restate(
            slots[slot_id],
            status="needs_review",
            kind="observed_unresolved",
            rationale=(
                "The value and its derivation still agree, but one unevidenced field in the "
                "case removes the current-source authority every fill depends on."
            ),
        )
    subtotal_id = f"{name}-subtotal"
    slots[subtotal_id] = _restate(
        slots[subtotal_id],
        status="needs_review",
        kind="arithmetic_dependency",
        keep_independent=False,
        rationale=(
            "No expected subtotal can be grounded here: its only input is an untrusted rate, "
            "so the review must publish no expectation instead of guessing one."
        ),
    )
    total_id = f"{name}-total"
    slots[total_id] = _restate(
        slots[total_id],
        status="needs_review",
        kind="arithmetic_dependency",
        rationale=(
            "The total still has an independent expectation from the recalculated comparison, "
            "but it cannot be accepted while the copy chain is unresolved."
        ),
    )
    copied_id = f"{name}-copied-total"
    slots[copied_id] = _restate(
        slots[copied_id],
        status="needs_review",
        kind="observed_source_binding",
        keep_independent=False,
        observed=ObservedExpectation(state="missing", raw_text=""),
        rationale=(
            "The field was never observed and carries no evidence, so it cannot be bound to "
            "the slot's source cell and must not be filled from the total."
        ),
    )
    structural = _structural(
        spec,
        arithmetic_kind="arithmetic_dependency",
        arithmetic_status="needs_review",
        extra=(
            (
                f"observed/{copied_id}",
                "source_purpose",
                "needs_review",
                "An observation with no citation cannot be shown to come from the case form.",
            ),
        ),
    )
    dispute = AdjudicationRecord(
        record_id="copied-total-treatment",
        question=(
            "Should an unobserved copy field be treated as an approved blank and derived, or "
            "stay missing until the page is re-read?"
        ),
        reviewers=("golden-reviewer-1", "golden-reviewer-2"),
        outcome="disputed",
        rationale=(
            "One reviewer reads the empty cell as an approved blank; the other holds that a "
            "field with no evidence at all was never observed. The fixture records the "
            "disagreement instead of choosing the reading that would make the case pass."
        ),
    )
    return GoldenFixture(
        case=_assemble(
            case_key="missing-observation",
            dimension=GoldenDimension.MISSING_DATA,
            title="An unevidenced field withdraws case-wide fill authority",
            summary=(
                "The copied total was never observed and cites nothing. Beyond blocking its own "
                "field, the missing citation withdraws the source authority that every other "
                "grounded fill in the case depends on."
            ),
            spec=spec,
            material=material,
            authorized=True,
            slots=slots,
            structural=structural,
            tasks=(
                _correction_task(
                    "observe-copied-total",
                    (f"observed/{copied_id}",),
                    "Re-read the copy field and record the observed value with its evidence.",
                    "Only a human can supply an observation the extraction never produced.",
                ),
            ),
            artifact=ExpectedArtifact(
                artifact_status="not_requested",
                rationale="No artifact may be produced while a reviewed field is unobserved.",
            ),
            adjudications=(dispute,),
            unresolved=(
                UnresolvedItem(
                    id=f"observed/{copied_id}",
                    reason="The two reviewers disagree on how to read the unobserved copy field.",
                    adjudication_id=dispute.record_id,
                ),
            ),
            verification_rationale="Unobserved evidence keeps the gate from passing.",
        ),
        material=material,
        request=golden_request(write_pdf=False),
        authorized=True,
    )


def _conflicting_case() -> GoldenFixture:
    spec = FixtureSpec(copied_total="7")
    material = build_material(spec)
    slots = _baseline_slots(spec, material)
    name = prefix(REGIONAL)
    forms = forms_document(material)
    copied_id = f"{name}-copied-total"
    citation = cite(forms, 2, copied_id)
    slots[copied_id] = _restate(
        slots[copied_id],
        status="failed",
        kind="observed_comparison",
        observed=ObservedExpectation(
            state="present",
            value="7",
            unit="percent_points",
            raw_text=citation.excerpt,
            citation=citation,
        ),
        rationale=(
            "The second page prints 7 where the grounded total is 5. Two source cells "
            "disagree, so the case fails instead of preferring either printed number."
        ),
    )
    structural = _structural(
        spec,
        arithmetic_overrides={f"{name}-copy": ("arithmetic", "failed")},
    )
    record = AdjudicationRecord(
        record_id="copy-conflict-outcome",
        question="Can the review accept either printed value when two form cells disagree?",
        reviewers=("golden-reviewer-1", "golden-reviewer-2"),
        outcome="agreed",
        decision="fail_and_return_for_correction",
        rationale=(
            "Both reviewers agree that a contradiction between the computed total and the "
            "copied cell is a finding for a human to correct, not a value to choose."
        ),
    )
    return GoldenFixture(
        case=_assemble(
            case_key="conflicting-sources",
            dimension=GoldenDimension.CONFLICTING_SOURCES,
            title="Copied total contradicts the grounded total",
            summary=(
                "The copy cell on the second page prints a different number from the total it "
                "repeats. The cross-page equality check and the field itself both fail."
            ),
            spec=spec,
            material=material,
            authorized=True,
            slots=slots,
            structural=structural,
            tasks=(
                _correction_task(
                    "resolve-copy-conflict",
                    (f"arithmetic/{name}-copy", f"observed/{copied_id}"),
                    "Correct the contradicting copy field or the total it was copied from.",
                    "The engine must not choose between two contradicting source cells.",
                ),
            ),
            artifact=ExpectedArtifact(
                artifact_status="not_requested",
                rationale="A failed case never reaches the writer boundary.",
            ),
            adjudications=(record,),
            verification_rationale="A failed arithmetic constraint fails the whole gate.",
        ),
        material=material,
        request=golden_request(write_pdf=False),
        authorized=True,
    )


def _unreliable_slots(
    spec: FixtureSpec, material: ReviewMaterial, rationale: str
) -> dict[str, ExpectedSlot]:
    """No side is reliable, so no field keeps an independently grounded expectation."""
    slots = _baseline_slots(spec, material)
    name = prefix(spec.contexts[0])
    for suffix in ("target-grade", "comparable-grade", "rate"):
        slot_id = f"{name}-{suffix}"
        slots[slot_id] = _restate(
            slots[slot_id],
            status="needs_review",
            kind="observed_missing",
            keep_independent=False,
            rationale=rationale,
        )
    for suffix in ("subtotal", "total", "copied-total"):
        slot_id = f"{name}-{suffix}"
        slots[slot_id] = _restate(
            slots[slot_id],
            status="needs_review",
            kind="arithmetic_dependency",
            keep_independent=False,
            rationale=(
                "Every derivation input traces back to an unconfirmed reading, so no grounded "
                "candidate exists for this field."
            ),
        )
    return slots


def _zero_confidence_case() -> GoldenFixture:
    spec = FixtureSpec(measured=False)
    material = build_material(spec)
    slots = _unreliable_slots(
        spec,
        material,
        rationale=(
            "The reading was located but never measured or confirmed, so the field has no "
            "independent expectation to compare the printed value against."
        ),
    )
    structural = _structural(
        spec,
        calculation="needs_review",
        factor_kind="evidence_reliability",
        factor_status="needs_review",
        arithmetic_kind="arithmetic_dependency",
        arithmetic_status="needs_review",
    )
    return GoldenFixture(
        case=_assemble(
            case_key="zero-confidence",
            dimension=GoldenDimension.ZERO_CONFIDENCE,
            title="Located values with zero measured confidence",
            summary=(
                "Both factor sides are model proposals with confidence 0. Nothing is evaluated "
                "and no field is filled until a reviewer confirms each exact side."
            ),
            spec=spec,
            material=material,
            authorized=True,
            slots=slots,
            structural=structural,
            tasks=_confirmation_tasks(REGIONAL),
            artifact=ExpectedArtifact(
                artifact_status="not_requested",
                rationale="Unconfirmed readings cannot produce an artifact.",
            ),
            verification_rationale=(
                "The comparison never evaluated, so the gate reports an open blocker."
            ),
        ),
        material=material,
        request=golden_request(write_pdf=False),
        authorized=True,
    )


def _unsupported_rule_case() -> GoldenFixture:
    unsupported = (
        "Frontage shape is graded by a narrative table the deterministic engine cannot express",
    )
    spec = FixtureSpec(unsupported_rule=True, unsupported=unsupported)
    material = build_material(spec)
    slots = _baseline_slots(spec, material)
    key = REGIONAL.key()
    structural = _structural(
        spec,
        calculation="needs_review",
        extra=(
            (
                "inventory",
                "unresolved",
                "needs_review",
                "The unsupported criteria rule is declared explicitly instead of ignored.",
            ),
            (
                key,
                "rule_coverage",
                "needs_review",
                "A critical rule of the set has no inventoried factor to evaluate.",
            ),
        ),
    )
    return GoldenFixture(
        case=_assemble(
            case_key="unsupported-rule",
            dimension=GoldenDimension.UNSUPPORTED_RULE,
            title="An unsupported criteria rule fails explicitly",
            summary=(
                "The criteria carry a second critical factor the deterministic engine cannot "
                "express. The supported factor still verifies, but the case cannot complete "
                "and the unsupported rule is reported rather than silently dropped."
            ),
            spec=spec,
            material=material,
            authorized=True,
            slots=slots,
            structural=structural,
            tasks=(
                ExpectedHumanTask(
                    task_ref="approve-supported-rules",
                    kind=TaskKind.RULES,
                    required_permission=Permission.APPROVE_RULES,
                    allowed_responses=(ResponseAction.APPROVE, ResponseAction.REJECT),
                    finding_ids=("inventory", key),
                    question=(
                        "Approve an expressible rule for the unsupported factor or record why "
                        "the case may proceed without it."
                    ),
                    rationale=(
                        "Only a rule authority can decide how an inexpressible criteria rule is "
                        "handled; the engine may not drop it."
                    ),
                ),
            ),
            artifact=ExpectedArtifact(
                artifact_status="not_requested",
                rationale="Incomplete rule coverage must not produce an artifact.",
            ),
            verification_rationale=(
                "A missing critical factor result keeps the independent gate from passing."
            ),
        ),
        material=material,
        request=golden_request(write_pdf=False),
        authorized=True,
    )


def _multi_context_case() -> GoldenFixture:
    spec = FixtureSpec(contexts=(REGIONAL, INDIVIDUAL))
    material = build_material(spec)
    slots = _baseline_slots(spec, material)
    return GoldenFixture(
        case=_assemble(
            case_key="multi-context",
            dimension=GoldenDimension.MULTIPLE_CONTEXTS,
            title="Two verified comparison contexts, one writer boundary",
            summary=(
                "A regional and an individual comparison both verify with their own subtotal, "
                "total and copy. The writer supports one context per artifact, so the review "
                "is complete while the artifact is explicitly unsupported."
            ),
            spec=spec,
            material=material,
            authorized=True,
            slots=slots,
            structural=_structural(spec),
            artifact=ExpectedArtifact(
                artifact_status="unsupported_contexts",
                rationale=(
                    "Two verified comparisons cannot be merged into one single-context "
                    "artifact, and the run says so instead of writing a partial form."
                ),
            ),
            verification_rationale="Both comparisons verify independently.",
        ),
        material=material,
        request=golden_request(write_pdf=True),
        authorized=True,
    )


def _unapproved_case() -> GoldenFixture:
    spec = FixtureSpec()
    material = build_material(spec)
    slots = _baseline_slots(spec, material)
    name = prefix(REGIONAL)
    for suffix in ("target-grade", "comparable-grade", "rate"):
        slot_id = f"{name}-{suffix}"
        slots[slot_id] = _restate(
            slots[slot_id],
            status="needs_review",
            kind="observed_unresolved",
            rationale=(
                "The value and the rule agree, but filling or accepting a field additionally "
                "requires approval of this exact material."
            ),
        )
    for suffix in ("subtotal", "total", "copied-total"):
        slot_id = f"{name}-{suffix}"
        slots[slot_id] = _restate(
            slots[slot_id],
            status="needs_review",
            kind="arithmetic_dependency",
            keep_independent=suffix != "subtotal",
            rationale=(
                "Without exact-material authority no field becomes a trusted derivation input, "
                "so the arithmetic chain never resolves."
            ),
        )
    structural = _structural(
        spec,
        approval="needs_review",
        arithmetic_kind="arithmetic_dependency",
        arithmetic_status="needs_review",
    )
    return GoldenFixture(
        case=_assemble(
            case_key="unapproved-material",
            dimension=GoldenDimension.UNAPPROVED_MATERIAL,
            title="Correct values without exact-material approval",
            summary=(
                "The same complete case is reviewed with no approval authority. Grades and "
                "rates still recalculate, but nothing may be accepted or filled, which "
                "separates candidate rule extraction from formal approval."
            ),
            spec=spec,
            material=material,
            authorized=False,
            slots=slots,
            structural=structural,
            tasks=(
                ExpectedHumanTask(
                    task_ref="approve-material",
                    kind=TaskKind.MATERIAL,
                    required_permission=Permission.APPROVE_MATERIAL,
                    allowed_responses=(ResponseAction.APPROVE, ResponseAction.REJECT),
                    finding_ids=("trust",),
                    question="Approve this exact case, policy and fact material for review.",
                    rationale=(
                        "Exact-material approval is a human authority; no recalculated value "
                        "can grant it."
                    ),
                ),
            ),
            artifact=ExpectedArtifact(
                artifact_status="not_requested",
                rationale="Unapproved material never reaches the writer boundary.",
            ),
            verification_rationale="Missing approval is an open blocker on every field.",
        ),
        material=material,
        request=golden_request(write_pdf=False),
        authorized=False,
    )


def build_revision_chain() -> tuple[ReviewMaterial, ReviewMaterial, ReviewMaterial]:
    """r1 proposes, r2 confirms both sides, r3 relabels a side and loses every confirmation."""
    first = build_material(FixtureSpec(measured=False, identity_version="r1"))
    parent = RevisionSnapshot.capture(first, "r1")
    second = parent.revise(parent.material, "r2").material
    for side in ("target", "comparable"):
        confirm_side(second.facts.pairs[0], side, reviewer=GOLDEN_REVIEWER)
    relabeled = RevisionSnapshot.capture(second, "r2").material
    relabeled.facts.pairs[0].target_reliability.method = "native_numeric"
    third = RevisionSnapshot.capture(second, "r2").revise(relabeled, "r3").material
    return first, second, third


def _revision_cases() -> tuple[GoldenFixture, ...]:
    spec = FixtureSpec(measured=False, identity_version="r1")
    first, second, third = build_revision_chain()
    proposal_rationale = (
        "A located reading with no measured confidence carries no independent expectation "
        "until the exact side is confirmed."
    )
    open_structural = _structural(
        spec,
        calculation="needs_review",
        factor_kind="evidence_reliability",
        factor_status="needs_review",
        arithmetic_kind="arithmetic_dependency",
        arithmetic_status="needs_review",
    )
    no_artifact = ExpectedArtifact(
        artifact_status="not_requested",
        rationale="The revision chain exercises authority lineage, not the writer boundary.",
    )
    return (
        GoldenFixture(
            case=_assemble(
                case_key="revision-r1",
                dimension=GoldenDimension.REVISION_CHAIN,
                title="Revision 1: model proposals await confirmation",
                summary=(
                    "The first revision holds located but unconfirmed readings. It is the "
                    "parent every later revision in this chain is compared against."
                ),
                spec=spec,
                material=first,
                authorized=True,
                slots=_unreliable_slots(spec, first, proposal_rationale),
                structural=open_structural,
                tasks=_confirmation_tasks(REGIONAL),
                artifact=no_artifact,
                verification_rationale="Nothing was evaluated, so the gate reports a blocker.",
            ),
            material=first,
            request=golden_request(write_pdf=False),
            authorized=True,
        ),
        GoldenFixture(
            case=_assemble(
                case_key="revision-r2",
                dimension=GoldenDimension.REVISION_CHAIN,
                title="Revision 2: confirmed sides verify the same readings",
                summary=(
                    "A reviewer confirms both sides of the factor. The identical readings now "
                    "evaluate, and every field verifies without changing a printed value."
                ),
                spec=spec,
                material=second,
                authorized=True,
                slots=_baseline_slots(spec, second),
                structural=_structural(spec),
                artifact=no_artifact,
                verification_rationale="Confirmed sides make the comparison evaluable.",
                parent_case_key="revision-r1",
            ),
            material=second,
            request=golden_request(write_pdf=False),
            authorized=True,
        ),
        GoldenFixture(
            case=_assemble(
                case_key="revision-r3",
                dimension=GoldenDimension.REVISION_CHAIN,
                title="Revision 3: relabelling cannot restore extraction authority",
                summary=(
                    "The third revision only relabels one side as native extraction. The "
                    "relabelled side is demoted to a proposal and every confirmation in the "
                    "revision is cleared, so the case returns to needing review."
                ),
                spec=spec,
                material=third,
                authorized=True,
                slots=_unreliable_slots(
                    spec,
                    third,
                    "A revision clears confirmations, and a method label cannot replace them.",
                ),
                structural=open_structural,
                tasks=_confirmation_tasks(REGIONAL),
                artifact=no_artifact,
                verification_rationale=(
                    "Approving the exact material does not restore a cleared confirmation."
                ),
                parent_case_key="revision-r2",
            ),
            material=third,
            request=golden_request(write_pdf=False),
            authorized=True,
        ),
    )


def golden_fixtures() -> tuple[GoldenFixture, ...]:
    """The reviewed acceptance matrix. Order is stable so manifests diff cleanly."""
    return (
        _normal_case(),
        _blank_derived_case(),
        _missing_page_case(),
        _missing_observation_case(),
        _conflicting_case(),
        _zero_confidence_case(),
        _unsupported_rule_case(),
        _multi_context_case(),
        _unapproved_case(),
        *_revision_cases(),
    )


def golden_suite() -> GoldenSuite:
    return GoldenSuite(cases=tuple(fixture.case for fixture in golden_fixtures()))


class GoldenParser:
    """Serves only the fixture's own documents; it opens and interprets nothing."""

    def __init__(self, material: ReviewMaterial) -> None:
        self.material = material

    async def parse_document(self, document_uri: str) -> ParsedDocument:
        source = next(
            (d for d in self.material.policy.registry.documents if d.uri == document_uri), None
        )
        if source is None:
            raise ValueError("Only documented golden fixture URIs are supported")
        return ParsedDocument(
            document_uri=document_uri,
            page_count=len(source.pages),
            source=source,
            content={"golden": True},
        )


class GoldenRuleProvider:
    def __init__(self, material: ReviewMaterial) -> None:
        self.material = material

    async def load_or_build_rules(self, criteria: ParsedDocument) -> ReviewPolicy:
        if criteria.document_uri != CRITERIA_URI:
            raise ValueError("Expected the golden criteria document")
        return self.material.policy


class GoldenFactExtractor:
    def __init__(self, material: ReviewMaterial) -> None:
        self.material = material

    async def extract_facts(self, document: ParsedDocument, *, case_id: str) -> CaseFacts:
        if document.document_uri != FORMS_URI or case_id != CASE_ID:
            raise ValueError("Expected the golden case document")
        return self.material.facts


class GoldenAuthorization:
    """Pins one exact fixture digest; it never approves arbitrary material."""

    def __init__(self, material: ReviewMaterial) -> None:
        self.digest = content_digest(material)

    def permits(self, material: ReviewMaterial) -> bool:
        return content_digest(material) == self.digest


def golden_adapters(
    fixture: GoldenFixture, *, pdf_writer: PDFWriter | None = None
) -> ReviewAdapters:
    return ReviewAdapters(
        mode="local",
        parser=GoldenParser(fixture.material),
        fact_extractor=GoldenFactExtractor(fixture.material),
        rule_provider=GoldenRuleProvider(fixture.material),
        authorization=GoldenAuthorization(fixture.material) if fixture.authorized else None,
        pdf_writer=pdf_writer if pdf_writer is not None else FakePDFWriter(),
    )


def manifest_path(directory: Path, case_key: str) -> Path:
    return directory / f"{case_key}.json"


def load_golden_manifests(directory: Path) -> GoldenSuite:
    """Read the reviewed manifests. Callers compare against these; they never rewrite them."""
    cases = [
        GoldenCase.model_validate_json(path.read_bytes())
        for path in sorted(directory.glob("*.json"))
        if path.name != "index.json"
    ]
    if not cases:
        raise ValueError("No reviewed golden manifest was found")
    return GoldenSuite(cases=tuple(cases))


def manifest_index(suite: GoldenSuite) -> dict[str, object]:
    """A short reviewable summary of the matrix; the manifests remain authoritative."""
    return {
        "schema_version": suite.schema_version,
        "generator_version": GENERATOR_VERSION,
        "cases": [
            {
                "case_key": case.case_key,
                "dimension": case.dimension.value,
                "title": case.title,
                "expected_status": case.expected_status.value,
                "artifact_status": case.expected_artifact.artifact_status,
                "human_tasks": [task.kind.value for task in case.expected_tasks],
                "unresolved": [item.id for item in case.unresolved],
                "material_digest": case.fixture.material_digest,
            }
            for case in suite.cases
        ],
    }


def manifest_documents(suite: GoldenSuite) -> dict[str, str]:
    """Serialized manifests keyed by file name, ready to be compared or written."""
    documents = {
        f"{case.case_key}.json": json.dumps(case.model_dump(mode="json"), indent=2, sort_keys=True)
        + "\n"
        for case in suite.cases
    }
    documents["index.json"] = json.dumps(manifest_index(suite), indent=2, sort_keys=True) + "\n"
    return documents
