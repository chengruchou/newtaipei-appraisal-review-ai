"""Regression evidence for immutable local PDF output inputs."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from unittest.mock import Mock

import pytest
from pypdf import PdfReader
from test_formal_pdf_output import (
    TOKEN,
    multi_request,
    placeholder_map,
    placeholder_request,
    render_config,
    write_box_glyph_font,
)

from appraisal_review.adapters.aws.pdf.s3_pdf_writer import S3PDFWriter
from appraisal_review.adapters.aws.storage.s3_object_store import S3ObjectStore
from appraisal_review.adapters.local.fake_pdf import FakePDFWriter
from appraisal_review.adapters.local.pdf_writer import LocalPDFWriter
from appraisal_review.adapters.local.placeholder_backfill import LocalPlaceholderBackfill
from appraisal_review.domain.pdf_models import PDFFontError, PDFWriteError


@pytest.mark.parametrize("mode", ["multi_context", "placeholder", "backfill"])
@pytest.mark.parametrize("existing_destination", [False, True])
def test_new_output_requires_approved_font_before_real_write(
    tmp_path: Path, mode: str, existing_destination: bool
) -> None:
    if mode == "multi_context":
        request, policy = multi_request(tmp_path, "output.pdf")
    else:
        request, policy = placeholder_request(tmp_path, placeholder_map(), "output.pdf")
    template = (tmp_path / "template.pdf").read_bytes()
    destination = tmp_path / "output.pdf"
    if existing_destination:
        destination.write_bytes(b"existing output must survive rejection")
    original = destination.read_bytes() if existing_destination else None
    config = render_config(approved_font_sha256=None, overwrite_existing=True)
    if mode == "backfill":
        write = LocalPlaceholderBackfill(
            render_config=config, template_policy=policy, values={TOKEN: "private value"}
        ).backfill(request)
    else:
        write = LocalPDFWriter(render_config=config, template_policy=policy).write_pdf(request)

    with pytest.raises(PDFFontError, match="requires an approved font digest"):
        asyncio.run(write)

    assert (tmp_path / "template.pdf").read_bytes() == template
    if existing_destination:
        assert destination.read_bytes() == original
    else:
        assert not destination.exists()
    assert not list(tmp_path.glob(".*.tmp"))


@pytest.mark.parametrize("alias", ["direct", "symlink", "hardlink", "parent_symlink"])
def test_backfill_protects_downloaded_artifact_even_with_overwrite(
    tmp_path: Path, alias: str
) -> None:
    request, policy = placeholder_request(tmp_path, placeholder_map())
    asyncio.run(
        LocalPDFWriter(render_config=render_config(), template_policy=policy).write_pdf(request)
    )
    artifact = tmp_path / "cloud.pdf"
    original = artifact.read_bytes()
    template = (tmp_path / "template.pdf").read_bytes()
    destination = artifact
    if alias in {"symlink", "hardlink"}:
        destination = tmp_path / "alias.pdf"
        if alias == "symlink":
            destination.symlink_to(artifact)
        else:
            destination.hardlink_to(artifact)
    elif alias == "parent_symlink":
        parent = tmp_path / "alias-dir"
        parent.symlink_to(tmp_path, target_is_directory=True)
        destination = parent / artifact.name
    backfill = LocalPlaceholderBackfill(
        render_config=render_config(overwrite_existing=True),
        template_policy=policy,
        values={TOKEN: "private value"},
    )
    with pytest.raises(PDFWriteError):
        asyncio.run(
            backfill.backfill(
                request.model_copy(update={"destination_uri": destination.as_uri()}),
                placeholder_artifact=artifact,
                expected_artifact_sha256=hashlib.sha256(original).hexdigest(),
            )
        )
    assert artifact.read_bytes() == original
    assert (tmp_path / "template.pdf").read_bytes() == template
    assert TOKEN in PdfReader(artifact).pages[0].extract_text()
    assert not list(tmp_path.glob(".*.tmp"))


@pytest.mark.parametrize("alias", ["symlink", "hardlink"])
def test_backfill_rechecks_download_alias_at_atomic_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, alias: str
) -> None:
    request, policy = placeholder_request(tmp_path, placeholder_map())
    asyncio.run(
        LocalPDFWriter(render_config=render_config(), template_policy=policy).write_pdf(request)
    )
    artifact = tmp_path / "cloud.pdf"
    original = artifact.read_bytes()
    template = (tmp_path / "template.pdf").read_bytes()
    destination = tmp_path / "revealed.pdf"
    backfill = LocalPlaceholderBackfill(
        render_config=render_config(overwrite_existing=True),
        template_policy=policy,
        values={TOKEN: "private value"},
    )
    verify = backfill.writer.verifier.verify

    def add_alias(**kwargs: object) -> None:
        verify(**kwargs)
        if alias == "symlink":
            destination.symlink_to(artifact)
        else:
            destination.hardlink_to(artifact)

    monkeypatch.setattr(backfill.writer.verifier, "verify", add_alias)
    with pytest.raises(PDFWriteError):
        asyncio.run(
            backfill.backfill(
                request.model_copy(update={"destination_uri": destination.as_uri()}),
                placeholder_artifact=artifact,
                expected_artifact_sha256=hashlib.sha256(original).hexdigest(),
            )
        )
    assert destination.samefile(artifact)
    assert artifact.read_bytes() == original
    assert (tmp_path / "template.pdf").read_bytes() == template
    assert TOKEN in PdfReader(artifact).pages[0].extract_text()
    assert not list(tmp_path.glob(".*.tmp"))


@pytest.mark.parametrize("capability", ["false", 1, False, True, None, "missing", "automatic"])
def test_s3_wrapper_preserves_literal_true_capability(capability: object) -> None:
    writer = FakePDFWriter()
    if capability == "automatic":
        writer = Mock(reveals_placeholders=False)
    elif capability != "missing":
        writer.supports_multiple_contexts = capability
    wrapper = S3PDFWriter(object_store=Mock(spec=S3ObjectStore), local_writer=writer)
    assert wrapper.supports_multiple_contexts is (capability is True)


def test_render_embeds_font_snapshot_after_approved_path_is_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request, policy = placeholder_request(tmp_path, placeholder_map())
    font_path = tmp_path / "approved.ttf"
    replacement = tmp_path / "replacement.ttf"
    write_box_glyph_font(font_path, TOKEN, family="ApprovedSnapshotFont")
    write_box_glyph_font(replacement, TOKEN, family="UnapprovedReplacementFont")
    approved = font_path.read_bytes()
    writer = LocalPDFWriter(
        render_config=render_config(
            font_path=font_path,
            font_name="SnapshotRegression",
            approved_font_sha256=hashlib.sha256(approved).hexdigest(),
        ),
        template_policy=policy,
    )
    validate = writer.preflight.validate

    def replace_after_preflight(*args: object):
        plan = validate(*args)
        font_path.write_bytes(replacement.read_bytes())
        return plan

    monkeypatch.setattr(writer.preflight, "validate", replace_after_preflight)
    result = asyncio.run(writer.write_pdf(request))
    assert result.artifact_created
    page = PdfReader(tmp_path / "cloud.pdf").pages[0]
    assert TOKEN in page.extract_text()
    embedded_names = [
        str(font.get_object()["/BaseFont"]) for font in page["/Resources"]["/Font"].values()
    ]
    assert any("ApprovedSnapshotFont" in name for name in embedded_names)
    assert not any("UnapprovedReplacementFont" in name for name in embedded_names)


def test_preflight_reads_font_once_for_approval_and_measurement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request, policy = placeholder_request(tmp_path, placeholder_map())
    font_path = tmp_path / "approved.ttf"
    write_box_glyph_font(font_path, TOKEN, family="SingleReadApprovedFont")
    approved = font_path.read_bytes()
    writer = LocalPDFWriter(
        render_config=render_config(
            font_path=font_path,
            font_name="SingleReadRegression",
            approved_font_sha256=hashlib.sha256(approved).hexdigest(),
        ),
        template_policy=policy,
    )
    read_bytes = Path.read_bytes
    reads = 0

    def replace_on_read(path: Path) -> bytes:
        nonlocal reads
        data = read_bytes(path)
        if path == font_path:
            reads += 1
            # Removal after the byte read also prohibits an indirect TTFont
            # path open for measurement or embedding.
            path.unlink()
        return data

    monkeypatch.setattr(Path, "read_bytes", replace_on_read)
    plan = writer.preflight.validate(request, tmp_path / "template.pdf")
    assert reads == 1
    assert plan.font_bytes == approved
    assert plan.fields[0].text_width == pytest.approx(len(TOKEN) * 6.0)
    result = writer.mutation.write_temporary(
        source_path=tmp_path / "template.pdf", output_path=tmp_path / "measured.pdf", plan=plan
    )
    writer.verifier.verify(
        source_path=tmp_path / "template.pdf",
        output_path=tmp_path / "measured.pdf",
        plan=plan,
        mutation_result=result,
    )
    assert TOKEN in PdfReader(tmp_path / "measured.pdf").pages[0].extract_text()


def test_render_rejects_cached_face_with_different_bytes(tmp_path: Path) -> None:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    request, policy = placeholder_request(tmp_path, placeholder_map())
    font_path = tmp_path / "approved.ttf"
    cached_path = tmp_path / "cached.ttf"
    write_box_glyph_font(font_path, TOKEN, family="SharedFaceCacheRegression")
    # Same internal face name with a different character map is a distinct font.
    write_box_glyph_font(cached_path, TOKEN + "Z", family="SharedFaceCacheRegression")
    pdfmetrics.registerFont(TTFont("CachedFaceRegression", str(cached_path)))
    writer = LocalPDFWriter(
        render_config=render_config(
            font_path=font_path,
            font_name="ApprovedFaceRegression",
            approved_font_sha256=hashlib.sha256(font_path.read_bytes()).hexdigest(),
        ),
        template_policy=policy,
    )
    with pytest.raises(PDFFontError, match="differs from the preflight bytes"):
        asyncio.run(writer.write_pdf(request))
    assert not (tmp_path / "cloud.pdf").exists()
    assert not list(tmp_path.glob(".*.tmp"))
