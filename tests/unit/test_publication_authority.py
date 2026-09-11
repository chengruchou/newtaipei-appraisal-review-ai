"""Real DynamoDB transaction regressions using the offline Moto service emulator."""

from __future__ import annotations

import hashlib
from io import BytesIO
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import boto3
import pytest
from boto3.dynamodb.types import TypeSerializer
from botocore.config import Config
from moto import mock_aws
from pypdf import PdfWriter

from appraisal_review.adapters.aws.artifacts.publication import (
    AttemptArtifactPublisher,
    CommittedResultResolver,
    DynamoDBManifestStore,
)
from appraisal_review.adapters.aws.storage.s3_object_store import S3ObjectStore
from appraisal_review.application.service_guards import Principal
from appraisal_review.domain.artifact_publication import (
    ArtifactKey,
    ManifestCandidate,
    PublicationError,
    PublishedArtifact,
    SourceVersion,
)
from appraisal_review.domain.factor_models import WorkflowStatus
from appraisal_review.domain.service_contracts import (
    ActorReference,
    Permission,
    RevisionReference,
    RunReference,
)
from appraisal_review.ports.artifact_publication import PublicationAttempt


def marshal(value):
    return {key: TypeSerializer().serialize(item) for key, item in value.items()}


class Clock:
    now = 100

    def __call__(self):
        return self.now


@pytest.fixture
def environment(tmp_path):
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName="publication",
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": name, "AttributeType": "S"} for name in ("pk", "sk")
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        s3 = boto3.client("s3", region_name="us-east-1", config=Config(signature_version="s3v4"))
        s3.create_bucket(Bucket="result-bucket")
        s3.put_bucket_versioning(
            Bucket="result-bucket", VersioningConfiguration={"Status": "Enabled"}
        )
        clock = Clock()
        store = DynamoDBManifestStore(
            client, table_name="publication", job_table_name="publication", clock=clock
        )
        object_store = S3ObjectStore(s3)
        publisher = AttemptArtifactPublisher(
            object_store=object_store,
            manifests=store,
            result_bucket="result-bucket",
            work_directory=tmp_path,
        )
        resolver = CommittedResultResolver(
            manifests=store,
            object_store=object_store,
            presigner=s3,
            result_bucket="result-bucket",
            work_directory=tmp_path,
        )
        yield Harness(client, s3, store, publisher, resolver, clock, tmp_path)


class Harness:
    def __init__(self, client, s3, store, publisher, resolver, clock, tmp_path):
        self.client, self.s3, self.store = client, s3, store
        self.publisher, self.resolver, self.clock, self.tmp_path = (
            publisher,
            resolver,
            clock,
            tmp_path,
        )
        self.run = RunReference(
            run_id=uuid4(),
            attempt_id=uuid4(),
            revision=RevisionReference(
                case_id="case-1", revision_id="rev-1", material_digest="0" * 64
            ),
        )
        self.attempt = PublicationAttempt(job_id=uuid4(), owner=uuid4(), expected_result_version=0)
        self.principal = Principal(
            actor=ActorReference(actor_id="worker-1", kind="system"),
            case_ids=frozenset({"case-1"}),
            permissions=frozenset({Permission.PUBLISH, Permission.REVIEW}),
        )
        self.job = {
            "pk": f"JOB#{self.attempt.job_id}",
            "sk": "META",
            "job_id": str(self.attempt.job_id),
            "case_id": "case-1",
            "current_run_id": str(self.run.run_id),
            "status": "running",
            "cancel_requested": False,
        }
        self.source = SourceVersion(document_id="doc-1", version="version-1", content_hash="4" * 64)
        self.row = {
            "pk": f"RUN#{self.run.run_id}",
            "sk": "META",
            "job_id": str(self.attempt.job_id),
            "run_id": str(self.run.run_id),
            "revision": self.run.revision.model_dump(mode="json"),
            "documents": [{"case_id": "case-1", **self.source.model_dump(mode="json")}],
            "status": "running",
            "attempt_id": str(self.run.attempt_id),
            "lease_owner": str(self.attempt.owner),
            "lease_expires_at": 200,
            "fencing_token": 1,
            "result_version": 0,
        }
        self.attempt_row = {
            "pk": self.row["pk"],
            "sk": f"ATTEMPT#{self.run.attempt_id}",
            "job_id": str(self.attempt.job_id),
            "run_id": str(self.run.run_id),
            "attempt_id": str(self.run.attempt_id),
            "owner": str(self.attempt.owner),
            "fencing_token": 1,
            "outcome": "running",
        }
        for row in (self.job, self.row, self.attempt_row):
            self.put(row)
        self.access = {
            **store.access_key("case-1", "worker-1"),
            "active": True,
            "permissions": [Permission.PUBLISH.value, Permission.REVIEW.value],
            "expires_at": 500,
        }
        self.put(self.access)
        self.candidate, self.data = self.new_candidate()
        self.candidate = self.stage(self.candidate, self.data)
        self.approve(self.candidate)

    def put(self, row):
        self.client.put_item(TableName="publication", Item=marshal(row))

    def new_candidate(self):
        buffer = BytesIO()
        pdf = PdfWriter()
        pdf.add_blank_page(width=72, height=72)
        pdf.add_metadata({"/AppraisalReviewWriterVersion": "2"})
        pdf.write(buffer)
        data = buffer.getvalue()
        identity = uuid4()
        artifact = PublishedArtifact(
            artifact_id=identity,
            key=ArtifactKey.for_run(self.run, identity).key(),
            content_hash=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
            writer_version="2",
            template_id="template-1",
            template_version="v1",
            template_hash="1" * 64,
            field_map_hash="2" * 64,
            font_hash="3" * 64,
            field_ids=("field-1",),
            page_count=1,
            source_versions=(self.source,),
        )
        return ManifestCandidate(
            run=self.run,
            result_version=1,
            review_status=WorkflowStatus.COMPLETED,
            artifacts=(artifact,),
        ), data

    def approve(self, candidate):
        self.grant = {
            **self.store.approval_key(candidate.run, self.principal.actor.actor_id),
            "active": True,
            "manifest_digest": candidate.digest(),
            "expires_at": 500,
        }
        self.put(self.grant)

    def stage(self, candidate, data):
        path = self.tmp_path / "staged.pdf"
        path.write_bytes(data)

        class PlaceholderWriter:
            reveals_placeholders = False

        artifact = self.publisher.stage(
            path, run=self.run, artifact=candidate.artifacts[0], writer=PlaceholderWriter()
        )
        return candidate.model_copy(update={"artifacts": (artifact,)})

    def publish(self, candidate=None, token=1):
        return self.publisher.publish(
            candidate or self.candidate,
            fencing_token=token,
            principal=self.principal,
            attempt=self.attempt,
        )


def test_expired_attempt_without_any_manifest_is_rejected(environment):
    h = environment
    h.clock.now = 200
    with pytest.raises(PublicationError, match="stale_publication"):
        h.publish()
    assert h.store.read("case-1", h.run.run_id) is None


def test_superseded_attempt_before_new_manifest_is_rejected(environment):
    h = environment
    h.row.update(attempt_id=str(uuid4()), fencing_token=2)
    h.put(h.row)
    with pytest.raises(PublicationError, match="stale_publication"):
        h.publish()
    assert h.store.read("case-1", h.run.run_id) is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("lease_owner", str(uuid4())),
        ("result_version", 1),
        ("status", "failed"),
        ("revision", {"case_id": "case-1", "revision_id": "rev-2", "material_digest": "0" * 64}),
    ],
)
def test_current_job_authority_fields_are_not_worker_claims(environment, field, value):
    h = environment
    h.row[field] = value
    h.put(h.row)
    with pytest.raises(PublicationError, match="stale_publication"):
        h.publish()


@pytest.mark.parametrize(
    "target,field,value",
    [
        ("job", "cancel_requested", True),
        ("job", "current_run_id", str(uuid4())),
        ("access", "active", False),
        ("grant", "active", False),
        ("grant", "manifest_digest", "9" * 64),
        ("grant", "expires_at", 100),
        ("attempt_row", "outcome", "lease_expired"),
    ],
)
def test_current_permission_job_and_attempt_are_transaction_conditions(
    environment, target, field, value
):
    h = environment
    row = getattr(h, target)
    row[field] = value
    h.put(row)
    with pytest.raises(PublicationError):
        h.publish()
    assert h.store.read("case-1", h.run.run_id) is None


def test_identical_retry_and_changed_content_under_same_token(environment):
    h = environment
    committed = h.publish()
    assert h.publish() == committed
    replacement, data = h.new_candidate()
    replacement = h.stage(replacement, data)
    h.approve(replacement)
    with pytest.raises(PublicationError, match="manifest_conflict"):
        h.publish(replacement)
    assert h.store.read("case-1", h.run.run_id) == committed


def test_unapproved_bytes_and_missing_source_or_font_fail(environment):
    h = environment
    for update in ({"source_versions": ()}, {"font_hash": None}):
        artifact = h.candidate.artifacts[0].model_copy(update=update)
        candidate = h.candidate.model_copy(update={"artifacts": (artifact,)})
        h.approve(candidate)
        with pytest.raises(PublicationError, match="artifact_evidence_missing"):
            h.publish(candidate)
    tampered = h.s3.put_object(
        Bucket="result-bucket",
        Key=h.candidate.artifacts[0].key,
        Body=b"tampered",
        ContentType="application/pdf",
    )
    artifact = h.candidate.artifacts[0].model_copy(update={"object_version": tampered["VersionId"]})
    candidate = h.candidate.model_copy(update={"artifacts": (artifact,)})
    h.approve(candidate)
    with pytest.raises(PublicationError, match="artifact_mismatch"):
        h.publish(candidate)


def test_revoked_download_and_expiry(environment):
    h = environment
    h.publish()
    artifact = h.candidate.artifacts[0]
    url = h.resolver.download_url(
        h.principal, "case-1", h.run.run_id, artifact.artifact_id, expires_in=99999
    )
    assert "Expires" in url or "X-Amz-Expires" in url
    # The request cannot outlive the current access grant (500 - 100).
    h.clock.now = 500
    with pytest.raises(PublicationError, match="publication_unauthorized"):
        h.resolver.download_url(h.principal, "case-1", h.run.run_id, artifact.artifact_id)
    h.clock.now = 100
    h.access["active"] = False
    h.put(h.access)
    with pytest.raises(PublicationError, match="publication_unauthorized"):
        h.resolver.fetch_verified(
            h.principal, "case-1", h.run.run_id, artifact.artifact_id, h.tmp_path / "denied.pdf"
        )
    assert not (h.tmp_path / "denied.pdf").exists()


@pytest.mark.parametrize("changed", ["lease", "permission", "documents", "attempt"])
def test_authority_changed_between_read_and_transaction_cannot_publish(environment, changed):
    h = environment

    def interleave(**kwargs):
        if changed == "lease":
            h.row["lease_expires_at"] = 100
            h.put(h.row)
        elif changed == "permission":
            h.access["active"] = False
            h.put(h.access)
        elif changed == "documents":
            h.row["documents"][0]["content_hash"] = "8" * 64
            h.put(h.row)
        else:
            h.row["attempt_id"] = str(uuid4())
            h.put(h.row)

    h.client.meta.events.register("before-parameter-build.dynamodb.TransactWriteItems", interleave)
    with pytest.raises(PublicationError, match="stale_publication"):
        h.publish()
    assert h.store.read("case-1", h.run.run_id) is None


def test_crash_after_transaction_ack_loss_replays_exact_committed_result(environment, monkeypatch):
    h = environment
    real = h.client.transact_write_items

    def lost_ack(**kwargs):
        real(**kwargs)
        raise TimeoutError("synthetic lost acknowledgment")

    monkeypatch.setattr(h.client, "transact_write_items", lost_ack)
    with pytest.raises(PublicationError, match="manifest_store_unavailable"):
        h.publish()
    committed = h.store.read("case-1", h.run.run_id)
    assert committed is not None
    monkeypatch.setattr(h.client, "transact_write_items", real)
    assert h.publish() == committed


def test_stage_retry_recovers_created_immutable_version(environment):
    h = environment
    assert h.stage(h.candidate, h.data) == h.candidate
    latest = h.s3.put_object(
        Bucket="result-bucket",
        Key=h.candidate.artifacts[0].key,
        Body=b"replacement",
        ContentType="application/pdf",
    )
    assert latest["VersionId"] != h.candidate.artifacts[0].object_version
    # Publication and downloads read the original immutable version, never latest.
    h.publish()
    target = h.tmp_path / "pinned.pdf"
    h.resolver.fetch_verified(
        h.principal, "case-1", h.run.run_id, h.candidate.artifacts[0].artifact_id, target
    )
    assert target.read_bytes() == h.data
    with pytest.raises(PublicationError, match="artifact_mismatch"):
        h.stage(h.candidate, h.data)


def test_committed_manifest_retains_version_after_newer_object_and_restart(environment):
    h = environment
    committed = h.publish()
    artifact = committed.candidate.artifacts[0]
    latest = h.s3.put_object(
        Bucket="result-bucket",
        Key=artifact.key,
        Body=b"a later object must not replace committed evidence",
        ContentType="application/pdf",
    )
    assert latest["VersionId"] != artifact.object_version
    assert h.publish() == committed

    # Fresh adapters recover the durable manifest and its pinned version.
    store = DynamoDBManifestStore(
        h.client, table_name="publication", job_table_name="publication", clock=h.clock
    )
    resolver = CommittedResultResolver(
        manifests=store,
        object_store=S3ObjectStore(h.s3),
        presigner=h.s3,
        result_bucket="result-bucket",
        work_directory=h.tmp_path,
    )
    assert resolver.result(h.principal, "case-1", h.run.run_id) == committed
    target = h.tmp_path / "retained-version.pdf"
    resolver.fetch_verified(h.principal, "case-1", h.run.run_id, artifact.artifact_id, target)
    assert target.read_bytes() == h.data
    url = resolver.download_url(h.principal, "case-1", h.run.run_id, artifact.artifact_id)
    assert parse_qs(urlparse(url).query)["versionId"] == [artifact.object_version]

    # Retention never restores expired or revoked download authority.
    h.access["active"] = False
    h.put(h.access)
    with pytest.raises(PublicationError, match="publication_unauthorized"):
        resolver.fetch_verified(
            h.principal, "case-1", h.run.run_id, artifact.artifact_id, h.tmp_path / "revoked.pdf"
        )
    assert not (h.tmp_path / "revoked.pdf").exists()


def test_download_revocation_during_object_read_leaves_no_file(environment, monkeypatch):
    h = environment
    h.publish()
    real = h.s3.get_object

    def revoke(**kwargs):
        result = real(**kwargs)
        h.access["active"] = False
        h.put(h.access)
        return result

    monkeypatch.setattr(h.s3, "get_object", revoke)
    target = h.tmp_path / "revoked.pdf"
    with pytest.raises(PublicationError, match="publication_unauthorized"):
        h.resolver.fetch_verified(
            h.principal, "case-1", h.run.run_id, h.candidate.artifacts[0].artifact_id, target
        )
    assert not target.exists()


def test_existing_destination_and_aliases_remain_unchanged(environment):
    h = environment
    h.publish()
    original = h.tmp_path / "source.pdf"
    original.write_bytes(b"protected original")
    linked = h.tmp_path / "linked.pdf"
    linked.symlink_to(original)
    for target in (original, linked):
        with pytest.raises(PublicationError, match="destination_exists"):
            h.resolver.fetch_verified(
                h.principal, "case-1", h.run.run_id, h.candidate.artifacts[0].artifact_id, target
            )
    assert original.read_bytes() == b"protected original"


def test_sdk_service_failure_is_sanitized(environment):
    from botocore.stub import Stubber

    h = environment
    with Stubber(h.client) as stub:
        stub.add_client_error(
            "get_item",
            service_error_code="AccessDeniedException",
            service_message="synthetic private diagnostic",
            expected_params={
                "TableName": "publication",
                "Key": marshal(h.store.manifest_key("case-1", h.run.run_id)),
                "ConsistentRead": True,
            },
        )
        with pytest.raises(PublicationError, match=r"^manifest_store_unavailable$"):
            h.store.read("case-1", h.run.run_id)
        stub.assert_no_pending_responses()


def test_clock_is_resampled_after_authority_io(environment, monkeypatch):
    h = environment
    real = h.store._get

    def delayed(table, key):
        result = real(table, key)
        if key["pk"].startswith("RUN#"):
            h.clock.now = 200
        return result

    monkeypatch.setattr(h.store, "_get", delayed)
    with pytest.raises(PublicationError, match="stale_publication"):
        h.publish()
    assert h.store.read("case-1", h.run.run_id) is None


def test_object_port_itself_rejects_changed_content_for_existing_key(environment):
    h = environment
    artifact = h.candidate.artifacts[0]
    with pytest.raises(PublicationError, match="artifact_mismatch"):
        h.publisher.objects.create(artifact.key, b"changed content")
    assert h.publisher.objects.read(artifact) == h.data
