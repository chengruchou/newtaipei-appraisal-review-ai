"""Generate isolated synthetic CJK review inputs, never approve external material.

Only a fresh directory is accepted. The opt-in approval applies to the fixed
synthetic content created here, using an isolated local approval store. It is
test authorization, not business approval or evidence of model accuracy.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Literal
from uuid import uuid4

import pymupdf
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

from appraisal_review.adapters.local.approval import LocalApprovalStore, current_reviewer
from appraisal_review.adapters.local.document_authority import (
    ConfiguredDocumentAuthorization,
    DocumentGrant,
    Ed25519ExportVerifier,
    TrustedExportKey,
)
from appraisal_review.adapters.local.document_manifest import InputManifest, InputSpec
from appraisal_review.adapters.local.document_storage import SQLiteDocumentStorage
from appraisal_review.adapters.local.pdf_config import (
    PDFRenderConfig,
    PDFTemplatePolicy,
    field_map_sha256,
)
from appraisal_review.adapters.local.pdf_parser import DocumentInput, LocalPDFParser
from appraisal_review.adapters.local.service import (
    LocalServiceConfiguration,
    LocalWriterConfiguration,
)
from appraisal_review.adapters.local.synthetic import synthetic_material
from appraisal_review.application.document_transfer import DocumentTransferService
from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.application.service_guards import Principal
from appraisal_review.document_cli import private_json
from appraisal_review.domain.confidence import confirm_side, confirmation_digest
from appraisal_review.domain.document_models import SourceCitation, SourceDocument, SourceRegistry
from appraisal_review.domain.document_transfer import (
    DocumentMetadata,
    DocumentOperation,
    ExportClaims,
    PrivacyAttestation,
    canonical_bytes,
    digest_bytes,
)
from appraisal_review.domain.factor_models import AgentReviewRequest, ReviewMaterial
from appraisal_review.domain.models import EvidenceRef
from appraisal_review.domain.pdf_models import PDFField, PDFFieldMap, PDFValueRef
from appraisal_review.domain.privacy_models import PrivacyManifest, PrivacyPage
from appraisal_review.domain.review_contracts import ComparisonContext, Reliability, content_digest
from appraisal_review.domain.service_contracts import ActorReference, Permission, RunReference

# Droid Sans Fallback bytes bundled by PyMuPDF; keep its upstream license in the
# dependency distribution. No font binary or generated PDF belongs in Git.
BUNDLED_CJK_SHA256 = "ee38813ea00c3e32add4268fff7fff9e39417b4913cb13be2415164a47807cc2"
FONT_NAME = "SyntheticIntegrationCJK"
PAGE_SIZE = (595.0, 420.0)


@dataclass(frozen=True)
class IntegrationFixture:
    directory: Path
    principal: Principal
    documents: DocumentTransferService
    snapshot: RevisionSnapshot
    run: RunReference
    configuration: LocalServiceConfiguration
    request: AgentReviewRequest
    font_sha256: str
    _configuration_json: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_configuration_json", self.configuration.model_dump_json())

    def authorize(self, snapshot: RevisionSnapshot) -> SyntheticRevisionAuthorization:
        """Authorize only an already confirmed exact revision of this generated case.

        Never confirms sides, changes material, signs business approval, or advances
        revision/run state. The caller must supply the authoritative stored snapshot.
        See docs/integration-fixture.md for the local synthetic trust boundary.
        """
        try:
            self._check_authorizable(snapshot)
        except Exception:
            raise ValueError("Synthetic authorization requirements are not satisfied") from None
        return SyntheticRevisionAuthorization(self, snapshot)

    def _check_authorizable(self, snapshot: RevisionSnapshot) -> None:
        material, revision = snapshot.material, snapshot.revision
        original = self.snapshot.material
        canonical = RevisionSnapshot.capture(
            material,
            revision.reference.revision_id,
            parent=revision.parent,
            changes=revision.changes,
        )
        if canonical.revision != revision:
            raise ValueError("Revision does not describe the exact material")
        if (
            material.policy.identity.model_dump(exclude={"version"})
            != original.policy.identity.model_dump(exclude={"version"})
            or material.policy.rule_sets != original.policy.rule_sets
            or material.policy.registry != original.policy.registry
            or material.policy.inventory != original.policy.inventory
            or material.facts.observed != original.facts.observed
            or revision.documents != self.snapshot.revision.documents
            or revision.rules != self.snapshot.revision.rules
            or len(material.facts.pairs) != len(original.facts.pairs)
        ):
            raise ValueError("Authored synthetic identities or policy changed")
        reviewers = {self.principal.actor.actor_id}
        reviewers.update(
            reliability.confirmation.reviewer
            for pair in original.facts.pairs
            for reliability in (pair.target_reliability, pair.comparable_reliability)
            if reliability.confirmation is not None
        )
        for pair, prior in zip(material.facts.pairs, original.facts.pairs, strict=True):
            if pair.context != prior.context or pair.pair.factor_id != prior.pair.factor_id:
                raise ValueError("Authored synthetic comparison changed")
            for side in ("target", "comparable"):
                observation, previous = getattr(pair.pair, side), getattr(prior.pair, side)
                reliability = getattr(pair, f"{side}_reliability")
                prior_reliability = getattr(prior, f"{side}_reliability")
                confirmation = reliability.confirmation
                if (
                    observation.value is None
                    or previous.value is None
                    or observation.value.type != previous.value.type
                    or observation.value.unit != previous.value.unit
                    or observation.model_dump(exclude={"value"})
                    != previous.model_dump(exclude={"value"})
                    or getattr(pair, f"{side}_sources") != getattr(prior, f"{side}_sources")
                    or reliability.model_dump(exclude={"method", "confirmation"})
                    != prior_reliability.model_dump(exclude={"method", "confirmation"})
                    or reliability.method != "reviewer_confirmed"
                    or confirmation is None
                    or confirmation.reviewer not in reviewers
                    or confirmation.input_digest != confirmation_digest(pair, side)
                ):
                    raise ValueError("Synthetic side requires its existing exact confirmation")
        config = LocalServiceConfiguration.model_validate_json(self._configuration_json)
        writer = config.writer
        if writer is None or self.configuration.model_dump_json() != self._configuration_json:
            raise ValueError("Authored synthetic writer configuration changed")
        if (
            digest_bytes(writer.template_path.read_bytes())
            != writer.template_policy.template_sha256
            or field_map_sha256(writer.field_map) != writer.template_policy.field_map_sha256
            or digest_bytes(writer.render.font_path.read_bytes()) != self.font_sha256
        ):
            raise ValueError("Authored synthetic output assets changed")
        for spec in config.inputs.documents:
            reference = next(
                ref for ref in revision.documents if ref.document_id == spec.document_id
            )
            current = self.documents.read(self.principal, reference)
            if (
                current.content != spec.path.read_bytes()
                or digest_bytes(current.content) != spec.expected_hash
            ):
                raise ValueError("Authored synthetic source bytes changed")


@dataclass(frozen=True)
class SyntheticRevisionAuthorization:
    """Exact synthetic material capability; not a signed or business approval receipt."""

    _fixture: IntegrationFixture = field(repr=False)
    _snapshot: RevisionSnapshot = field(repr=False)

    def permits(self, material: ReviewMaterial) -> bool:
        try:
            if content_digest(material) != self._snapshot.revision.reference.material_digest:
                return False
            self._fixture._check_authorizable(self._snapshot)
            return True
        except Exception:
            return False


def _bundled_font() -> bytes:
    content = bytes(pymupdf.Font("cjk").buffer)  # type: ignore[no-untyped-call]
    if digest_bytes(content) != BUNDLED_CJK_SHA256:
        raise ValueError("Bundled synthetic CJK font digest changed")
    font = TTFont(FONT_NAME, BytesIO(content))
    if not all(ord(char) in font.face.charToGlyph for char in "合成測試標的比較道路寬度修正率優劣"):
        raise ValueError("Bundled synthetic font lacks required CJK glyphs")
    pdfmetrics.registerFont(font)
    return content


def _write_pdf(path: Path, pages: tuple[tuple[str, ...], ...], *, template: bool = False) -> None:
    canvas = Canvas(str(path), pagesize=PAGE_SIZE, invariant=1)
    canvas.setAuthor("")
    canvas.setCreator("")
    canvas.setTitle("Synthetic CJK integration fixture")
    canvas.setSubject("Synthetic test data only; no business approval")
    for lines in pages:
        # Source-template preflight accepts Base14 metrics only. The writer
        # embeds legible CJK grades from the pinned font into these blank fields.
        canvas.setFont("Helvetica" if template else FONT_NAME, 13)
        for index, line in enumerate(lines):
            y = (375, 335, 282, 242, 202, 132)[index] if template else 375 - index * 40
            canvas.drawString(30, y, line)
        if template:
            canvas.rect(300, 270, 180, 35)
            canvas.rect(300, 230, 180, 35)
            canvas.rect(300, 190, 180, 35)
            canvas.rect(300, 120, 180, 35)
        canvas.showPage()
    canvas.save()


def _citation(source: SourceDocument, page: int, text: str) -> SourceCitation:
    matches = [r for r in source.pages[page - 1].regions if r.kind == "text" and text in r.text]
    if len(matches) != 1:
        raise ValueError("Synthetic text must resolve to one parsed source region")
    region = matches[0]
    return SourceCitation(
        document_id=source.document_id,
        version=source.version,
        content_hash=source.content_hash,
        page=page,
        region_id=region.id,
        bbox=region.bbox,
        excerpt=region.text,
    )


def _material(case_id: str, sources: list[SourceDocument]) -> ReviewMaterial:
    material = synthetic_material()
    material.policy.identity.case_id = material.facts.identity.case_id = case_id
    material.policy.registry = SourceRegistry(documents=sources)
    criteria, forms = sources
    criteria_refs = [
        _citation(criteria, 1, text) for text in ("未滿 10", "達 10", "優對劣", "劣對優", "同等級")
    ]
    scoped_seed = material.policy.rule_sets[0]
    context_seed = material.policy.inventory.contexts[0]
    slot_seed = material.policy.inventory.slots[0]
    pair_seed = material.facts.pairs[0]
    observed_seed = material.facts.observed[0]
    material.policy.rule_sets = []
    material.policy.inventory.contexts = []
    material.policy.inventory.slots = []
    material.facts.pairs = []
    material.facts.observed = []
    for number, target, comparable, rate in ((1, 10, 9, 5), (2, 9, 10, -5)):
        context = ComparisonContext(
            scope="regional", target_id="synthetic-target", comparable_id=f"synthetic-comp-{number}"
        )
        target_ref = _citation(forms, number, f"標的道路寬度 {target} 公尺")
        comparable_ref = _citation(forms, number, f"比較道路寬度 {comparable} 公尺")
        rate_ref = _citation(forms, number, f"填報修正率 {rate} 百分點")
        scoped = scoped_seed.model_copy(deep=True)
        scoped.context = context
        scoped.evidence = criteria_refs
        scoped.source_version = criteria.version
        scoped.rules.source_document.document_id = criteria.document_id
        scoped.rules.source_document.content_hash = criteria.content_hash
        # Explicit synthetic rule authorization for this authored test case only.
        # Observation confirmation and exact material authorization stay separate.
        scoped.rules.status = "approved"
        material.policy.rule_sets.append(scoped)
        inventory_context = context_seed.model_copy(deep=True)
        inventory_context.context = context
        inventory_context.evidence = [target_ref, comparable_ref]
        material.policy.inventory.contexts.append(inventory_context)
        slot = slot_seed.model_copy(deep=True)
        slot.id, slot.context, slot.evidence = f"c{number}-rate", context, [rate_ref]
        material.policy.inventory.slots.append(slot)
        observed = observed_seed.model_copy(deep=True)
        observed.slot_id, observed.value = slot.id, str(rate)
        observed.raw_text, observed.evidence = rate_ref.excerpt, [rate_ref]
        material.facts.observed.append(observed)
        pair = pair_seed.model_copy(deep=True)
        pair.context = context
        for side, value, ref in (
            ("target", target, target_ref),
            ("comparable", comparable, comparable_ref),
        ):
            observation = getattr(pair.pair, side)
            observation.raw_text = ref.excerpt
            observation.value.value = float(value)
            observation.confidence = 0
            observation.evidence = [
                EvidenceRef(
                    document_id=ref.document_id,
                    source_file=forms.uri,
                    page=ref.page,
                    confidence=0,
                    bounding_box=ref.bbox,
                    coordinate_system="pdf_bottom_left",
                )
            ]
            setattr(pair, f"{side}_sources", [ref])
            setattr(
                pair,
                f"{side}_reliability",
                Reliability(
                    method="model_proposed",
                    selection="not_applicable",
                    confidence_kind="localization_only",
                    provenance="parser_registry",
                    producer="synthetic-integration-fixture-v1",
                ),
            )
        material.facts.pairs.append(pair)
    material.policy.inventory.inspected_pages = {
        source.document_id: [page.number for page in source.pages] for source in sources
    }
    return ReviewMaterial.model_validate_json(material.model_dump_json())


async def create_integration_fixture(
    directory: Path, *, approve_synthetic: bool = False
) -> IntegrationFixture:
    """Create a fresh test case; accept no external content, identities or authority.

    ``approve_synthetic=True`` confirms only this generated fixed test material.
    It keeps raw/evidence confidence zero. Default fixtures need human review.
    The returned C2 service owns one isolated SQLite store and in-process grants.
    ``configuration`` supplies local paths; never serialize it to a cloud request.
    """
    if type(approve_synthetic) is not bool:
        raise ValueError("Synthetic approval opt-in must be boolean")
    font_bytes = _bundled_font()
    directory = directory.resolve()
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    font_path = directory / "synthetic-cjk.ttf"
    font_path.write_bytes(font_bytes)
    actor, case, key_id = uuid4(), uuid4(), uuid4()
    principal = Principal(
        ActorReference(actor_id=str(actor), kind="human"),
        frozenset({str(case)}),
        frozenset(Permission),
    )
    key = Ed25519PrivateKey.generate()
    authority = ConfiguredDocumentAuthorization(
        (
            DocumentGrant(
                actor,
                case,
                frozenset({"criteria", "forms"}),
                frozenset(DocumentOperation),
            ),
        )
    )
    verifier = Ed25519ExportVerifier(
        (
            TrustedExportKey(
                key_id,
                key.public_key(),
                actor,
                frozenset({case}),
            ),
        )
    )
    storage = SQLiteDocumentStorage(directory / "documents.sqlite")
    documents = DocumentTransferService(storage, authority, verifier, storage)
    specs: list[InputSpec] = []
    sources: list[SourceDocument] = []
    metadata: list[DocumentMetadata] = []
    criteria_lines = (
        "合成測試規則 - 僅限本機測試",
        "道路寬度未滿 10 公尺為劣",
        "道路寬度達 10 公尺為優",
        "標的優對劣修正率 5 百分點",
        "標的劣對優修正率 -5 百分點",
        "同等級修正率 0 百分點",
    )
    forms_pages = tuple(
        (
            f"合成測試估價表 - 比較案例 {number}",
            f"標的道路寬度 {target} 公尺",
            f"比較道路寬度 {comparable} 公尺",
            f"填報修正率 {rate} 百分點",
        )
        for number, target, comparable, rate in ((1, 10, 9, 5), (2, 9, 10, -5))
    )
    source_pages: tuple[tuple[Literal["criteria", "forms"], tuple[tuple[str, ...], ...]], ...] = (
        ("criteria", (criteria_lines,)),
        ("forms", forms_pages),
    )
    for purpose, pages in source_pages:
        path = directory / f"{purpose}.pdf"
        local_source_id = uuid4()
        # Independent source identity is visible in the generated bytes as well.
        pages = tuple((*lines, f"SYNTHETIC SOURCE {local_source_id}") for lines in pages)
        _write_pdf(path, pages)
        content = path.read_bytes()
        now = datetime.now(UTC)
        manifest = PrivacyManifest(
            case_id=case,
            document_id=local_source_id,
            sanitized_digest=digest_bytes(content),
            byte_size=len(content),
            pages=tuple(
                PrivacyPage(number=i, width=PAGE_SIZE[0], height=PAGE_SIZE[1])
                for i in range(1, len(pages) + 1)
            ),
            occurrences=(),
        )
        claims = ExportClaims(
            key_id=key_id,
            export_id=uuid4(),
            principal_id=actor,
            manifest=manifest,
            purpose=purpose,
            confirmed_at=now,
            expires_at=now + timedelta(minutes=10),
        )
        admitted = documents.ingest(
            principal,
            content,
            PrivacyAttestation(
                claims=claims,
                signature_hex=key.sign(canonical_bytes(claims)).hex(),
            ),
        )
        metadata.append(admitted)
        readback = documents.read(principal, admitted.reference)
        if readback.content != content:
            raise ValueError("C2 synthetic admission changed exact bytes")
        reference = admitted.reference
        spec = InputSpec(
            path=path,
            document_id=reference.document_id,
            version=reference.version,
            role=purpose,
            expected_hash=reference.content_hash,
        )
        specs.append(spec)
        parsed = LocalPDFParser([]).parse_bytes(
            readback.content,
            DocumentInput(**spec.model_dump()),
            uri=path.as_uri(),
        )
        if parsed.source is None:
            raise ValueError("C2 synthetic source produced no parser registry")
        if documents.read(principal, reference) != readback:
            raise ValueError("C2 synthetic source changed during parsing")
        sources.append(parsed.source)
    material = _material(str(case), sources)
    private_json(directory / "unconfirmed.json", material.model_dump(mode="json"))
    approval_path = None
    if approve_synthetic:
        reviewer = current_reviewer()
        for pair in material.facts.pairs:
            confirm_side(pair, "target", reviewer=f"{reviewer.uid}:{reviewer.name}")
            confirm_side(pair, "comparable", reviewer=f"{reviewer.uid}:{reviewer.name}")
        approval_path = directory / "approval"
        local_authority = LocalApprovalStore.initialize(approval_path, reviewer)
        local_authority.approve(material, expected_digest=content_digest(material))
    snapshot = RevisionSnapshot.capture(material, str(uuid4()))
    run = RunReference(run_id=uuid4(), revision=snapshot.revision.reference)
    documents.create_snapshot(principal, run, snapshot.revision)
    for reference in snapshot.revision.documents:
        if documents.read_snapshot(principal, run, reference) != documents.read(
            principal, reference
        ):
            raise ValueError("C2 synthetic run binding differs from admitted source")
    template = directory / "template.pdf"
    _write_pdf(
        template,
        tuple(
            (
                f"SYNTHETIC OUTPUT - COMPARISON {number}",
                "LOCAL TEST ONLY - NO BUSINESS APPROVAL",
                "Target grade",
                "Comparable grade",
                "Road adjustment",
                "Total adjustment",
            )
            for number in (1, 2)
        ),
        template=True,
    )
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
        for name, value, box in placements:
            fields.append(
                PDFField(
                    field_id=f"c{page}-{name}",
                    page=page,
                    bounding_box=box,
                    value_ref=PDFValueRef(
                        **pair.context.model_dump(),
                        value=value,
                        factor_id=pair.pair.factor_id if name != "total" else None,
                    ),
                )
            )
    field_map = PDFFieldMap(template_id="synthetic-cjk-multi-context-v1", fields=fields)
    output = directory / "output"
    output.mkdir()
    configuration = LocalServiceConfiguration(
        inputs=InputManifest(identity=material.policy.identity, documents=specs),
        material_path=directory / "material.json",
        expected_material_digest=content_digest(material),
        revision_id=snapshot.revision.reference.revision_id,
        approval_store=approval_path,
        writer=LocalWriterConfiguration(
            template_path=template,
            output_directory=output,
            field_map=field_map,
            template_policy=PDFTemplatePolicy(
                template_id=field_map.template_id,
                template_sha256=digest_bytes(template.read_bytes()),
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
        case_id=str(case),
        criteria_document_uri=specs[0].path.as_uri(),
        case_document_uri=specs[1].path.as_uri(),
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
            "dataset_kind": "synthetic",
            "synthetic_approval": approve_synthetic,
            "synthetic_rule_approval": True,
            "business_approval": False,
            "font_sha256": BUNDLED_CJK_SHA256,
            "font_source": "PyMuPDF bundled Droid Sans Fallback",
            "c2_documents": [item.reference.model_dump(mode="json") for item in metadata],
        },
    )
    return IntegrationFixture(
        directory, principal, documents, snapshot, run, configuration, request, BUNDLED_CJK_SHA256
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--approve-synthetic", action="store_true")
    args = parser.parse_args()
    asyncio.run(
        create_integration_fixture(args.directory, approve_synthetic=args.approve_synthetic)
    )
    print("Synthetic CJK fixture ready; no external documents or business approvals.")


if __name__ == "__main__":
    main()
