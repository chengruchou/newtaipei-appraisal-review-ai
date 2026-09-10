"""Offline SDK shape, retention policy and exact cloud-test cleanup checks."""

import base64
import io
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import boto3
import pytest
from botocore.stub import Stubber

from appraisal_review.adapters.aws.document_storage import (
    S3DocumentConfiguration,
    S3DocumentStorage,
)
from appraisal_review.adapters.local.document_storage import SQLiteDocumentStorage
from appraisal_review.domain.document_transfer import ObjectKey, ObjectLabels, digest_bytes
from cloud_tests.document_smoke import JournaledClient, exercise_storage


def test_sdk_requests_are_version_pinned_owner_pinned_and_private():
    client = boto3.client(
        "s3",
        region_name="us-east-1",
        aws_access_key_id="synthetic",
        aws_secret_access_key="synthetic",
    )
    config = S3DocumentConfiguration("synthetic-documents", "111122223333", uuid4())
    storage = S3DocumentStorage(client, config)
    common = {"Bucket": config.bucket, "ExpectedBucketOwner": config.expected_owner}
    key = ObjectKey(kind="content", scope=uuid4(), identity=uuid4())
    content = b"%PDF-1.7\nsynthetic\n%%EOF\n"
    labels = ObjectLabels(
        case_id=uuid4(),
        uploader=uuid4(),
        created_at=datetime.now(UTC),
        content_hash=digest_bytes(content),
        byte_size=len(content),
        content_type="application/pdf",
        purpose="forms",
    )
    checksum = base64.b64encode(bytes.fromhex(digest_bytes(content))).decode()
    full_key = config.prefix + key.relative_key()
    with Stubber(client) as stub:
        stub.add_response("get_bucket_versioning", {"Status": "Enabled"}, common)
        stub.add_response(
            "get_bucket_encryption",
            {
                "ServerSideEncryptionConfiguration": {
                    "Rules": [
                        {
                            "ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"},
                            "BucketKeyEnabled": False,
                        }
                    ]
                }
            },
            common,
        )
        stub.add_response(
            "get_public_access_block",
            {
                "PublicAccessBlockConfiguration": {
                    k: True
                    for k in (
                        "BlockPublicAcls",
                        "IgnorePublicAcls",
                        "BlockPublicPolicy",
                        "RestrictPublicBuckets",
                    )
                }
            },
            common,
        )
        stub.add_response(
            "get_bucket_ownership_controls",
            {"OwnershipControls": {"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]}},
            common,
        )
        stub.add_response("get_bucket_policy_status", {"PolicyStatus": {"IsPublic": False}}, common)
        stub.add_response(
            "put_object",
            {"VersionId": "specific-object-version"},
            {
                **common,
                "Key": full_key,
                "Body": content,
                "IfNoneMatch": "*",
                "ServerSideEncryption": "AES256",
                "ContentType": "application/pdf",
                "ChecksumSHA256": checksum,
                "Metadata": {
                    **labels.headers(),
                    "object-id": str(key.identity),
                    "document-id": str(key.scope),
                },
                "Tagging": f"classification=sanitized&namespace={config.namespace}",
            },
        )
        stub.add_response(
            "get_object",
            {
                "VersionId": "specific-object-version",
                "Body": io.BytesIO(content),
                "ContentLength": len(content),
                "ChecksumSHA256": checksum,
                "ServerSideEncryption": "AES256",
            },
            {
                **common,
                "Key": full_key,
                "VersionId": "specific-object-version",
                "ChecksumMode": "ENABLED",
            },
        )
        version = storage.create(key, content, labels)
        assert storage.read(key, version=version, limit=1024).content == content
        stub.assert_no_pending_responses()


def test_shared_live_probe_contract_against_local_storage(tmp_path):
    exercise_storage(SQLiteDocumentStorage(tmp_path / "storage.sqlite"))


def test_template_retains_sources_and_scopes_runtime_permissions():
    template = json.loads(
        (Path(__file__).resolve().parents[1] / "infra/documents/stack.json").read_text()
    )
    bucket = template["Resources"]["Documents"]
    assert bucket["DeletionPolicy"] == bucket["UpdateReplacePolicy"] == "Retain"
    props = bucket["Properties"]
    assert "LifecycleConfiguration" not in props
    assert props["VersioningConfiguration"] == {"Status": "Enabled"}
    assert all(props["PublicAccessBlockConfiguration"].values())
    statements = template["Resources"]["DocumentsPolicy"]["Properties"]["PolicyDocument"][
        "Statement"
    ]
    by_sid = {s["Sid"]: s for s in statements}
    grant = by_sid["RuntimeNamespaceOnly"]
    assert grant["Principal"] == {"AWS": {"Ref": "RuntimeRoleArn"}}
    assert grant["Resource"] == {"Fn::Sub": "${Documents.Arn}/sanitized/${Namespace}/*"}
    assert set(grant["Action"]) == {
        "s3:GetObject",
        "s3:GetObjectVersion",
        "s3:PutObject",
        "s3:PutObjectTagging",
    }
    assert by_sid["RequireConditionalCreation"]["Condition"] == {
        "Null": {"s3:if-none-match": "true"}
    }
    assert "s3:DeleteObjectVersion" in by_sid["RuntimeCannotDeleteEvidenceOrRemoveTags"]["Action"]
    assert "s3:PutObjectTagging" not in by_sid["RuntimeCannotDeleteEvidenceOrRemoveTags"]["Action"]
    assert by_sid["RejectAdditionalTagKeys"]["Condition"] == {
        "ForAnyValue:StringNotEquals": {"s3:RequestObjectTagKeys": ["classification", "namespace"]}
    }


def test_cleanup_only_exact_created_versions_with_matching_tags(tmp_path):
    config = S3DocumentConfiguration("synthetic-documents", "111122223333", uuid4())
    calls = []

    class Client:
        def put_object(self, **kwargs):
            return {"VersionId": "owned-version"}

        def get_object_tagging(self, **kwargs):
            calls.append(("tags", kwargs))
            return {
                "TagSet": [
                    {"Key": "classification", "Value": "sanitized"},
                    {"Key": "namespace", "Value": str(config.namespace)},
                ]
            }

        def delete_object(self, **kwargs):
            calls.append(("delete", kwargs))

    client = Client()
    wrapped = JournaledClient(client, tmp_path / "journal.jsonl")
    wrapped.put_object(
        Bucket=config.bucket,
        Key=config.prefix + "content/owned/object",
        ExpectedBucketOwner=config.expected_owner,
    )
    assert wrapped.cleanup(client, config) == 1
    assert [name for name, _ in calls] == ["tags", "delete"]
    assert all(args["VersionId"] == "owned-version" for _, args in calls)
    calls.clear()
    wrapped.created[0]["key"] = "another-namespace/teammate"
    with pytest.raises(Exception, match="document_unauthorized"):
        wrapped.cleanup(client, config)
    assert calls == []
