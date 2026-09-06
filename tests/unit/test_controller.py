import asyncio

from appraisal_review.application.controller import ReviewAgentController
from appraisal_review.domain.factor_models import (
    AgentReviewRequest,
    CorrectionMatrix,
    FactorObservation,
    FactorPair,
    FactorRule,
    FactorRuleSet,
    Grade,
    IntervalBand,
    NormalizedValue,
    PDFFieldMap,
    RuleApplicability,
    RuleSource,
    WorkflowStatus,
)
from appraisal_review.domain.models import EvidenceRef
from appraisal_review.domain.pdf_models import PDFWriteRequest, PDFWriteResult
from appraisal_review.ports.workflow import ParsedDocument


def rule_set(status: str = "approved") -> FactorRuleSet:
    return FactorRuleSet(
        rule_set_id="portable-v1",
        version="1.0.0",
        status=status,
        applicability=RuleApplicability(
            jurisdiction="example",
            land_use_category="commercial",
        ),
        source_document=RuleSource(document_id="criteria", content_hash="sha256:test"),
        rules=[
            FactorRule(
                id="road.v1",
                factor_id="road.width",
                kind="numeric_interval",
                unit="m",
                intervals=[
                    IntervalBand(grade=Grade.INFERIOR, maximum=10.0),
                    IntervalBand(grade=Grade.EXCELLENT, minimum=10.0),
                ],
                correction_matrix=CorrectionMatrix(
                    values={
                        "inferior": {"inferior": 0.0, "excellent": -5.0},
                        "excellent": {"inferior": 5.0, "excellent": 0.0},
                    }
                ),
            )
        ],
    )


def observation(value: float, *, with_evidence: bool = True) -> FactorObservation:
    evidence = [EvidenceRef(document_id="case", page=1, confidence=0.99)] if with_evidence else []
    return FactorObservation(
        value=NormalizedValue(type="number", value=value, unit="m"),
        evidence=evidence,
        confidence=0.99,
    )


class Parser:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def parse_document(self, document_uri: str) -> ParsedDocument:
        self.calls.append(document_uri)
        return ParsedDocument(document_uri=document_uri, page_count=1)


class RuleProvider:
    def __init__(self, rules: FactorRuleSet) -> None:
        self.rules = rules

    async def load_or_build_rules(self, criteria: ParsedDocument) -> FactorRuleSet:
        return self.rules


class Extractor:
    def __init__(self, *, with_evidence: bool = True) -> None:
        self.with_evidence = with_evidence
        self.called = False

    async def extract_facts(self, document: ParsedDocument, *, case_id: str) -> list[FactorPair]:
        self.called = True
        return [
            FactorPair(
                factor_id="road.width",
                target=observation(10.0, with_evidence=self.with_evidence),
                comparable=observation(9.0),
            )
        ]


class Writer:
    def __init__(self) -> None:
        self.called = False
        self.request: PDFWriteRequest | None = None

    async def write_pdf(self, request: PDFWriteRequest) -> PDFWriteResult:
        self.called = True
        self.request = request
        return PDFWriteResult(
            output_uri=request.destination_uri,
            page_count=1,
            written_field_ids=[field.field_id for field in request.field_map.fields],
        )


def test_candidate_rules_stop_before_case_extraction() -> None:
    parser = Parser()
    extractor = Extractor()
    controller = ReviewAgentController(
        parser=parser,
        fact_extractor=extractor,
        rule_provider=RuleProvider(rule_set("candidate")),
    )

    result = asyncio.run(
        controller.review(
            AgentReviewRequest(
                case_id="case-1",
                criteria_document_uri="criteria.pdf",
                case_document_uri="file:///synthetic/case.pdf",
            )
        )
    )

    assert result.status is WorkflowStatus.NEEDS_REVIEW
    assert parser.calls == ["criteria.pdf"]
    assert not extractor.called


def test_missing_evidence_blocks_pdf_writing() -> None:
    writer = Writer()
    controller = ReviewAgentController(
        parser=Parser(),
        fact_extractor=Extractor(with_evidence=False),
        rule_provider=RuleProvider(rule_set()),
        pdf_writer=writer,
    )
    request = AgentReviewRequest(
        case_id="case-1",
        criteria_document_uri="criteria.pdf",
        case_document_uri="file:///synthetic/case.pdf",
        pdf_template_uri="file:///synthetic/template.pdf",
        output_pdf_uri="file:///synthetic/output.pdf",
        field_map=PDFFieldMap(template_id="v1", fields=[]),
    )

    result = asyncio.run(controller.review(request))

    assert result.status is WorkflowStatus.NEEDS_REVIEW
    assert not writer.called


def test_verified_case_can_reach_completed_pdf_state() -> None:
    from dataclasses import replace

    from appraisal_review.adapters.local.synthetic import synthetic_adapters, synthetic_request
    from appraisal_review.application.bootstrap import build_controller
    from appraisal_review.config import Settings

    writer = Writer()
    controller = build_controller(
        Settings(_env_file=None), adapters=replace(synthetic_adapters(), pdf_writer=writer)
    )
    result = asyncio.run(controller.review(synthetic_request("completed")))
    assert result.status is WorkflowStatus.COMPLETED
    assert result.output_pdf_uri == "file:///synthetic/output.pdf"
    assert writer.called
    assert result.artifact_status == "written"
    assert result.case_review.status.value == "verified"
    assert writer.request is not None
    assert writer.request.source_uri == "file:///synthetic/form-template.pdf"
    assert [event.sequence for event in result.audit_events] == list(
        range(1, len(result.audit_events) + 1)
    )


def test_legacy_calculation_keeps_the_same_runtime_confidence_semantics():
    from unittest.mock import AsyncMock

    for threshold, expected in [(0.85, "verified"), (0.95, "needs_review")]:
        pair = FactorPair(factor_id="road.width", target=observation(10), comparable=observation(9))
        pair.target.confidence = pair.comparable.confidence = 0.90
        writer = Writer()
        controller = ReviewAgentController(
            parser=Parser(),
            rule_provider=RuleProvider(rule_set()),
            fact_extractor=AsyncMock(extract_facts=AsyncMock(return_value=[pair])),
            minimum_confidence=threshold,
            pdf_writer=writer,
        )
        run = asyncio.run(
            controller.review(
                AgentReviewRequest(
                    case_id="case-1",
                    criteria_document_uri="criteria.pdf",
                    case_document_uri="file:///synthetic/case.pdf",
                )
            )
        )
        assert run.review.summary.status.value == expected
        # Factor-only adapters still lack the complete case authorization boundary.
        assert run.status is WorkflowStatus.NEEDS_REVIEW
        assert not run.verification.can_complete and not writer.called
