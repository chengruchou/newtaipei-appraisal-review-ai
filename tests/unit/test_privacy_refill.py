"""Real local PDF refill with explicit synthetic publisher, plan authority and OCR doubles."""

from __future__ import annotations

import hashlib
import json
import runpy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import jsonschema
import pymupdf
import pytest
from pydantic import ValidationError

from appraisal_review.adapters.local.privacy.refill import (
    IsolatedPrivacyRefillProcessor,
    LocalRefillFileSink,
)
from appraisal_review.adapters.local.privacy.source import IsolatedPrivacyPDF, LocalSnapshotStore
from appraisal_review.application.privacy_guards import PrivacyFault
from appraisal_review.application.privacy_refill import LocalPrivacyRefillExecutor, validate_refill
from appraisal_review.domain.privacy_mapping import LocalMappingHandle, LocalMappingRecord
from appraisal_review.domain.privacy_models import (
    PlaceholderOccurrence,
    PrivacyManifest,
    PrivacyPage,
    PrivacyRegion,
    PrivacyReviewCommand,
    RehydrationField,
    RehydrationPlan,
    ReviewSelection,
    SensitiveCandidate,
    SensitiveCategory,
    placeholder_text,
)
from appraisal_review.domain.privacy_refill import (
    FinalLocalManifest,
    PublishedRefillArtifact,
    PublishedRefillDescriptor,
    RefillTarget,
    verify_occurrences,
)
from appraisal_review.domain.privacy_scan import TextObservation


class SyntheticPublisher:
    """Test fixture only: no credentials, network release or production provenance."""

    def __init__(self, artifact, plan):
        self.artifact, self.plan, self.active = artifact, plan, True

    def current(self, plan):
        if not self.permits(plan, self.artifact):
            raise ValueError("Synthetic release is not current")
        return self.artifact

    def permits(self, plan, artifact):
        return self.active and plan == self.plan and artifact == self.artifact


class SyntheticPlanAuthority:
    def __init__(self, plan):
        self.plan, self.active = plan, True

    def permits(self, plan):
        return self.active and plan == self.plan


class SyntheticMappingReader:
    def __init__(self, record, handle):
        self.record, self.handle = record, handle

    def read(self, handle):
        if handle != self.handle:
            raise ValueError("Synthetic handle differs")
        return self.record


class SyntheticRefillOCR:
    """Known observations of generated PDFs, not actual OCR accuracy acceptance."""

    def __init__(self, target):
        self.target, self.calls = target, 0
        self.before = (
            TextObservation(
                text=placeholder_text(target.entity_id),
                region=target.region,
                origin="ocr",
                confidence=0.99,
            ),
        )
        self.after = (
            TextObservation(
                text="Synthetic readable output",
                region=target.region,
                origin="ocr",
                confidence=0.99,
            ),
        )

    def read(self, preview, page, *, timeout):
        self.calls += 1
        return self.before if self.calls == 1 else self.after


def scenario(tmp_path, *, operation="restore_text", text="測試姓名", present=True, font=True):
    original_path = tmp_path / "synthetic-original.pdf"
    with pymupdf.open() as document:
        page = document.new_page(width=500, height=400)
        page.insert_textbox(pymupdf.Rect(30, 30, 470, 300), text, fontname="china-t", fontsize=12)
        page.draw_line((30, 60), (110, 70), color=(0, 0, 1), width=3)
        document.save(original_path)
    store = LocalSnapshotStore(tmp_path, IsolatedPrivacyPDF(tmp_path))
    source = store.capture(original_path.name, case_id=uuid4())
    pages = (PrivacyPage(number=1, width=500.0, height=400.0),)
    entity, occurrence, output_field = uuid4(), uuid4(), uuid4()
    target = RefillTarget(
        occurrence_id=occurrence,
        entity_id=entity,
        output_field_id=output_field,
        region=PrivacyRegion(page=1, bbox=(20.0, 290.0, 240.0, 350.0)),
        present=present,
    )
    with pymupdf.open() as cloud:
        page = cloud.new_page(width=500, height=400)
        if present:
            page.insert_text((25, 75), placeholder_text(entity), fontsize=9)
        page.insert_text((25, 200), "Cloud rate 9.75%  Total 123456.78", fontsize=12)
        page.draw_rect(pymupdf.Rect(20, 180, 350, 225))
        cloud_bytes = cloud.tobytes()
    (tmp_path / "synthetic-cloud.pdf").write_bytes(cloud_bytes)
    command = PrivacyReviewCommand(
        source=source,
        selection_revision=1,
        policy_digest="b" * 64,
        reviewed_pages=(1,),
        selections=(
            ReviewSelection(
                disposition="redact",
                entity_id=entity,
                candidate=SensitiveCandidate(
                    candidate_id=uuid4(),
                    category=SensitiveCategory.NAME,
                    region=PrivacyRegion(page=1, bbox=(20.0, 320.0, 150.0, 380.0)),
                    raw_text=text,
                    crop_id=uuid4(),
                    detector_id="synthetic-fixture",
                    detector_version="1",
                ),
            ),
        ),
    )
    manifest = PrivacyManifest(
        case_id=source.case_id,
        document_id=source.document_id,
        sanitized_digest="c" * 64,
        byte_size=100,
        pages=pages,
        occurrences=(
            PlaceholderOccurrence(occurrence_id=occurrence, entity_id=entity, region=target.region),
        ),
    )
    now = datetime.now(UTC)
    mapping = LocalMappingRecord(
        map_id=uuid4(),
        created_at=now,
        expires_at=now + timedelta(hours=1),
        command=command,
        manifest=manifest,
    )
    descriptor = PublishedRefillDescriptor(
        case_id=source.case_id,
        document_id=source.document_id,
        run_id=uuid4(),
        revision_id=uuid4(),
        artifact_digest=hashlib.sha256(cloud_bytes).hexdigest(),
        base_sanitized_digest=manifest.sanitized_digest,
        template_digest="d" * 64,
        pages=pages,
        targets=(target,),
    )
    artifact = PublishedRefillArtifact(descriptor, cloud_bytes)
    plan = RehydrationPlan(
        case_id=source.case_id,
        document_id=source.document_id,
        map_id=mapping.map_id,
        run_id=descriptor.run_id,
        revision_id=descriptor.revision_id,
        artifact_digest=descriptor.artifact_digest,
        template_digest=descriptor.template_digest,
        pages=pages,
        fields=(
            RehydrationField(
                occurrence_id=occurrence,
                entity_id=entity,
                output_field_id=output_field,
                destination=None if operation == "omit" else target.region,
                operation=operation,
            ),
        ),
    )
    handle = LocalMappingHandle(
        case_id=source.case_id,
        map_id=mapping.map_id,
        key_reference=uuid4(),
        expires_at=mapping.expires_at,
        envelope_digest="e" * 64,
    )
    publisher = SyntheticPublisher(artifact, plan)
    authority = SyntheticPlanAuthority(plan)
    ocr = SyntheticRefillOCR(target)
    if not present:
        ocr.before = ocr.after
    processor = IsolatedPrivacyRefillProcessor(
        tmp_path,
        font_digest=hashlib.sha256(pymupdf.Font("cjk").buffer).hexdigest() if font else None,
    )
    executor = LocalPrivacyRefillExecutor(
        mappings=SyntheticMappingReader(mapping, handle),
        sources=store,
        publisher=publisher,
        authority=authority,
        processor=processor,
        ocr=ocr,
        sink=LocalRefillFileSink(store, tmp_path, workspace=tmp_path),
    )
    return executor, mapping, handle, plan, publisher, authority, ocr


def test_real_chinese_refill_reopens_and_preserves_cloud_numbers_and_inputs(tmp_path):
    executor, mapping, handle, plan, publisher, _, _ = scenario(tmp_path)
    original = (tmp_path / "synthetic-original.pdf").read_bytes()
    cloud = publisher.artifact.pdf
    result = executor.execute(handle, plan)
    assert (tmp_path / "final-local.pdf").read_bytes() == result.pdf
    assert (tmp_path / "synthetic-original.pdf").read_bytes() == original
    assert (tmp_path / "synthetic-cloud.pdf").read_bytes() == cloud
    with (
        pymupdf.open(stream=result.pdf, filetype="pdf") as pdf,
        pymupdf.open(stream=cloud, filetype="pdf") as before,
    ):
        assert "測試姓名" in pdf[0].get_text()
        assert "PT_" not in pdf[0].get_text()
        clip = pymupdf.Rect(15, 175, 360, 235)
        assert (
            pdf[0].get_pixmap(clip=clip, dpi=144).samples
            == before[0].get_pixmap(clip=clip, dpi=144).samples
        )
        assert any("Droid" in f[3] and pdf.extract_font(f[0])[3] for f in pdf[0].get_fonts())
    assert result.manifest.business_authority == "unchanged"
    assert result.manifest.state == "refilled_local"
    assert result.manifest.map_id == mapping.map_id
    with pytest.raises(PrivacyFault):
        executor.execute(handle, plan)
    assert (tmp_path / "final-local.pdf").read_bytes() == result.pdf


@pytest.mark.parametrize(
    "operation,present", [("restore_original_crop", True), ("omit", True), ("omit", False)]
)
def test_crop_and_explicit_omission(tmp_path, operation, present):
    executor, _, handle, plan, _, _, _ = scenario(tmp_path, operation=operation, present=present)
    result = executor.execute(handle, plan)
    assert result.manifest.signature_effect == "visual_only_no_digital_signature"
    assert bool(result.manifest.omitted_fields) == (operation == "omit")
    with pymupdf.open(stream=result.pdf, filetype="pdf") as pdf:
        assert "PT_" not in pdf[0].get_text()
        if operation == "restore_original_crop":
            assert len(pdf[0].get_images()) == 2


@pytest.mark.parametrize(
    "mutation",
    [
        "case_id",
        "document_id",
        "map_id",
        "run_id",
        "revision_id",
        "artifact_digest",
        "template_digest",
    ],
)
def test_plan_identity_mismatches_rejected(tmp_path, mutation):
    _, mapping, _, plan, publisher, _, _ = scenario(tmp_path)
    value = "a" * 64 if mutation.endswith("digest") else uuid4()
    with pytest.raises(ValueError):
        validate_refill(mapping, plan.model_copy(update={mutation: value}), publisher.artifact)


@pytest.mark.parametrize(
    "mutation", ["unknown", "duplicate", "missing", "misplaced", "malformed", "low-confidence"]
)
def test_token_inventory_failures_never_write(tmp_path, mutation):
    executor, _, handle, plan, _, _, ocr = scenario(tmp_path)
    original = ocr.before[0]
    if mutation == "unknown":
        ocr.before = (original.model_copy(update={"text": placeholder_text(uuid4())}),)
    elif mutation == "duplicate":
        ocr.before = (original, original)
    elif mutation == "missing":
        ocr.before = ocr.after
    elif mutation == "misplaced":
        ocr.before = (
            original.model_copy(
                update={"region": PrivacyRegion(page=1, bbox=(300.0, 20.0, 490.0, 40.0))}
            ),
        )
    elif mutation == "malformed":
        ocr.before = (original.model_copy(update={"text": "PT_invalid"}),)
    else:
        ocr.before = (original.model_copy(update={"confidence": 0.5}),)
    with pytest.raises(PrivacyFault):
        executor.execute(handle, plan)
    assert not (tmp_path / "final-local.pdf").exists()


@pytest.mark.parametrize("kind", ["publisher", "authority", "during-write"])
def test_live_authority_and_current_revision_required(tmp_path, kind):
    executor, _, handle, plan, publisher, authority, ocr = scenario(tmp_path)
    if kind == "publisher":
        publisher.active = False
    elif kind == "authority":
        authority.active = False
    else:
        original = ocr.read

        def revoke(*args, **kwargs):
            result = original(*args, **kwargs)
            if ocr.calls == 2:
                publisher.active = False
            return result

        ocr.read = revoke
    with pytest.raises(PrivacyFault):
        executor.execute(handle, plan)
    assert not (tmp_path / "final-local.pdf").exists()


@pytest.mark.parametrize("kind", ["font", "overflow", "glyph"])
def test_unapproved_font_overflow_and_missing_glyph_fail_without_output(tmp_path, kind):
    text = (
        "測試姓名" * 100
        if kind == "overflow"
        else ("\U0010ffff" if kind == "glyph" else "測試姓名")
    )
    executor, _, handle, plan, _, _, _ = scenario(tmp_path, text=text, font=kind != "font")
    with pytest.raises(PrivacyFault):
        executor.execute(handle, plan)
    assert not (tmp_path / "final-local.pdf").exists()


def test_overlapping_or_undeclared_fields_rejected(tmp_path):
    _, mapping, _, plan, publisher, _, _ = scenario(tmp_path)
    first = publisher.artifact.descriptor.targets[0]
    with pytest.raises(ValidationError):
        PublishedRefillDescriptor.model_validate(
            publisher.artifact.descriptor.model_copy(
                update={
                    "targets": (
                        first,
                        first.model_copy(
                            update={"occurrence_id": uuid4(), "output_field_id": uuid4()}
                        ),
                    )
                }
            )
        )
    field = plan.fields[0]
    changed = plan.model_copy(
        update={"fields": (field.model_copy(update={"output_field_id": uuid4()}),)}
    )
    with pytest.raises(ValueError):
        validate_refill(mapping, changed, publisher.artifact)


def test_missing_token_requires_explicit_omission_in_approved_plan(tmp_path):
    executor, _, handle, plan, _, _, _ = scenario(tmp_path, present=False)
    with pytest.raises(PrivacyFault):
        executor.execute(handle, plan)


def test_unknown_native_token_not_hidden_by_ocr_double(tmp_path):
    executor, _, handle, plan, publisher, authority, _ = scenario(tmp_path)
    with pymupdf.open(stream=publisher.artifact.pdf, filetype="pdf") as pdf:
        pdf[0].insert_text((25, 270), placeholder_text(uuid4()), fontsize=9)
        data = pdf.tobytes()
    digest = hashlib.sha256(data).hexdigest()
    descriptor = publisher.artifact.descriptor.model_copy(update={"artifact_digest": digest})
    publisher.artifact = PublishedRefillArtifact(descriptor, data)
    plan = plan.model_copy(update={"artifact_digest": digest})
    publisher.plan = authority.plan = plan
    with pytest.raises(PrivacyFault):
        executor.execute(handle, plan)
    assert not (tmp_path / "final-local.pdf").exists()


def test_same_entity_multiple_occurrences_need_separate_positions(tmp_path):
    _, _, _, _, publisher, _, ocr = scenario(tmp_path)
    first = publisher.artifact.descriptor.targets[0]
    second = first.model_copy(
        update={
            "occurrence_id": uuid4(),
            "output_field_id": uuid4(),
            "region": PrivacyRegion(page=1, bbox=(20.0, 100.0, 240.0, 150.0)),
        }
    )
    observations = (ocr.before[0], ocr.before[0].model_copy(update={"region": second.region}))
    verify_occurrences(
        observations, (first, second), publisher.artifact.descriptor.pages, require_all=True
    )
    with pytest.raises(ValueError):
        verify_occurrences(
            observations[:1], (first, second), publisher.artifact.descriptor.pages, require_all=True
        )


def test_final_token_remnant_never_publishes(tmp_path):
    executor, _, handle, plan, _, _, ocr = scenario(tmp_path)
    ocr.after = ocr.before
    with pytest.raises(PrivacyFault):
        executor.execute(handle, plan)
    assert not (tmp_path / "final-local.pdf").exists()


def test_refill_contracts_are_reproducible(tmp_path):
    root = Path(__file__).resolve().parents[2]
    runpy.run_path(str(root / "scripts/export_privacy_refill_contracts.py"))["export"](tmp_path)
    schema = json.loads((tmp_path / "schemas/local-privacy-refill-v1.json").read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    assert (tmp_path / "schemas/local-privacy-refill-v1.json").read_bytes() == (
        root / "schemas/local-privacy-refill-v1.json"
    ).read_bytes()
    for name, model in (
        ("publisher", PublishedRefillDescriptor),
        ("plan", RehydrationPlan),
        ("final-manifest", FinalLocalManifest),
    ):
        path = f"examples/privacy-refill-v1/{name}.json"
        assert (tmp_path / path).read_bytes() == (root / path).read_bytes()
        raw = (tmp_path / path).read_text()
        jsonschema.validate(json.loads(raw), {**schema, "$ref": f"#/$defs/{model.__name__}"})
        assert model.model_validate_json(raw)


def test_changed_original_never_publishes(tmp_path):
    executor, _, handle, plan, _, _, _ = scenario(tmp_path)
    with pymupdf.open(
        stream=(tmp_path / "synthetic-original.pdf").read_bytes(), filetype="pdf"
    ) as pdf:
        pdf[0].insert_text((200, 250), "Synthetic external replacement")
        replacement = pdf.tobytes()
    (tmp_path / "synthetic-original.pdf").write_bytes(replacement)
    with pytest.raises(PrivacyFault):
        executor.execute(handle, plan)
    assert not (tmp_path / "final-local.pdf").exists()
