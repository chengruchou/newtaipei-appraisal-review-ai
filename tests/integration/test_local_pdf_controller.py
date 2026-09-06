from __future__ import annotations

import asyncio
import hashlib
from datetime import date
from pathlib import Path
from typing import Literal

import pytest
import reportlab
from pypdf import PdfReader
from reportlab.pdfgen.canvas import Canvas

from appraisal_review.adapters.local.object_access import local_path_from_uri
from appraisal_review.adapters.local.pdf_config import PDFRenderConfig, PDFTemplatePolicy
from appraisal_review.adapters.local.pdf_writer import LocalPDFWriter
from appraisal_review.application.bootstrap import ReviewAdapters, build_controller
from appraisal_review.config import Settings
from appraisal_review.domain.document_models import (
    SourceCitation,
    SourceDocument,
    SourcePage,
    SourceRegion,
    SourceRegistry,
)
from appraisal_review.domain.factor_models import (
    AgentReviewRequest,
    AgentReviewRun,
    CaseFacts,
    CorrectionMatrix,
    EvidencedPair,
    FactorObservation,
    FactorPair,
    FactorRule,
    FactorRuleSet,
    Grade,
    IntervalBand,
    NormalizedValue,
    PDFField,
    PDFFieldMap,
    ReviewMaterial,
    ReviewPolicy,
    RuleApplicability,
    RuleSource,
    ScopedRules,
    WorkflowStatus,
)
from appraisal_review.domain.models import EvidenceRef
from appraisal_review.domain.pdf_models import (
    PDFErrorCode,
    PDFValueRef,
    PDFWriteRequest,
    PDFWriteResult,
)
from appraisal_review.domain.review_contracts import (
    CaseIdentity,
    ComparisonContext,
    InventoryContext,
    ObservedValue,
    Reliability,
    ReviewInventory,
    ReviewSlot,
    content_digest,
)
from appraisal_review.ports.workflow import ParsedDocument


class IntegrationParser:
    def __init__(self, sources: list[SourceDocument]) -> None:
        self.sources = {source.uri: source for source in sources}
        self.calls: list[str] = []

    async def parse_document(self, document_uri: str) -> ParsedDocument:
        self.calls.append(document_uri)
        source = self.sources.get(document_uri)
        if source is None:
            raise ValueError("Unexpected integration-test document")
        return ParsedDocument(
            document_uri=document_uri, page_count=len(source.pages), source=source
        )


class IntegrationRuleProvider:
    def __init__(self, rules: ReviewPolicy | FactorRuleSet) -> None:
        self.rules = rules

    async def load_or_build_rules(self, criteria: ParsedDocument) -> ReviewPolicy | FactorRuleSet:
        return self.rules


class IntegrationExtractor:
    def __init__(self, facts: CaseFacts) -> None:
        self.facts = facts
        self.calls = 0

    async def extract_facts(self, document: ParsedDocument, *, case_id: str) -> CaseFacts:
        self.calls += 1
        return self.facts


class IntegrationAuthorization:
    def __init__(self, material: ReviewMaterial) -> None:
        self.expected_digest = content_digest(material)

    def permits(self, material: ReviewMaterial) -> bool:
        return content_digest(material) == self.expected_digest


class CountingLocalWriter:
    def __init__(self, delegate: LocalPDFWriter) -> None:
        self.delegate = delegate
        self.calls: list[PDFWriteRequest] = []

    async def write_pdf(self, request: PDFWriteRequest) -> PDFWriteResult:
        self.calls.append(request)
        return await self.delegate.write_pdf(request)


def _vera_font() -> Path:
    return Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"


def _write_form(path: Path, *, occupied: bool = False) -> None:
    canvas = Canvas(str(path), pagesize=(320, 220))
    canvas.drawString(20, 190, "SYNTHETIC FORM")
    if occupied:
        canvas.drawString(175, 90, "OCCUPIED")
    canvas.save()


def _write_data_source(path: Path) -> None:
    canvas = Canvas(str(path), pagesize=(400, 260))
    canvas.drawString(20, 220, "SYNTHETIC EVALUATION BASIS")
    canvas.showPage()
    canvas.drawString(20, 220, "SYNTHETIC SOURCE DETAILS")
    canvas.save()


def _request(data_source: Path, template: Path, destination: Path) -> AgentReviewRequest:
    return AgentReviewRequest(
        case_id="integration-case",
        criteria_document_uri="file:///synthetic/integration-criteria.pdf",
        case_document_uri=data_source.as_uri(),
        pdf_template_uri=template.as_uri(),
        output_pdf_uri=destination.as_uri(),
        field_map=PDFFieldMap(
            template_id="integration-form-v1",
            fields=[
                PDFField(
                    field_id="road-adjustment",
                    page=1,
                    bounding_box=(160, 75, 290, 110),
                    operation="fill_blank",
                    value_ref=PDFValueRef(
                        scope="regional",
                        target_id="target",
                        comparable_id="comparison-1",
                        factor_id="road.width",
                        value="adjustment_percent",
                    ),
                )
            ],
        ),
    )


def _material(
    request: AgentReviewRequest,
    *,
    rule_status: Literal["candidate", "approved", "rejected"] = "approved",
    evidence: bool = True,
    factor_id: str = "road.width",
) -> ReviewMaterial:
    criteria_text = "Synthetic road-width criteria"
    forms_text = "Target 10 m; comparable 9 m; adjustment 5 percent"
    criteria_hash = hashlib.sha256(criteria_text.encode()).hexdigest()
    forms_hash = hashlib.sha256(
        local_path_from_uri(request.case_document_uri).read_bytes()
    ).hexdigest()
    criteria_region = SourceRegion(
        id="criteria-road-width",
        kind="text",
        bbox=(20, 180, 280, 230),
        text=criteria_text,
    )
    forms_region = SourceRegion(
        id="forms-road-width",
        kind="text",
        bbox=(20, 180, 360, 230),
        text=forms_text,
    )
    criteria_source = SourceDocument(
        document_id="integration-criteria",
        uri=request.criteria_document_uri,
        content_hash=criteria_hash,
        version="1",
        role="criteria",
        pages=[
            SourcePage(
                number=1,
                width=400,
                height=260,
                crop_box=(0, 0, 400, 260),
                has_text=True,
                regions=[criteria_region],
            )
        ],
    )
    forms_source = SourceDocument(
        document_id="integration-form",
        uri=request.case_document_uri,
        content_hash=forms_hash,
        version="1",
        role="forms",
        pages=[
            SourcePage(
                number=1,
                width=400,
                height=260,
                crop_box=(0, 0, 400, 260),
                has_text=True,
                regions=[forms_region],
            ),
            SourcePage(
                number=2,
                width=400,
                height=260,
                crop_box=(0, 0, 400, 260),
                has_text=True,
                regions=[],
            ),
        ],
    )

    def citation(source: SourceDocument, region: SourceRegion) -> SourceCitation:
        return SourceCitation(
            document_id=source.document_id,
            content_hash=source.content_hash,
            version=source.version,
            page=1,
            region_id=region.id,
            bbox=region.bbox,
            excerpt=region.text,
        )

    criteria_ref = citation(criteria_source, criteria_region)
    forms_ref = citation(forms_source, forms_region)
    context = ComparisonContext(
        scope="regional",
        target_id="target",
        comparable_id="comparison-1",
    )
    identity = CaseIdentity(
        case_id=request.case_id,
        version="1",
        district="synthetic",
        zone="synthetic",
        land_use_category="synthetic",
        effective_date=date(2026, 9, 6),
    )
    rules = FactorRuleSet(
        rule_set_id="integration-road-v1",
        version="1.0.0",
        status=rule_status,
        applicability=RuleApplicability(
            jurisdiction="synthetic",
            land_use_category="synthetic",
        ),
        source_document=RuleSource(
            document_id=criteria_source.document_id,
            content_hash=criteria_source.content_hash,
            pages=[1],
        ),
        rules=[
            FactorRule(
                id="integration-road.v1",
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
    policy = ReviewPolicy(
        identity=identity,
        registry=SourceRegistry(documents=[criteria_source, forms_source]),
        rule_sets=[
            ScopedRules(
                context=context,
                rules=rules,
                source_version=criteria_source.version,
                zone=identity.zone,
                evidence=[criteria_ref],
            )
        ],
        inventory=ReviewInventory(
            contexts=[
                InventoryContext(
                    context=context,
                    factor_ids=["road.width"],
                    evidence=[forms_ref],
                )
            ],
            slots=[
                ReviewSlot(
                    id="road-adjustment",
                    context=context,
                    factor_id="road.width",
                    value="adjustment_percent",
                    evidence=[forms_ref],
                )
            ],
            inspected_pages={
                criteria_source.document_id: [1],
                forms_source.document_id: [1, 2],
            },
        ),
    )

    def observation(value: float, *, include_evidence: bool = True) -> FactorObservation:
        return FactorObservation(
            raw_text=forms_text,
            value=NormalizedValue(type="number", value=value, unit="m"),
            evidence=[
                EvidenceRef(
                    document_id=forms_source.document_id,
                    source_file=forms_source.uri,
                    page=1,
                    confidence=0.99,
                    bounding_box=forms_ref.bbox,
                    coordinate_system="pdf_bottom_left",
                )
            ]
            if include_evidence
            else [],
            confidence=0.99,
        )

    facts = CaseFacts(
        identity=identity,
        pairs=[
            EvidencedPair(
                context=context,
                pair=FactorPair(
                    factor_id=factor_id,
                    target=observation(10.0, include_evidence=evidence),
                    comparable=observation(9.0),
                ),
                target_sources=[forms_ref],
                comparable_sources=[forms_ref],
                target_reliability=Reliability(
                    method="native_numeric",
                    selection="not_applicable",
                    confidence_kind="measured",
                    provenance="native_extraction",
                    producer="integration-native-v1",
                ),
                comparable_reliability=Reliability(
                    method="native_numeric",
                    selection="not_applicable",
                    confidence_kind="measured",
                    provenance="native_extraction",
                    producer="integration-native-v1",
                ),
            )
        ],
        observed=[
            ObservedValue(
                slot_id="road-adjustment",
                state="present",
                value="5",
                unit="percent_points",
                raw_text=forms_text,
                evidence=[forms_ref],
            )
        ],
    )
    return ReviewMaterial(policy=policy, facts=facts)


def _writer() -> CountingLocalWriter:
    return CountingLocalWriter(
        LocalPDFWriter(
            render_config=PDFRenderConfig(font_path=_vera_font()),
            template_policy=PDFTemplatePolicy(
                template_id="integration-form-v1",
                editable_pages=frozenset({1}),
            ),
        )
    )


def _adapters(
    request: AgentReviewRequest,
    writer: CountingLocalWriter,
    *,
    rule_status: Literal["candidate", "approved", "rejected"] = "approved",
    evidence: bool = True,
    factor_id: str = "road.width",
) -> tuple[ReviewAdapters, IntegrationParser, IntegrationExtractor]:
    material = _material(
        request,
        rule_status=rule_status,
        evidence=evidence,
        factor_id=factor_id,
    )
    parser = IntegrationParser(material.policy.registry.documents)
    extractor = IntegrationExtractor(material.facts)
    rules: ReviewPolicy | FactorRuleSet = material.policy
    if rule_status == "candidate":
        # Retain explicit coverage of the legacy candidate-rule early stop.
        rules = material.policy.rule_sets[0].rules
    return (
        ReviewAdapters(
            mode="local",
            parser=parser,
            fact_extractor=extractor,
            rule_provider=IntegrationRuleProvider(rules),
            authorization=IntegrationAuthorization(material),
            pdf_writer=writer,
        ),
        parser,
        extractor,
    )


def _run(adapters: ReviewAdapters, request: AgentReviewRequest) -> AgentReviewRun:
    controller = build_controller(Settings(_env_file=None), adapters=adapters)
    return asyncio.run(controller.review(request))


def test_real_local_writer_completes_through_composition_root(tmp_path: Path) -> None:
    data_source = tmp_path / "evaluation-basis.pdf"
    template = tmp_path / "form-template.pdf"
    destination = tmp_path / "form-completed.pdf"
    _write_data_source(data_source)
    _write_form(template)
    data_source_hash = hashlib.sha256(data_source.read_bytes()).digest()
    template_hash = hashlib.sha256(template.read_bytes()).digest()
    request = _request(data_source, template, destination)
    writer = _writer()
    adapters, parser, _ = _adapters(request, writer)

    result = _run(adapters, request)

    assert result.status is WorkflowStatus.COMPLETED
    assert len(writer.calls) == 1
    assert writer.calls[0].result == result.review
    assert writer.calls[0].source_uri == template.as_uri()
    assert writer.calls[0].source_uri != data_source.as_uri()
    assert parser.calls == [request.criteria_document_uri, data_source.as_uri()]
    assert result.output_pdf_uri == destination.as_uri()
    assert result.pdf_result is not None
    assert result.pdf_result.output_uri == destination.as_uri()
    assert result.pdf_result.page_count == 1
    assert result.pdf_result.written_field_ids == ["road-adjustment"]
    assert result.pdf_result.warnings == []
    assert destination.is_file()
    output = PdfReader(destination)
    assert len(output.pages) == 1
    assert "SYNTHETIC FORM" in output.pages[0].extract_text()
    assert "+5.00%" in output.pages[0].extract_text()
    assert hashlib.sha256(data_source.read_bytes()).digest() == data_source_hash
    assert hashlib.sha256(template.read_bytes()).digest() == template_hash


@pytest.mark.parametrize(
    ("rule_status", "evidence", "factor_id", "expected_status", "case_is_parsed"),
    [
        ("candidate", True, "road.width", WorkflowStatus.NEEDS_REVIEW, False),
        ("approved", False, "road.width", WorkflowStatus.NEEDS_REVIEW, True),
        ("approved", True, "unknown.factor", WorkflowStatus.FAILED, True),
    ],
)
def test_review_gates_make_zero_real_writer_calls(
    tmp_path: Path,
    rule_status: Literal["candidate", "approved", "rejected"],
    evidence: bool,
    factor_id: str,
    expected_status: WorkflowStatus,
    case_is_parsed: bool,
) -> None:
    data_source = tmp_path / "evaluation-basis.pdf"
    template = tmp_path / "form-template.pdf"
    destination = tmp_path / "form-completed.pdf"
    _write_data_source(data_source)
    _write_form(template)
    request = _request(data_source, template, destination)
    writer = _writer()
    adapters, parser, extractor = _adapters(
        request,
        writer,
        rule_status=rule_status,
        evidence=evidence,
        factor_id=factor_id,
    )

    result = _run(adapters, request)

    assert result.status is expected_status
    assert writer.calls == []
    assert result.output_pdf_uri is None
    assert not destination.exists()
    assert (request.case_document_uri in parser.calls) is case_is_parsed
    assert extractor.calls == (1 if case_is_parsed else 0)


def test_real_writer_error_retains_review_and_publishes_nothing(tmp_path: Path) -> None:
    data_source = tmp_path / "evaluation-basis.pdf"
    template = tmp_path / "occupied-form.pdf"
    destination = tmp_path / "form-completed.pdf"
    _write_data_source(data_source)
    _write_form(template, occupied=True)
    request = _request(data_source, template, destination)
    writer = _writer()
    adapters, _, _ = _adapters(request, writer)

    result = _run(adapters, request)

    assert len(writer.calls) == 1
    assert result.status is WorkflowStatus.FAILED
    assert result.review is not None
    assert result.review.results[0].adjustment_percent == 5.0
    assert result.verification is not None and result.verification.can_complete
    assert result.pdf_error is not None
    assert result.pdf_error.code is PDFErrorCode.PLACEMENT
    assert result.output_pdf_uri is None
    assert result.pdf_result is None
    assert not destination.exists()
    assert not list(tmp_path.glob(".form-completed.pdf.*.tmp"))
