"""Explicit offline fixtures. No source documents are opened or interpreted."""

from typing import Literal

from appraisal_review.adapters.local.fake_pdf import FakePDFWriter
from appraisal_review.application.bootstrap import ReviewAdapters
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
    RuleApplicability,
    RuleSource,
)
from appraisal_review.domain.models import EvidenceRef
from appraisal_review.domain.pdf_models import PDFField, PDFFieldMap, PDFValueRef
from appraisal_review.ports.workflow import ParsedDocument

CRITERIA_URI = "file:///synthetic/criteria.pdf"
CASE_URI = "file:///synthetic/verified.pdf"
UNRESOLVED_URI = "file:///synthetic/needs-review.pdf"


class SyntheticParser:
    async def parse_document(self, document_uri: str) -> ParsedDocument:
        if document_uri not in {CRITERIA_URI, CASE_URI, UNRESOLVED_URI}:
            raise ValueError("Only documented synthetic fixture URIs are supported")
        return ParsedDocument(document_uri=document_uri, page_count=1, content={"synthetic": True})


class SyntheticRuleProvider:
    async def load_or_build_rules(self, criteria: ParsedDocument) -> FactorRuleSet:
        if criteria.document_uri != CRITERIA_URI:
            raise ValueError("Expected synthetic criteria")
        return FactorRuleSet(
            rule_set_id="synthetic-road-v1",
            version="1.0.0",
            status="approved",
            applicability=RuleApplicability(
                jurisdiction="synthetic", land_use_category="synthetic"
            ),
            source_document=RuleSource(
                document_id="synthetic-criteria", content_hash="synthetic-not-a-document", pages=[1]
            ),
            rules=[
                FactorRule(
                    id="synthetic-road.v1",
                    factor_id="synthetic.road_width",
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


class SyntheticFactExtractor:
    async def extract_facts(self, document: ParsedDocument, *, case_id: str) -> list[FactorPair]:
        if document.document_uri not in {CASE_URI, UNRESOLVED_URI}:
            raise ValueError("Expected synthetic case")

        def observation(value: float, *, missing: bool = False) -> FactorObservation:
            return FactorObservation(
                raw_text=f"Synthetic fixture: {value} m",
                value=NormalizedValue(type="number", value=value, unit="m"),
                confidence=0.99,
                evidence=[]
                if missing
                else [
                    EvidenceRef(
                        document_id="synthetic-case",
                        source_file=document.document_uri,
                        page=1,
                        confidence=0.99,
                        bounding_box=(1, 2, 30, 40),
                        coordinate_system="pdf_bottom_left",
                    )
                ],
            )

        return [
            FactorPair(
                factor_id="synthetic.road_width",
                target=observation(10.0, missing=document.document_uri == UNRESOLVED_URI),
                comparable=observation(9.0),
            )
        ]


def synthetic_adapters() -> ReviewAdapters:
    return ReviewAdapters(
        mode="local",
        parser=SyntheticParser(),
        fact_extractor=SyntheticFactExtractor(),
        rule_provider=SyntheticRuleProvider(),
        pdf_writer=FakePDFWriter(),
    )


def synthetic_request(
    scenario: Literal["verified", "completed", "needs_review"],
) -> AgentReviewRequest:
    """Synthetic completion means the fake writer returned, not that a PDF exists."""
    if scenario not in {"verified", "completed", "needs_review"}:
        raise ValueError("Unknown synthetic scenario")
    return AgentReviewRequest(
        case_id="synthetic-case",
        criteria_document_uri=CRITERIA_URI,
        case_document_uri=UNRESOLVED_URI if scenario == "needs_review" else CASE_URI,
        output_pdf_uri=None if scenario == "verified" else "file:///synthetic/output.pdf",
        field_map=None
        if scenario == "verified"
        else PDFFieldMap(
            template_id="synthetic-form",
            fields=[
                PDFField(
                    field_id="road-rate",
                    page=1,
                    bounding_box=(1, 2, 30, 40),
                    value_ref=PDFValueRef(
                        scope="regional",
                        target_id="synthetic-target",
                        comparable_id="synthetic-comparable",
                        factor_id="synthetic.road_width",
                        value="adjustment_percent",
                    ),
                )
            ],
        ),
    )
