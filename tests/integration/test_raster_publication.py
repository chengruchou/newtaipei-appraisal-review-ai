"""Actual sanitized templates and Controller output at the publication boundary."""

import asyncio
import importlib
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
import test_privacy_browser_rehearsal as privacy_harness
from test_privacy_browser_rehearsal import upload

from appraisal_review.adapters.local.integrated_publication import (
    IntegratedResultProjection,
    PublicationEvidenceWriter,
    PublicationInputs,
    TrustedRasterPublication,
)
from appraisal_review.adapters.local.pdf_writer import LocalPDFWriter
from appraisal_review.application.bootstrap import build_controller
from appraisal_review.application.document_review import document_adapters
from appraisal_review.config import Settings
from appraisal_review.domain.artifact_publication import PublicationError, SourceVersion
from appraisal_review.domain.confidence import confirm_side
from appraisal_review.domain.factor_models import WorkflowStatus
from appraisal_review.domain.job_contracts import JobStatus
from appraisal_review.domain.service_contracts import RunReference
from appraisal_review.ports.jobs import JobRecord

privacy_rehearsal = privacy_harness.rehearsal
rehearsal_workspace = privacy_harness.rehearsal_workspace


@pytest.fixture
def raster_scene(privacy_rehearsal):
    bridge, client, ready = privacy_rehearsal
    for source_id in bridge.source_ids:
        upload(client, source_id)
    helper = importlib.import_module("privacy_raster_fixture")
    fixture = asyncio.run(
        helper.create_sanitized_synthetic_case(
            bridge.config.workspace / "publication-case",
            bridge.principal,
            bridge.documents,
            bridge.sink.receipts,
            snapshot=ready[0],
            approve_authored_synthetic_rules=True,
        )
    )
    material = fixture.snapshot.material
    for pair in material.facts.pairs:
        for side in ("target", "comparable"):
            confirm_side(pair, side, reviewer=fixture.principal.actor.actor_id)
    snapshot = type(fixture.snapshot).capture(
        material,
        str(uuid4()),
        parent=fixture.snapshot.revision.reference,
    )
    authorization = fixture.authorize(snapshot)
    config = fixture.configuration.writer
    writer = PublicationEvidenceWriter(
        LocalPDFWriter(
            render_config=config.render,
            template_policy=config.template_policy,
        ),
        config,
    )
    run = RunReference(run_id=uuid4(), attempt_id=uuid4(), revision=snapshot.revision.reference)
    writer.run_id = run.run_id
    adapters = document_adapters(fixture.configuration.inputs.parser(), material, authorization)
    controller = build_controller(
        Settings(runtime_mode="local", synthetic_demo=False),
        adapters=replace(adapters, pdf_writer=writer),
    )
    review = asyncio.run(controller.review(fixture.request))
    assert review.status == WorkflowStatus.COMPLETED, review.model_dump(mode="json")
    assert len(review.pdf_result.written_field_ids) == 8
    record = JobRecord(
        job_id=uuid4(),
        case_id=run.revision.case_id,
        principal_id=fixture.principal.actor.actor_id,
        status=JobStatus.RUNNING,
        current_run=run,
    )
    inputs = PublicationInputs(
        config,
        fixture.request,
        writer,
        "synthetic-raster-v1",
        fixed_synthetic_assets=True,
        raster_assets=TrustedRasterPublication(
            source_versions=tuple(
                SourceVersion(
                    document_id=item.document_id,
                    version=item.version,
                    content_hash=item.content_hash,
                )
                for item in snapshot.revision.documents
            ),
            template_hash=config.template_policy.template_sha256,
            authorization=authorization,
        ),
    )
    return SimpleNamespace(**locals())


def test_actual_sanitized_raster_can_publish_with_exact_authored_assets(raster_scene):
    scene = raster_scene
    artifact = IntegratedResultProjection._verified_artifact(
        scene.record,
        scene.review,
        scene.inputs,
        scene.snapshot,
    )
    assert artifact.template_hash == scene.config.template_policy.template_sha256
    assert artifact.page_count == 2 and len(artifact.field_ids) == 8


@pytest.mark.parametrize("failure", ["default", "boolean", "source", "template", "authority"])
def test_native_markers_are_not_bypassed_by_flags_or_mismatched_assets(raster_scene, failure):
    scene = raster_scene
    pin = scene.inputs.raster_assets
    if failure == "default":
        pin = None
    elif failure == "boolean":
        pin = True
    elif failure == "source":
        pin = replace(pin, source_versions=(pin.source_versions[0],))
    elif failure == "template":
        pin = replace(pin, template_hash="0" * 64)
    else:

        class Denied:
            def permits(self, material):
                return False

        pin = replace(pin, authorization=Denied())
    with pytest.raises(PublicationError, match="artifact_evidence_missing"):
        IntegratedResultProjection._verified_artifact(
            scene.record,
            scene.review,
            replace(scene.inputs, raster_assets=pin),
            scene.snapshot,
        )
