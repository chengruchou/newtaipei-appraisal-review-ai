"""Explicit offline fixtures. No source documents are opened or interpreted."""

from typing import Literal

from appraisal_review.adapters.local.fake_pdf import FakePDFWriter
from appraisal_review.application.bootstrap import ReviewAdapters
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
    CorrectionMatrix,
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
from appraisal_review.domain.models import EvidenceRef
from appraisal_review.domain.pdf_models import PDFField, PDFFieldMap, PDFValueRef
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

CRITERIA_URI = "file:///synthetic/criteria.pdf"
CASE_URI = "file:///synthetic/verified.pdf"
UNRESOLVED_URI = "file:///synthetic/needs-review.pdf"
UNRESOLVED_CRITERIA_URI = "file:///synthetic/needs-review-criteria.pdf"


def synthetic_material(document_uri: str = CASE_URI) -> ReviewMaterial:
    """A fixed, explicitly synthetic case with a complete one-page inventory."""
    import hashlib
    from datetime import date

    context = ComparisonContext(
        scope="regional", target_id="synthetic-target", comparable_id="synthetic-comparable"
    )
    sources = []
    for role, uri, text in [
        (
            "criteria",
            UNRESOLVED_CRITERIA_URI if document_uri == UNRESOLVED_URI else CRITERIA_URI,
            "Synthetic road threshold: 10 m",
        ),
        ("forms", document_uri, "Synthetic target 10 m; comparable 9 m; rate 5"),
    ]:
        sources.append(
            SourceDocument(
                document_id=f"synthetic-{role}",
                uri=uri,
                content_hash=hashlib.sha256(text.encode()).hexdigest(),
                version="1",
                role="criteria" if role == "criteria" else "forms",
                pages=[
                    SourcePage(
                        number=1,
                        width=100,
                        height=100,
                        crop_box=(0, 0, 100, 100),
                        has_text=True,
                        regions=[
                            SourceRegion(id="body", kind="text", bbox=(1, 2, 90, 40), text=text)
                        ],
                    )
                ],
            )
        )
    registry = SourceRegistry(documents=sources)

    def citation(index: int) -> SourceCitation:
        doc = sources[index]
        return SourceCitation(
            document_id=doc.document_id,
            content_hash=doc.content_hash,
            version=doc.version,
            page=1,
            region_id="body",
            bbox=(1, 2, 90, 40),
            excerpt=doc.pages[0].regions[0].text,
        )

    criteria_ref, form_ref = citation(0), citation(1)
    identity = CaseIdentity(
        case_id="synthetic-case",
        version="1",
        district="synthetic",
        zone="synthetic",
        land_use_category="synthetic",
        effective_date=date(2026, 9, 5),
    )
    rules = FactorRuleSet(
        rule_set_id="synthetic-road-v1",
        version="1.0.0",
        status="candidate",
        applicability=RuleApplicability(jurisdiction="synthetic", land_use_category="synthetic"),
        source_document=RuleSource(
            document_id=sources[0].document_id, content_hash=sources[0].content_hash, pages=[1]
        ),
        rules=[
            FactorRule(
                id="synthetic-road.v1",
                factor_id="synthetic.road_width",
                kind="numeric_interval",
                unit="m",
                intervals=[
                    IntervalBand(grade=Grade.INFERIOR, maximum=10),
                    IntervalBand(grade=Grade.EXCELLENT, minimum=10),
                ],
                correction_matrix=CorrectionMatrix(
                    values={
                        "inferior": {"inferior": 0, "excellent": -5},
                        "excellent": {"inferior": 5, "excellent": 0},
                    }
                ),
            )
        ],
    )
    policy = ReviewPolicy(
        identity=identity,
        registry=registry,
        rule_sets=[
            ScopedRules(
                context=context,
                rules=rules,
                source_version="1",
                zone="synthetic",
                evidence=[criteria_ref],
            )
        ],
        inventory=ReviewInventory(
            contexts=[
                InventoryContext(
                    context=context, factor_ids=["synthetic.road_width"], evidence=[form_ref]
                )
            ],
            slots=[
                ReviewSlot(
                    id="road-rate",
                    context=context,
                    factor_id="synthetic.road_width",
                    value="adjustment_percent",
                    evidence=[form_ref],
                )
            ],
            inspected_pages={d.document_id: [1] for d in sources},
        ),
    )

    def observation(value: float, missing: bool = False) -> FactorObservation:
        return FactorObservation(
            raw_text=form_ref.excerpt,
            value=NormalizedValue(type="number", value=value, unit="m"),
            confidence=0.99,
            evidence=[]
            if missing
            else [
                EvidenceRef(
                    document_id=form_ref.document_id,
                    source_file=document_uri,
                    page=1,
                    confidence=0.99,
                    bounding_box=form_ref.bbox,
                    coordinate_system="pdf_bottom_left",
                )
            ],
        )

    facts = CaseFacts(
        identity=identity,
        pairs=[
            EvidencedPair(
                context=context,
                pair=FactorPair(
                    factor_id="synthetic.road_width",
                    target=observation(10, document_uri == UNRESOLVED_URI),
                    comparable=observation(9),
                ),
                target_sources=[form_ref],
                comparable_sources=[form_ref],
                target_reliability=Reliability(
                    method="native_numeric",
                    selection="not_applicable",
                    confidence_kind="measured",
                    provenance="native_extraction",
                    producer="synthetic-native-v1",
                ),
                comparable_reliability=Reliability(
                    method="native_numeric",
                    selection="not_applicable",
                    confidence_kind="measured",
                    provenance="native_extraction",
                    producer="synthetic-native-v1",
                ),
            )
        ],
        observed=[
            ObservedValue(
                slot_id="road-rate",
                state="present",
                value="5",
                unit="percent_points",
                raw_text=form_ref.excerpt,
                evidence=[form_ref],
            )
        ],
    )
    return ReviewMaterial(policy=policy, facts=facts)


class SyntheticParser:
    async def parse_document(self, document_uri: str) -> ParsedDocument:
        if document_uri not in {CRITERIA_URI, CASE_URI, UNRESOLVED_URI, UNRESOLVED_CRITERIA_URI}:
            raise ValueError("Only documented synthetic fixture URIs are supported")
        material = synthetic_material(
            UNRESOLVED_URI
            if document_uri in {UNRESOLVED_URI, UNRESOLVED_CRITERIA_URI}
            else CASE_URI
        )
        source = next(d for d in material.policy.registry.documents if d.uri == document_uri)
        return ParsedDocument(
            document_uri=document_uri, page_count=1, source=source, content={"synthetic": True}
        )


class SyntheticRuleProvider:
    def __init__(self, case_uri: str = CASE_URI) -> None:
        self.case_uri = case_uri

    async def load_or_build_rules(self, criteria: ParsedDocument) -> ReviewPolicy:
        if criteria.document_uri not in {CRITERIA_URI, UNRESOLVED_CRITERIA_URI}:
            raise ValueError("Expected synthetic criteria")
        return synthetic_material(
            UNRESOLVED_URI if criteria.document_uri == UNRESOLVED_CRITERIA_URI else self.case_uri
        ).policy


class SyntheticFactExtractor:
    async def extract_facts(self, document: ParsedDocument, *, case_id: str) -> CaseFacts:
        if document.document_uri not in {CASE_URI, UNRESOLVED_URI} or case_id != "synthetic-case":
            raise ValueError("Expected synthetic case")
        return synthetic_material(document.document_uri).facts


class SyntheticAuthorization:
    """Fixed fixture digests; never accepts arbitrary production material."""

    def permits(self, material: ReviewMaterial) -> bool:
        return content_digest(material) in {
            content_digest(synthetic_material(uri)) for uri in (CASE_URI, UNRESOLVED_URI)
        }


def synthetic_adapters(*, case_uri: str = CASE_URI) -> ReviewAdapters:
    return ReviewAdapters(
        mode="local",
        parser=SyntheticParser(),
        fact_extractor=SyntheticFactExtractor(),
        rule_provider=SyntheticRuleProvider(case_uri),
        authorization=SyntheticAuthorization(),
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
        criteria_document_uri=UNRESOLVED_CRITERIA_URI
        if scenario == "needs_review"
        else CRITERIA_URI,
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
