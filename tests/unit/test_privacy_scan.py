"""Synthetic PDFs and explicit OCR doubles; real engine acceptance is separate."""

from __future__ import annotations

import asyncio
import hashlib
import os
import runpy
import sys
import time
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import jsonschema
import pymupdf
import pytest
from pydantic import ValidationError

from appraisal_review.adapters.local.privacy.detector import detect_candidates
from appraisal_review.adapters.local.privacy.ocr import (
    OCRAsset,
    TesseractConfig,
    TesseractOCR,
    parse_tsv,
)
from appraisal_review.adapters.local.privacy.pdf_worker import ScanLimits
from appraisal_review.adapters.local.privacy.process import ScanFailure, run_bounded
from appraisal_review.adapters.local.privacy.scanner import LocalPrivacyScanner
from appraisal_review.adapters.local.privacy.source import (
    IsolatedPrivacyPDF,
    LocalRaster,
    LocalSnapshotStore,
    confined_read,
)
from appraisal_review.domain.privacy_models import LocalSourcePage, PrivacyRegion, SensitiveCategory
from appraisal_review.domain.privacy_scan import (
    PageScan,
    PrivacyScanReport,
    ScanIssue,
    TextObservation,
    pixel_region,
)


def make_pdf(path: Path, kinds=("native",), *, rotation=0, crop=False):
    with pymupdf.open() as picture:
        p = picture.new_page(width=300, height=400)
        p.insert_text((25, 80), "ocr@example.invalid", fontsize=14)
        png = p.get_pixmap().tobytes("png")
    with pymupdf.open() as doc:
        for kind in kinds:
            page = doc.new_page(width=300, height=400)
            if kind in {"scanned", "mixed"}:
                page.insert_image(page.rect, stream=png)
            if kind in {"native", "mixed"}:
                page.insert_text((30, 50), "Name: Synthetic Person")
                page.insert_text((30, 110), "native@example.invalid")
            if crop:
                page.set_cropbox(pymupdf.Rect(10, 20, 290, 380))
            page.set_rotation(rotation)
        doc.save(path)


def store_for(tmp_path, limits=None):
    return LocalSnapshotStore(tmp_path, IsolatedPrivacyPDF(tmp_path, limits))


def tsv(*, text="ocr@example.invalid", confidence="72.5", x="20", width="120"):
    return (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        f"5\t1\t1\t1\t1\t1\t{x}\t30\t{width}\t15\t{confidence}\t{text}\n"
    ).encode()


def ocr_double():
    ocr = Mock(spec=TesseractOCR)
    ocr.preflight.return_value = "synthetic-engine-version"
    ocr.config = Mock(
        assets=(
            OCRAsset(language="eng", sha256="a" * 64),
            OCRAsset(language="chi_tra", sha256="b" * 64),
        )
    )
    return ocr


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_real_native_snapshot_preserves_bytes_crop_and_rotation(tmp_path, rotation):
    path = tmp_path / "synthetic.pdf"
    make_pdf(path, rotation=rotation, crop=True)
    original = path.read_bytes()
    store = store_for(tmp_path)
    snapshot = store.capture(path.name, case_id=uuid4())
    report = asyncio.run(LocalPrivacyScanner(store).scan(snapshot))
    assert report.status == "needs_review"
    assert report.pages[0].status == "processed"
    assert report.pages[0].mode == "native"
    assert len(report.pages[0].manual_categories) == len(SensitiveCategory)
    assert {c.category for c in report.candidates} >= {
        SensitiveCategory.EMAIL,
        SensitiveCategory.NAME,
    }
    assert all(o.confidence is None for o in report.pages[0].observations)
    assert snapshot.pages[0].rotation == rotation
    assert snapshot.pages[0].crop_box == (10, 20, 290, 380)
    assert snapshot.pages[0].width == 280 and snapshot.pages[0].height == 360
    raster = store.pdf.render(store.read(snapshot), 1, timeout=5.0)
    assert (raster.width, raster.height) == (560, 720)
    assert path.read_bytes() == original == store.read(snapshot)
    assert snapshot.source_digest == hashlib.sha256(original).hexdigest()
    assert store.source_file(snapshot) == path
    assert "source_file" not in report.model_dump_json()
    assert PrivacyScanReport.model_validate_json(report.model_dump_json()) == report


def test_snapshot_survives_path_replacement_and_rejects_forged_identity(tmp_path):
    path = tmp_path / "synthetic.pdf"
    make_pdf(path)
    store = store_for(tmp_path)
    snapshot = store.capture(path.name, case_id=uuid4())
    original = store.read(snapshot)
    path.write_bytes(b"replacement after capture")
    assert store.read(snapshot) == original
    assert asyncio.run(LocalPrivacyScanner(store).scan(snapshot)).status == "needs_review"
    for field, value in (
        ("case_id", uuid4()),
        ("source_digest", "a" * 64),
        ("snapshot_id", uuid4()),
    ):
        with pytest.raises(ScanFailure, match="invalid_scan_input"):
            store.read(snapshot.model_copy(update={field: value}))
    store.forget(snapshot)
    with pytest.raises(ScanFailure):
        store.read(snapshot)


@pytest.mark.parametrize(
    "relative",
    [
        "../source.pdf",
        "/source.pdf",
        "a/../source.pdf",
        "a\\b.pdf",
        "C:/source.pdf",
        "a//b.pdf",
        "./source.pdf",
        "a\0.pdf",
        "",
    ],
)
def test_paths_cannot_escape_confined_root(tmp_path, relative):
    with pytest.raises(ScanFailure, match="invalid_scan_input"):
        confined_read(tmp_path, relative, 1000)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux openat/no-follow security acceptance")
def test_linux_refuses_symlink_parent_leaf_hardlink_and_fifo(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "source.pdf").write_bytes(b"synthetic bytes")
    root = tmp_path / "root"
    root.mkdir()
    (root / "parent").symlink_to(outside, target_is_directory=True)
    (root / "leaf").symlink_to(outside / "source.pdf")
    os.link(outside / "source.pdf", root / "hardlink")
    os.mkfifo(root / "fifo")
    for relative in ("parent/source.pdf", "leaf", "hardlink", "fifo"):
        with pytest.raises(ScanFailure):
            confined_read(root, relative, 1000)


def test_capture_rejects_over_limit_and_mid_read_mutation(tmp_path, monkeypatch):
    path = tmp_path / "source.pdf"
    path.write_bytes(b"synthetic bytes")
    with pytest.raises(ScanFailure, match="resource_limit"):
        confined_read(tmp_path, path.name, 3)
    original_read = os.read

    def mutate(fd, size):
        data = original_read(fd, size)
        if data:
            path.write_bytes(b"changed with a different length")
        return data

    monkeypatch.setattr(os, "read", mutate)
    with pytest.raises(ScanFailure, match="invalid_scan_input"):
        confined_read(tmp_path, path.name, 1000)


@pytest.mark.parametrize("kind", ["scanned", "mixed", "blank"])
def test_real_image_and_empty_pages_block_without_ocr(tmp_path, kind):
    make_pdf(tmp_path / "synthetic.pdf", ("native", kind))
    store = store_for(tmp_path)
    snapshot = store.capture("synthetic.pdf", case_id=uuid4())
    scanner = LocalPrivacyScanner(store)
    report = asyncio.run(scanner.scan(snapshot))
    assert report.status == "blocked"
    assert report.pages[0].status == "processed"
    assert report.pages[1].status == "blocked"
    assert report.pages[1].issues == (ScanIssue.OCR_UNAVAILABLE,)
    assert len(report.pages) == 2
    with pytest.raises(ScanFailure, match="ocr_unavailable"):
        asyncio.run(scanner.detect(snapshot))
    capability = scanner.capabilities()
    assert capability.native_pdf and not capability.local_ocr
    assert capability.visual_detector == "manual_review_required"


def test_real_mixed_render_with_explicit_ocr_double_preserves_scores(tmp_path):
    make_pdf(tmp_path / "synthetic.pdf", ("scanned", "mixed"))
    store = store_for(tmp_path)
    snapshot = store.capture("synthetic.pdf", case_id=uuid4())
    ocr = ocr_double()
    ocr.recognize.side_effect = lambda raster, page, **kw: parse_tsv(
        tsv(), raster, page, max_text=1000
    )
    report = asyncio.run(LocalPrivacyScanner(store, ocr).scan(snapshot))
    assert report.status == "needs_review"
    assert [p.mode for p in report.pages] == ["scanned", "mixed"]
    assert all(ScanIssue.LOW_CONFIDENCE in p.issues for p in report.pages)
    assert all(
        o.confidence == 0.725 for p in report.pages for o in p.observations if o.origin == "ocr"
    )
    assert any(o.origin == "native" for o in report.pages[1].observations)
    assert ocr.recognize.call_count == 2
    assert len(asyncio.run(LocalPrivacyScanner(store, ocr).detect(snapshot))) > 0


@pytest.mark.parametrize("outcome", ["empty", "timeout", "malformed", "error"])
def test_ocr_failures_never_yield_complete_scan(tmp_path, outcome):
    make_pdf(tmp_path / "synthetic.pdf", ("mixed",))
    store = store_for(tmp_path)
    snapshot = store.capture("synthetic.pdf", case_id=uuid4())
    ocr = ocr_double()
    if outcome == "empty":
        ocr.recognize.return_value = ()
    elif outcome == "timeout":
        ocr.recognize.side_effect = ScanFailure(ScanIssue.TIMEOUT)
    elif outcome == "malformed":
        ocr.recognize.side_effect = ScanFailure(ScanIssue.OCR_FAILED)
    else:
        ocr.recognize.side_effect = RuntimeError("SYNTHETIC-PRIVATE-ERROR")
    report = asyncio.run(LocalPrivacyScanner(store, ocr).scan(snapshot))
    assert report.status == "blocked"
    assert "SYNTHETIC-PRIVATE-ERROR" not in report.model_dump_json()


@pytest.mark.parametrize("data", [b"not a PDF", b"%PDF-1.7\ncorrupt"])
def test_bad_pdf_is_explicitly_rejected(tmp_path, data):
    (tmp_path / "bad.pdf").write_bytes(data)
    with pytest.raises(ScanFailure, match="invalid_scan_input"):
        store_for(tmp_path).capture("bad.pdf", case_id=uuid4())


def test_encrypted_and_oversized_pdf_rejected(tmp_path):
    with pymupdf.open() as doc:
        doc.new_page()
        doc.save(
            tmp_path / "encrypted.pdf",
            encryption=pymupdf.PDF_ENCRYPT_AES_256,
            owner_pw="synthetic-owner",
            user_pw="synthetic-user",
        )
    with pytest.raises(ScanFailure):
        store_for(tmp_path).capture("encrypted.pdf", case_id=uuid4())
    make_pdf(tmp_path / "two.pdf", ("native", "native"))
    for limits in (ScanLimits(max_pages=1), ScanLimits(max_pixels=100), ScanLimits(max_text=2)):
        with pytest.raises(ScanFailure):
            store_for(tmp_path, limits).capture("two.pdf", case_id=uuid4())


def test_store_has_explicit_memory_limit(tmp_path):
    make_pdf(tmp_path / "synthetic.pdf")
    store = LocalSnapshotStore(tmp_path, IsolatedPrivacyPDF(tmp_path), max_store_bytes=1)
    with pytest.raises(ScanFailure, match="resource_limit"):
        store.capture("synthetic.pdf", case_id=uuid4())


def test_process_timeout_output_limit_and_error_channels(tmp_path, capsys):
    command = [sys.executable, "-I", "-c"]
    start = time.monotonic()
    with pytest.raises(ScanFailure, match="processing_timeout"):
        run_bounded(
            [*command, "import time; time.sleep(10)"],
            b"",
            cwd=tmp_path,
            timeout=0.2,
            max_output=100,
        )
    assert time.monotonic() - start < 5
    with pytest.raises(ScanFailure, match="resource_limit"):
        run_bounded([*command, "print('x'*100000)"], b"", cwd=tmp_path, timeout=5.0, max_output=100)
    with pytest.raises(ScanFailure) as caught:
        run_bounded(
            [*command, "raise RuntimeError('SYNTHETIC-PRIVATE-ERROR')"],
            b"",
            cwd=tmp_path,
            timeout=5.0,
            max_output=100,
        )
    assert "SYNTHETIC-PRIVATE-ERROR" not in str(caught.value)
    assert "SYNTHETIC-PRIVATE-ERROR" not in str(capsys.readouterr())
    assert (
        run_bounded(
            [*command, "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"],
            b"synthetic",
            cwd=tmp_path,
            timeout=5.0,
            max_output=100,
        )
        == b"synthetic"
    )


@pytest.mark.parametrize("bad", ["nan", "inf", "-1", "101"])
def test_ocr_confidence_is_never_repaired_or_invented(bad):
    page = LocalSourcePage(
        number=1, width=300.0, height=400.0, rotation=0, crop_box=(0.0, 0.0, 300.0, 400.0)
    )
    with pytest.raises(ScanFailure, match="ocr_failed"):
        parse_tsv(tsv(confidence=bad), LocalRaster(600, 800, b""), page, max_text=1000)


@pytest.mark.parametrize("bad", ["-1", "nan", "inf", "601"])
def test_ocr_coordinates_cannot_escape_render(bad):
    page = LocalSourcePage(
        number=1, width=300.0, height=400.0, rotation=0, crop_box=(0.0, 0.0, 300.0, 400.0)
    )
    with pytest.raises(ScanFailure):
        parse_tsv(tsv(x=bad), LocalRaster(600, 800, b""), page, max_text=1000)


def test_raster_mapping_uses_actual_rounded_dimensions():
    page = LocalSourcePage(
        number=1, width=300.5, height=400.25, rotation=90, crop_box=(10.0, 20.0, 310.5, 420.25)
    )
    assert pixel_region((0.0, 0.0, 601.0, 801.0), 601, 801, page).bbox == (0.0, 0.0, 300.5, 400.25)
    assert pixel_region((0.0, 0.0, 300.5, 400.5), 601, 801, page).bbox == (
        0.0,
        200.125,
        150.25,
        400.25,
    )
    with pytest.raises(ValueError):
        pixel_region((0.0, 0.0, 1.0, 1.0), 0, 1, page)


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("synthetic@example.invalid", SensitiveCategory.EMAIL),
        ("A123456789", SensitiveCategory.IDENTITY_NUMBER),
        ("12345678", SensitiveCategory.BUSINESS_NUMBER),
        ("0912-345-678", SensitiveCategory.PHONE),
        ("Name:\nSynthetic Person", SensitiveCategory.NAME),
        ("地址: 合成路1號", SensitiveCategory.ADDRESS),
        ("Account: 1234567890", SensitiveCategory.ACCOUNT),
        ("Birth date: 2000-01-01", SensitiveCategory.BIRTH_DATE),
        ("地號: 合成段1地號", SensitiveCategory.PARCEL),
        ("Contact: Synthetic Contact", SensitiveCategory.CASE_CONTACT),
        ("持分: 合成所有權", SensitiveCategory.OWNERSHIP_FINANCIAL),
    ],
)
def test_structured_and_labeled_candidates_keep_local_evidence(text, category):
    observation = TextObservation(
        text=text, origin="native", region=PrivacyRegion(page=1, bbox=(0.0, 0.0, 200.0, 50.0))
    )
    candidates = detect_candidates((observation,))
    match = next(c for c in candidates if c.category == category)
    assert match.raw_text in text and match.region == observation.region
    assert match.confidence is None and match.candidate_id.version == 4


def test_zero_matches_do_not_create_a_safety_claim_or_guess_a_name():
    observation = TextObservation(
        text="Unlabeled Synthetic Person",
        origin="native",
        region=PrivacyRegion(page=1, bbox=(0.0, 0.0, 200.0, 50.0)),
    )
    assert detect_candidates((observation,)) == ()
    page = PageScan(page=1, mode="native", status="processed", observations=(observation,))
    assert SensitiveCategory.NAME in page.manual_categories
    for values in (
        {"status": "processed", "issues": (ScanIssue.TIMEOUT,)},
        {"manual_categories": ()},
        {"observations": ()},
    ):
        with pytest.raises(ValidationError):
            PageScan.model_validate(page.model_copy(update=values))


def test_detector_and_ocr_have_explicit_text_and_candidate_limits():
    observation = TextObservation(
        text="a@example.invalid b@example.invalid",
        origin="native",
        region=PrivacyRegion(page=1, bbox=(0.0, 0.0, 200.0, 50.0)),
    )
    with pytest.raises(ScanFailure, match="resource_limit"):
        detect_candidates((observation,), max_candidates=1)
    page = LocalSourcePage(
        number=1, width=300.0, height=400.0, rotation=0, crop_box=(0.0, 0.0, 300.0, 400.0)
    )
    with pytest.raises(ScanFailure, match="resource_limit"):
        parse_tsv(tsv(), LocalRaster(600, 800, b""), page, max_text=1)
    with pytest.raises(ScanFailure, match="ocr_empty"):
        parse_tsv(tsv(text=""), LocalRaster(600, 800, b""), page, max_text=1000)


def test_ocr_assets_are_explicit_verified_and_never_downloaded(tmp_path, monkeypatch):
    exe, model = tmp_path / "synthetic-engine", tmp_path / "eng.traineddata"
    exe.write_bytes(b"synthetic executable placeholder")
    model.write_bytes(b"synthetic model placeholder")
    config = TesseractConfig(
        executable=exe,
        executable_sha256=hashlib.sha256(exe.read_bytes()).hexdigest(),
        tessdata=tmp_path,
        assets=(OCRAsset(language="eng", sha256=hashlib.sha256(model.read_bytes()).hexdigest()),),
    )
    runner = Mock(return_value=b"tesseract 5.0.0\n")
    monkeypatch.setattr("appraisal_review.adapters.local.privacy.ocr.run_bounded", runner)
    ocr = TesseractOCR(config, tmp_path)
    assert ocr.preflight(timeout=5.0) == "5.0.0"
    model.write_bytes(b"changed")
    with pytest.raises(ScanFailure, match="ocr_unavailable"):
        ocr.preflight(timeout=5.0)
    model.unlink()
    with pytest.raises(ScanFailure, match="ocr_unavailable"):
        ocr.preflight(timeout=5.0)
    assert runner.call_count == 1


def test_tesseract_transport_receives_only_render_and_fixed_arguments(tmp_path, monkeypatch):
    config = TesseractConfig(
        executable=tmp_path / "not-installed",
        executable_sha256="a" * 64,
        tessdata=tmp_path,
        assets=(OCRAsset(language="eng", sha256="b" * 64),),
    )
    runner = Mock(return_value=tsv())
    monkeypatch.setattr("appraisal_review.adapters.local.privacy.ocr.run_bounded", runner)
    ocr = TesseractOCR(config, tmp_path)
    monkeypatch.setattr(ocr, "preflight", Mock(return_value="synthetic-engine-version"))
    raster = LocalRaster(600, 800, b"\x89PNG\r\n\x1a\nsynthetic")
    page = LocalSourcePage(
        number=1, width=300.0, height=400.0, rotation=0, crop_box=(0.0, 0.0, 300.0, 400.0)
    )
    observations = ocr.recognize(raster, page, dpi=144, timeout=5.0, max_text=1000)
    assert observations[0].confidence == 0.725
    command, payload = runner.call_args.args
    assert command[1:3] == ["stdin", "stdout"]
    assert "tessedit_create_tsv=1" in command and payload == raster.png
    assert "synthetic" not in " ".join(command)


def test_scan_contract_is_local_only_and_fixture_is_current(tmp_path):
    import json

    root = Path(__file__).resolve().parents[2]
    exporter = runpy.run_path(str(root / "scripts/export_privacy_scan_contracts.py"))
    exporter["export"](tmp_path)
    for relative in ("schemas/local-privacy-scan-v1.json", "examples/privacy-scan-v1/blocked.json"):
        assert (tmp_path / relative).read_bytes() == (root / relative).read_bytes()
    schema = json.loads((tmp_path / "schemas/local-privacy-scan-v1.json").read_text())
    raw = (tmp_path / "examples/privacy-scan-v1/blocked.json").read_text()
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(json.loads(raw), {**schema, "$ref": "#/$defs/PrivacyScanReport"})
    assert PrivacyScanReport.model_validate_json(raw).status == "blocked"


def test_missing_chinese_language_cannot_claim_image_page_support(tmp_path):
    make_pdf(tmp_path / "synthetic.pdf", ("scanned",))
    store = store_for(tmp_path)
    snapshot = store.capture("synthetic.pdf", case_id=uuid4())
    ocr = ocr_double()
    ocr.config.assets = (OCRAsset(language="eng", sha256="a" * 64),)
    scanner = LocalPrivacyScanner(store, ocr)
    assert not scanner.capabilities().local_ocr
    assert asyncio.run(scanner.scan(snapshot)).status == "blocked"
    ocr.recognize.assert_not_called()


def test_ocr_provenance_is_required_for_processed_scanned_page():
    observation = TextObservation(
        text="synthetic",
        origin="ocr",
        confidence=0.5,
        region=PrivacyRegion(page=1, bbox=(0.0, 0.0, 10.0, 10.0)),
    )
    with pytest.raises(ValidationError, match="OCR provenance"):
        PageScan(page=1, mode="scanned", status="processed", observations=(observation,))


@pytest.mark.parametrize("text", ["Name:", "Name:\n", "姓名\uff1a ", "Account: "])
def test_empty_labels_never_create_missing_sensitive_values(text):
    observation = TextObservation(
        text=text, origin="native", region=PrivacyRegion(page=1, bbox=(0.0, 0.0, 200.0, 50.0))
    )
    assert detect_candidates((observation,)) == ()


def test_detection_deadline_and_page_coverage_are_explicit(tmp_path, monkeypatch):
    observation = TextObservation(
        text="a@example.invalid",
        origin="native",
        region=PrivacyRegion(page=1, bbox=(0.0, 0.0, 200.0, 50.0)),
    )
    with pytest.raises(ScanFailure, match="processing_timeout"):
        detect_candidates((observation,), deadline=0.0)
    make_pdf(tmp_path / "synthetic.pdf", ("native", "native"))
    store = store_for(tmp_path)
    snapshot = store.capture("synthetic.pdf", case_id=uuid4())
    scanner = LocalPrivacyScanner(store)
    monkeypatch.setattr(
        "appraisal_review.adapters.local.privacy.scanner.time.monotonic",
        Mock(side_effect=[0.0, 31.0, 32.0]),
    )
    report = scanner._scan(snapshot)
    assert report.status == "blocked"
    assert [p.issues for p in report.pages] == [(ScanIssue.TIMEOUT,), (ScanIssue.TIMEOUT,)]


def test_snapshot_count_is_bounded_and_forget_releases_a_slot(tmp_path):
    make_pdf(tmp_path / "synthetic.pdf")
    store = store_for(tmp_path)
    snapshots = [store.capture("synthetic.pdf", case_id=uuid4()) for _ in range(8)]
    assert len({s.snapshot_id for s in snapshots}) == 8
    with pytest.raises(ScanFailure, match="resource_limit"):
        store.capture("synthetic.pdf", case_id=uuid4())
    store.forget(snapshots[0])
    assert store.capture("synthetic.pdf", case_id=uuid4()) not in snapshots


def test_observation_flood_is_refused_before_pattern_matching():
    observation = TextObservation(
        text="a@example.invalid",
        origin="native",
        region=PrivacyRegion(page=1, bbox=(0.0, 0.0, 200.0, 50.0)),
    )
    with pytest.raises(ScanFailure, match="resource_limit"):
        detect_candidates((observation,) * 10001)
