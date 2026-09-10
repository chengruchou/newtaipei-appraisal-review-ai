"""Real sanitized admission, injected page extraction and same-template writing."""

import asyncio
import importlib
from dataclasses import replace
from uuid import uuid4

import pymupdf
import pytest
import test_privacy_browser_rehearsal as privacy_harness
from test_privacy_browser_rehearsal import upload

from appraisal_review.adapters.aws.extraction_errors import ExtractionError
from appraisal_review.adapters.local.pdf_writer import LocalPDFWriter
from appraisal_review.adapters.local.service import LocalReviewService
from appraisal_review.application.bootstrap import build_controller
from appraisal_review.application.document_review import document_adapters
from appraisal_review.config import Settings
from appraisal_review.domain.confidence import confirm_side
from appraisal_review.domain.document_transfer import digest_bytes
from appraisal_review.domain.factor_models import WorkflowStatus

rehearsal_workspace = privacy_harness.rehearsal_workspace
privacy_rehearsal = privacy_harness.rehearsal


def test_raster_case_uses_actual_template_and_completes_only_confirmed_revision(privacy_rehearsal):
    bridge, client, ready = privacy_rehearsal
    helper = importlib.import_module("privacy_raster_fixture")
    for source_id in bridge.source_ids:
        upload(client, source_id)
    fixture = asyncio.run(
        helper.create_sanitized_synthetic_case(
            bridge.config.workspace / "writer-case",
            bridge.principal,
            bridge.documents,
            bridge.sink.receipts,
            snapshot=ready[0],
            approve_authored_synthetic_rules=True,
        )
    )
    config = fixture.configuration
    material = fixture.snapshot.material
    local = LocalReviewService(config)
    assert local.snapshot.material == material
    assert local.snapshot.revision.reference == fixture.snapshot.revision.reference
    assert config.approval_store is None
    assert config.writer is not None
    forms = next(r for r in fixture.snapshot.revision.documents if r.purpose == "forms")
    original = bridge.documents.read(bridge.principal, forms).content
    assert config.writer.template_path.read_bytes() == original
    assert config.writer.template_policy.template_sha256 == forms.content_hash
    assert len(fixture.request.field_map.fields) == 8
    assert len(material.facts.pairs) == 2
    for pair in material.facts.pairs:
        for side in ("target", "comparable"):
            observation = getattr(pair.pair, side)
            assert observation.confidence == 0
            assert getattr(pair, f"{side}_reliability").confirmation is None
            for ref in getattr(pair, f"{side}_sources"):
                assert ref.excerpt == "" and ref.region_id == f"p{ref.page}-image"
                assert material.policy.registry.resolves(ref)
    writer = LocalPDFWriter(
        render_config=config.writer.render, template_policy=config.writer.template_policy
    )

    def controller(current, authorization):
        adapters = document_adapters(config.inputs.parser(), current, authorization)
        return build_controller(
            Settings(runtime_mode="local", synthetic_demo=False),
            adapters=replace(adapters, pdf_writer=writer),
        )

    result = asyncio.run(controller(material, None).review(fixture.request))
    assert result.status == WorkflowStatus.NEEDS_REVIEW
    output = config.writer.output_directory / "completed.pdf"
    assert not output.exists()
    with pytest.raises(ValueError, match="Synthetic authorization"):
        fixture.authorize(fixture.snapshot)
    for pair in material.facts.pairs:
        for side in ("target", "comparable"):
            confirm_side(pair, side, reviewer=fixture.principal.actor.actor_id)
    resumed = type(fixture.snapshot).capture(
        material, str(uuid4()), parent=fixture.snapshot.revision.reference
    )
    fixture._check_authorizable(resumed)
    result = asyncio.run(controller(material, fixture.authorize(resumed)).review(fixture.request))
    (fixture.directory / "controller-result.json").write_text(result.model_dump_json(indent=2))
    assert result.status == WorkflowStatus.COMPLETED, result.model_dump(mode="json")
    assert len(result.case_review.comparisons) == 2
    assert config.writer.template_path.read_bytes() == original
    assert digest_bytes(original) == forms.content_hash
    assert len(result.pdf_result.written_field_ids) == 8
    with pymupdf.open(stream=original, filetype="pdf") as before, pymupdf.open(output) as after:
        assert len(before) == len(after) == 2
        for page in range(2):
            assert before[page].rect == after[page].rect
            # The actual sanitizer's token margin survives byte-for-byte in rendered pixels.
            clip = pymupdf.Rect(595, 0, before[page].rect.width, before[page].rect.height)
            assert (
                before[page].get_pixmap(clip=clip).samples
                == after[page].get_pixmap(clip=clip).samples
            )
            assert ("+5.00%" if page == 0 else "-5.00%") in after[page].get_text()
            assert "優" in after[page].get_text() and "劣" in after[page].get_text()


def test_synthetic_rule_opt_in_rejects_valid_tamper_and_default_stays_candidate(privacy_rehearsal):
    bridge, client, ready = privacy_rehearsal
    helper = importlib.import_module("privacy_raster_fixture")
    for source_id in bridge.source_ids:
        upload(client, source_id)
    material = ready[0].material
    for scoped in material.policy.rule_sets:
        scoped.rules.rules[0].intervals[0].maximum = 8.0
        scoped.rules.rules[0].intervals[1].minimum = 8.0
    tampered = type(ready[0]).capture(material, str(uuid4()))
    with pytest.raises(ValueError, match="exact authored synthetic rules"):
        asyncio.run(
            helper.create_sanitized_synthetic_case(
                bridge.config.workspace / "wrong-rules",
                bridge.principal,
                bridge.documents,
                bridge.sink.receipts,
                snapshot=tampered,
                approve_authored_synthetic_rules=True,
            )
        )
    assert not (bridge.config.workspace / "wrong-rules").exists()
    fixture = asyncio.run(
        helper.create_sanitized_synthetic_case(
            bridge.config.workspace / "pending-rules",
            bridge.principal,
            bridge.documents,
            bridge.sink.receipts,
            snapshot=ready[0],
        )
    )
    assert all(s.rules.status == "candidate" for s in fixture.snapshot.material.policy.rule_sets)
    assert fixture.configuration.approval_store is None
    assert not list(fixture.configuration.writer.output_directory.iterdir())


@pytest.mark.parametrize("forgery", ["original", "excerpt", "geometry"])
def test_injected_model_cannot_supply_native_or_outside_evidence(privacy_rehearsal, forgery):
    bridge, client, ready = privacy_rehearsal
    helper = importlib.import_module("privacy_raster_fixture")
    for source_id in bridge.source_ids:
        upload(client, source_id)
    refs = {r.purpose: r for r in ready[0].revision.documents}
    calls = []

    def proposed(source, page, png):
        calls.append((source, page, png))
        answer = helper.authored_raster_proposal(source, page, png)
        citation = answer.rules[0].evidence[0]
        if forgery == "original":
            native = next(iter(bridge.config.sources.values())).snapshot
            citation.document_id = str(native.document_id)
            citation.content_hash = native.source_digest
        elif forgery == "excerpt":
            citation.excerpt = "合成測試規則"
        else:
            citation.bbox = (0.0, 0.0, 816.0, 420.0)
        return answer

    with pytest.raises(ExtractionError, match="invalid_source_reference"):
        asyncio.run(
            helper.prepare_sanitized_synthetic_material(
                bridge.documents,
                bridge.principal,
                ready[0].material.policy.identity,
                refs["criteria"],
                refs["forms"],
                proposed,
            )
        )
    assert len(calls) == 1
    source, page, png = calls[0]
    assert source.document_id == refs["criteria"].document_id and page == 1
    assert png.startswith(b"\x89PNG")
    assert not source.pages[0].has_text
