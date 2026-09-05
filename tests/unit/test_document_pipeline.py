"""Actual PDF -> typed sources -> candidate assembly -> Controller/HTTP/invocation."""

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from appraisal_review.adapters.aws.agentcore.runtime import invoke
from appraisal_review.adapters.local.approval import LocalApprovalStore, current_reviewer
from appraisal_review.adapters.local.pdf_parser import DocumentInput, LocalPDFParser
from appraisal_review.adapters.local.references import ReferenceCatalog, ReferenceSection
from appraisal_review.adapters.local.synthetic import synthetic_material
from appraisal_review.api.app import create_app
from appraisal_review.api.dependencies import get_controller_factory
from appraisal_review.application.bootstrap import build_controller
from appraisal_review.application.document_review import assemble, document_adapters
from appraisal_review.config import Settings
from appraisal_review.domain.document_models import SourceCitation, SourceRegistry
from appraisal_review.domain.extraction_models import PageExtraction, PageProposal, ProposedRule
from appraisal_review.domain.factor_models import AgentReviewRequest
from appraisal_review.domain.models import EvidenceRef
from appraisal_review.domain.review_contracts import content_digest


def material_from_pdf(tmp_path):
    import runpy

    make_pdf = runpy.run_path(str(Path(__file__).with_name("test_pdf_parser.py")))["make_pdf"]
    specs = []
    for role in ["criteria", "forms"]:
        path = tmp_path / f"{role}.pdf"
        make_pdf(path)
        specs.append(DocumentInput(path, role, "synthetic-v1", role))
    parser = LocalPDFParser(specs)
    sources = [asyncio.run(parser.parse_document(s.path.as_uri())).source for s in specs]
    material = synthetic_material()
    material.policy.registry = SourceRegistry(documents=sources)

    def cite(source, text):
        region = next(r for r in source.pages[0].regions if r.kind == "cell" and r.text == text)
        return SourceCitation(
            document_id=source.document_id,
            content_hash=source.content_hash,
            version=source.version,
            page=1,
            region_id=region.id,
            bbox=region.bbox,
            excerpt=text,
        )

    target, comparable = cite(sources[1], "18"), cite(sources[1], "6")
    blank = cite(sources[1], "")
    criterion = cite(sources[0], "18")
    rules = material.policy.rule_sets[0]
    rules.source_version, rules.evidence = sources[0].version, [criterion]
    rules.rules.source_document.document_id = sources[0].document_id
    rules.rules.source_document.content_hash = sources[0].content_hash
    material.policy.inventory.inspected_pages = {s.document_id: [1, 2] for s in sources}
    material.policy.inventory.inspected_tables = {
        s.document_id: sorted({r.table_id for p in s.pages for r in p.regions if r.table_id})
        for s in sources
    }
    material.policy.inventory.contexts[0].evidence = [target, comparable]
    slot = material.policy.inventory.slots[0]
    slot.value, slot.id, slot.factor_id = "target_grade", "grade", "synthetic.road_width"
    slot.derivable_blank, slot.evidence = True, [blank]
    # Synthetic declared pending output; source citation is explicitly located.
    value = material.facts.observed[0]
    value.slot_id, value.state, value.value, value.unit = "grade", "blank", None, None
    value.evidence, value.raw_text = [blank], ""
    pair = material.facts.pairs[0]
    pair.target_sources, pair.comparable_sources = [target], [comparable]
    for obs, ref, numeric in [
        (pair.pair.target, target, 18),
        (pair.pair.comparable, comparable, 6),
    ]:
        obs.raw_text, obs.value.value = ref.excerpt, float(numeric)
        obs.evidence = [
            EvidenceRef(
                document_id=ref.document_id,
                source_file=sources[1].uri,
                page=ref.page,
                confidence=1,
                bounding_box=ref.bbox,
                coordinate_system="pdf_bottom_left",
            )
        ]
    return parser, material


def test_actual_pdf_and_controlled_approval_use_existing_http_and_invocation(tmp_path):
    parser, material = material_from_pdf(tmp_path)
    store = LocalApprovalStore.initialize(tmp_path / "approval", current_reviewer())

    def factory():
        return build_controller(
            Settings(_env_file=None), adapters=document_adapters(parser, material, store)
        )

    request = AgentReviewRequest(
        case_id=material.policy.identity.case_id,
        criteria_document_uri=material.policy.registry.documents[0].uri,
        case_document_uri=material.policy.registry.documents[1].uri,
    )
    run = asyncio.run(factory().review(request))
    assert run.status.value == "needs_review" and not run.verification.can_complete
    store.approve(material, expected_digest=content_digest(material))
    app = create_app()
    app.dependency_overrides[get_controller_factory] = lambda: factory
    http = TestClient(app).post("/v1/reviews", json=request.model_dump(mode="json"))
    assert http.status_code == 200
    assert http.json() == asyncio.run(
        invoke(request.model_dump(mode="json"), controller_factory=factory)
    )
    assert http.json()["status"] == "verified" and http.json()["output_pdf_uri"] is None
    assert http.json()["case_review"]["coverage"]["missing"] == []


def test_multi_page_candidate_assembly_preserves_missing_and_duplicate_scope(tmp_path):
    _parser, material = material_from_pdf(tmp_path)
    criteria, forms = material.policy.registry.documents
    rules = material.policy.rule_sets[0]
    proposals = [
        (
            criteria,
            1,
            PageProposal(
                rules=[
                    ProposedRule(
                        scope="regional", rule=rules.rules.rules[0], evidence=rules.evidence
                    )
                ]
            ),
        ),
        (criteria, 2, PageProposal()),
        (
            forms,
            1,
            PageProposal(contexts=material.policy.inventory.contexts, pairs=material.facts.pairs),
        ),
        (
            forms,
            2,
            PageProposal(
                contexts=material.policy.inventory.contexts,
                slots=material.policy.inventory.slots,
                observed=material.facts.observed,
            ),
        ),
    ]
    results = [
        PageExtraction(
            document_id=source.document_id,
            content_hash=source.content_hash,
            page=page,
            proposal=proposal,
            model_id="synthetic",
            region="synthetic",
            input_tokens=1,
            output_tokens=1,
            elapsed_seconds=0,
            attempts=1,
        )
        for source, page, proposal in proposals
    ]
    combined = assemble(material.policy.identity, material.policy.registry, results)
    assert len(combined.policy.inventory.contexts) == 1
    assert combined.policy.inventory.inspected_pages == {"criteria": [1, 2], "forms": [1, 2]}
    assert combined.policy.rule_sets[0].rules.status == "candidate"
    assert combined.facts.pairs == material.facts.pairs


def test_reference_versioned_copy_check_is_a_candidate_with_exact_evidence(tmp_path):
    _, material = material_from_pdf(tmp_path)
    ref = material.policy.rule_sets[0].evidence[0]
    catalog = ReferenceCatalog(
        registry=material.policy.registry,
        sections=[
            ReferenceSection(
                id="synthetic-copy-procedure", title="Synthetic reference", evidence=[ref]
            )
        ],
    )
    check = catalog.propose_copy_check(
        "synthetic-copy-procedure", id="copy", source_slot="total", target_slot="copied"
    )
    assert check.kind == "equals" and check.evidence == [ref]
    catalog.sections[0].evidence[0].version = "other-version"
    try:
        catalog.section("synthetic-copy-procedure")
    except ValueError:
        pass
    else:
        raise AssertionError("Stale reference should fail")
