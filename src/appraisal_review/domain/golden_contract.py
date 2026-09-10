"""Full-case golden expectations authored from sources, never captured from engine output.

Every expected value declares how a reviewer derived it: a citation into the fixture,
a deterministic rule or arithmetic derivation, or a recorded human adjudication. A
disputed adjudication stays unresolved instead of being guessed into a passing value.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from appraisal_review.domain.document_models import Digest, DocumentModel, SourceCitation
from appraisal_review.domain.factor_models import ArtifactStatus, EvaluationStatus, Grade
from appraisal_review.domain.review_contracts import CaseIdentity, ComparisonContext
from appraisal_review.domain.service_contracts import (
    Permission,
    ResponseAction,
    TaskKind,
    VerificationDiagnostic,
)

SlotValue = Literal["target_grade", "comparable_grade", "adjustment_percent", "subtotal", "total"]
ObservedState = Literal["present", "blank", "missing", "not_present", "not_applicable"]
FindingStatus = Literal["verified", "needs_review", "failed"]
DocumentRole = Literal["criteria", "forms", "reference", "brief"]


class GoldenModel(DocumentModel):
    """Golden manifests are data contracts; unknown keys are a review failure."""


class GoldenDimension(StrEnum):
    NORMAL = "normal"
    MISSING_DATA = "missing_data"
    MISSING_PAGE = "missing_page"
    CONFLICTING_SOURCES = "conflicting_sources"
    ZERO_CONFIDENCE = "zero_confidence"
    UNSUPPORTED_RULE = "unsupported_rule"
    MULTIPLE_CONTEXTS = "multiple_contexts"
    UNAPPROVED_MATERIAL = "unapproved_material"
    REVISION_CHAIN = "revision_chain"


class ExpectationBasis(StrEnum):
    """Why a reviewer believes an expected value, never "because the engine said so"."""

    CLASSIFICATION = "rule_classification"
    CORRECTION = "rule_correction"
    SUMMARY = "rule_summary"
    ARITHMETIC = "arithmetic_derivation"
    ADJUDICATION = "human_adjudication"


class DataUseStatement(GoldenModel):
    origin: Literal["synthetic"] = "synthetic"
    generator: str = Field(min_length=1)
    contains_real_case_data: Literal[False] = False
    statement: str = Field(min_length=1)


class DocumentProvenance(GoldenModel):
    document_id: str = Field(min_length=1)
    role: DocumentRole
    version: str = Field(min_length=1)
    content_hash: Digest
    page_count: int = Field(ge=1)


class FixtureProvenance(GoldenModel):
    """Binds a manifest to the exact synthetic material it was authored against."""

    generator_version: str = Field(min_length=1)
    material_digest: Digest
    documents: tuple[DocumentProvenance, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_documents(self) -> FixtureProvenance:
        if len({d.document_id for d in self.documents}) != len(self.documents):
            raise ValueError("Duplicate fixture document")
        return self


class GradeDerivation(GoldenModel):
    """Reviewer-side classification: this measurement falls in this rule band."""

    rule_set_id: str = Field(min_length=1)
    rule_id: str = Field(min_length=1)
    side: Literal["target", "comparable"]
    measurement: Decimal
    unit: str | None = None
    grade: Grade


class CorrectionDerivation(GoldenModel):
    """Reviewer-side matrix lookup, recomputed from the fixture rule set."""

    rule_set_id: str = Field(min_length=1)
    rule_id: str = Field(min_length=1)
    target_grade: Grade
    comparable_grade: Grade


class SummaryDerivation(GoldenModel):
    """A context total re-derived from every inventoried factor, not from printed text.

    The reviewer names only the rule set. Both grades of every factor are re-classified
    from the fixture measurements, so nothing about the expected total is self-asserted.
    """

    rule_set_id: str = Field(min_length=1)


class ArithmeticDerivation(GoldenModel):
    """Reviewer-side arithmetic over other expected observations, not over results."""

    operation: Literal["sum", "equals"]
    input_slot_ids: tuple[str, ...] = Field(min_length=1)
    quantum: Decimal = Field(default=Decimal("0.01"), gt=0)

    @model_validator(mode="after")
    def matching_inputs(self) -> ArithmeticDerivation:
        if len(set(self.input_slot_ids)) != len(self.input_slot_ids):
            raise ValueError("Duplicate arithmetic input")
        if self.operation == "equals" and len(self.input_slot_ids) != 1:
            raise ValueError("equals requires exactly one input")
        return self


class ObservedExpectation(GoldenModel):
    """What the synthetic form actually shows, with the citation that proves it."""

    state: ObservedState
    value: str | None = None
    unit: Literal["percent_points", "ratio", "grade"] | None = None
    raw_text: str = ""
    citation: SourceCitation | None = None

    @model_validator(mode="after")
    def cited_state(self) -> ObservedExpectation:
        if (self.state == "present") != (self.value is not None):
            raise ValueError("Only present observations carry a value, including zero")
        if self.state in {"present", "blank"} and self.citation is None:
            raise ValueError("A present or blank observation must cite the fixture")
        if (self.value is not None) != (self.unit is not None):
            raise ValueError("A present observation declares its unit")
        return self


class IndependentExpectation(GoldenModel):
    """The reviewer's own derivation of the value the field should hold."""

    basis: ExpectationBasis
    value: str
    classification: GradeDerivation | None = None
    correction: CorrectionDerivation | None = None
    summary: SummaryDerivation | None = None
    arithmetic: ArithmeticDerivation | None = None
    adjudication_id: str | None = None
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def matching_derivation(self) -> IndependentExpectation:
        supplied = {
            ExpectationBasis.CLASSIFICATION: self.classification is not None,
            ExpectationBasis.CORRECTION: self.correction is not None,
            ExpectationBasis.SUMMARY: self.summary is not None,
            ExpectationBasis.ARITHMETIC: self.arithmetic is not None,
            ExpectationBasis.ADJUDICATION: self.adjudication_id is not None,
        }
        if not supplied[self.basis] or sum(supplied.values()) != 1:
            raise ValueError("Exactly the declared derivation must be supplied")
        return self


class ExpectedSlot(GoldenModel):
    """One reviewed inventory field: what is printed, what it should be, and why."""

    slot_id: str = Field(min_length=1)
    slot_value: SlotValue
    context: ComparisonContext
    factor_id: str | None = None
    derivable_blank: bool = False
    observed: ObservedExpectation
    independent: IndependentExpectation | None = None
    expected_status: FindingStatus
    expected_kind: str = Field(min_length=1)
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def grounded_expectation(self) -> ExpectedSlot:
        factor_bound = self.slot_value in {"target_grade", "comparable_grade", "adjustment_percent"}
        if factor_bound != (self.factor_id is not None):
            raise ValueError("Factor-bound slot values require a factor binding")
        if self.expected_status == "verified" and self.independent is None:
            raise ValueError("A verified field needs an independent expected value")
        # A blank the system must leave alone is the important case for a form writer, so
        # derivation authority is what needs a blank, not the other way round.
        if self.derivable_blank and self.observed.state != "blank":
            raise ValueError("Only a blank field can carry derivation authority")
        if (
            self.observed.state == "blank"
            and not self.derivable_blank
            and self.expected_status == "verified"
        ):
            raise ValueError("An unapproved blank cannot be filled, so it cannot verify")
        return self


class ExpectedFinding(GoldenModel):
    id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    status: FindingStatus
    rationale: str = Field(min_length=1)


class ExpectedCoverage(GoldenModel):
    missing: tuple[str, ...] = ()
    unsupported: tuple[str, ...] = ()


class ExpectedVerification(GoldenModel):
    """Deterministic gate outcome plus the sanitized service diagnostics it publishes."""

    status: EvaluationStatus
    critical: tuple[VerificationDiagnostic, ...] = ()
    warnings: tuple[VerificationDiagnostic, ...] = ()
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def matching_status(self) -> ExpectedVerification:
        if (self.status is EvaluationStatus.VERIFIED) and (self.critical or self.warnings):
            raise ValueError("A verified gate publishes no blocking diagnostic")
        return self


class ExpectedTaskSide(GoldenModel):
    context: ComparisonContext
    factor_id: str = Field(min_length=1)
    side: Literal["target", "comparable"]


class ExpectedHumanTask(GoldenModel):
    """The human decision this case must raise; the producer is out of B1's scope."""

    task_ref: str = Field(min_length=1)
    kind: TaskKind
    required_permission: Permission
    allowed_responses: tuple[ResponseAction, ...] = Field(min_length=1)
    finding_ids: tuple[str, ...] = Field(min_length=1)
    side: ExpectedTaskSide | None = None
    result_digest: Digest | None = None
    question: str = Field(min_length=1)
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def unique_findings(self) -> ExpectedHumanTask:
        if len(set(self.finding_ids)) != len(self.finding_ids):
            raise ValueError("Duplicate blocking finding")
        if len(set(self.allowed_responses)) != len(self.allowed_responses):
            raise ValueError("Duplicate allowed response")
        return self


class ExpectedArtifact(GoldenModel):
    artifact_status: ArtifactStatus
    field_ids: tuple[str, ...] = ()
    context: ComparisonContext | None = None
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def written_needs_fields(self) -> ExpectedArtifact:
        placed = self.artifact_status in {"written", "simulated"}
        if placed != bool(self.field_ids) or placed != (self.context is not None):
            raise ValueError("A placed artifact declares its context and exact field coverage")
        if len(set(self.field_ids)) != len(self.field_ids):
            raise ValueError("Duplicate artifact field")
        return self


class ExpectedRuleState(GoldenModel):
    """Candidate extraction and formal approval are separate facts about one rule set."""

    rule_set_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    authored_status: Literal["candidate", "approved", "rejected"]
    material_authority: Literal["exact_material_approved", "not_approved"]
    business_approval: Literal["pending", "granted"]
    rationale: str = Field(min_length=1)


class AdjudicationRecord(GoldenModel):
    """Two-reviewer record. A dispute is recorded, never resolved by the fixture author."""

    record_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    reviewers: tuple[str, ...] = Field(min_length=2)
    adjudicated_on: date
    outcome: Literal["agreed", "disputed"]
    decision: str | None = None
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def recorded_outcome(self) -> AdjudicationRecord:
        if len(set(self.reviewers)) != len(self.reviewers):
            raise ValueError("A second reviewer must be a different person")
        if (self.outcome == "agreed") != (self.decision is not None):
            raise ValueError("Only an agreed adjudication carries a decision")
        return self


class UnresolvedItem(GoldenModel):
    id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    adjudication_id: str | None = None


class GoldenCase(GoldenModel):
    """One full-case acceptance contract shared by review, verification and writing."""

    schema_version: Literal["golden-1"] = "golden-1"
    case_key: str = Field(min_length=1)
    dimension: GoldenDimension
    title: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    data_use: DataUseStatement
    fixture: FixtureProvenance
    identity: CaseIdentity
    parent_case_key: str | None = None
    material_authority: Literal["exact_material_approved", "not_approved"]
    expected_status: EvaluationStatus
    expected_slots: tuple[ExpectedSlot, ...] = ()
    expected_findings: tuple[ExpectedFinding, ...] = Field(min_length=1)
    expected_coverage: ExpectedCoverage = ExpectedCoverage()
    expected_verification: ExpectedVerification
    expected_tasks: tuple[ExpectedHumanTask, ...] = ()
    expected_artifact: ExpectedArtifact
    expected_rules: tuple[ExpectedRuleState, ...] = Field(min_length=1)
    adjudications: tuple[AdjudicationRecord, ...] = ()
    unresolved: tuple[UnresolvedItem, ...] = ()

    @model_validator(mode="after")
    def internally_consistent(self) -> GoldenCase:
        self._unique_identities()
        findings: dict[str, list[ExpectedFinding]] = {}
        for finding in self.expected_findings:
            findings.setdefault(finding.id, []).append(finding)
        for slot in self.expected_slots:
            reported = findings.get(f"observed/{slot.slot_id}", [])
            if not any(
                (finding.status, finding.kind) == (slot.expected_status, slot.expected_kind)
                for finding in reported
            ):
                raise ValueError(f"Slot {slot.slot_id} disagrees with its expected finding")
        for task in self.expected_tasks:
            for id in task.finding_ids:
                blocking = findings.get(id, [])
                if not blocking:
                    raise ValueError(f"Task {task.task_ref} cites an unexpected finding")
                if all(finding.status == "verified" for finding in blocking):
                    raise ValueError(f"Task {task.task_ref} cannot block on a verified finding")
        self._recorded_disputes()
        self._declared_status()
        return self

    def _unique_identities(self) -> None:
        groups: list[list[str]] = [
            [slot.slot_id for slot in self.expected_slots],
            [task.task_ref for task in self.expected_tasks],
            [record.record_id for record in self.adjudications],
            [item.id for item in self.unresolved],
            [rule.rule_set_id for rule in self.expected_rules],
        ]
        if any(len(values) != len(set(values)) for values in groups):
            raise ValueError("Duplicate golden identity")

    def _recorded_disputes(self) -> None:
        records = {record.record_id: record for record in self.adjudications}
        referenced = {item.adjudication_id for item in self.unresolved if item.adjudication_id}
        cited = {
            slot.independent.adjudication_id
            for slot in self.expected_slots
            if slot.independent is not None and slot.independent.adjudication_id
        }
        if not (referenced | cited) <= set(records):
            raise ValueError("An unrecorded adjudication was referenced")
        disputed = {id for id, record in records.items() if record.outcome == "disputed"}
        if disputed & cited:
            raise ValueError("A disputed question cannot ground an expected value")
        if not disputed <= referenced:
            raise ValueError("A disputed question must stay explicitly unresolved")

    def _declared_status(self) -> None:
        statuses = {finding.status for finding in self.expected_findings}
        expected = (
            EvaluationStatus.FAILED
            if "failed" in statuses
            else EvaluationStatus.NEEDS_REVIEW
            if statuses != {"verified"} or self.expected_coverage.missing
            else EvaluationStatus.VERIFIED
        )
        if self.expected_status is not expected:
            raise ValueError("Declared case status disagrees with the expected findings")
        if self.expected_artifact.artifact_status == "written" and (
            self.expected_status is not EvaluationStatus.VERIFIED
        ):
            raise ValueError("Only a verified case can declare a written artifact")


class GoldenSuite(GoldenModel):
    """The whole reviewed matrix; CI reads this, live evaluation never rewrites it."""

    schema_version: Literal["golden-1"] = "golden-1"
    cases: tuple[GoldenCase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_cases(self) -> GoldenSuite:
        keys = [case.case_key for case in self.cases]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate golden case")
        for case in self.cases:
            if case.parent_case_key is not None and case.parent_case_key not in keys:
                raise ValueError("Unknown parent case")
        return self
