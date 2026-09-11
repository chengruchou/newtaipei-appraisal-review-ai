"""Actual Controller output is verified again before a durable local publication."""

import asyncio
import importlib
import json
import sqlite3
from contextlib import closing
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from appraisal_review.adapters.local.approval import LocalApprovalStore
from appraisal_review.adapters.local.artifact_publication import (
    AttemptArtifactPublisher,
    CommittedResultResolver,
)
from appraisal_review.adapters.local.integrated_publication import (
    IntegratedResultProjection,
    PublicationEvidenceWriter,
    PublicationInputs,
)
from appraisal_review.adapters.local.integrated_service import LocalMaterialCatalog
from appraisal_review.adapters.local.pdf_writer import LocalPDFWriter
from appraisal_review.adapters.local.service import LocalArtifactEvidence
from appraisal_review.adapters.local.sqlite_publication import (
    SQLiteArtifactObjectStore,
    SQLiteManifestRepository,
)
from appraisal_review.adapters.local.sqlite_review_store import SQLiteReviewStore
from appraisal_review.application.bootstrap import build_controller
from appraisal_review.application.document_review import document_adapters
from appraisal_review.config import Settings
from appraisal_review.domain.artifact_publication import PublicationError
from appraisal_review.domain.factor_models import WorkflowStatus
from appraisal_review.domain.service_contracts import FencedArtifactManifest, ReviewSubmission


def test_projection_contract_available():
    assert callable(IntegratedResultProjection)
    assert callable(PublicationEvidenceWriter)
    assert callable(PublicationInputs)


def make_scene(tmp_path, monkeypatch, *, approved=True, sink_failure=False):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    helper = importlib.import_module("integration_fixture")
    fixture = asyncio.run(
        helper.create_integration_fixture(tmp_path / "fixture", approve_synthetic=approved)
    )
    store = SQLiteReviewStore(tmp_path / "private/review.sqlite3", clock=lambda: 1000)
    job_id, run_id = uuid4(), fixture.run.run_id
    asyncio.run(
        store.create_job(
            fixture.principal,
            ReviewSubmission(
                revision=fixture.run.revision,
                documents=fixture.snapshot.revision.documents,
                idempotency_key="publish-test",
            ),
            job_id=job_id,
            run_id=run_id,
            now=1000,
        )
    )
    attempt = asyncio.run(
        store.claim(job_id=job_id, run_id=run_id, owner=uuid4(), lease_seconds=600, now=1000)
    )
    record = asyncio.run(store.read_job(job_id=job_id))
    config = fixture.configuration
    captured = []
    delegate = LocalPDFWriter(
        render_config=config.writer.render, template_policy=config.writer.template_policy
    )
    with closing(sqlite3.connect(store.path)) as connection, connection:
        connection.execute("CREATE TABLE evidence_test(run_id TEXT PRIMARY KEY, payload TEXT)")

    def capture(evidence):
        if sink_failure:
            raise OSError("Evidence persistence failed")
        assert evidence.destination.exists()
        with closing(sqlite3.connect(store.path)) as connection, connection:
            connection.execute(
                "INSERT INTO evidence_test VALUES (?,?)",
                (str(run_id), json.dumps(asdict(evidence), default=str)),
            )
        captured.append(evidence)

    writer = PublicationEvidenceWriter(delegate, config.writer, on_evidence=capture)
    writer.run_id = run_id
    authority = LocalApprovalStore(config.approval_store) if config.approval_store else None
    adapters = document_adapters(config.inputs.parser(), fixture.snapshot.material, authority)
    controller = build_controller(
        Settings(runtime_mode="local", synthetic_demo=False),
        adapters=replace(adapters, pdf_writer=writer),
    )
    review = asyncio.run(controller.review(fixture.request))
    if sink_failure:
        assert review.status == WorkflowStatus.FAILED
        assert not captured
    elif approved:
        assert review.status == WorkflowStatus.COMPLETED
        assert captured == [writer.evidence]
    else:
        assert review.status == WorkflowStatus.NEEDS_REVIEW
        assert not captured
    monkeypatch.setattr(delegate, "write_pdf", AsyncMock(side_effect=AssertionError("extra write")))
    catalog = LocalMaterialCatalog(store)
    catalog.register(fixture.principal, fixture.snapshot)
    sources_checked = []

    def sources(principal, run, versions):
        for version in versions:
            ref = next(
                d
                for d in fixture.snapshot.revision.documents
                if d.document_id == version.document_id
            )
            fixture.documents.read_snapshot(principal, run, ref)
        sources_checked.append(versions)

    repo = SQLiteManifestRepository(
        store, source_authorizer=sources, trusted_synthetic_approval=True, clock=lambda: 1000
    )
    objects = SQLiteArtifactObjectStore(store)
    publisher = AttemptArtifactPublisher(objects=objects, manifests=repo)
    approvals = []

    async def principal_getter(actor, case):
        assert actor == fixture.principal.actor.actor_id and case == fixture.run.revision.case_id
        return fixture.principal

    inputs = PublicationInputs(
        config.writer,
        fixture.request,
        writer,
        template_version="synthetic-v1",
        fixed_synthetic_assets=True,
    )
    holder = [inputs]

    def exact(candidate, principal):
        assert principal == fixture.principal
        assert candidate.artifacts[0].template_hash == config.writer.template_policy.template_sha256
        assert candidate.artifacts[0].font_hash == fixture.font_sha256
        approvals.append(candidate.digest())

    projection = IntegratedResultProjection(
        publisher=publisher,
        manifests=repo,
        catalog=catalog,
        principal_getter=principal_getter,
        inputs_getter=lambda record, attempt: holder[0],
        synthetic_authorizer=exact,
    )
    return SimpleNamespace(**locals())


def test_actual_two_context_pdf_commits_and_authenticated_bytes_match(tmp_path, monkeypatch):
    scene = make_scene(tmp_path, monkeypatch)
    result = asyncio.run(scene.projection(scene.record, scene.attempt, scene.review))
    manifest = result.artifacts[0]
    assert isinstance(manifest, FencedArtifactManifest)
    assert len(manifest.contexts) == 2 and manifest.context == manifest.contexts[0]
    assert manifest.page_count == 2 and len(manifest.field_ids) == 8
    committed = scene.repo.read(scene.record.case_id, scene.attempt.run_id)
    assert committed.manifest_digest == manifest.manifest_digest
    assert len(committed.candidate.artifacts[0].source_versions) == 2
    resolved, data = CommittedResultResolver(
        objects=scene.objects, manifests=scene.repo
    ).verified_bytes(
        scene.fixture.principal, scene.record.case_id, scene.attempt.run_id, manifest.artifact_id
    )
    assert data == scene.writer.evidence.destination.read_bytes()
    assert resolved.placeholder_only is True and len(scene.sources_checked) >= 3
    assert scene.approvals == [committed.manifest_digest]
    assert "cases/" not in manifest.model_dump_json() and "file:" not in manifest.model_dump_json()
    # Durable evidence can be restored into a freshly composed non-revealing writer.
    restored = PublicationEvidenceWriter(scene.delegate, scene.config.writer)
    with closing(sqlite3.connect(scene.store.path)) as connection, connection:
        saved = json.loads(connection.execute("SELECT payload FROM evidence_test").fetchone()[0])
    restored.run_id = UUID(saved["run_id"])
    restored.evidence = LocalArtifactEvidence(
        **(
            saved
            | {
                "run_id": restored.run_id,
                "destination": Path(saved["destination"]),
                "file_identity": tuple(saved["file_identity"]),
            }
        )
    )
    assert restored.evidence == scene.writer.evidence
    scene.holder[0] = replace(scene.inputs, writer=restored)
    retry = asyncio.run(scene.projection(scene.record, scene.attempt, scene.review))
    assert retry == result
    scene.delegate.write_pdf.assert_not_awaited()


def test_blocked_controller_produces_no_output_or_publication(tmp_path, monkeypatch):
    scene = make_scene(tmp_path, monkeypatch, approved=False)
    result = asyncio.run(scene.projection(scene.record, scene.attempt, scene.review))
    assert result.business_status == WorkflowStatus.NEEDS_REVIEW and not result.artifacts
    assert not scene.approvals
    assert scene.repo.read(scene.record.case_id, scene.attempt.run_id) is None
    scene.delegate.write_pdf.assert_not_awaited()


@pytest.mark.parametrize(
    "damage",
    [
        "evidence",
        "font",
        "template",
        "field_map",
        "bytes",
        "sources",
        "capability",
        "synthetic",
        "token",
        "original",
        "case_gate",
        "verification",
        "pdf_result",
        "second_context",
    ],
)
def test_publication_rejects_changed_assets_and_claims_before_grant(tmp_path, monkeypatch, damage):
    scene = make_scene(tmp_path, monkeypatch)
    review = scene.review.model_copy(deep=True)
    if damage == "evidence":
        scene.writer.evidence = None
    elif damage == "font":
        scene.config.writer.render.font_path.write_bytes(b"changed font")
    elif damage == "template":
        scene.config.writer.template_path.write_bytes(b"changed template")
    elif damage == "field_map":
        scene.inputs.request.field_map.fields.pop()
    elif damage == "bytes":
        scene.writer.evidence.destination.write_bytes(b"changed output")
    elif damage == "sources":
        scene.config.inputs.documents[0].path.write_bytes(b"changed source")
    elif damage == "capability":
        scene.delegate.reveals_placeholders = True
    elif damage == "synthetic":
        scene.holder[0] = replace(scene.inputs, fixed_synthetic_assets=False)
    elif damage == "token":
        scene.holder[0] = replace(scene.inputs, expected_placeholder_tokens=("[PERSON_1]",))
    elif damage == "original":
        scene.holder[0] = replace(scene.inputs, forbidden_originals=("SYNTHETIC OUTPUT",))
    elif damage == "case_gate":
        review.case_review.coverage.missing.append("missing-critical")
    elif damage == "verification":
        review.verification.critical_errors.append("unverified")
    elif damage == "pdf_result":
        review.pdf_result.written_field_ids.pop()
    elif damage == "second_context":
        review.case_review.comparisons[1].summary.total_adjustment_percent = 999
    with pytest.raises(PublicationError):
        asyncio.run(scene.projection(scene.record, scene.attempt, review))
    assert not scene.approvals
    assert scene.repo.read(scene.record.case_id, scene.attempt.run_id) is None
    scene.delegate.write_pdf.assert_not_awaited()


def test_mismatched_controller_source_request_is_not_publishable(tmp_path, monkeypatch):
    scene = make_scene(tmp_path, monkeypatch)
    wrong = scene.inputs.request.model_copy(
        update={"criteria_document_uri": scene.inputs.request.case_document_uri}
    )
    scene.holder[0] = replace(scene.inputs, request=wrong)
    with pytest.raises(PublicationError, match="artifact_evidence_missing"):
        asyncio.run(scene.projection(scene.record, scene.attempt, scene.review))
    assert not scene.approvals


def test_evidence_sink_failure_prevents_completed_controller_result(tmp_path, monkeypatch):
    scene = make_scene(tmp_path, monkeypatch, sink_failure=True)
    result = asyncio.run(scene.projection(scene.record, scene.attempt, scene.review))
    assert result.business_status == WorkflowStatus.FAILED and not result.artifacts
    assert not scene.approvals
    with closing(sqlite3.connect(scene.store.path)) as connection, connection:
        assert connection.execute("SELECT count(*) FROM evidence_test").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM publication_manifests").fetchone()[0] == 0
    scene.delegate.write_pdf.assert_not_awaited()


def test_wrapper_requires_literal_multi_context_capability(tmp_path, monkeypatch):
    scene = make_scene(tmp_path, monkeypatch)
    for value in (None, False, 1, "true"):
        monkeypatch.setattr(scene.delegate, "supports_multiple_contexts", value)
        assert scene.writer.supports_multiple_contexts is False
    monkeypatch.setattr(scene.delegate, "supports_multiple_contexts", True)
    assert scene.writer.supports_multiple_contexts is True
