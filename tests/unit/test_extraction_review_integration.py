"""Synthetic PDFs through extraction, actual confirmation CLI and isolated approval."""

import asyncio
import json
import runpy
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

from appraisal_review.adapters.aws.document_extraction import (
    BedrockDocumentExtractor,
    ExtractionConfig,
)
from appraisal_review.adapters.local.approval import LocalApprovalStore, current_reviewer
from appraisal_review.adapters.local.pdf_parser import DocumentInput, LocalPDFParser
from appraisal_review.adapters.local.synthetic import synthetic_material
from appraisal_review.application.bootstrap import build_controller
from appraisal_review.application.document_review import assemble, document_adapters
from appraisal_review.config import Settings
from appraisal_review.domain.document_models import SourceCitation, SourceRegistry
from appraisal_review.domain.extraction_models import PageProposal, ProposedRule
from appraisal_review.domain.factor_models import AgentReviewRequest, ReviewMaterial
from appraisal_review.domain.review_contracts import content_digest


def extracted_pdf_material(tmp_path, *, case_role="forms"):
    make_pdf = runpy.run_path(str(Path(__file__).with_name("test_pdf_parser.py")))["make_pdf"]
    specs = []
    for role in ("criteria", "forms"):
        path = tmp_path / f"{role}.pdf"
        make_pdf(path)
        specs.append(DocumentInput(path, role, "synthetic-v1", role))
    parser = LocalPDFParser(specs)
    sources = [asyncio.run(parser.parse_document(s.path.as_uri())).source for s in specs]
    criteria, forms = sources

    def cite(source, text, kind="cell"):
        region = next(
            r
            for r in source.pages[0].regions
            if r.kind == kind and text in r.text and (text or not r.text)
        )
        return SourceCitation(
            document_id=source.document_id,
            content_hash=source.content_hash,
            version=source.version,
            page=1,
            region_id=region.id,
            bbox=region.bbox,
            excerpt=region.text,
        )

    # Domain seed only; no legacy evidence is included in any mocked model response.
    seed = synthetic_material()
    case_source = forms if case_role == "forms" else criteria
    target, comparable, blank = (
        cite(case_source, "18"),
        cite(case_source, "6"),
        cite(case_source, ""),
    )
    pair = seed.facts.pairs[0].model_copy(deep=True)
    pair.target_sources, pair.comparable_sources = [target], [comparable]
    for obs, ref, number in ((pair.pair.target, target, 18), (pair.pair.comparable, comparable, 6)):
        obs.raw_text, obs.value.value, obs.evidence = ref.excerpt, number, []
    entry = seed.policy.inventory.contexts[0].model_copy(deep=True)
    entry.evidence = [target, comparable]
    slot = seed.policy.inventory.slots[0].model_copy(deep=True)
    slot.id, slot.value, slot.evidence, slot.derivable_blank = (
        "grade",
        "target_grade",
        [blank],
        True,
    )
    observed = seed.facts.observed[0].model_copy(deep=True)
    observed.slot_id, observed.state, observed.value, observed.unit = slot.id, "blank", None, None
    observed.raw_text, observed.evidence = "", [blank]
    criterion = cite(criteria, "Synthetic rule:", kind="text")
    proposals = {
        ("criteria", 1): PageProposal(
            rules=[
                ProposedRule(
                    scope="regional",
                    rule=seed.policy.rule_sets[0].rules.rules[0],
                    evidence=[criterion],
                )
            ]
        ),
        ("forms", 1): PageProposal(
            contexts=[entry], pairs=[pair], slots=[slot], observed=[observed]
        ),
    }
    if case_role == "criteria":
        misplaced = proposals.pop(("forms", 1))
        criteria_proposal = proposals[("criteria", 1)]
        criteria_proposal.contexts = misplaced.contexts
        criteria_proposal.pairs = misplaced.pairs
        criteria_proposal.slots = misplaced.slots
        criteria_proposal.observed = misplaced.observed
    client = Mock()
    extractor = BedrockDocumentExtractor(
        client, ExtractionConfig(model_id="synthetic-model", region="synthetic-region", attempts=1)
    )
    extractions = []
    for source in sources:
        for page in source.pages:
            proposal = proposals.get((source.document_id, page.number), PageProposal())
            proposal.accounted_table_ids = sorted({r.table_id for r in page.regions if r.table_id})
            payload = proposal.model_dump(mode="json")
            for proposed in payload["pairs"]:
                for side in ("target", "comparable"):
                    proposed["pair"][side].pop("evidence")
            client.converse.return_value = {
                "stopReason": "end_turn",
                "output": {"message": {"content": [{"text": json.dumps(payload)}]}},
                "usage": {"inputTokens": 100, "outputTokens": 20},
            }
            extractions.append(
                asyncio.run(
                    extractor.extract_page(
                        source, page.number, asyncio.run(parser.render(source.uri, page.number))
                    )
                )
            )
            request_text = client.converse.call_args.kwargs["messages"][0]["content"][0]["text"]
            assert all(d.uri not in request_text for d in sources)
    material = assemble(seed.policy.identity, SourceRegistry(documents=sources), extractions)
    return parser, material


def test_source_citations_reach_review_after_confirmation_and_exact_approval(tmp_path):
    parser, material = extracted_pdf_material(tmp_path)
    pair = material.facts.pairs[0]
    assert pair.target_reliability.method == "model_proposed"
    for side in ("target", "comparable"):
        reliability = getattr(pair, f"{side}_reliability")
        assert reliability.confidence_kind == "localization_only"
        assert reliability.provenance == "parser_registry"
        assert reliability.confirmation is None
    assert pair.pair.target.confidence == pair.pair.comparable.confidence == 0
    assert pair.target_sources[0].bbox != pair.comparable_sources[0].bbox
    request = AgentReviewRequest(
        case_id=material.policy.identity.case_id,
        criteria_document_uri=material.policy.registry.documents[0].uri,
        case_document_uri=material.policy.registry.documents[1].uri,
    )
    store_path = tmp_path / "synthetic-approval"
    store = LocalApprovalStore(store_path)

    def review(candidate):
        controller = build_controller(
            Settings(_env_file=None), adapters=document_adapters(parser, candidate, store)
        )
        return asyncio.run(controller.review(request))

    unconfirmed = review(material)
    assert unconfirmed.status.value == "needs_review" and not unconfirmed.verification.can_complete
    pending_path, confirmed_path = tmp_path / "pending.json", tmp_path / "confirmed.json"
    pending_path.write_text(material.model_dump_json())
    subprocess.run(
        [
            sys.executable,
            "-m",
            "appraisal_review.document_cli",
            "confirm-facts",
            "--material",
            str(pending_path),
            "--expected-digest",
            content_digest(material),
            "--output",
            str(confirmed_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    confirmed = ReviewMaterial.model_validate_json(confirmed_path.read_text())
    assert content_digest(confirmed) != content_digest(material)
    assert confirmed.facts.pairs[0].target_reliability.method == "reviewer_confirmed"
    for side in ("target", "comparable"):
        original = getattr(pair.pair, side)
        confirmed_observation = getattr(confirmed.facts.pairs[0].pair, side)
        assert confirmed_observation == original
        assert confirmed_observation.confidence == 0
        assert all(e.confidence == 0 for e in confirmed_observation.evidence)
        confirmation = getattr(confirmed.facts.pairs[0], f"{side}_reliability").confirmation
        reviewer = current_reviewer()
        assert confirmation.reviewer == f"{reviewer.uid}:{reviewer.name}"
    unapproved = review(confirmed)
    assert unapproved.status.value == "needs_review" and not unapproved.verification.can_complete
    assert any(
        f.kind == "approval" and f.status == "needs_review" for f in unapproved.case_review.findings
    )
    store = LocalApprovalStore.initialize(store_path, current_reviewer())
    store.approve(confirmed, expected_digest=content_digest(confirmed))
    run = review(confirmed)
    assert run.status.value == "verified"
    assert run.verification.can_complete and not run.case_review.coverage.missing
    assert run.pdf_result is None and run.output_pdf_uri is None

    source = material.policy.registry.documents[1]
    for observation, refs in (
        (pair.pair.target, pair.target_sources),
        (pair.pair.comparable, pair.comparable_sources),
    ):
        assert len(observation.evidence) == len(refs)
        for evidence, ref in zip(observation.evidence, refs, strict=True):
            assert evidence.source_file == source.uri
            assert evidence.document_id == ref.document_id and evidence.page == ref.page
            assert evidence.bounding_box == ref.bbox
            assert evidence.block_ids == [ref.region_id]
            assert evidence.coordinate_system == "pdf_bottom_left" and evidence.confidence == 0
    changed = confirmed.model_copy(deep=True)
    changed.facts.pairs[0].pair.target.evidence[0].page = 2
    assert not store.permits(changed)
    rejected = review(changed)
    assert not rejected.verification.can_complete and rejected.output_pdf_uri is None
    assert any(
        f.kind == "approval" and f.status == "needs_review" for f in rejected.case_review.findings
    )


def test_real_pipeline_rejects_later_confidence_provenance_and_citation_changes(tmp_path):
    parser, material = extracted_pdf_material(tmp_path)
    pending, output = tmp_path / "pending.json", tmp_path / "confirmed.json"
    pending.write_text(material.model_dump_json())
    subprocess.run(
        [
            sys.executable,
            "-m",
            "appraisal_review.document_cli",
            "confirm-facts",
            "--material",
            str(pending),
            "--expected-digest",
            content_digest(material),
            "--output",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    confirmed = ReviewMaterial.model_validate_json(output.read_text())
    store = LocalApprovalStore.initialize(tmp_path / "approval", current_reviewer())
    store.approve(confirmed, expected_digest=content_digest(confirmed))
    request = AgentReviewRequest(
        case_id=material.policy.identity.case_id,
        criteria_document_uri=material.policy.registry.documents[0].uri,
        case_document_uri=material.policy.registry.documents[1].uri,
    )
    for tamper in ("outer", "measured", "provenance", "citation"):
        changed = confirmed.model_copy(deep=True)
        pair = changed.facts.pairs[0]
        if tamper == "outer":
            pair.pair.target.confidence = 0.99
        elif tamper == "measured":
            pair.pair.comparable.evidence[0].confidence = 0.99
        elif tamper == "provenance":
            pair.target_reliability.provenance = "native_extraction"
        else:
            pair.target_sources = pair.comparable_sources
        assert not store.permits(changed)
        controller = build_controller(
            Settings(_env_file=None), adapters=document_adapters(parser, changed, store)
        )
        run = asyncio.run(controller.review(request))
        assert not run.verification.can_complete
        assert run.pdf_result is None and run.output_pdf_uri is None


def test_actual_criteria_examples_cannot_become_case_facts_after_confirmation(tmp_path):
    parser, material = extracted_pdf_material(tmp_path, case_role="criteria")
    assert material.facts.pairs[0].target_sources[0].document_id == "criteria"
    pending, output = tmp_path / "pending.json", tmp_path / "confirmed.json"
    pending.write_text(material.model_dump_json())
    subprocess.run(
        [
            sys.executable,
            "-m",
            "appraisal_review.document_cli",
            "confirm-facts",
            "--material",
            str(pending),
            "--expected-digest",
            content_digest(material),
            "--output",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    confirmed = ReviewMaterial.model_validate_json(output.read_text())
    store = LocalApprovalStore.initialize(tmp_path / "approval", current_reviewer())
    store.approve(confirmed, expected_digest=content_digest(confirmed))
    request = AgentReviewRequest(
        case_id=material.policy.identity.case_id,
        criteria_document_uri=material.policy.registry.documents[0].uri,
        case_document_uri=material.policy.registry.documents[1].uri,
    )
    controller = build_controller(
        Settings(_env_file=None), adapters=document_adapters(parser, confirmed, store)
    )
    run = asyncio.run(controller.review(request))
    assert not run.verification.can_complete
    assert any(f.kind == "source_purpose" for f in run.case_review.findings)
    assert any("source_purpose" in item for item in material.policy.inventory.unresolved)
    assert run.output_pdf_uri is None and run.pdf_result is None
