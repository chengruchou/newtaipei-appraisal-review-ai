"""Renewing a lapsed download window, and the things renewal must never do.

The window is short on purpose, so a reviewer who steps away comes back to a refused
download. Renewal exists so that fetching the same committed bytes again does not require
re-running a case. The dangerous mistake is letting renewal become a way back in after
authority was withdrawn, so the revocation tests here matter more than the happy path.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace
from io import BytesIO
from typing import Any
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
from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.domain.artifact_publication import (
    ArtifactKey,
    ManifestCandidate,
    PublicationError,
    PublishedArtifact,
    SourceVersion,
)
from appraisal_review.domain.factor_models import WorkflowStatus
from appraisal_review.domain.service_contracts import ActorReference, Permission
from appraisal_review.ports.artifact_publication import PublicationAttempt
from appraisal_review.testing.job_store_contract import principal, submission

# A short window so the grant lapses while the attempt lease is still live: the subject
# here is renewal, not the job lifecycle. CAP is the hard maximum the code enforces.
TTL = 5
CAP = 900


class Clock:
    now = 1000

    def __call__(self) -> int:
        return self.now


@pytest.fixture
def scene(tmp_path: Any) -> dict[str, Any]:
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
    sources_checked: list[Any] = []

    def source_authorizer(person: Any, run: Any, sources: Any) -> None:
        sources_checked.append((person, run, sources))

    manifests = SQLiteManifestRepository(
        store,
        source_authorizer=source_authorizer,
        trusted_synthetic_approval=True,
        clock=clock,
        approval_ttl_seconds=TTL,
    )
    publisher = AttemptArtifactPublisher(objects=objects, manifests=manifests)
    local = tmp_path / "output.pdf"
    local.write_bytes(data)

    class PlaceholderWriter:
        reveals_placeholders = False

    staged = publisher.stage(local, run=run, artifact=artifact, writer=PlaceholderWriter())
    candidate = ManifestCandidate(
        run=run, result_version=1, review_status=WorkflowStatus.COMPLETED, artifacts=(staged,)
    )
    manifests.approve_exact(candidate, person)
    publisher.publish(
        candidate,
        fencing_token=claim.fencing_token,
        principal=person,
        attempt=PublicationAttempt(
            job_id=job_id, owner=claim.owner, expected_result_version=claim.expected_result_version
        ),
    )
    resolver = CommittedResultResolver(objects=objects, manifests=manifests)
    return dict(
        store=store,
        clock=clock,
        person=person,
        candidate=candidate,
        manifests=manifests,
        objects=objects,
        resolver=resolver,
        data=data,
        case_id=candidate.run.revision.case_id,
        sources_checked=sources_checked,
    )


def fetch(h: dict[str, Any]) -> bytes:
    _, data = h["resolver"].verified_bytes(
        h["person"],
        h["case_id"],
        h["candidate"].run.run_id,
        h["candidate"].artifacts[0].artifact_id,
    )
    return bytes(data)


def object_rows(h: dict[str, Any]) -> int:
    with h["store"]._connect() as connection:
        return int(connection.execute("SELECT count(*) FROM publication_objects").fetchone()[0])


class TestRenewingALapsedWindow:
    def test_a_lapsed_download_is_refused_then_renewed(self, scene: dict[str, Any]) -> None:
        h = scene
        assert fetch(h) == h["data"]

        h["clock"].now += TTL + 1
        with pytest.raises(PublicationError):
            fetch(h)

        expires = h["manifests"].reauthorize_download(h["person"], h["case_id"])
        assert expires == h["clock"].now + TTL
        assert fetch(h) == h["data"], "the same committed bytes must come back unchanged"

    def test_renewal_regenerates_nothing(self, scene: dict[str, Any]) -> None:
        h = scene
        before = object_rows(h)
        h["clock"].now += TTL + 1
        h["manifests"].reauthorize_download(h["person"], h["case_id"])
        assert object_rows(h) == before, "renewal must not write a new object"

    def test_renewal_cannot_outlast_the_configured_window(self, scene: dict[str, Any]) -> None:
        # The cap is the protection; a renewal that could extend it would make the short
        # window decorative.
        h = scene
        h["clock"].now += TTL + 1
        first = h["manifests"].reauthorize_download(h["person"], h["case_id"])
        second = h["manifests"].reauthorize_download(h["person"], h["case_id"])
        assert first == second == h["clock"].now + TTL

    def test_renewal_rechecks_source_admission(self, scene: dict[str, Any]) -> None:
        h = scene
        h["sources_checked"].clear()
        h["clock"].now += TTL + 1
        h["manifests"].reauthorize_download(h["person"], h["case_id"])
        assert h["sources_checked"], "a withdrawn source must still be able to block a renewal"

    def test_a_configured_window_over_the_cap_is_refused(self, scene: dict[str, Any]) -> None:
        # Renewal inherits this cap, so the cap itself has to be unconfigurable upward.
        with pytest.raises(ValueError, match="between 1 and 900"):
            SQLiteManifestRepository(
                scene["store"],
                source_authorizer=lambda *args: None,
                approval_ttl_seconds=CAP + 1,
            )


class TestRenewalIsNotAWayBackIn:
    def test_a_revoked_grant_is_never_renewed(self, scene: dict[str, Any]) -> None:
        # The whole reason renewal is dangerous. Revocation withdraws authority; if renewal
        # could reissue a grant afterwards, revoke would only pause access for 15 minutes.
        h = scene
        h["manifests"].revoke(h["case_id"])
        with pytest.raises(PublicationError) as refused:
            h["manifests"].reauthorize_download(h["person"], h["case_id"])
        assert refused.value.code == "publication_revoked"
        with pytest.raises(PublicationError):
            fetch(h)

    def test_revocation_still_holds_after_the_window_lapses(self, scene: dict[str, Any]) -> None:
        h = scene
        h["clock"].now += TTL + 1
        h["manifests"].revoke(h["case_id"])
        with pytest.raises(PublicationError) as refused:
            h["manifests"].reauthorize_download(h["person"], h["case_id"])
        assert refused.value.code == "publication_revoked"

    def test_a_case_with_no_committed_output_cannot_be_renewed(self, scene: dict[str, Any]) -> None:
        # The principal genuinely holds this case, so the refusal is about missing output
        # rather than missing permission.
        h = scene
        empty = str(uuid4())
        holder = replace(h["person"], case_ids=h["person"].case_ids | {empty})
        with pytest.raises(PublicationError) as refused:
            h["manifests"].reauthorize_download(holder, empty)
        assert refused.value.code == "unpublished"

    def test_a_model_principal_cannot_renew(self, scene: dict[str, Any]) -> None:
        h = scene
        robot = replace(h["person"], actor=ActorReference(actor_id="a-model", kind="model"))
        with pytest.raises(PublicationError) as refused:
            h["manifests"].reauthorize_download(robot, h["case_id"])
        assert refused.value.code == "publication_unauthorized"

    def test_a_principal_without_the_case_permission_cannot_renew(
        self, scene: dict[str, Any]
    ) -> None:
        h = scene
        stranger = replace(h["person"], case_ids=frozenset())
        with pytest.raises(ServiceFault):
            h["manifests"].reauthorize_download(stranger, h["case_id"])

    def test_another_actor_cannot_renew_this_case(self, scene: dict[str, Any]) -> None:
        h = scene
        other = replace(h["person"], actor=ActorReference(actor_id=str(uuid4()), kind="human"))
        with pytest.raises(PublicationError) as refused:
            h["manifests"].reauthorize_download(other, h["case_id"])
        assert refused.value.code in {"unpublished", "publication_unauthorized"}
