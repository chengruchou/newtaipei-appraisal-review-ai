"""Synthesized lifecycle rules must retain manifest-pinned object versions."""

from aws_cdk import App
from aws_cdk.assertions import Template

from infra.cdk.stacks.appraisal_review_stack import AppraisalReviewStack


def test_result_lifecycle_preserves_referenced_versions(tmp_path):
    app = App(outdir=str(tmp_path / "cdk.out"))
    stack = AppraisalReviewStack(app, "RetentionRegression")
    template = Template.from_stack(stack).to_json()
    buckets = {
        key: value
        for key, value in template["Resources"].items()
        if value["Type"] == "AWS::S3::Bucket"
    }
    result = next(value for key, value in buckets.items() if key.startswith("ReviewResults"))
    assert result["DeletionPolicy"] == "Retain"
    assert result["UpdateReplacePolicy"] == "Retain"
    properties = result["Properties"]
    assert properties["VersioningConfiguration"] == {"Status": "Enabled"}
    assert all(properties["PublicAccessBlockConfiguration"].values())
    assert (
        properties["BucketEncryption"]["ServerSideEncryptionConfiguration"][0][
            "ServerSideEncryptionByDefault"
        ]["SSEAlgorithm"]
        == "AES256"
    )
    rules = properties["LifecycleConfiguration"]["Rules"]
    assert any(
        rule.get("AbortIncompleteMultipartUpload") == {"DaysAfterInitiation": 7} for rule in rules
    )
    for rule in rules:
        assert "NoncurrentVersionExpiration" not in rule
        assert "ExpirationInDays" not in rule
        assert "ExpirationDate" not in rule

    policies = [
        value["Properties"]["PolicyDocument"]
        for key, value in template["Resources"].items()
        if value["Type"] == "AWS::S3::BucketPolicy" and key.startswith("ReviewResults")
    ]
    assert len(policies) == 1
    assert any(
        statement["Effect"] == "Deny"
        and statement.get("Condition") == {"Bool": {"aws:SecureTransport": "false"}}
        for statement in policies[0]["Statement"]
    )
