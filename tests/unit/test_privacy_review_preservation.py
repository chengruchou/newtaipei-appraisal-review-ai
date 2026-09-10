"""Synthetic unchanged-geometry regressions across local privacy consumers."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pymupdf
import pytest
from test_privacy_refill import scenario
from test_privacy_review import confirm, review_all, version

from appraisal_review.application.privacy_bundle import LocalSanitizedVerifier
from appraisal_review.application.privacy_export import sanitize_reviewer_text
from appraisal_review.application.privacy_guards import PrivacyFault
from appraisal_review.application.privacy_review import LocalPrivacyReviewService
from appraisal_review.domain.privacy_bundle import SanitizedBundle
from appraisal_review.domain.privacy_models import SensitiveCategory, placeholder_text
from appraisal_review.domain.privacy_review import EditPrivacyRegion, PrivacyPagePreview
from appraisal_review.domain.privacy_scan import PageScan, PrivacyScanReport, TextObservation


def reviewed_edit(command, mode):
    scanner, sources, previews, human = Mock(), Mock(), Mock(), Mock()
    scanner.scan = AsyncMock(
        return_value=PrivacyScanReport(
            source=command.source,
            native_engine_version="synthetic",
            status="needs_review",
            pages=tuple(
                PageScan(
                    page=p.number,
                    mode="native",
                    status="processed",
                    observations=(
                        TextObservation(
                            text="synthetic page",
                            region=command.selections[0].candidate.region,
                            origin="native",
                        ),
                    ),
                )
                for p in command.source.pages
            ),
            candidates=tuple(s.candidate for s in command.selections),
        )
    )
    previews.preview.return_value = PrivacyPagePreview(500, 400, b"synthetic-preview")
    human.confirm.return_value = "synthetic-reviewer"
    service = LocalPrivacyReviewService(
        scanner=scanner, sources=sources, previews=previews, human=human
    )
    asyncio.run(service.rescan(command.source, policy_digest=command.policy_digest))
    review_all(service)
    prior = service.list().command
    old_approval = confirm(service)
    before = prior.selections[0]
    # None requests a fresh local entity (split/group-only edit).
    entity = None if mode == "group" else before.entity_id
    region = before.candidate.region
    if mode == "moved":
        x0, y0, x1, y1 = region.bbox
        region = region.model_copy(update={"bbox": (x0 + 1, y0, x1 + 1, y1)})
    changed = service.edit(
        EditPrivacyRegion(
            **version(service),
            candidate_id=before.candidate.candidate_id,
            region=region,
            category=SensitiveCategory.CASE_CONTACT
            if mode == "category"
            else before.candidate.category,
            entity_id=entity,
        )
    ).command
    assert changed.selection_revision == prior.selection_revision + 1
    assert service.list().state == "awaiting_confirmation"
    assert not service.permits(old_approval, changed, now=old_approval.approved_at)
    if mode == "group":
        assert changed.selections[0].entity_id != before.entity_id
    review_all(service)
    renewed = confirm(service)
    assert renewed.approval_id != old_approval.approval_id
    return service.list().command, before.candidate


@pytest.mark.parametrize("mode", ["noop", "group", "category"])
@pytest.mark.parametrize("canary", ["synthetic@example.invalid", "測試姓名"])
def test_unchanged_region_preserves_evidence_and_sanitization(tmp_path, mode, canary):
    _, mapping, _, _, _, _, _ = scenario(tmp_path, text=canary)
    command, original = reviewed_edit(mapping.command, mode)
    candidate = command.selections[0].candidate
    assert candidate.raw_text == original.raw_text
    assert candidate.crop_id == original.crop_id
    assert candidate.confidence == original.confidence
    assert candidate.detector_id == original.detector_id
    assert candidate.detector_version == original.detector_version
    assert candidate.region == original.region
    draft = sanitize_reviewer_text(f"Check {canary}", command)
    assert canary not in draft.text
    assert draft.text == f"Check {placeholder_text(command.selections[0].entity_id)}"


@pytest.mark.parametrize("mode", ["noop", "group", "category"])
def test_residual_verifier_retains_original_canary_after_edit(tmp_path, mode):
    import hashlib

    _, mapping, _, _, publisher, _, _ = scenario(tmp_path)
    command, original = reviewed_edit(mapping.command, mode)
    entity = command.selections[0].entity_id
    occurrence = mapping.manifest.occurrences[0].model_copy(update={"entity_id": entity})
    pdf = publisher.artifact.pdf
    manifest = mapping.manifest.model_copy(
        update={
            "sanitized_digest": hashlib.sha256(pdf).hexdigest(),
            "byte_size": len(pdf),
            "occurrences": (occurrence,),
        }
    )
    processor, ocr = Mock(), Mock()
    processor.verify.return_value = (PrivacyPagePreview(500, 400, b"synthetic-preview"),)
    ocr.read.return_value = (
        TextObservation(
            text=f"{placeholder_text(entity)} {original.raw_text}",
            region=occurrence.region,
            origin="ocr",
            confidence=0.99,
        ),
    )
    verifier = LocalSanitizedVerifier(processor, ocr)
    with pytest.raises(PrivacyFault):
        verifier.verify(command, b"synthetic-source", SanitizedBundle(pdf, manifest))
    assert not verifier.permits(command, manifest)


@pytest.mark.parametrize("mode", ["noop", "group", "category"])
@pytest.mark.parametrize("canary", ["synthetic@example.invalid", "測試姓名"])
def test_real_text_refill_after_unchanged_region_edit(tmp_path, mode, canary):
    executor, mapping, handle, plan, publisher, _, ocr = scenario(tmp_path, text=canary)
    command, _ = reviewed_edit(mapping.command, mode)
    # Publish a synthetic placeholder artifact for this newly issued entity/plan.
    import hashlib

    from appraisal_review.domain.privacy_refill import PublishedRefillArtifact

    entity = command.selections[0].entity_id
    with pymupdf.open() as cloud:
        page = cloud.new_page(width=500, height=400)
        page.insert_text((25, 75), placeholder_text(entity), fontsize=9)
        page.insert_text((25, 200), "Cloud rate 9.75%  Total 123456.78", fontsize=12)
        page.draw_rect(pymupdf.Rect(20, 180, 350, 225))
        cloud_bytes = cloud.tobytes()
    (tmp_path / "synthetic-cloud.pdf").write_bytes(cloud_bytes)
    target = publisher.artifact.descriptor.targets[0].model_copy(update={"entity_id": entity})
    descriptor = publisher.artifact.descriptor.model_copy(
        update={
            "targets": (target,),
            "artifact_digest": hashlib.sha256(cloud_bytes).hexdigest(),
        }
    )
    plan = plan.model_copy(
        update={
            "artifact_digest": descriptor.artifact_digest,
            "fields": (plan.fields[0].model_copy(update={"entity_id": entity}),),
        }
    )
    publisher.artifact = PublishedRefillArtifact(descriptor, cloud_bytes)
    publisher.plan = plan
    executor._authority.plan = plan
    ocr.before = (ocr.before[0].model_copy(update={"text": placeholder_text(entity)}),)
    manifest = mapping.manifest.model_copy(
        update={
            "occurrences": (
                mapping.manifest.occurrences[0].model_copy(update={"entity_id": entity}),
            )
        }
    )
    executor._mappings.record = mapping.model_copy(
        update={"command": command, "manifest": manifest}
    )
    original_bytes = (tmp_path / "synthetic-original.pdf").read_bytes()
    result = executor.execute(handle, plan)
    with pymupdf.open(stream=result.pdf, filetype="pdf") as document:
        assert canary in document[0].get_text()
        # Existing cloud content is deliberately rasterized; verify its actual pixels.
        with pymupdf.open(stream=publisher.artifact.pdf, filetype="pdf") as cloud:
            clip = pymupdf.Rect(15, 175, 360, 235)
            assert document[0].get_pixmap(clip=clip, dpi=144).samples == (
                cloud[0].get_pixmap(clip=clip, dpi=144).samples
            )
    assert (tmp_path / "synthetic-original.pdf").read_bytes() == original_bytes
    assert (tmp_path / "synthetic-cloud.pdf").read_bytes() == publisher.artifact.pdf


@pytest.mark.parametrize("confidence", [None, 0.0, 0.31])
def test_unchanged_region_never_changes_detector_confidence(tmp_path, confidence):
    _, mapping, _, _, _, _, _ = scenario(tmp_path)
    selection = mapping.command.selections[0]
    candidate = selection.candidate.model_copy(update={"confidence": confidence})
    command = mapping.command.model_copy(
        update={"selections": (selection.model_copy(update={"candidate": candidate}),)}
    )
    edited, _ = reviewed_edit(command, "category")
    assert edited.selections[0].candidate.confidence == confidence


def test_moved_region_does_not_reuse_old_text_or_crop(tmp_path):
    _, mapping, _, _, _, _, _ = scenario(tmp_path)
    edited, original = reviewed_edit(mapping.command, "moved")
    candidate = edited.selections[0].candidate
    assert candidate.region != original.region
    assert candidate.raw_text is None
    assert candidate.confidence is None
    assert candidate.crop_id != original.crop_id
    assert candidate.detector_id == "local-manual-region"
