"""Synthetic page-model injection over real sanitized C2 raster bytes.

Source authoring is local-only. Extraction receives no originals, privacy maps,
native source citations or approvals. Model responses below are explicit fixture
inputs, not measured OCR/model accuracy. Rules and facts remain candidates.
"""

from __future__ import annotations

import json
import math
import struct
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from integration_fixture import (
    BUNDLED_CJK_SHA256,
    FONT_NAME,
    IntegrationFixture,
    _bundled_font,
    _write_pdf,
)
from reportlab.pdfgen.canvas import Canvas

from appraisal_review.adapters.aws.document_extraction import (
    BedrockDocumentExtractor,
    ExtractionConfig,
)
from appraisal_review.adapters.local.document_manifest import InputManifest, InputSpec
from appraisal_review.adapters.local.document_storage import SQLiteDocumentStorage
from appraisal_review.adapters.local.pdf_config import (
    PDFRenderConfig,
    PDFTemplatePolicy,
    field_map_sha256,
)
from appraisal_review.adapters.local.pdf_parser import DocumentInput, LocalPDFParser
from appraisal_review.adapters.local.png_validation import validate_png
from appraisal_review.adapters.local.service import (
    LocalServiceConfiguration,
    LocalWriterConfiguration,
)
from appraisal_review.adapters.local.snapshot_renderer import SnapshotPDFRenderer
from appraisal_review.application.document_review import assemble
from appraisal_review.application.document_transfer import DocumentTransferService
from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.application.service_guards import Principal
from appraisal_review.document_cli import private_json
from appraisal_review.domain.document_models import SourceCitation, SourceDocument, SourceRegistry
from appraisal_review.domain.document_transfer import (
    DocumentMetadata,
    canonical_bytes,
    digest_bytes,
)
from appraisal_review.domain.extraction_contracts import SanitizedSourceReference
from appraisal_review.domain.extraction_models import PageExtraction, PageProposal, ProposedRule
from appraisal_review.domain.factor_models import (
    AgentReviewRequest,
    CorrectionMatrix,
    EvidencedPair,
    FactorObservation,
    FactorPair,
    FactorRule,
    Grade,
    IntervalBand,
    NormalizedValue,
    ReviewMaterial,
)
from appraisal_review.domain.pdf_models import PDFField, PDFFieldMap, PDFValueRef
from appraisal_review.domain.privacy_models import (
    PrivacyRegion,
    SensitiveCandidate,
    SensitiveCategory,
)
from appraisal_review.domain.review_contracts import (
    CaseIdentity,
    ComparisonContext,
    InventoryContext,
    ObservedValue,
    Reliability,
    ReviewSlot,
    content_digest,
)
from appraisal_review.domain.service_contracts import DocumentReference, Permission, RunReference
from appraisal_review.ports.document_extraction import AuthorizedSanitizedSnapshot

PageModel = Callable[[SourceDocument, int, bytes], PageProposal]


@dataclass(frozen=True)
class PrivacySyntheticSource:
    purpose: Literal["criteria", "forms"]
    path: Path
    candidates: tuple[SensitiveCandidate, ...]


def write_privacy_sources(directory: Path) -> tuple[PrivacySyntheticSource, ...]:
    """Author fixed CJK sources in an existing empty local directory, no grants.

    Every page has one synthetic name candidate. The bottom region
    (30, 10, 250, 45) is deliberately blank for an additional browser selection.
    Original candidate geometry is for local redaction only, never extraction.
    """
    directory = directory.resolve()
    if not directory.is_dir() or any(directory.iterdir()):
        raise ValueError("Synthetic privacy sources require an existing empty directory")
    _bundled_font()
    directory.chmod(0o700)
    authored: tuple[tuple[Literal["criteria", "forms"], tuple[tuple[str, ...], ...]], ...] = (
        (
            "criteria",
            (
                (
                    "合成測試規則 - 僅供本機驗收",
                    "道路寬度未滿 10 公尺為劣",
                    "道路寬度達 10 公尺為優",
                    "標的優對劣修正率 5 百分點",
                    "標的劣對優修正率 -5 百分點",
                    "同等級修正率 0 百分點",
                ),
            ),
        ),
        (
            "forms",
            tuple(
                (
                    f"合成測試估價表 - 比較案例 {page}",
                    f"標的道路寬度 {target} 公尺",
                    f"比較道路寬度 {comparable} 公尺",
                    f"填報修正率 {rate} 百分點",
                )
                for page, target, comparable, rate in ((1, 10, 9, 5), (2, 9, 10, -5))
            ),
        ),
    )
    result = []
    for purpose, pages in authored:
        canaries = tuple(f"合成機敏姓名-{purpose}-{page}" for page in range(1, len(pages) + 1))
        path = directory / f"synthetic-{purpose}.pdf"
        pages = tuple(
            (*lines, canary, f"SYNTHETIC SOURCE {uuid4()}")
            for lines, canary in zip(pages, canaries, strict=True)
        )
        if purpose == "criteria":
            _write_pdf(path, pages)
        else:
            canvas = Canvas(str(path), pagesize=(595, 420), invariant=1)
            canvas.setAuthor("")
            canvas.setCreator("")
            canvas.setTitle("Synthetic raster review form")
            for lines in pages:
                canvas.setFont(FONT_NAME, 13)
                for line, y in zip(lines, (375, 335, 295, 255, 80, 55), strict=True):
                    canvas.drawString(30, y, line)
                canvas.setFont("Helvetica", 9)
                for label, y in (
                    ("Target grade", 270),
                    ("Comparable grade", 230),
                    ("Road adjustment", 190),
                    ("Total adjustment", 120),
                ):
                    canvas.drawString(490, y + 12, label)
                    canvas.rect(300, y, 180, 35)
                canvas.showPage()
            canvas.save()
        content = path.read_bytes()
        parsed = LocalPDFParser([]).parse_bytes(
            content,
            DocumentInput(
                path=path,
                document_id=str(uuid4()),
                version="1",
                role=purpose,
                expected_hash=digest_bytes(content),
            ),
            uri=path.as_uri(),
        )
        if parsed.source is None:
            raise ValueError("Synthetic source parser produced no registry")
        candidates = []
        for page, canary in enumerate(canaries, 1):
            regions = [
                r
                for r in parsed.source.pages[page - 1].regions
                if r.kind == "text" and canary in r.text
            ]
            if len(regions) != 1:
                raise ValueError("Synthetic privacy canary must have one real text region")
            x0, y0, x1, y1 = regions[0].bbox
            candidates.append(
                SensitiveCandidate(
                    candidate_id=uuid4(),
                    category=SensitiveCategory.NAME,
                    region=PrivacyRegion(page=page, bbox=(x0 - 4, y0 - 4, x1 + 4, y1 + 4)),
                    raw_text=canary,
                    crop_id=uuid4(),
                    confidence=0.0,
                    detector_id="synthetic-authored-candidate",
                    detector_version="1",
                )
            )
        result.append(PrivacySyntheticSource(purpose, path, tuple(candidates)))
    return tuple(result)


def _image_citation(source: SourceDocument, page: int) -> SourceCitation:
    geometry = source.pages[page - 1]
    regions = [r for r in geometry.regions if r.id == f"p{page}-image" and r.kind == "image"]
    if geometry.has_text or any(r.text for r in geometry.regions) or len(regions) != 1:
        raise ValueError("Expected sanitized image-only page, not original native evidence")
    region = regions[0]
    if region.text or region.bbox != (0, 0, geometry.width, geometry.height):
        raise ValueError("Raster image region does not match actual page geometry")
    return SourceCitation(
        document_id=source.document_id,
        version=source.version,
        content_hash=source.content_hash,
        page=page,
        region_id=region.id,
        bbox=region.bbox,
        excerpt="",
    )


def authored_raster_proposal(source: SourceDocument, page: int, png: bytes) -> PageProposal:
    """Explicit injected fixture response. No actual OCR or model accuracy claim.

    The observations are fixed authored test data. Localization comes ONLY from
    the newly parsed sanitized image page; no text is added to that registry.
    """
    ref = _image_citation(source, page)
    if source.role == "criteria" and page == 1:
        return PageProposal(
            rules=[
                ProposedRule(
                    scope="regional",
                    evidence=[ref],
                    rule=FactorRule(
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
                    ),
                )
            ]
        )
    if source.role != "forms" or page not in (1, 2):
        raise ValueError("No authored synthetic model response for this page")
    target, comparable, rate = (10, 9, 5) if page == 1 else (9, 10, -5)
    context = ComparisonContext(
        scope="regional", target_id="synthetic-target", comparable_id=f"synthetic-comp-{page}"
    )
    slot = ReviewSlot(
        id=f"c{page}-rate",
        context=context,
        factor_id="synthetic.road_width",
        value="adjustment_percent",
        evidence=[ref],
    )

    def observation(value: int, label: str) -> FactorObservation:
        return FactorObservation(
            raw_text=f"{label}道路寬度 {value} 公尺",
            value=NormalizedValue(type="number", value=float(value), unit="m"),
            confidence=0.0,
            evidence=[],
        )

    return PageProposal(
        contexts=[
            InventoryContext(context=context, factor_ids=["synthetic.road_width"], evidence=[ref])
        ],
        pairs=[
            EvidencedPair(
                context=context,
                pair=FactorPair(
                    factor_id="synthetic.road_width",
                    target=observation(target, "標的"),
                    comparable=observation(comparable, "比較"),
                ),
                target_sources=[ref],
                comparable_sources=[ref],
                target_reliability=Reliability(method="model_proposed", selection="not_applicable"),
                comparable_reliability=Reliability(
                    method="model_proposed", selection="not_applicable"
                ),
            )
        ],
        slots=[slot],
        observed=[
            ObservedValue(
                slot_id=slot.id,
                state="present",
                value=str(rate),
                unit="percent_points",
                raw_text=f"填報修正率 {rate} 百分點",
                evidence=[ref],
            )
        ],
    )


class _InjectedPageClient:
    """Local page-model injection through the real Converse response validator.

    Zero usage is declared synthetic test telemetry for this non-tokenizing
    callback, not a measurement of a provider or an unknown usage replacement.
    """

    def __init__(self, source: SourceDocument, page: int, png: bytes, proposals: PageModel) -> None:
        self.source, self.page, self.png, self.proposals = source, page, png, proposals

    def converse(self, **kwargs: Any) -> dict[str, Any]:
        content = kwargs["messages"][0]["content"]
        payload = json.loads(content[0]["text"])
        if (
            payload["source_data"]["document_id"] != self.source.document_id
            or payload["source_data"]["content_hash"] != self.source.content_hash
            or payload["source_data"]["page"]
            != self.source.pages[self.page - 1].model_dump(mode="json")
            or content[1]["image"]["source"]["bytes"] != self.png
        ):
            raise ValueError("Injected page request does not bind actual rendered source")
        proposal = self.proposals(self.source.model_copy(deep=True), self.page, self.png)
        return {
            "stopReason": "end_turn",
            "output": {"message": {"content": [{"text": proposal.model_dump_json()}]}},
            "usage": {"inputTokens": 0, "outputTokens": 0},
        }


async def prepare_sanitized_synthetic_material(
    documents: DocumentTransferService,
    principal: Principal,
    identity: CaseIdentity,
    criteria_ref: DocumentReference,
    forms_ref: DocumentReference,
    proposals: PageModel,
) -> ReviewMaterial:
    """Prepare candidates from actual admitted raster sources, before job admission.

    C2 pre-run read authorization is checked before parsing, rendering, callback
    execution, and after every page. No original bytes or locations are accepted.
    The private extractor regression seam is used only for this offline injected
    model. It does not enable the deliberately closed legacy provider endpoint.
    """
    if not callable(proposals):
        raise ValueError("An explicit synthetic page-model callback is required")
    if (
        criteria_ref.purpose != "criteria"
        or forms_ref.purpose != "forms"
        or criteria_ref.document_id == forms_ref.document_id
        or any(ref.case_id != identity.case_id for ref in (criteria_ref, forms_ref))
    ):
        raise ValueError("Synthetic preparation requires exact criteria/forms case references")
    principal.require(identity.case_id, Permission.REVIEW)
    if (identity.district, identity.zone, identity.land_use_category) != ("synthetic",) * 3:
        raise ValueError("Only explicitly synthetic case identity is supported")
    sources: list[SourceDocument] = []
    extractions: list[PageExtraction] = []
    reads = []
    renderer = SnapshotPDFRenderer()
    config = ExtractionConfig(
        model_id="synthetic-injected-raster-page-v1",
        region="local",
        attempts=1,
        max_output_tokens=4096,
        timeout_seconds=30,
    )
    for reference in (criteria_ref, forms_ref):
        admitted = documents.read(principal, reference)
        manifest = admitted.metadata.attestation.claims.manifest
        if not isinstance(documents.storage, SQLiteDocumentStorage):
            raise ValueError("Synthetic preparation requires the owned local SQLite C2 store")
        cache = documents.storage.database.parent / "sanitized-input-cache"
        if cache.is_symlink():
            raise ValueError("Synthetic sanitized cache cannot be a symlink")
        cache.mkdir(mode=0o700, exist_ok=True)
        cache_path = cache / f"{reference.document_id}-{reference.content_hash}.pdf"
        try:
            with cache_path.open("xb") as stream:
                stream.write(admitted.content)
            cache_path.chmod(0o600)
        except FileExistsError:
            if cache_path.is_symlink() or cache_path.read_bytes() != admitted.content:
                raise ValueError("Existing sanitized cache does not match admitted bytes") from None
        parsed = LocalPDFParser([]).parse_bytes(
            admitted.content,
            DocumentInput(
                path=cache_path,
                document_id=reference.document_id,
                version=reference.version,
                role="criteria" if reference.purpose == "criteria" else "forms",
                expected_hash=reference.content_hash,
            ),
            uri=cache_path.resolve().as_uri(),
        )
        if parsed.source is None:
            raise ValueError("Sanitized bytes produced no source registry")
        source = parsed.source
        expected_pages = 1 if reference.purpose == "criteria" else 2
        if len(source.pages) != expected_pages or len(manifest.pages) != expected_pages:
            raise ValueError("Synthetic source page count changed")
        for page, bound in zip(source.pages, manifest.pages, strict=True):
            if abs(page.width - bound.width) > 0.001 or abs(page.height - bound.height) > 0.001:
                raise ValueError("Sanitized manifest/page geometry mismatch")
            _image_citation(source, page.number)
        snapshot = AuthorizedSanitizedSnapshot(
            SanitizedSourceReference(
                document=reference,
                privacy_contract_version="privacy-v1",
                privacy_manifest_digest=digest_bytes(canonical_bytes(manifest)),
                page_count=expected_pages,
            ),
            admitted.content,
            source.model_dump_json().encode(),
        )
        if documents.read(principal, reference) != admitted:
            raise ValueError("Sanitized source changed after parsing")
        reads.append((reference, admitted))
        sources.append(source)
        for page in source.pages:
            png = await renderer.render(snapshot, page.number)
            validate_png(
                png,
                max_bytes=config.max_image_bytes,
                max_width=8000,
                max_height=8000,
                max_pixels=16_000_000,
            )
            if struct.unpack(">II", png[16:24]) != (
                math.ceil(page.width * 1.5),
                math.ceil(page.height * 1.5),
            ):
                raise ValueError("Actual raster PNG dimensions differ from parsed page")
            if documents.read(principal, reference) != admitted:
                raise ValueError("Sanitized source changed before model callback")
            extractor = BedrockDocumentExtractor(
                _InjectedPageClient(source, page.number, png, proposals), config
            )
            result = await extractor._extract_page(source, page.number, png)
            if documents.read(principal, reference) != admitted:
                raise ValueError("Sanitized source changed during model callback")
            extractions.append(result)
    for reference, admitted in reads:
        if documents.read(principal, reference) != admitted:
            raise ValueError("Sanitized source changed before candidate assembly")
    return assemble(identity, SourceRegistry(documents=sources), extractions)


async def material_from_admitted(
    principal: Principal,
    documents: DocumentTransferService,
    receipts: Sequence[DocumentMetadata],
) -> RevisionSnapshot:
    """Convenience wrapper for the rehearsal's two exact C2 admission receipts."""
    by_purpose = {item.reference.purpose: item.reference for item in receipts}
    if len(receipts) != 2 or set(by_purpose) != {"criteria", "forms"}:
        raise ValueError("Exactly one criteria and one forms admission receipt are required")
    identity = CaseIdentity(
        case_id=by_purpose["criteria"].case_id,
        version="1",
        district="synthetic",
        zone="synthetic",
        land_use_category="synthetic",
        effective_date=date(2026, 9, 11),
    )
    material = await prepare_sanitized_synthetic_material(
        documents,
        principal,
        identity,
        by_purpose["criteria"],
        by_purpose["forms"],
        authored_raster_proposal,
    )
    return RevisionSnapshot.capture(material, str(uuid4()))


async def create_sanitized_synthetic_case(
    directory: Path,
    principal: Principal,
    documents: DocumentTransferService,
    receipts: Sequence[DocumentMetadata],
    *,
    snapshot: RevisionSnapshot | None = None,
    approve_authored_synthetic_rules: bool = False,
) -> IntegrationFixture:
    """Compose a local fixture from this rehearsal's newly admitted raster PDFs.

    Trusted local orchestration must supply receipts from write_privacy_sources
    through the privacy review bridge, never arbitrary uploaded business files.
    The rule opt-in is a synthetic protocol capability: exact fixed authored
    rules are checked before changing their status. It never confirms facts.
    The returned snapshot has a new revision and local cached sanitized URIs;
    register THAT snapshot, not the earlier extraction snapshot. C2 identities,
    hashes, coordinates and empty image excerpts remain unchanged.

    No original file is an argument or template. Writer overlays preserve the
    sanitizer's token column and use blank fields authored in the same PDF.
    """
    if type(approve_authored_synthetic_rules) is not bool:
        raise ValueError("Synthetic rule opt-in must be boolean")
    by_purpose = {item.reference.purpose: item for item in receipts}
    if len(receipts) != 2 or set(by_purpose) != {"criteria", "forms"}:
        raise ValueError("Exactly one criteria and one forms receipt required")
    snapshot = snapshot or await material_from_admitted(principal, documents, receipts)
    material = snapshot.material
    if set(snapshot.revision.documents) != {item.reference for item in receipts}:
        raise ValueError("Snapshot must bind exactly the supplied admission receipts")
    identity = material.policy.identity
    principal.require(identity.case_id, Permission.REVIEW)
    if (identity.district, identity.zone, identity.land_use_category) != ("synthetic",) * 3:
        raise ValueError("Only authored synthetic case identities are supported")
    criteria = next(s for s in material.policy.registry.documents if s.role == "criteria")
    expected_rule = authored_raster_proposal(criteria, 1, b"").rules[0].rule
    if len(material.policy.rule_sets) != 2 or any(
        scoped.rules.rules != [expected_rule]
        or scoped.rules.status != "candidate"
        or scoped.zone != "synthetic"
        or scoped.context.scope != "regional"
        for scoped in material.policy.rule_sets
    ):
        raise ValueError("Rules do not match the exact authored synthetic rules")
    if len(material.facts.pairs) != 2:
        raise ValueError("Exactly two authored synthetic comparisons required")
    for page, pair in enumerate(material.facts.pairs, 1):
        expected_values = (10, 9) if page == 1 else (9, 10)
        if (
            pair.context
            != ComparisonContext(
                scope="regional",
                target_id="synthetic-target",
                comparable_id=f"synthetic-comp-{page}",
            )
            or pair.pair.factor_id != "synthetic.road_width"
        ):
            raise ValueError("Comparison differs from the authored synthetic context")
        for side, label, value in zip(
            ("target", "comparable"), ("標的", "比較"), expected_values, strict=True
        ):
            observation = getattr(pair.pair, side)
            reliability = getattr(pair, f"{side}_reliability")
            if (
                observation.confidence != 0
                or observation.raw_text != f"{label}道路寬度 {value} 公尺"
                or observation.value != NormalizedValue(type="number", value=float(value), unit="m")
                or reliability.confirmation is not None
                or reliability.method != "model_proposed"
                or not observation.evidence
                or any(ref.confidence != 0 for ref in observation.evidence)
            ):
                raise ValueError("Only unconfirmed authored synthetic observations are eligible")
    directory = directory.resolve()
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    specs = []
    for purpose in ("criteria", "forms"):
        receipt = by_purpose[purpose]
        admitted = documents.read(principal, receipt.reference)
        if admitted.metadata != receipt:
            raise ValueError("Admission receipt does not match the current exact C2 receipt")
        reference = receipt.reference
        path = directory / f"sanitized-{purpose}.pdf"
        path.write_bytes(admitted.content)
        path.chmod(0o600)
        spec = InputSpec(
            path=path,
            document_id=reference.document_id,
            version=reference.version,
            role=purpose,
            expected_hash=reference.content_hash,
        )
        parsed = LocalPDFParser([]).parse_bytes(
            admitted.content, DocumentInput(**spec.model_dump()), uri=path.as_uri()
        )
        previous = next(s for s in material.policy.registry.documents if s.role == purpose)
        if parsed.source is None or parsed.source.model_dump(
            exclude={"uri"}
        ) != previous.model_dump(exclude={"uri"}):
            raise ValueError("Cached sanitized bytes differ from the extraction registry")
        for geometry in parsed.source.pages:
            _image_citation(parsed.source, geometry.number)
            # The real privacy raster processor adds exactly a 220pt token column.
            if abs(geometry.width - 815) > 0.001 or abs(geometry.height - 420) > 0.001:
                raise ValueError("Authored sanitized form geometry changed")
        previous.uri = path.as_uri()
        for pair in material.facts.pairs:
            for side in ("target", "comparable"):
                for evidence in getattr(pair.pair, side).evidence:
                    if evidence.document_id == reference.document_id:
                        evidence.source_file = path.as_uri()
        if documents.read(principal, reference) != admitted:
            raise ValueError("Source changed while caching sanitized bytes")
        specs.append(spec)
    if approve_authored_synthetic_rules:
        for scoped in material.policy.rule_sets:
            scoped.rules.status = "approved"
    # Capture a real successor: source bytes are unchanged, material localization
    # URIs and the explicit synthetic rule status are now part of its digest.
    snapshot = snapshot.revise(material, str(uuid4()))
    material = snapshot.material
    run = RunReference(run_id=uuid4(), revision=snapshot.revision.reference)
    documents.create_snapshot(principal, run, snapshot.revision)
    font_path = directory / "synthetic-cjk.ttf"
    font_path.write_bytes(_bundled_font())
    font_path.chmod(0o600)
    fields = []
    for page, pair in enumerate(material.facts.pairs, 1):
        placements: tuple[
            tuple[
                str,
                Literal[
                    "target_grade",
                    "comparable_grade",
                    "adjustment_percent",
                    "total_adjustment_percent",
                ],
                tuple[int, int, int, int],
            ],
            ...,
        ] = (
            ("target", "target_grade", (305, 275, 475, 300)),
            ("comparable", "comparable_grade", (305, 235, 475, 260)),
            ("rate", "adjustment_percent", (305, 195, 475, 220)),
            ("total", "total_adjustment_percent", (305, 125, 475, 150)),
        )
        for name, field_value, bbox in placements:
            fields.append(
                PDFField(
                    field_id=f"c{page}-{name}",
                    page=page,
                    bounding_box=bbox,
                    # A full-page raster is occupied PDF content even where its
                    # pixels are white. Use the writer's explicit annotation
                    # operation after the real blank-pixel validation below.
                    operation="annotate",
                    value_ref=PDFValueRef(
                        **pair.context.model_dump(),
                        value=field_value,
                        factor_id=pair.pair.factor_id if name != "total" else None,
                    ),
                )
            )
    field_map = PDFFieldMap(template_id="synthetic-sanitized-multi-context-v1", fields=fields)
    template = specs[1].path
    # Validate actual blank raster interiors, never overlay authored facts/tokens.
    import pymupdf

    with pymupdf.open(template) as pdf:  # type: ignore[no-untyped-call]
        for field in fields:
            x0, y0, x1, y1 = field.bounding_box
            page = pdf[field.page - 1]
            pixels = page.get_pixmap(
                clip=pymupdf.Rect(x0, page.rect.height - y1, x1, page.rect.height - y0),  # type: ignore[no-untyped-call]
                colorspace=pymupdf.csRGB,
                alpha=False,
            )
            if min(pixels.samples) < 250:
                raise ValueError("Synthetic writer field is not blank in the admitted raster")
    output = directory / "output"
    output.mkdir(mode=0o700)
    configuration = LocalServiceConfiguration(
        inputs=InputManifest(identity=material.policy.identity, documents=specs),
        material_path=directory / "material.json",
        expected_material_digest=content_digest(material),
        revision_id=snapshot.revision.reference.revision_id,
        approval_store=None,
        writer=LocalWriterConfiguration(
            template_path=template,
            output_directory=output,
            field_map=field_map,
            template_policy=PDFTemplatePolicy(
                template_id=field_map.template_id,
                template_sha256=by_purpose["forms"].reference.content_hash,
                field_map_sha256=field_map_sha256(field_map),
                editable_pages=frozenset({1, 2}),
            ),
            render=PDFRenderConfig(
                font_path=font_path,
                font_name=FONT_NAME,
                approved_font_sha256=BUNDLED_CJK_SHA256,
                grade_labels={
                    "excellent": "優",
                    "slightly_superior": "略優",
                    "normal": "普通",
                    "slightly_inferior": "略劣",
                    "inferior": "劣",
                },
            ),
        ),
    )
    request = AgentReviewRequest(
        case_id=identity.case_id,
        criteria_document_uri=specs[0].path.as_uri(),
        case_document_uri=template.as_uri(),
        pdf_template_uri=template.as_uri(),
        output_pdf_uri=(output / "completed.pdf").as_uri(),
        field_map=field_map,
    )
    for name, model in (
        ("material", material),
        ("config", configuration),
        ("request", request),
        ("revision", snapshot.revision),
        ("run", run),
    ):
        private_json(directory / f"{name}.json", model.model_dump(mode="json"))
    private_json(
        directory / "synthetic-only.json",
        {
            "dataset_kind": "synthetic-sanitized-raster",
            "synthetic_rule_approval": approve_authored_synthetic_rules,
            "business_approval": False,
            "human_confirmation": False,
            "font_sha256": BUNDLED_CJK_SHA256,
        },
    )
    return IntegrationFixture(
        directory, principal, documents, snapshot, run, configuration, request, BUNDLED_CJK_SHA256
    )
