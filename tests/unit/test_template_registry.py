"""Versioned template/font/field-map registry validation and approved-font binding."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from pypdf import PdfReader
from test_formal_pdf_output import (
    TOKEN,
    multi_context_map,
    multi_request,
    placeholder_map,
    render_config,
    vera_font,
    write_box_glyph_font,
)

from appraisal_review.adapters.local.pdf_config import PDFTemplatePolicy, field_map_sha256
from appraisal_review.adapters.local.pdf_writer import LocalPDFWriter
from appraisal_review.adapters.local.placeholder_backfill import LocalPlaceholderBackfill
from appraisal_review.adapters.local.template_registry import (
    ApprovedFont,
    TemplatePageGeometry,
    TemplateRegistry,
    TemplateVersion,
    font_sha256,
    load_template_registry,
)
from appraisal_review.domain.pdf_models import PDFField, PDFFieldMap, PDFFontError, PDFWriteRequest


def entry(**changes: object) -> TemplateVersion:
    field_map = multi_context_map()
    values: dict[str, object] = {
        "template_id": "formal-v1",
        "version": "2026.09",
        "policy": PDFTemplatePolicy(
            template_id="formal-v1",
            template_sha256="0" * 64,
            field_map_sha256=field_map_sha256(field_map),
            editable_pages=frozenset({1, 2}),
        ),
        "field_map": field_map,
        "fonts": (ApprovedFont(font_name="AppraisalCJK", sha256=font_sha256(vera_font())),),
        "pages": (
            TemplatePageGeometry(page=1, width=320.0, height=220.0),
            TemplatePageGeometry(page=2, width=320.0, height=220.0),
        ),
    }
    values.update(changes)
    return TemplateVersion.model_validate(values)


def test_registration_validates_identity_digest_pages_and_geometry() -> None:
    registered = entry()
    assert registered.contexts() == (
        ("regional", "target-a", "comp-1"),
        ("regional", "target-a", "comp-2"),
    )
    assert registered.placeholder_tokens() == ()
    with pytest.raises(ValidationError, match="identity must match"):
        entry(template_id="another-template", version="2026.09")
    with pytest.raises(ValidationError, match="approved digest"):
        entry(
            policy=PDFTemplatePolicy(
                template_id="formal-v1",
                template_sha256="0" * 64,
                field_map_sha256="1" * 64,
                editable_pages=frozenset({1, 2}),
            )
        )
    with pytest.raises(ValidationError, match="cover every classified page"):
        entry(pages=(TemplatePageGeometry(page=1, width=320.0, height=220.0),))
    with pytest.raises(ValidationError, match="exceeds its declared page geometry"):
        entry(
            pages=(
                TemplatePageGeometry(page=1, width=150.0, height=220.0),
                TemplatePageGeometry(page=2, width=320.0, height=220.0),
            )
        )
    with pytest.raises(ValidationError, match="explicitly editable pages"):
        entry(
            policy=PDFTemplatePolicy(
                template_id="formal-v1",
                template_sha256="0" * 64,
                field_map_sha256=field_map_sha256(multi_context_map()),
                editable_pages=frozenset({1}),
                reference_only_pages=frozenset({2}),
            )
        )
    with pytest.raises(ValidationError, match="unique"):
        entry(
            fonts=(
                ApprovedFont(font_name="AppraisalCJK", sha256="0" * 64),
                ApprovedFont(font_name="AppraisalCJK", sha256="1" * 64),
            )
        )


def test_placeholder_entry_registers_with_its_own_policy() -> None:
    field_map = placeholder_map()
    registered = entry(
        field_map=field_map,
        policy=PDFTemplatePolicy(
            template_id="formal-v1",
            template_sha256="0" * 64,
            field_map_sha256=field_map_sha256(field_map),
            editable_pages=frozenset({1, 2}),
        ),
    )
    assert registered.contexts() == ()
    assert registered.placeholder_tokens() == (TOKEN,)


def test_registry_selects_exact_versions_only(tmp_path: Path) -> None:
    registry = TemplateRegistry(templates=(entry(), entry(version="2026.10")))
    assert registry.select("formal-v1", "2026.10").version == "2026.10"
    with pytest.raises(ValueError, match="not registered"):
        registry.select("formal-v1", "2026.11")
    with pytest.raises(ValidationError, match="registered exactly once"):
        TemplateRegistry(templates=(entry(), entry()))
    stored = tmp_path / "registry.json"
    stored.write_text(registry.model_dump_json())
    assert load_template_registry(stored).select("formal-v1", "2026.09") == entry()


def test_approved_font_binding_is_enforced_at_write_time(tmp_path: Path) -> None:
    request, policy = multi_request(tmp_path)
    registered = entry(
        policy=policy,
        fonts=(ApprovedFont(font_name="AppraisalCJK", sha256=font_sha256(vera_font())),),
    )
    bound = registered.render_configuration(
        render_config(), font_name="AppraisalCJK", font_path=vera_font()
    )
    assert bound.approved_font_sha256 == font_sha256(vera_font())
    result = asyncio.run(
        LocalPDFWriter(render_config=bound, template_policy=policy).write_pdf(request)
    )
    assert result.artifact_created
    with pytest.raises(ValueError, match="not approved"):
        registered.render_configuration(
            render_config(), font_name="UnknownFont", font_path=vera_font()
        )
    tampered = tmp_path / "tampered.ttf"
    tampered.write_bytes(vera_font().read_bytes() + b"\n")
    mismatched = bound.model_copy(update={"font_path": tampered})
    second, _ = multi_request(tmp_path, "second.pdf")
    with pytest.raises(PDFFontError, match="approved digest"):
        asyncio.run(
            LocalPDFWriter(render_config=mismatched, template_policy=policy).write_pdf(second)
        )
    assert not (tmp_path / "second.pdf").exists()


def test_registered_multicontext_assets_write_reopen_and_backfill(tmp_path: Path) -> None:
    request, policy = multi_request(tmp_path, "placeholder.pdf")
    original_template = (tmp_path / "template.pdf").read_bytes()
    field_map = PDFFieldMap(
        template_id=request.field_map.template_id,
        fields=[
            *request.field_map.fields,
            PDFField(
                field_id="private-identity",
                page=1,
                bounding_box=(40.0, 40.0, 260.0, 70.0),
                placeholder_token=TOKEN,
            ),
        ],
    )
    policy = policy.model_copy(update={"field_map_sha256": field_map_sha256(field_map)})
    font_path = tmp_path / "synthetic.ttf"
    revealed = "新北市測試"
    write_box_glyph_font(font_path, TOKEN + revealed + "+-%.0123456789", "RegistrySyntheticCJK")
    approved_digest = font_sha256(font_path)
    registered = entry(
        policy=policy,
        field_map=field_map,
        fonts=(ApprovedFont(font_name="RegistrySyntheticCJK", sha256=approved_digest),),
    )
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(TemplateRegistry(templates=(registered,)).model_dump_json())
    selected = load_template_registry(registry_path).select("formal-v1", "2026.09")
    config = selected.render_configuration(
        render_config(), font_name="RegistrySyntheticCJK", font_path=font_path
    )
    request = PDFWriteRequest.model_validate(
        {**request.model_dump(), "field_map": selected.field_map.model_dump()}
    )
    writer = LocalPDFWriter(render_config=config, template_policy=selected.policy)
    result = asyncio.run(writer.write_pdf(request))
    expected_ids = [field.field_id for field in selected.field_map.fields]
    assert result.written_field_ids == expected_ids
    artifact = tmp_path / "placeholder.pdf"
    placeholder_bytes = artifact.read_bytes()
    reader = PdfReader(artifact)
    assert len(reader.pages) == 2
    assert "+5.00%" in reader.pages[0].extract_text()
    assert "-3.00%" in reader.pages[1].extract_text()
    assert TOKEN in reader.pages[0].extract_text()
    assert revealed not in "".join(page.extract_text() for page in reader.pages)
    assert reader.metadata is not None
    assert json.loads(reader.metadata["/AppraisalReviewFieldIds"]) == expected_ids
    assert reader.metadata["/AppraisalReviewWriterVersion"] == "2"
    backfill = LocalPlaceholderBackfill(
        render_config=config, template_policy=selected.policy, values={TOKEN: revealed}
    )
    local = asyncio.run(
        backfill.backfill(
            request.model_copy(update={"destination_uri": (tmp_path / "revealed.pdf").as_uri()}),
            placeholder_artifact=artifact,
            expected_artifact_sha256=hashlib.sha256(placeholder_bytes).hexdigest(),
        )
    )
    assert local.written_field_ids == expected_ids
    local_pages = PdfReader(tmp_path / "revealed.pdf").pages
    assert revealed in local_pages[0].extract_text()
    assert TOKEN not in local_pages[0].extract_text()
    assert "+5.00%" in local_pages[0].extract_text()
    assert "-3.00%" in local_pages[1].extract_text()
    assert artifact.read_bytes() == placeholder_bytes
    assert (tmp_path / "template.pdf").read_bytes() == original_template
    assert font_sha256(font_path) == approved_digest
