"""Real synthetic PDF rebuild/inspection; OCR and human authority doubles are explicit."""

from __future__ import annotations

import hashlib
import io
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock
from uuid import uuid4

import pymupdf
import pytest
from pypdf import PdfReader, PdfWriter

from appraisal_review.adapters.local.privacy.pdf_worker import ScanLimits
from appraisal_review.adapters.local.privacy.process import ScanFailure
from appraisal_review.adapters.local.privacy.sanitize import IsolatedPrivacyRasterProcessor
from appraisal_review.adapters.local.privacy.source import IsolatedPrivacyPDF, LocalSnapshotStore
from appraisal_review.application.privacy_bundle import (
    LocalSanitizedBundleBuilder,
    LocalSanitizedVerifier,
)
from appraisal_review.application.privacy_guards import PrivacyFault
from appraisal_review.domain.privacy_bundle import SanitizedBundle
from appraisal_review.domain.privacy_models import (
    LocalPrivacyApproval,
    PrivacyReviewCommand,
    ReviewSelection,
    SensitiveCandidate,
    SensitiveCategory,
    placeholder_text,
    privacy_review_digest,
)
from appraisal_review.domain.privacy_scan import TextObservation


def setup(tmp_path, *, rotation=0, fractional=False, crop=False):
    path = tmp_path / "synthetic.pdf"
    with pymupdf.open() as pdf:
        p = pdf.new_page(width=320.25 if fractional else 320, height=420.25 if fractional else 420)
        p.insert_text((35, 60), "CANARY-PRIVATE-1234", fontsize=12)
        p.insert_text((35, 180), "Rate 1.25%  Total 987654.32", fontsize=12)
        p.draw_rect(pymupdf.Rect(25, 155, 290, 210), color=(0, 0, 0))
        pdf.set_metadata({"title": "CANARY-PRIVATE-1234"})
        pdf.embfile_add("CANARY-PRIVATE-1234.txt", b"CANARY-PRIVATE-1234")
        p.add_text_annot((20, 250), "CANARY-PRIVATE-1234")
        p.set_rotation(rotation)
        if crop:
            p.set_cropbox(pymupdf.Rect(10, 20, 310, 410))
        pdf.save(path)
    store = LocalSnapshotStore(tmp_path, IsolatedPrivacyPDF(tmp_path))
    snapshot = store.capture(path.name, case_id=uuid4())
    observation = store.inspection(snapshot).pages[0].observations[0]
    candidate = SensitiveCandidate(
        candidate_id=uuid4(),
        category=SensitiveCategory.NAME,
        region=observation.region,
        raw_text=observation.text,
        detector_id="synthetic",
        detector_version="1",
    )
    command = PrivacyReviewCommand(
        source=snapshot,
        selection_revision=1,
        policy_digest="b" * 64,
        reviewed_pages=(1,),
        selections=(ReviewSelection(candidate=candidate, disposition="redact", entity_id=uuid4()),),
    )
    processor = IsolatedPrivacyRasterProcessor(tmp_path)
    return store, command, processor


def output_ocr(bundle):
    ocr = Mock()
    ocr.read.return_value = tuple(
        TextObservation(
            text=placeholder_text(o.entity_id), region=o.region, origin="ocr", confidence=0.99
        )
        for o in bundle.manifest.occurrences
    )
    return ocr


def approval(command):
    now = datetime.now(UTC)
    return LocalPrivacyApproval(
        approval_id=uuid4(),
        case_id=command.source.case_id,
        snapshot_id=command.source.snapshot_id,
        review_digest=privacy_review_digest(command),
        principal_id="synthetic-test-reviewer",
        approved_at=now,
        expires_at=now + timedelta(minutes=15),
    )


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_actual_raster_rebuild_verifies_and_preserves_original(tmp_path, rotation):
    store, command, processor = setup(tmp_path, rotation=rotation)
    original = store.read(command.source)
    bundle = processor.build(command, original)
    previews = processor.verify(command, original, bundle)
    assert len(previews) == 1 and previews[0].png.startswith(b"\x89PNG")
    assert store.source_file(command.source).read_bytes() == original
    assert bundle.filename == "sanitized.pdf"
    reader = PdfReader(io.BytesIO(bundle.pdf))
    assert reader.metadata is None and not reader.attachments
    assert not reader.pages[0].extract_text()
    assert "/Annots" not in reader.pages[0]
    assert "/AcroForm" not in reader.trailer["/Root"]
    assert b"CANARY-PRIVATE-1234" not in bundle.pdf
    for image in reader.pages[0].images:
        assert b"CANARY-PRIVATE-1234" not in image.data
    assert bundle.manifest.pages[0].width > command.source.pages[0].width
    assert bundle.manifest.occurrences[0].region.bbox[0] > command.source.pages[0].width


def test_fractional_page_dimensions_are_verified_or_fail_closed(tmp_path):
    store, command, processor = setup(tmp_path, fractional=True)
    bundle = processor.build(command, store.read(command.source))
    assert processor.verify(command, store.read(command.source), bundle)


def rewritten(bundle, action):
    from pypdf.generic import ArrayObject, DictionaryObject, NameObject, TextStringObject

    writer = PdfWriter(clone_from=io.BytesIO(bundle.pdf))
    if action == "metadata":
        writer.add_metadata({"/Title": "CANARY-PRIVATE-1234"})
    elif action == "attachment":
        writer.add_attachment("hidden.txt", b"CANARY-PRIVATE-1234")
    elif action == "page":
        writer.add_blank_page(100, 100)
    elif action == "javascript":
        writer.add_js("app.alert('CANARY-PRIVATE-1234')")
    elif action == "form":
        writer.root_object[NameObject("/AcroForm")] = DictionaryObject(
            {
                NameObject("/Fields"): ArrayObject(),
                NameObject("/XFA"): TextStringObject("CANARY-PRIVATE-1234"),
            }
        )
    elif action == "hidden":
        writer.root_object[NameObject("/OCProperties")] = DictionaryObject(
            {NameObject("/Name"): TextStringObject("CANARY-PRIVATE-1234")}
        )
    output = io.BytesIO()
    writer.write(output)
    data = output.getvalue() + (b"\nCANARY-PRIVATE-1234" if action == "trailing" else b"")
    manifest = bundle.manifest.model_copy(
        update={"sanitized_digest": hashlib.sha256(data).hexdigest(), "byte_size": len(data)}
    )
    return SanitizedBundle(data, manifest)


@pytest.mark.parametrize(
    "action", ["metadata", "attachment", "page", "trailing", "javascript", "form", "hidden"]
)
def test_extra_surfaces_rejected_even_with_updated_digest(tmp_path, action):
    store, command, processor = setup(tmp_path)
    original = store.read(command.source)
    bundle = processor.build(command, original)
    verifier = LocalSanitizedVerifier(processor, output_ocr(bundle))
    with pytest.raises(PrivacyFault):
        verifier.verify(command, original, rewritten(bundle, action))
    assert not verifier.permits(command, bundle.manifest)


@pytest.mark.parametrize(
    "ocr_failure", ["missing", "canary", "low-confidence", "no-token", "wrong-page"]
)
def test_ocr_failure_never_issues_verified_bundle(tmp_path, ocr_failure):
    store, command, processor = setup(tmp_path)
    original = store.read(command.source)
    bundle = processor.build(command, original)
    ocr = output_ocr(bundle)
    observation = ocr.read.return_value[0]
    if ocr_failure == "missing":
        ocr.read.side_effect = ValueError("OCR unavailable")
    elif ocr_failure == "low-confidence":
        ocr.read.return_value = (observation.model_copy(update={"confidence": 0.5}),)
    elif ocr_failure == "wrong-page":
        ocr.read.return_value = (
            observation.model_copy(
                update={"region": observation.region.model_copy(update={"page": 2})}
            ),
        )
    else:
        text = "CANARY-PRIVATE-1234" if ocr_failure == "canary" else "No readable token"
        ocr.read.return_value = (observation.model_copy(update={"text": text}),)
    verifier = LocalSanitizedVerifier(processor, ocr)
    with pytest.raises(PrivacyFault):
        verifier.verify(command, original, bundle)
    assert not verifier.permits(command, bundle.manifest)


def test_bundle_requires_live_authority_before_and_after_processing(tmp_path):
    store, command, processor = setup(tmp_path)
    draft = processor.build(command, store.read(command.source))
    ocr = output_ocr(draft)
    verifier = LocalSanitizedVerifier(processor, ocr)
    authority = Mock()
    authority.permits.return_value = True
    builder = LocalSanitizedBundleBuilder(
        sources=store, authority=authority, processor=processor, verifier=verifier
    )
    bundle = builder.build(command, approval(command))
    assert verifier.permits(command, bundle.manifest)
    authority.permits.side_effect = [True, False]
    with pytest.raises(PrivacyFault):
        builder.build(command, approval(command))
    authority.permits.side_effect = [False]
    with pytest.raises(PrivacyFault):
        builder.build(command, approval(command))


@pytest.mark.parametrize("inside", [False, True])
def test_changed_pixel_is_rejected_with_canonical_structure(tmp_path, inside):
    from appraisal_review.adapters.local.privacy.sanitize_worker import encode_pages

    store, command, processor = setup(tmp_path)
    original = store.read(command.source)
    bundle = processor.build(command, original)
    reader = PdfReader(io.BytesIO(bundle.pdf))
    image = reader.pages[0]["/Resources"]["/XObject"]["/Raster"]
    rgb = bytearray(image.get_data())
    position = 0
    if inside:
        box = command.selections[0].candidate.region.bbox
        x = int((box[0] + box[2]) / 2 * 2)
        y = int((command.source.pages[0].height - (box[1] + box[3]) / 2) * 2)
        position = (y * int(image["/Width"]) + x) * 3
    rgb[position] ^= 1
    pdf = encode_pages(
        [(int(image["/Width"]), int(image["/Height"]), bytes(rgb))], bundle.manifest.pages
    )
    tampered = SanitizedBundle(
        pdf,
        bundle.manifest.model_copy(
            update={"sanitized_digest": hashlib.sha256(pdf).hexdigest(), "byte_size": len(pdf)}
        ),
    )
    with pytest.raises(ScanFailure):
        processor.verify(command, original, tampered)


def test_limits_and_token_overflow_reject_without_scaling_over_original(tmp_path):
    store, command, _ = setup(tmp_path)
    original = store.read(command.source)
    processor = IsolatedPrivacyRasterProcessor(tmp_path, ScanLimits(max_pixels=100))
    with pytest.raises(ScanFailure):
        processor.build(command, original)
    many = command.model_copy(
        update={
            "selections": tuple(
                command.selections[0].model_copy(
                    update={
                        "candidate": command.selections[0].candidate.model_copy(
                            update={"candidate_id": uuid4()}
                        )
                    }
                )
                for _ in range(30)
            )
        }
    )
    with pytest.raises(ValueError, match="Token column capacity"):
        IsolatedPrivacyRasterProcessor(tmp_path).build(many, original)


def test_rotated_cropbox_uses_original_local_coordinates(tmp_path):
    store, command, processor = setup(tmp_path, rotation=90, crop=True)
    original = store.read(command.source)
    bundle = processor.build(command, original)
    assert processor.verify(command, original, bundle)


def test_changed_manifest_lineage_or_token_position_rejected(tmp_path):
    store, command, processor = setup(tmp_path)
    original = store.read(command.source)
    bundle = processor.build(command, original)
    for changed in (
        bundle.manifest.model_copy(update={"case_id": uuid4()}),
        bundle.manifest.model_copy(
            update={
                "occurrences": (
                    bundle.manifest.occurrences[0].model_copy(update={"entity_id": uuid4()}),
                )
            }
        ),
    ):
        with pytest.raises(ScanFailure):
            processor.verify(command, original, SanitizedBundle(bundle.pdf, changed))


def test_failed_reverification_revokes_previous_verifier_record(tmp_path):
    store, command, processor = setup(tmp_path)
    original = store.read(command.source)
    bundle = processor.build(command, original)
    ocr = output_ocr(bundle)
    verifier = LocalSanitizedVerifier(processor, ocr)
    verifier.verify(command, original, bundle)
    assert verifier.permits(command, bundle.manifest)
    ocr.read.side_effect = ValueError("Private failure text")
    with pytest.raises(PrivacyFault) as error:
        verifier.verify(command, original, bundle)
    assert str(error.value) == "privacy_verification_failed"
    assert not verifier.permits(command, bundle.manifest)
