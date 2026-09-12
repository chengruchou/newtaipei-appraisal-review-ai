"""Synthetic fixture acceptance using real C2, parser, Controller and PDF writer."""

import asyncio
import importlib
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pymupdf
import pytest
from pypdf import PdfReader

from appraisal_review.adapters.local.approval import LocalApprovalStore
from appraisal_review.adapters.local.pdf_writer import LocalPDFWriter
from appraisal_review.application.bootstrap import build_controller
from appraisal_review.application.document_review import document_adapters
from appraisal_review.config import Settings
from appraisal_review.domain.confidence import confirm_side
from appraisal_review.domain.document_transfer import DocumentFault, digest_bytes
from appraisal_review.domain.factor_models import WorkflowStatus


@pytest.fixture
def helper(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    return importlib.import_module("integration_fixture")


def test_fresh_cases_have_independent_opaque_ids_and_exact_c2_parser_evidence(helper, tmp_path):
    first = asyncio.run(helper.create_integration_fixture(tmp_path / "first"))
    second = asyncio.run(helper.create_integration_fixture(tmp_path / "second"))
    identities = []
    for fixture in (first, second):
        revision = fixture.snapshot.revision
        material = fixture.snapshot.material
        identities.extend(
            [revision.reference.case_id, fixture.principal.actor.actor_id]
            + [ref.document_id for ref in revision.documents]
        )
        assert len(revision.rules) == len(material.facts.pairs) == 2
        assert fixture.run.revision == revision.reference
        for reference in revision.documents:
            admitted = fixture.documents.read_snapshot(fixture.principal, fixture.run, reference)
            spec = next(
                d
                for d in fixture.configuration.inputs.documents
                if d.document_id == reference.document_id
            )
            assert admitted.content == spec.path.read_bytes()
            assert digest_bytes(admitted.content) == reference.content_hash == spec.expected_hash
            parsed = asyncio.run(
                fixture.configuration.inputs.parser().parse_document(spec.path.as_uri())
            )
            source = next(
                d
                for d in material.policy.registry.documents
                if d.document_id == reference.document_id
            )
            assert parsed.source == source
            assert "合成測試" in source.model_dump_json()
        for pair in material.facts.pairs:
            for side in ("target", "comparable"):
                observation = getattr(pair.pair, side)
                assert observation.confidence == 0
                assert all(e.confidence == 0 for e in observation.evidence)
                refs = getattr(pair, f"{side}_sources")
                assert len(refs) == 1 and material.policy.registry.resolves(refs[0])
                assert observation.raw_text == refs[0].excerpt
                assert observation.evidence[0].bounding_box == refs[0].bbox
                assert getattr(pair, f"{side}_reliability").confirmation is None
        assert fixture.configuration.approval_store is None
        assert not (fixture.directory / "approval").exists()
    assert all(UUID(value).version == 4 for value in identities)
    assert len(identities) == len(set(identities))
    with pytest.raises(DocumentFault):
        first.documents.read(second.principal, first.snapshot.revision.documents[0])


@pytest.mark.parametrize("approved", [False, True])
def test_actual_controller_and_writer_gate_synthetic_multi_context_output(
    helper, tmp_path, approved, monkeypatch
):
    fixture = asyncio.run(
        helper.create_integration_fixture(tmp_path / "case", approve_synthetic=approved)
    )
    config = fixture.configuration
    material = fixture.snapshot.material
    assert config.writer is not None
    writer = LocalPDFWriter(
        render_config=config.writer.render, template_policy=config.writer.template_policy
    )
    writes = AsyncMock(wraps=writer.write_pdf)
    monkeypatch.setattr(writer, "write_pdf", writes)
    authority = LocalApprovalStore(config.approval_store) if config.approval_store else None
    adapters = document_adapters(config.inputs.parser(), material, authority)
    controller = build_controller(
        Settings(runtime_mode="local", synthetic_demo=False),
        adapters=replace(adapters, pdf_writer=writer),
    )
    before = {d.path: d.path.read_bytes() for d in config.inputs.documents}
    before[config.writer.template_path] = config.writer.template_path.read_bytes()
    result = asyncio.run(controller.review(fixture.request))
    assert result.status == (
        WorkflowStatus.COMPLETED if approved else WorkflowStatus.NEEDS_REVIEW
    ), result.model_dump(mode="json", exclude={"audit_events", "case_review", "review"})
    output = config.writer.output_directory / "completed.pdf"
    assert output.exists() is approved
    assert all(path.read_bytes() == content for path, content in before.items())
    assert all(
        getattr(pair.pair, side).confidence == 0
        for pair in material.facts.pairs
        for side in ("target", "comparable")
    )
    if approved:
        writes.assert_awaited_once()
        assert authority.permits(material)
        assert len(result.case_review.comparisons) == 2
        assert set(result.pdf_result.written_field_ids) == {
            field.field_id for field in fixture.request.field_map.fields
        }
        reader = PdfReader(output)
        assert len(reader.pages) == 2
        assert "+5.00%" in reader.pages[0].extract_text()
        assert "-5.00%" in reader.pages[1].extract_text()
        for page in reader.pages:
            assert "SYNTHETIC OUTPUT" in page.extract_text()
            assert "優" in page.extract_text() and "劣" in page.extract_text()
        assert config.writer.render.approved_font_sha256 == fixture.font_sha256
    else:
        writes.assert_not_awaited()
        assert result.pdf_result is None


def test_fixture_font_is_pinned_and_has_real_cjk_glyphs(helper, tmp_path):
    fixture = asyncio.run(helper.create_integration_fixture(tmp_path / "case"))
    font_bytes = fixture.configuration.writer.render.font_path.read_bytes()
    assert digest_bytes(font_bytes) == helper.BUNDLED_CJK_SHA256 == fixture.font_sha256
    font = pymupdf.Font(fontbuffer=font_bytes)
    glyphs = [font.has_glyph(ord(char)) for char in "合成測試道路寬度"]
    assert all(glyphs) and len(set(glyphs)) == len(glyphs)
    with pymupdf.open(fixture.configuration.inputs.documents[0].path) as pdf:
        assert "合成測試" in pdf[0].get_text()
        assert pdf[0].get_pixmap().width > 0


def test_fixture_rejects_changed_bundled_font_without_creating_case(helper, monkeypatch, tmp_path):
    monkeypatch.setattr(pymupdf, "Font", lambda *_: SimpleNamespace(buffer=b"changed"))
    destination = tmp_path / "case"
    with pytest.raises(ValueError, match="font digest"):
        asyncio.run(helper.create_integration_fixture(destination))
    assert not destination.exists()


def test_fixture_never_reuses_an_existing_directory(helper, tmp_path):
    original = tmp_path / "preserved.txt"
    original.write_text("synthetic existing data")
    with pytest.raises(FileExistsError):
        asyncio.run(helper.create_integration_fixture(tmp_path, approve_synthetic=True))
    assert original.read_text() == "synthetic existing data"
    assert not (tmp_path / "approval").exists()


def confirmed_revision(fixture):
    candidate = fixture.snapshot.revise(fixture.snapshot.material, str(uuid4()))
    material = candidate.material
    for pair in material.facts.pairs:
        for side in ("target", "comparable"):
            confirm_side(pair, side, reviewer=fixture.principal.actor.actor_id)
    return helper_snapshot(candidate, material)


def helper_snapshot(previous, material):
    return type(previous).capture(
        material,
        previous.revision.reference.revision_id,
        parent=previous.revision.parent,
        changes=previous.revision.changes,
    )


def test_synthetic_authorize_requires_existing_confirmations_and_binds_exact_revision(
    helper, tmp_path
):
    fixture = asyncio.run(helper.create_integration_fixture(tmp_path / "case"))
    with pytest.raises(ValueError, match="Synthetic authorization"):
        fixture.authorize(fixture.snapshot)
    resumed = confirmed_revision(fixture)
    original = resumed._material_json, resumed._revision_json
    authority = fixture.authorize(resumed)
    assert authority.permits(resumed.material)
    assert not authority.permits(fixture.snapshot.material)
    assert original == (resumed._material_json, resumed._revision_json)
    assert not (fixture.directory / "approval").exists()
    changed = resumed.material
    changed.facts.pairs[0].pair.target.value.value = 11.0
    assert not authority.permits(changed)
    assert all(
        getattr(pair.pair, side).confidence == 0
        for pair in resumed.material.facts.pairs
        for side in ("target", "comparable")
    )


@pytest.mark.parametrize(
    "change",
    [
        "case",
        "rules",
        "registry",
        "template",
        "font",
        "source-bytes",
        "confidence",
        "missing-confirmation",
        "foreign-reviewer",
        "stale-confirmation",
        "forged-revision",
    ],
)
def test_synthetic_authorize_rejects_changed_boundaries(helper, tmp_path, change):
    fixture = asyncio.run(helper.create_integration_fixture(tmp_path / "case"))
    resumed = confirmed_revision(fixture)
    material = resumed.material
    pair = material.facts.pairs[0]
    if change == "case":
        material.policy.identity.case_id = material.facts.identity.case_id = str(uuid4())
    elif change == "rules":
        material.policy.rule_sets[0].rules.rules[0].intervals[0].maximum = 8.0
        material.policy.rule_sets[0].rules.rules[0].intervals[1].minimum = 8.0
    elif change == "registry":
        material.policy.registry.documents[0].content_hash = "f" * 64
    elif change in {"template", "font", "source-bytes"}:
        path = {
            "template": fixture.configuration.writer.template_path,
            "font": fixture.configuration.writer.render.font_path,
            "source-bytes": fixture.configuration.inputs.documents[0].path,
        }[change]
        path.write_bytes(path.read_bytes() + b"synthetic modification")
    elif change == "confidence":
        pair.pair.target.confidence = 0.5
        confirm_side(pair, "target", reviewer=fixture.principal.actor.actor_id)
    elif change == "missing-confirmation":
        pair.target_reliability.confirmation = None
    elif change == "foreign-reviewer":
        confirm_side(pair, "target", reviewer=str(uuid4()))
    elif change == "stale-confirmation":
        pair.pair.target.value.value = 11.0
    # Keep tampered models structurally valid so the fixture authority itself
    # must reject them. A foreign case cannot retain the original case's parent.
    candidate = (
        type(resumed).capture(material, str(uuid4()))
        if change == "case"
        else helper_snapshot(resumed, material)
    )
    if change == "forged-revision":
        candidate = type(candidate)(fixture.snapshot._material_json, candidate._revision_json)
    with pytest.raises(ValueError, match="Synthetic authorization"):
        fixture.authorize(candidate)
    assert not (fixture.directory / "approval").exists()
