"""Real SQLite transactions and immutable output, using SQLiteReviewStore authority."""

import asyncio
import hashlib
import json
import sqlite3
from dataclasses import replace
from io import BytesIO
from uuid import uuid4

import pytest
from pypdf import PdfWriter

from appraisal_review.adapters.local.artifact_publication import (
    AttemptArtifactPublisher,
    CommittedResultResolver,
)
from appraisal_review.adapters.local.sqlite_publication import (
    SQLiteArtifactObjectStore,
    SQLiteManifestRepository,
)
from appraisal_review.adapters.local.sqlite_review_store import SQLiteReviewStore
from appraisal_review.domain.artifact_publication import (
    ArtifactKey,
    ManifestCandidate,
    PublicationError,
    PublishedArtifact,
    SourceVersion,
)
from appraisal_review.domain.factor_models import WorkflowStatus
from appraisal_review.domain.service_contracts import Permission
from appraisal_review.ports.artifact_publication import PublicationAttempt
from appraisal_review.testing.job_store_contract import principal, submission


class Clock:
    now = 1000

    def __call__(self):
        return self.now


@pytest.fixture
def scene(tmp_path):
    clock = Clock()
    store = SQLiteReviewStore(tmp_path / "private" / "review.sqlite3", clock=clock)
    person = replace(principal(), permissions=frozenset({Permission.REVIEW, Permission.PUBLISH}))
    request = submission()
    job_id, run_id = uuid4(), uuid4()
    asyncio.run(store.create_job(person, request, job_id=job_id, run_id=run_id, now=clock.now))
    claim = asyncio.run(
        store.claim(job_id=job_id, run_id=run_id, owner=uuid4(), lease_seconds=60, now=clock.now)
    )
    run = (asyncio.run(store.read_job(job_id=job_id))).current_run
    buffer = BytesIO()
    pdf = PdfWriter()
    pdf.add_blank_page(width=72, height=72)
    pdf.add_metadata({"/AppraisalReviewWriterVersion": "2"})
    pdf.write(buffer)
    data = buffer.getvalue()
    artifact_id = uuid4()
    artifact = PublishedArtifact(
        artifact_id=artifact_id,
        key=ArtifactKey.for_run(run, artifact_id).key(),
        content_hash=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        writer_version="2",
        template_id="synthetic-template",
        template_version="1",
        template_hash="1" * 64,
        field_map_hash="2" * 64,
        font_hash="3" * 64,
        field_ids=("field-1",),
        page_count=1,
        source_versions=tuple(
            SourceVersion(document_id=d.document_id, version=d.version, content_hash=d.content_hash)
            for d in request.documents
        ),
    )
    objects = SQLiteArtifactObjectStore(store)
    sources_checked = []

    def source_authorizer(person, run, sources):
        sources_checked.append((person, run, sources))

    manifests = SQLiteManifestRepository(
        store, source_authorizer=source_authorizer, trusted_synthetic_approval=True, clock=clock
    )
    publisher = AttemptArtifactPublisher(objects=objects, manifests=manifests)
    local = tmp_path / "output.pdf"
    local.write_bytes(data)

    class PlaceholderWriter:
        reveals_placeholders = False

    artifact = publisher.stage(local, run=run, artifact=artifact, writer=PlaceholderWriter())
    candidate = ManifestCandidate(
        run=run, result_version=1, review_status=WorkflowStatus.COMPLETED, artifacts=(artifact,)
    )
    attempt = PublicationAttempt(
        job_id=job_id, owner=claim.owner, expected_result_version=claim.expected_result_version
    )
    return dict(
        store=store,
        clock=clock,
        person=person,
        candidate=candidate,
        attempt=attempt,
        claim=claim,
        objects=objects,
        manifests=manifests,
        publisher=publisher,
        data=data,
        sources_checked=sources_checked,
    )


def publish(h):
    return h["publisher"].publish(
        h["candidate"],
        fencing_token=h["claim"].fencing_token,
        principal=h["person"],
        attempt=h["attempt"],
    )


def test_explicit_approval_required_then_verified_bytes_reopen_and_restart(scene):
    h = scene
    with pytest.raises(PublicationError):
        publish(h)
    h["manifests"].approve_exact(h["candidate"], h["person"])
    committed = publish(h)
    fresh = SQLiteReviewStore(h["store"].path, clock=h["clock"])
    repo = SQLiteManifestRepository(fresh, source_authorizer=lambda *args: None, clock=h["clock"])
    resolver = CommittedResultResolver(objects=SQLiteArtifactObjectStore(fresh), manifests=repo)
    artifact, data = resolver.verified_bytes(
        h["person"],
        h["candidate"].run.revision.case_id,
        h["candidate"].run.run_id,
        h["candidate"].artifacts[0].artifact_id,
    )
    assert data == h["data"] and artifact == committed.candidate.artifacts[0]
    assert h["sources_checked"]
    assert publish(h) == committed


def test_approval_is_disabled_by_default_and_models_cannot_approve(scene):
    h = scene
    repo = SQLiteManifestRepository(
        h["store"], source_authorizer=lambda *args: None, clock=h["clock"]
    )
    with pytest.raises(PublicationError, match="approval_disabled"):
        repo.approve_exact(h["candidate"], h["person"])
    model = replace(h["person"], actor=h["person"].actor.model_copy(update={"kind": "model"}))
    with pytest.raises(PublicationError, match="publication_unauthorized"):
        h["manifests"].approve_exact(h["candidate"], model)


@pytest.mark.parametrize(
    "change", ["expired", "cancel", "attempt", "owner", "fence", "version", "revision", "run"]
)
def test_current_authority_is_reread_in_manifest_transaction(scene, change):
    h = scene
    h["manifests"].approve_exact(h["candidate"], h["person"])
    with sqlite3.connect(h["store"].path) as connection:
        state = json.loads(
            connection.execute("SELECT payload FROM review_state WHERE singleton=1").fetchone()[0]
        )
        job = state["jobs"][str(h["attempt"].job_id)]
        run = state["runs"][0]["value"]
        if change == "expired":
            h["clock"].now += 60
        elif change == "cancel":
            job["cancel_requested"] = True
        elif change == "attempt":
            run["attempt_id"] = str(uuid4())
        elif change == "owner":
            run["lease_owner"] = str(uuid4())
        elif change == "fence":
            run["fencing_token"] += 1
        elif change == "version":
            run["result_version"] += 1
        elif change == "revision":
            run["revision"]["revision_id"] = "changed-revision"
        else:
            job["current_run_id"] = str(uuid4())
        connection.execute("UPDATE review_state SET payload=?", (json.dumps(state),))
    with pytest.raises(PublicationError):
        publish(h)
    with sqlite3.connect(h["store"].path) as connection:
        assert connection.execute("SELECT count(*) FROM publication_manifests").fetchone()[0] == 0


def test_immutable_object_port_rejects_changed_bytes_and_wrong_version(scene):
    h = scene
    artifact = h["candidate"].artifacts[0]
    assert h["objects"].create(artifact.key, h["data"]) == artifact.object_version
    with pytest.raises(PublicationError, match="artifact_mismatch"):
        h["objects"].create(artifact.key, b"changed")
    with pytest.raises(PublicationError, match="artifact_mismatch"):
        h["objects"].read(artifact.model_copy(update={"object_version": "wrong"}))
    assert h["objects"].read(artifact) == h["data"]


def test_model_cannot_use_granted_owner_identity_for_publication_or_download(scene):
    h = scene
    h["manifests"].approve_exact(h["candidate"], h["person"])
    publish(h)
    model = replace(h["person"], actor=h["person"].actor.model_copy(update={"kind": "model"}))
    resolver = CommittedResultResolver(objects=h["objects"], manifests=h["manifests"])
    with pytest.raises(PublicationError, match="publication_unauthorized"):
        resolver.verified_bytes(
            model,
            h["candidate"].run.revision.case_id,
            h["candidate"].run.run_id,
            h["candidate"].artifacts[0].artifact_id,
        )
    with pytest.raises(PublicationError, match="publication_unauthorized"):
        h["publisher"].publish(
            h["candidate"],
            fencing_token=h["claim"].fencing_token,
            principal=model,
            attempt=h["attempt"],
        )


def test_cancelled_or_superseded_job_cannot_expose_old_manifest(scene):
    h = scene
    h["manifests"].approve_exact(h["candidate"], h["person"])
    publish(h)
    asyncio.run(h["store"].cancel(job_id=h["attempt"].job_id, now=h["clock"].now))
    resolver = CommittedResultResolver(objects=h["objects"], manifests=h["manifests"])
    with pytest.raises(PublicationError, match="stale_publication"):
        resolver.verified_bytes(
            h["person"],
            h["candidate"].run.revision.case_id,
            h["candidate"].run.run_id,
            h["candidate"].artifacts[0].artifact_id,
        )


@pytest.mark.parametrize("mutation", ["revoke", "source", "cancel"])
def test_callback_runs_outside_transaction_and_racing_mutation_is_rechecked(scene, mutation):
    h = scene
    h["manifests"].approve_exact(h["candidate"], h["person"])

    def interleave(person, run, sources):
        # A second real connection can write while the external callback runs.
        if mutation == "revoke":
            h["manifests"].revoke(run.revision.case_id)
        elif mutation == "cancel":
            asyncio.run(h["store"].cancel(job_id=h["attempt"].job_id, now=h["clock"].now))
        else:
            with sqlite3.connect(h["store"].path, timeout=0.1) as connection:
                payload = json.loads(
                    connection.execute("SELECT payload FROM review_state").fetchone()[0]
                )
                payload["runs"][0]["value"]["documents"][0]["version"] = "replacement"
                connection.execute("UPDATE review_state SET payload=?", (json.dumps(payload),))

    h["manifests"].source_authorizer = interleave
    with pytest.raises(PublicationError):
        publish(h)
    with sqlite3.connect(h["store"].path) as connection:
        assert connection.execute("SELECT count(*) FROM publication_manifests").fetchone()[0] == 0


def test_approval_revoked_during_its_source_callback_cannot_regrant(scene):
    h = scene
    h["manifests"].source_authorizer = lambda person, run, sources: h["manifests"].revoke(
        run.revision.case_id
    )
    with pytest.raises(PublicationError, match="stale_publication"):
        h["manifests"].approve_exact(h["candidate"], h["person"])
    with sqlite3.connect(h["store"].path) as connection:
        assert connection.execute("SELECT count(*) FROM publication_grants").fetchone()[0] == 0


def test_source_denial_and_revocation_on_download_return_no_bytes(scene):
    h = scene
    h["manifests"].approve_exact(h["candidate"], h["person"])
    publish(h)

    def denied(*args):
        raise ValueError("synthetic source access revoked")

    h["manifests"].source_authorizer = denied
    resolver = CommittedResultResolver(objects=h["objects"], manifests=h["manifests"])
    with pytest.raises(PublicationError, match="publication_unauthorized"):
        resolver.verified_bytes(
            h["person"],
            h["candidate"].run.revision.case_id,
            h["candidate"].run.run_id,
            h["candidate"].artifacts[0].artifact_id,
        )
    h["manifests"].source_authorizer = lambda *args: None
    h["manifests"].revoke(h["candidate"].run.revision.case_id)
    with pytest.raises(PublicationError, match="publication_unauthorized"):
        resolver.verified_bytes(
            h["person"],
            h["candidate"].run.revision.case_id,
            h["candidate"].run.run_id,
            h["candidate"].artifacts[0].artifact_id,
        )


def test_same_token_changed_manifest_and_expired_grants_fail(scene):
    h = scene
    h["manifests"].approve_exact(h["candidate"], h["person"])
    committed = publish(h)
    replacement = h["candidate"].model_copy(
        update={
            "artifacts": (h["candidate"].artifacts[0].model_copy(update={"template_version": "2"}),)
        }
    )
    with pytest.raises(PublicationError, match="manifest_conflict"):
        h["manifests"].approve_exact(replacement, h["person"])
    with pytest.raises(PublicationError, match="manifest_conflict"):
        h["publisher"].publish(
            replacement,
            fencing_token=h["claim"].fencing_token,
            principal=h["person"],
            attempt=h["attempt"],
        )
    with sqlite3.connect(h["store"].path) as connection:
        assert (
            connection.execute("SELECT digest FROM publication_manifests").fetchone()[0]
            == committed.manifest_digest
        )
    h["clock"].now += 900
    with pytest.raises(PublicationError, match="publication_unauthorized"):
        h["manifests"].authorize(
            h["person"], h["candidate"].run.revision.case_id, Permission.REVIEW
        )


def test_completed_job_remains_downloadable_only_with_bound_result_reference(scene):
    from appraisal_review.application.job_state import JobEvent
    from appraisal_review.domain.service_contracts import ExecutionStatus
    from appraisal_review.ports.jobs import ResultReference

    h = scene
    h["manifests"].approve_exact(h["candidate"], h["person"])
    committed = publish(h)
    artifact = committed.candidate.artifacts[0]
    reference = ResultReference(
        run_id=h["claim"].run_id,
        result_version=1,
        fencing_token=h["claim"].fencing_token,
        execution_status=ExecutionStatus.SUCCEEDED,
        business_status=WorkflowStatus.COMPLETED,
        artifact_status="written",
        result_digest="4" * 64,
        artifact_ids=(artifact.artifact_id,),
    )
    asyncio.run(
        h["store"].finish(
            h["claim"], event=JobEvent.PUBLISH_RESULT, now=h["clock"].now, result=reference
        )
    )
    h["clock"].now += 60
    resolver = CommittedResultResolver(objects=h["objects"], manifests=h["manifests"])
    assert (
        resolver.verified_bytes(
            h["person"],
            committed.candidate.run.revision.case_id,
            h["claim"].run_id,
            artifact.artifact_id,
        )[1]
        == h["data"]
    )
    with sqlite3.connect(h["store"].path) as connection:
        state = json.loads(connection.execute("SELECT payload FROM review_state").fetchone()[0])
        state["results"][0]["artifact_ids"] = []
        connection.execute("UPDATE review_state SET payload=?", (json.dumps(state),))
    with pytest.raises(PublicationError, match="stale_publication"):
        resolver.verified_bytes(
            h["person"],
            committed.candidate.run.revision.case_id,
            h["claim"].run_id,
            artifact.artifact_id,
        )


def test_revocation_during_object_io_blocks_final_download_check(scene, monkeypatch):
    h = scene
    h["manifests"].approve_exact(h["candidate"], h["person"])
    publish(h)
    read = h["objects"].read

    def revoke_on_read(artifact):
        data = read(artifact)
        h["manifests"].revoke(h["candidate"].run.revision.case_id)
        return data

    monkeypatch.setattr(h["objects"], "read", revoke_on_read)
    resolver = CommittedResultResolver(objects=h["objects"], manifests=h["manifests"])
    with pytest.raises(PublicationError, match="publication_unauthorized"):
        resolver.verified_bytes(
            h["person"],
            h["candidate"].run.revision.case_id,
            h["candidate"].run.run_id,
            h["candidate"].artifacts[0].artifact_id,
        )


def test_clock_resampled_after_callback_rejects_expired_lease(scene):
    h = scene
    h["manifests"].approve_exact(h["candidate"], h["person"])

    def delay(*args):
        h["clock"].now += 60

    h["manifests"].source_authorizer = delay
    with pytest.raises(PublicationError, match="stale_publication"):
        publish(h)


def child_command(h, action):
    import sys

    code = r"""
import asyncio, os, sys
from dataclasses import replace
from pathlib import Path
from uuid import UUID
from appraisal_review.adapters.local.sqlite_review_store import SQLiteReviewStore
from appraisal_review.adapters.local.sqlite_publication import (
    SQLiteManifestRepository, SQLiteArtifactObjectStore,
)
from appraisal_review.adapters.local.artifact_publication import (
    AttemptArtifactPublisher, CommittedResultResolver,
)
from appraisal_review.domain.artifact_publication import ManifestCandidate
from appraisal_review.domain.service_contracts import Permission
from appraisal_review.ports.artifact_publication import PublicationAttempt
from appraisal_review.testing.job_store_contract import principal
path, raw, job_id, owner, action = sys.argv[1:]
store = SQLiteReviewStore(Path(path), clock=lambda:1000)
candidate = ManifestCandidate.model_validate_json(raw)
person = replace(principal(), permissions=frozenset({Permission.REVIEW, Permission.PUBLISH}))
repo = SQLiteManifestRepository(store, source_authorizer=lambda *args:None, clock=lambda:1000)
objects = SQLiteArtifactObjectStore(store)
if action == "crash_before_commit":
    original = store._connect
    class CrashConnection:
        def __init__(self): self.connection = original()
        def __getattr__(self, name): return getattr(self.connection,name)
        def execute(self, statement, *args):
            result = self.connection.execute(statement,*args)
            if statement.startswith("INSERT INTO publication_manifests"):
                os._exit(73)
            return result
    store._connect = CrashConnection
publisher = AttemptArtifactPublisher(objects=objects,manifests=repo)
if action != "read":
    committed=publisher.publish(candidate,fencing_token=1,principal=person,attempt=PublicationAttempt(job_id=UUID(job_id),owner=UUID(owner),expected_result_version=0))
    if action == "crash_after_commit": os._exit(74)
else:
    resolver=CommittedResultResolver(objects=objects,manifests=repo)
    artifact,data=resolver.verified_bytes(person,candidate.run.revision.case_id,candidate.run.run_id,candidate.artifacts[0].artifact_id)
    assert len(data)==artifact.size_bytes
    print(artifact.content_hash)
"""
    return [
        sys.executable,
        "-c",
        code,
        str(h["store"].path),
        h["candidate"].model_dump_json(),
        str(h["attempt"].job_id),
        str(h["attempt"].owner),
        action,
    ]


@pytest.mark.parametrize(
    "action,exit_code", [("crash_before_commit", 73), ("crash_after_commit", 74)]
)
def test_actual_process_crash_recovers_atomic_manifest_and_verified_download(
    scene, action, exit_code
):
    import subprocess

    h = scene
    h["manifests"].approve_exact(h["candidate"], h["person"])
    child = subprocess.run(child_command(h, action), capture_output=True, text=True, timeout=15)
    assert child.returncode == exit_code, child.stderr
    with sqlite3.connect(h["store"].path) as connection:
        count = connection.execute("SELECT count(*) FROM publication_manifests").fetchone()[0]
    assert count == (0 if action == "crash_before_commit" else 1)
    committed = publish(h)
    reader = subprocess.run(child_command(h, "read"), capture_output=True, text=True, timeout=15)
    assert reader.returncode == 0, reader.stderr
    assert reader.stdout.strip() == committed.candidate.artifacts[0].content_hash


def test_two_processes_replay_one_durable_manifest(scene):
    import subprocess

    h = scene
    h["manifests"].approve_exact(h["candidate"], h["person"])
    children = [
        subprocess.Popen(
            child_command(h, "publish"), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        for _ in range(2)
    ]
    for child in children:
        _, errors = child.communicate(timeout=15)
        assert child.returncode == 0, errors
    with sqlite3.connect(h["store"].path) as connection:
        assert connection.execute("SELECT count(*) FROM publication_manifests").fetchone()[0] == 1
    assert publish(h).candidate == h["candidate"]


def test_real_store_lease_takeover_blocks_old_attempt_before_new_manifest(scene):
    h = scene
    h["manifests"].approve_exact(h["candidate"], h["person"])
    h["clock"].now += 60
    leases = asyncio.run(h["store"].expired_leases(now=h["clock"].now, limit=10))
    assert len(leases) == 1
    asyncio.run(h["store"].expire_lease(leases[0], now=h["clock"].now))
    newer = asyncio.run(
        h["store"].claim(
            job_id=h["claim"].job_id,
            run_id=h["claim"].run_id,
            owner=uuid4(),
            lease_seconds=60,
            now=h["clock"].now,
        )
    )
    assert newer.fencing_token == h["claim"].fencing_token + 1
    with pytest.raises(PublicationError, match="stale_publication"):
        publish(h)
    with sqlite3.connect(h["store"].path) as connection:
        assert connection.execute("SELECT count(*) FROM publication_manifests").fetchone()[0] == 0


def test_download_source_callback_receives_current_authenticated_principal(scene):
    h = scene
    h["manifests"].approve_exact(h["candidate"], h["person"])
    publish(h)
    current = replace(h["person"], permissions=frozenset({Permission.REVIEW}))
    observed = []
    h["manifests"].source_authorizer = lambda person, run, sources: observed.append(person)
    resolver = CommittedResultResolver(objects=h["objects"], manifests=h["manifests"])
    resolver.verified_bytes(
        current,
        h["candidate"].run.revision.case_id,
        h["candidate"].run.run_id,
        h["candidate"].artifacts[0].artifact_id,
    )
    assert observed and all(person == current for person in observed)


def test_revoked_case_cannot_be_resurrected_while_external_source_grant_is_changing(scene):
    h = scene
    h["manifests"].approve_exact(h["candidate"], h["person"])
    h["manifests"].revoke(h["candidate"].run.revision.case_id)
    # Revocation must stay effective even before an external grant has finished changing.
    with pytest.raises(PublicationError, match="publication_unauthorized"):
        h["manifests"].approve_exact(h["candidate"], h["person"])


def test_conflicting_reapproval_preserves_committed_grant(scene):
    h = scene
    h["manifests"].approve_exact(h["candidate"], h["person"])
    original = publish(h)
    changed = h["candidate"].model_copy(
        update={
            "artifacts": (
                h["candidate"]
                .artifacts[0]
                .model_copy(update={"template_version": "different-version"}),
            )
        }
    )
    with pytest.raises(PublicationError, match="manifest_conflict"):
        h["manifests"].approve_exact(changed, h["person"])
    assert (
        h["manifests"].read(original.candidate.run.revision.case_id, original.candidate.run.run_id)
        == original
    )
