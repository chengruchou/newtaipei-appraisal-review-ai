"""Fenced attempt-scoped publication, authorized downloads and reconciliation."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from pypdf import PdfReader
from test_formal_pdf_output import TOKEN, placeholder_map, placeholder_request, render_config

from appraisal_review.adapters.aws.artifacts.publication import (
    AttemptArtifactPublisher,
    CommittedResultResolver,
    DynamoDBManifestStore,
)
from appraisal_review.adapters.aws.storage.s3_object_store import S3ObjectStore
from appraisal_review.adapters.local.pdf_writer import LocalPDFWriter
from appraisal_review.application.service_guards import Principal, ServiceFault
from appraisal_review.domain.artifact_publication import (
    ArtifactKey,
    ManifestCandidate,
    PublicationError,
    PublishedArtifact,
)
from appraisal_review.domain.factor_models import WorkflowStatus
from appraisal_review.domain.service_contracts import (
    ActorReference,
    Permission,
    RevisionReference,
    RunReference,
)

BUCKET = "result-bucket"
PDF_BYTES = b"%PDF-1.4 synthetic attempt artifact"


class FakeConditionFailure(Exception):
    def __init__(self) -> None:
        super().__init__("conditional check failed")
        self.response = {"Error": {"Code": "ConditionalCheckFailedException"}}


class FakeDynamo:
    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}
        self.fail_next_put: Exception | None = None

    def put_item(self, **kwargs: Any) -> Any:
        if self.fail_next_put is not None:
            error, self.fail_next_put = self.fail_next_put, None
            raise error
        item = kwargs["Item"]
        record = item["case_id"]["S"]
        token = int(item["fencing_token"]["N"])
        digest = item["manifest_digest"]["S"]
        existing = self.items.get(record)
        if existing is not None and not (
            existing["fencing_token"] < token
            or (existing["fencing_token"] == token and existing["manifest_digest"] == digest)
        ):
            raise FakeConditionFailure()
        self.items[record] = {
            "fencing_token": token,
            "manifest_digest": digest,
            "manifest": item["manifest"]["S"],
        }
        return {}

    def get_item(self, **kwargs: Any) -> Any:
        record = kwargs["Key"]["case_id"]["S"]
        existing = self.items.get(record)
        if existing is None:
            return {}
        return {
            "Item": {
                "fencing_token": {"N": str(existing["fencing_token"])},
                "manifest_digest": {"S": existing["manifest_digest"]},
                "manifest": {"S": existing["manifest"]},
            }
        }


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.fail_downloads = 0

    def download_file(self, Bucket: str, Key: str, Filename: str) -> None:
        if self.fail_downloads > 0:
            self.fail_downloads -= 1
            raise TimeoutError("transient read failure")
        Path(Filename).write_bytes(self.objects[(Bucket, Key)])

    def put_object(self, **kwargs: Any) -> None:
        key = (kwargs["Bucket"], kwargs["Key"])
        if kwargs.get("IfNoneMatch") == "*" and key in self.objects:
            raise FakeConditionFailure()
        self.objects[key] = kwargs["Body"].read()


class FakePresign:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], int]] = []

    def generate_presigned_url(
        self, ClientMethod: str, Params: dict[str, Any], ExpiresIn: int
    ) -> str:
        self.calls.append((ClientMethod, Params, ExpiresIn))
        return f"https://signed.example/{Params['Key']}?expires={ExpiresIn}"


def make_run(case_id: str = "case-1") -> RunReference:
    return RunReference(
        run_id=uuid4(),
        revision=RevisionReference(case_id=case_id, revision_id="rev-1", material_digest="0" * 64),
        attempt_id=uuid4(),
    )


def make_artifact(run: RunReference, data: bytes = PDF_BYTES) -> PublishedArtifact:
    artifact_id = uuid4()
    return PublishedArtifact(
        artifact_id=artifact_id,
        key=ArtifactKey.for_run(run, artifact_id).key(),
        content_hash=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        writer_version="2",
        template_id="formal-v1",
        template_version="2026.09",
        template_hash="1" * 64,
        field_map_hash="2" * 64,
        field_ids=("case-identity",),
        page_count=1,
    )


def candidate_for(run: RunReference, artifact: PublishedArtifact) -> ManifestCandidate:
    return ManifestCandidate(
        run=run,
        result_version=1,
        review_status=WorkflowStatus.COMPLETED,
        artifacts=(artifact,),
    )


def build(
    tmp_path: Path,
) -> tuple[AttemptArtifactPublisher, CommittedResultResolver, FakeS3, FakeDynamo, FakePresign]:
    s3 = FakeS3()
    dynamo = FakeDynamo()
    presign = FakePresign()
    store = S3ObjectStore(s3)
    manifests = DynamoDBManifestStore(dynamo, table_name="cases")
    publisher = AttemptArtifactPublisher(
        object_store=store, manifests=manifests, result_bucket=BUCKET
    )
    resolver = CommittedResultResolver(
        manifests=manifests, object_store=store, presigner=presign, result_bucket=BUCKET
    )
    return publisher, resolver, s3, dynamo, presign


def reviewer(case_id: str = "case-1", *, permitted: bool = True) -> Principal:
    return Principal(
        actor=ActorReference(actor_id="reviewer-1", kind="human"),
        case_ids=frozenset({case_id}),
        permissions=frozenset({Permission.REVIEW} if permitted else {Permission.CONFIRM}),
    )


def staged(
    tmp_path: Path, publisher: AttemptArtifactPublisher, run: RunReference
) -> PublishedArtifact:
    local = tmp_path / f"local-{uuid4()}.pdf"
    local.write_bytes(PDF_BYTES)
    artifact = make_artifact(run)
    return publisher.stage(local, run=run, artifact=artifact, writer=object())


def test_key_layout_separates_case_run_attempt_and_rejects_foreign_keys() -> None:
    run = make_run()
    artifact = make_artifact(run)
    parsed = ArtifactKey.parse(artifact.key)
    assert (parsed.case_id, parsed.run_id, parsed.attempt_id) == (
        "case-1",
        run.run_id,
        run.attempt_id,
    )
    for bad in (
        "cases/case-1/../case-2/artifact.pdf",
        f"cases/case-1/runs/{run.run_id}/artifacts/{artifact.artifact_id}.pdf",
        "cases/case-1/runs/not-a-uuid/attempts/also-bad/artifacts/x.pdf",
        artifact.key + ".bak",
    ):
        with pytest.raises(ValueError, match="attempt-scoped artifact layout"):
            ArtifactKey.parse(bad)
    with pytest.raises(ValueError, match="must bind an attempt"):
        ArtifactKey.for_run(run.model_copy(update={"attempt_id": None}), uuid4())
    foreign = make_artifact(make_run("case-2"))
    with pytest.raises(ValueError, match="this exact attempt"):
        candidate_for(run, foreign)


def test_two_attempts_race_only_current_fencing_token_publishes(tmp_path: Path) -> None:
    publisher, resolver, _, _, _ = build(tmp_path)
    case_run = make_run()
    first = candidate_for(case_run, staged(tmp_path, publisher, case_run))
    committed_one = publisher.publish(first, fencing_token=1)
    later_attempt = case_run.model_copy(update={"attempt_id": uuid4()})
    second = candidate_for(later_attempt, staged(tmp_path, publisher, later_attempt))
    committed_two = publisher.publish(second, fencing_token=2)
    # The stale attempt cannot overwrite the newer committed result.
    with pytest.raises(PublicationError, match="stale_publication"):
        publisher.publish(first, fencing_token=1)
    # Identical re-commit under the current token stays idempotent.
    assert publisher.publish(second, fencing_token=2) == committed_two
    # A different candidate under the same token is a conflict, not a rewrite.
    replacement = candidate_for(later_attempt, staged(tmp_path, publisher, later_attempt))
    with pytest.raises(PublicationError, match="manifest_conflict"):
        publisher.publish(replacement, fencing_token=2)
    current = resolver.result(reviewer(), case_run.revision.case_id, case_run.run_id)
    assert current == committed_two
    assert current is not None and current.fencing_token == 2
    assert committed_one.fencing_token == 1


def test_object_presence_or_worker_claim_is_not_success(tmp_path: Path) -> None:
    publisher, resolver, s3, _, _ = build(tmp_path)
    run = make_run()
    artifact = staged(tmp_path, publisher, run)
    assert (BUCKET, artifact.key) in s3.objects
    assert resolver.result(reviewer(), run.revision.case_id, run.run_id) is None
    with pytest.raises(PublicationError, match="unpublished"):
        resolver.fetch_verified(
            reviewer(),
            run.revision.case_id,
            run.run_id,
            artifact.artifact_id,
            tmp_path / "out.pdf",
        )


def test_tampered_digest_size_or_content_is_rejected(tmp_path: Path) -> None:
    publisher, resolver, s3, dynamo, _ = build(tmp_path)
    run = make_run()
    artifact = staged(tmp_path, publisher, run)
    local = tmp_path / "declared.pdf"
    local.write_bytes(PDF_BYTES)
    wrong_size = make_artifact(run).model_copy(update={"size_bytes": 5})
    with pytest.raises(PublicationError, match="artifact_mismatch"):
        publisher.stage(local, run=run, artifact=wrong_size, writer=object())
    s3.objects[(BUCKET, artifact.key)] = b"%PDF-1.4 tampered bytes"
    with pytest.raises(PublicationError, match="artifact_mismatch"):
        publisher.publish(candidate_for(run, artifact), fencing_token=1)
    s3.objects[(BUCKET, artifact.key)] = b"not a pdf at all padded to size length!!"
    with pytest.raises(PublicationError, match="artifact_mismatch"):
        publisher.publish(candidate_for(run, artifact), fencing_token=1)
    s3.objects[(BUCKET, artifact.key)] = PDF_BYTES
    committed = publisher.publish(candidate_for(run, artifact), fencing_token=1)
    record = next(iter(dynamo.items))
    dynamo.items[record]["manifest"] = committed.model_dump_json().replace(
        artifact.content_hash, "3" * 64
    )
    with pytest.raises(PublicationError, match="manifest_corrupt"):
        resolver.result(reviewer(), run.revision.case_id, run.run_id)


def test_unauthorized_principals_cannot_query_or_download(tmp_path: Path) -> None:
    publisher, resolver, _, _, _ = build(tmp_path)
    run = make_run()
    artifact = staged(tmp_path, publisher, run)
    publisher.publish(candidate_for(run, artifact), fencing_token=1)
    for principal in (reviewer("case-2"), reviewer(permitted=False)):
        with pytest.raises(ServiceFault):
            resolver.result(principal, run.revision.case_id, run.run_id)
        with pytest.raises(ServiceFault):
            resolver.download_url(principal, run.revision.case_id, run.run_id, artifact.artifact_id)
        with pytest.raises(ServiceFault):
            resolver.fetch_verified(
                principal,
                run.revision.case_id,
                run.run_id,
                artifact.artifact_id,
                tmp_path / "denied.pdf",
            )


def test_download_authority_is_short_lived_and_manifest_bound(tmp_path: Path) -> None:
    publisher, resolver, _, _, presign = build(tmp_path)
    run = make_run()
    artifact = staged(tmp_path, publisher, run)
    publisher.publish(candidate_for(run, artifact), fencing_token=1)
    url = resolver.download_url(
        reviewer(), run.revision.case_id, run.run_id, artifact.artifact_id, expires_in=99999
    )
    method, params, expires = presign.calls[-1]
    assert method == "get_object"
    assert params == {"Bucket": BUCKET, "Key": artifact.key}
    assert expires == 900 and "expires=900" in url
    resolver.download_url(
        reviewer(), run.revision.case_id, run.run_id, artifact.artifact_id, expires_in=30
    )
    assert presign.calls[-1][2] == 30
    with pytest.raises(PublicationError, match="unpublished"):
        resolver.download_url(reviewer(), run.revision.case_id, run.run_id, uuid4())


def test_reconciliation_recommits_after_crash_and_retries_transient_reads(
    tmp_path: Path,
) -> None:
    publisher, resolver, s3, dynamo, _ = build(tmp_path)
    run = make_run()
    artifact = staged(tmp_path, publisher, run)
    candidate = candidate_for(run, artifact)
    dynamo.fail_next_put = TimeoutError("crash before manifest commit")
    with pytest.raises(PublicationError, match="manifest_store_unavailable"):
        publisher.publish(candidate, fencing_token=1)
    assert resolver.result(reviewer(), run.revision.case_id, run.run_id) is None
    committed = publisher.publish(candidate, fencing_token=1)
    s3.fail_downloads = 1
    fetched = resolver.fetch_verified(
        reviewer(),
        run.revision.case_id,
        run.run_id,
        artifact.artifact_id,
        tmp_path / "fetched.pdf",
    )
    assert fetched == artifact
    assert (tmp_path / "fetched.pdf").read_bytes() == PDF_BYTES
    assert committed.manifest_digest == candidate.digest()


def test_revealed_backfill_output_is_never_publishable(tmp_path: Path) -> None:
    publisher, _, _, _, _ = build(tmp_path)
    run = make_run()
    local = tmp_path / "revealed.pdf"
    local.write_bytes(PDF_BYTES)

    class Revealing:
        reveals_placeholders = True

    with pytest.raises(PublicationError, match="revealed_output_forbidden"):
        publisher.stage(local, run=run, artifact=make_artifact(run), writer=Revealing())
    with pytest.raises(Exception, match=r"placeholder_only"):
        PublishedArtifact.model_validate(
            {**make_artifact(run).model_dump(mode="json"), "placeholder_only": False}
        )


def test_cjk_placeholder_pdf_uploads_commits_downloads_and_reopens(tmp_path: Path) -> None:
    field_map = placeholder_map()
    request, policy = placeholder_request(tmp_path, field_map, "attempt-local.pdf")
    writer = LocalPDFWriter(render_config=render_config(), template_policy=policy)
    written = asyncio.run(writer.write_pdf(request))
    assert written.artifact_created
    local = tmp_path / "attempt-local.pdf"
    data = local.read_bytes()

    publisher, resolver, _, _, _ = build(tmp_path)
    run = make_run()
    artifact_id = uuid4()
    artifact = PublishedArtifact(
        artifact_id=artifact_id,
        key=ArtifactKey.for_run(run, artifact_id).key(),
        content_hash=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        writer_version="2",
        template_id=policy.template_id,
        template_version="2026.09",
        template_hash=policy.template_sha256,
        field_map_hash=policy.field_map_sha256,
        field_ids=tuple(written.written_field_ids),
        page_count=written.page_count,
    )
    publisher.stage(local, run=run, artifact=artifact, writer=writer)
    publisher.publish(candidate_for(run, artifact), fencing_token=1)
    downloaded = tmp_path / "downloaded.pdf"
    resolver.fetch_verified(reviewer(), run.revision.case_id, run.run_id, artifact_id, downloaded)
    text = PdfReader(downloaded).pages[0].extract_text()
    assert TOKEN in text
