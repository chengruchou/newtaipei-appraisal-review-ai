"""Versioned template/font/field-map registry validation and approved-font binding."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_formal_pdf_output import (
    TOKEN,
    multi_context_map,
    multi_request,
    placeholder_map,
    render_config,
    vera_font,
)

from appraisal_review.adapters.local.pdf_config import PDFTemplatePolicy, field_map_sha256
from appraisal_review.adapters.local.pdf_writer import LocalPDFWriter
from appraisal_review.adapters.local.template_registry import (
    ApprovedFont,
    TemplatePageGeometry,
    TemplateRegistry,
    TemplateVersion,
    font_sha256,
    load_template_registry,
)
from appraisal_review.domain.pdf_models import PDFFontError


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
