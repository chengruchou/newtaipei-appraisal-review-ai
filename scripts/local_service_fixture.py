"""Create fresh, strictly synthetic PDFs/material/approval for local acceptance.

This helper never accepts existing material, rules, documents or an approval store.
It grants test authorization only to the fixed synthetic case it creates itself.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
from pathlib import Path

import reportlab
from pydantic import BaseModel
from reportlab.pdfgen.canvas import Canvas

from appraisal_review.adapters.local.approval import LocalApprovalStore, current_reviewer
from appraisal_review.adapters.local.document_manifest import InputManifest, InputSpec
from appraisal_review.adapters.local.pdf_config import (
    PDFRenderConfig,
    PDFTemplatePolicy,
    field_map_sha256,
)
from appraisal_review.adapters.local.service import (
    LocalServiceConfiguration,
    LocalWriterConfiguration,
)
from appraisal_review.adapters.local.synthetic import synthetic_material, synthetic_request
from appraisal_review.document_cli import private_json as _private_json
from appraisal_review.domain.confidence import confirm_side
from appraisal_review.domain.document_models import SourceCitation, SourceRegistry
from appraisal_review.domain.factor_models import AgentReviewRequest
from appraisal_review.domain.models import EvidenceRef
from appraisal_review.domain.review_contracts import Reliability, content_digest


def private_json(path: Path, value: BaseModel) -> None:
    _private_json(path, value.model_dump(mode="json"))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def create_fixture(directory: Path) -> LocalServiceConfiguration:
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    material = synthetic_material()
    specs = []
    for role, text in (
        ("criteria", "Synthetic road threshold: 10 m"),
        ("forms", "Synthetic target 10 m; comparable 9 m; rate 5"),
    ):
        path = directory / f"{role}.pdf"
        canvas = Canvas(str(path), pagesize=(400, 260), invariant=1)
        canvas.setAuthor("")
        canvas.setTitle("Synthetic local acceptance input")
        canvas.drawString(20, 220, text)
        canvas.save()
        specs.append(
            InputSpec(
                path=path,
                document_id=f"synthetic-{role}",
                version="1",
                role=role,
                expected_hash=digest(path),
            )
        )
    inputs = InputManifest(identity=material.policy.identity, documents=specs)
    parser = inputs.parser()
    documents = []
    citations = []
    for spec in specs:
        parsed = await parser.parse_document(spec.path.as_uri())
        assert parsed.source is not None
        doc = parsed.source
        documents.append(doc)
        region = doc.pages[0].regions[0]
        citations.append(
            SourceCitation(
                document_id=doc.document_id,
                version=doc.version,
                content_hash=doc.content_hash,
                page=1,
                region_id=region.id,
                bbox=region.bbox,
                excerpt=region.text,
            )
        )
    criteria_ref, forms_ref = citations
    material.policy.registry = SourceRegistry(documents=documents)
    rules = material.policy.rule_sets[0]
    rules.evidence = [criteria_ref]
    rules.rules.source_document.content_hash = criteria_ref.content_hash
    material.policy.inventory.contexts[0].evidence = [forms_ref]
    material.policy.inventory.slots[0].evidence = [forms_ref]
    material.facts.observed[0].evidence = [forms_ref]
    pair = material.facts.pairs[0]
    pair.target_sources = pair.comparable_sources = [forms_ref]
    for observation in (pair.pair.target, pair.pair.comparable):
        observation.raw_text = forms_ref.excerpt
        observation.confidence = 0
        observation.evidence = [
            EvidenceRef(
                document_id=forms_ref.document_id,
                source_file=documents[1].uri,
                page=1,
                confidence=0,
                bounding_box=forms_ref.bbox,
                coordinate_system="pdf_bottom_left",
            )
        ]
    for side in ("target", "comparable"):
        setattr(
            pair,
            f"{side}_reliability",
            Reliability(
                method="model_proposed",
                selection="not_applicable",
                confidence_kind="localization_only",
                provenance="parser_registry",
                producer="synthetic-fixture-v1",
            ),
        )
    private_json(directory / "unconfirmed.json", material)
    reviewer = current_reviewer()
    confirm_side(pair, "target", reviewer=f"{reviewer.uid}:{reviewer.name}")
    confirm_side(pair, "comparable", reviewer=f"{reviewer.uid}:{reviewer.name}")
    authority = LocalApprovalStore.initialize(directory / "approval", reviewer)
    authority.approve(material, expected_digest=content_digest(material))
    private_json(directory / "material.json", material)
    template = directory / "template.pdf"
    canvas = Canvas(str(template), pagesize=(320, 220), invariant=1)
    canvas.setAuthor("")
    canvas.setTitle("Synthetic output template")
    canvas.drawString(20, 190, "SYNTHETIC FORM")
    canvas.rect(160, 75, 130, 35)
    canvas.save()
    request = synthetic_request("completed")
    assert request.field_map is not None
    request.field_map.fields[0].bounding_box = (160, 75, 290, 110)
    request.criteria_document_uri = specs[0].path.as_uri()
    request.case_document_uri = specs[1].path.as_uri()
    request.pdf_template_uri = template.as_uri()
    output = directory / "output"
    output.mkdir()
    request.output_pdf_uri = (output / "completed.pdf").as_uri()
    config = LocalServiceConfiguration(
        inputs=inputs,
        material_path=directory / "material.json",
        expected_material_digest=content_digest(material),
        revision_id="fixture-r1",
        approval_store=directory / "approval",
        writer=LocalWriterConfiguration(
            template_path=template,
            output_directory=output,
            field_map=request.field_map,
            template_policy=PDFTemplatePolicy(
                template_id=request.field_map.template_id,
                template_sha256=digest(template),
                field_map_sha256=field_map_sha256(request.field_map),
                editable_pages=frozenset({1}),
            ),
            render=PDFRenderConfig(
                font_path=Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"
            ),
        ),
    )
    private_json(directory / "config.json", config)
    private_json(directory / "request-write.json", request)
    private_json(
        directory / "request.json",
        AgentReviewRequest(
            case_id=request.case_id,
            criteria_document_uri=request.criteria_document_uri,
            case_document_uri=request.case_document_uri,
        ),
    )
    private_json(directory / "config-no-writer.json", config.model_copy(update={"writer": None}))
    missing = material.model_copy(deep=True)
    missing.facts.pairs[0].pair.target.evidence = []
    missing.facts.pairs[0].target_reliability.confirmation = None
    missing.facts.pairs[0].target_reliability.method = "model_proposed"
    private_json(directory / "needs-review.json", missing)
    private_json(
        directory / "config-needs-review.json",
        config.model_copy(
            update={
                "material_path": directory / "needs-review.json",
                "expected_material_digest": content_digest(missing),
                "revision_id": "fixture-r2",
            }
        ),
    )
    private_json(directory / "inputs.json", inputs)
    return config


def main() -> None:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--directory", type=Path, required=True)
    args = cli.parse_args()
    asyncio.run(create_fixture(args.directory))
    print("Synthetic fixture ready. No external documents or rules were authorized.")


if __name__ == "__main__":
    main()
