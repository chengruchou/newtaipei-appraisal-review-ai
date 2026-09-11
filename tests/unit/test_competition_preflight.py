"""Negative security configurations and exact profile/routing bindings, offline only."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from appraisal_review.application.competition_preflight import (
    check_model_destinations,
    check_profile,
    check_template,
    sagemaker_quota,
)
from appraisal_review.domain.competition_profile import ENTRYPOINTS, CompetitionProfile

ROOT = Path(__file__).resolve().parents[2]
ROLE = "arn:aws:iam::123456789012:role/competition"
MODEL = "arn:aws:bedrock:us-east-1::foundation-model/example.v1"


def ready_profile(**changes: Any) -> CompetitionProfile:
    """Synthetic local configuration, no real account or organizer approval."""
    data = json.loads((ROOT / "config/competition-profile.pending.json").read_bytes())
    data.update(
        {
            "account_id": "123456789012",
            "role_arn": ROLE,
            "role_permissions_evidence_sha256": "e" * 64,
            "iam_actions": [
                "bedrock:InvokeModel",
                "bedrock:CountTokens",
                "bedrock:GetFoundationModel",
                "bedrock:GetInferenceProfile",
                "bedrock-agentcore:InvokeAgentRuntime",
            ],
            "models": [
                {
                    "model_id": "example.v1",
                    "kind": "foundation",
                    "purpose": "extractor",
                    "context_window_tokens": 100,
                    "destinations": [{"arn": MODEL}],
                    "destination_snapshot_sha256": "a" * 64,
                }
            ],
            "budget": {
                "scope": "team-wide",
                "window_seconds": 86400,
                "max_calls": 100,
                "max_input_tokens": 10000,
                "max_output_tokens": 1000,
                "max_cost_usd": "0.1",
                "pricing_evidence_sha256": "b" * 64,
                "window_id": "synthetic-window",
                "window_start": datetime.now(UTC)
                .replace(hour=0, minute=0, second=0, microsecond=0)
                .isoformat(),
                "operation_reservations": [
                    {
                        "operation": "Converse",
                        "model_id": "example.v1",
                        "max_request_bytes": 20000,
                        "input_tokens": 100,
                        "output_tokens": 10,
                        "cost_usd": "0.01",
                        "input_price_per_million_usd": "1",
                        "output_price_per_million_usd": "1",
                        "maximum_request_fee_usd": "0",
                        "bound_evidence_sha256": "f" * 64,
                        "destination_snapshot_sha256": "a" * 64,
                    }
                ],
            },
            "throttle": {
                "scope": "team-wide",
                "interval_seconds": 1.1,
                "central_store_arn": "arn:aws:dynamodb:us-east-1:123456789012:table/shared",
                "connected_entrypoints": sorted(ENTRYPOINTS),
                "scope_approval_reference": "synthetic-test-only",
                "integration_evidence_sha256": "c" * 64,
            },
            "invocation_principal_arns": [ROLE],
            "invocation_auth_evidence_sha256": "d" * 64,
            "data_policy_digest": "e" * 64,
            "synthetic_financial_organizer_reference": "synthetic-test-only",
            "resources": [
                {
                    "name": "runtime",
                    "kind": "runtime",
                    "mode": "reuse",
                    "arn": "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/example",
                    "owner": "test",
                    "stop_method": "disable_invocation_and_stop_sessions",
                    "stop_verification_reference": "synthetic-stop-only",
                }
            ],
        }
    )
    return CompetitionProfile.model_validate({**data, **changes})


def codes(profile: CompetitionProfile) -> set[str]:
    return {
        f.code
        for f in check_profile(
            profile,
            trusted_profile_digest=profile.digest,
            trusted_data_policy_digest="e" * 64,
            observed_account_id="123456789012",
            observed_role_arn=ROLE,
        )
    }


def test_offline_positive_is_bound_to_external_pin_and_identity() -> None:
    p = ready_profile()
    assert codes(p) == set()
    assert {f.code for f in check_profile(p)} >= {"profile_not_approved", "identity_not_verified"}
    assert "identity_not_verified" in {
        f.code
        for f in check_profile(
            p,
            trusted_profile_digest=p.digest,
            observed_account_id="999999999999",
            observed_role_arn=ROLE,
        )
    }


@pytest.mark.parametrize(
    "field,expected",
    [
        ("budget", "budget_not_approved"),
        ("sources", "source_coverage_incomplete"),
        ("data_policy_digest", "data_policy_not_bound"),
        ("role_permissions_evidence_sha256", "role_permissions_unverified"),
        ("synthetic_financial_organizer_reference", "synthetic_financial_permission_unknown"),
        ("invocation_auth_evidence_sha256", "invocation_auth_unverified"),
    ],
)
def test_external_approval_cannot_fill_missing_evidence(field: str, expected: str) -> None:
    assert expected in codes(ready_profile(**{field: None}))


def test_model_destinations_require_full_set_and_exact_foundation_identity() -> None:
    p = ready_profile()
    assert check_model_destinations(p, "example.v1", [MODEL])
    extra = MODEL.replace("us-east-1", "us-west-2")
    for arns in ([], [MODEL, MODEL], [MODEL, extra], [extra]):
        assert not check_model_destinations(p, "example.v1", arns)
    assert not check_model_destinations(p, "unknown", [MODEL])
    data = p.model_dump(mode="json")
    data["models"][0]["destinations"] = [{"arn": extra}]
    assert codes(CompetitionProfile.model_validate(data)) >= {
        "foundation_destination_mismatch",
        "cross_region_routing_disabled",
    }


def test_global_and_cross_region_need_separate_authorization() -> None:
    data = ready_profile().model_dump(mode="json")
    data["models"][0].update({"model_id": "global.example", "kind": "system_profile"})
    assert "global_routing_disabled" in codes(CompetitionProfile.model_validate(data))
    data.update({"allow_global": True, "allow_cross_region": True})
    assert "routing_not_approved" in codes(CompetitionProfile.model_validate(data))


def test_local_limiter_or_unconnected_tools_cannot_claim_team_coverage() -> None:
    data = ready_profile().model_dump(mode="json")
    data["throttle"].update(
        {
            "central_store_arn": None,
            "connected_entrypoints": ["extractor"],
            "includes_control_plane": False,
            "includes_count_tokens": False,
        }
    )
    assert codes(CompetitionProfile.model_validate(data)) >= {
        "team_entrypoints_not_covered",
        "conservative_api_scope_required",
        "shared_dispatch_store_missing",
    }


def test_service_ceiling_is_not_identity_permission_or_api_name() -> None:
    p = ready_profile()
    data = p.model_dump(mode="json")
    data["iam_actions"].remove("bedrock:InvokeModel")
    data["iam_actions"].append("bedrock:Converse")
    assert codes(CompetitionProfile.model_validate(data)) >= {
        "iam_action_not_listed",
        "required_iam_actions_missing",
    }


def test_reuse_requires_exact_arn_stop_and_no_duplicate_inventory() -> None:
    data = ready_profile().model_dump(mode="json")
    resource = data["resources"][0]
    resource.update({"arn": None, "stop_method": "no_compute", "stop_verification_reference": None})
    assert codes(CompetitionProfile.model_validate(data)) >= {
        "reuse_arn_missing",
        "stop_method_invalid",
        "stop_verification_missing",
    }
    data = ready_profile().model_dump(mode="json")
    data["resources"].append({**data["resources"][0], "name": "duplicate"})
    assert "duplicate_resource_arn" in codes(CompetitionProfile.model_validate(data))


def bucket_template() -> dict[str, Any]:
    return {
        "Resources": {
            "Evidence": {
                "Type": "AWS::S3::Bucket",
                "DeletionPolicy": "Retain",
                "UpdateReplacePolicy": "Retain",
                "Properties": {
                    "VersioningConfiguration": {"Status": "Enabled"},
                    "PublicAccessBlockConfiguration": {
                        k: True
                        for k in (
                            "BlockPublicAcls",
                            "IgnorePublicAcls",
                            "BlockPublicPolicy",
                            "RestrictPublicBuckets",
                        )
                    },
                    "BucketEncryption": {
                        "ServerSideEncryptionConfiguration": [
                            {"ServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}
                        ]
                    },
                },
            },
            "TLS": {
                "Type": "AWS::S3::BucketPolicy",
                "Properties": {
                    "Bucket": {"Ref": "Evidence"},
                    "PolicyDocument": {
                        "Statement": [
                            {
                                "Effect": "Deny",
                                "Principal": "*",
                                "Action": "s3:*",
                                "Resource": [
                                    {"Fn::GetAtt": ["Evidence", "Arn"]},
                                    {"Fn::Sub": "${Evidence.Arn}/*"},
                                ],
                                "Condition": {"Bool": {"aws:SecureTransport": "false"}},
                            }
                        ]
                    },
                },
            },
        }
    }


def template_codes(template: dict[str, Any]) -> set[str]:
    return {f.code for f in check_template(template)}


def test_private_encrypted_versioned_bucket_with_tls_and_retention_passes() -> None:
    assert check_template(bucket_template()) == ()


@pytest.mark.parametrize(
    "change,expected",
    [
        ({"PublicAccessBlockConfiguration": {}}, "s3_public_access_not_blocked"),
        ({"AccessControl": "PublicRead"}, "s3_acl_not_private"),
        ({"VersioningConfiguration": {"Status": "Suspended"}}, "s3_versioning_required"),
        ({"BucketEncryption": {}}, "s3_encryption_required"),
        (
            {
                "LifecycleConfiguration": {
                    "Rules": [
                        {
                            "Status": "Enabled",
                            "NoncurrentVersionExpiration": {"NoncurrentDays": 365},
                        }
                    ]
                }
            },
            "pinned_versions_may_expire",
        ),
    ],
)
def test_s3_regressions_fail(change: dict[str, Any], expected: str) -> None:
    template = bucket_template()
    template["Resources"]["Evidence"]["Properties"].update(change)
    assert expected in template_codes(template)


def test_tls_deny_on_another_bucket_or_restricted_principal_is_insufficient() -> None:
    t = bucket_template()
    statement = t["Resources"]["TLS"]["Properties"]["PolicyDocument"]["Statement"][0]
    statement["Resource"] = "arn:aws:s3:::another/*"
    assert "s3_tls_deny_required" in template_codes(t)
    t = bucket_template()
    t["Resources"]["TLS"]["Properties"]["PolicyDocument"]["Statement"][0]["Principal"] = {
        "AWS": ROLE
    }
    assert "s3_tls_deny_required" in template_codes(t)


@pytest.mark.parametrize("cidr", ["0.0.0.0/0", "::/0", "0:0:0:0:0:0:0:0/0", {"Ref": "Cidr"}])
def test_open_or_unresolved_ingress_is_blocked(cidr: object) -> None:
    t = {
        "Resources": {
            "Ingress": {
                "Type": "AWS::EC2::SecurityGroupIngress",
                "Properties": {
                    "IpProtocol": "-1",
                    "CidrIpv6" if isinstance(cidr, str) and ":" in cidr else "CidrIp": cidr,
                },
            }
        }
    }
    assert "security_group_public_ingress" in template_codes(t)


def test_conditional_public_rds_and_public_emr_remain_findings() -> None:
    t = {
        "Resources": {
            "Database": {
                "Type": "AWS::RDS::DBInstance",
                "Properties": {"PubliclyAccessible": {"Fn::If": ["Public", True, False]}},
            },
            "Cluster": {"Type": "AWS::EMR::Cluster", "Properties": {"VisibleToAllUsers": False}},
        }
    }
    assert template_codes(t) >= {
        "rds_private_access_unverified",
        "emr_private_subnet_evidence_required",
    }


@pytest.mark.parametrize("instance", ["g5.xlarge", "p4d.24xlarge", "vt1.3xlarge"])
def test_ec2_gpu_zero_quota_is_distinct_from_sagemaker_endpoints(instance: str) -> None:
    t = {
        "Resources": {
            "Compute": {"Type": "AWS::EC2::Instance", "Properties": {"InstanceType": instance}}
        }
    }
    assert "ec2_gpu_zero_quota" in template_codes(t)


def test_agentcore_public_network_uses_authentication_gate_not_blanket_rejection() -> None:
    t = {
        "Resources": {
            "Runtime": {
                "Type": "AWS::BedrockAgentCore::Runtime",
                "Properties": {"NetworkConfiguration": {"NetworkMode": "PUBLIC"}, "RoleArn": ROLE},
            }
        }
    }
    assert check_template(t) == ()
    t["Resources"]["Runtime"]["Properties"]["AuthorizerConfiguration"] = {}
    assert "runtime_authorizer_unverified" in template_codes(t)
    p = ready_profile(invocation_auth_evidence_sha256=None)
    assert "invocation_auth_unverified" in codes(p)


def test_public_grant_and_wildcard_model_permissions_fail() -> None:
    t = {
        "Resources": {
            "Role": {
                "Type": "AWS::IAM::Role",
                "Properties": {
                    "Policies": [
                        {
                            "PolicyDocument": {
                                "Statement": [
                                    {
                                        "Effect": "Allow",
                                        "Action": "bedrock:InvokeModel",
                                        "Resource": "*",
                                    },
                                    {
                                        "Effect": "Allow",
                                        "Action": "bedrock:*",
                                        "Principal": {"AWS": "*"},
                                        "Resource": "*",
                                    },
                                ]
                            }
                        }
                    ]
                },
            }
        }
    }
    assert template_codes(t) >= {
        "model_permission_unbounded",
        "public_principal_grant",
        "iam_action_not_listed",
    }


def test_cli_missing_source_and_approval_returns_bounded_json(tmp_path: Path) -> None:
    template = tmp_path / "template.json"
    template.write_text(json.dumps(bucket_template()))
    environment = dict(os.environ)
    environment.pop("REVIEW_COMPETITION_PROFILE_SHA256", None)
    run = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/check_competition.py"),
            "--profile",
            str(ROOT / "config/competition-profile.pending.json"),
            "--template",
            str(template),
        ],
        capture_output=True,
        text=True,
        env=environment,
    )
    assert run.returncode == 1, run.stderr
    report = json.loads(run.stdout)
    assert report["status"] == "blocked"
    assert report["live_acceptance"] == "not_performed"
    assert {f["code"] for f in report["findings"]} >= {
        "source_file_missing",
        "profile_not_approved",
    }
    assert str(tmp_path) not in run.stdout


def test_cli_rejects_invalid_profile_without_echoing_inputs(tmp_path: Path) -> None:
    profile = tmp_path / "profile.json"
    profile.write_text('{"profile_id":"private sensitive value"}')
    run = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/check_competition.py"),
            "--profile",
            str(profile),
            "--template",
            str(profile),
        ],
        capture_output=True,
        text=True,
    )
    assert run.returncode == 1
    assert "private sensitive value" not in run.stdout + run.stderr
    assert json.loads(run.stdout)["findings"][0]["code"] == "invalid_input"


def test_runtime_template_has_no_competition_security_findings() -> None:
    spec = importlib.util.spec_from_file_location(
        "competition_cli", ROOT / "scripts/check_competition.py"
    )
    assert spec and spec.loader
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    template = yaml.load(
        (ROOT / "infra/runtime/runtime-stack.yaml").read_text(), Loader=cli.CfnLoader
    )
    assert "model_permission_unbounded" in template_codes(template)
    assert (
        check_template(
            template,
            parameters={"ModelResourceArns": [MODEL]},
            approved_model_arns=frozenset({MODEL}),
        )
        == ()
    )
    # A later version under the same key must not expire a manifest-pinned version.
    changed = copy.deepcopy(template)
    changed["Resources"]["Results"]["Properties"]["LifecycleConfiguration"] = {
        "Rules": [{"Status": "Enabled", "NoncurrentVersionExpirationInDays": 30}]
    }
    assert "pinned_versions_may_expire" in template_codes(changed)


def test_sagemaker_source_quotas_keep_workload_dimensions_and_zero_distinct() -> None:
    assert sagemaker_quota("endpoint/ml.g5.xlarge", "us-east-1") == 2
    assert sagemaker_quota("training-job/ml.g5.xlarge", "us-east-1") == 0
    assert sagemaker_quota("endpoint/ml.g5.2xlarge", "us-west-2") == 2
    assert sagemaker_quota("training-job/ml.g5.2xlarge", "us-west-2") == 0
    assert sagemaker_quota("unknown", "us-east-1") is None


def test_sagemaker_endpoint_quota_aggregates_variants_without_new_training_path() -> None:
    t = {
        "Resources": {
            "Endpoint": {
                "Type": "AWS::SageMaker::EndpointConfig",
                "Properties": {
                    "ProductionVariants": [
                        {"InstanceType": "ml.g5.xlarge", "InitialInstanceCount": 2}
                    ],
                },
            }
        }
    }
    assert check_template(t, primary_region="us-east-1") == ()
    t["Resources"]["Endpoint"]["Properties"]["ProductionVariants"].append(
        {"InstanceType": "ml.g5.xlarge", "InitialInstanceCount": 1}
    )
    assert "sagemaker_endpoint_quota_exceeded" in {
        f.code for f in check_template(t, primary_region="us-east-1")
    }


def test_arbitrary_data_policy_digest_cannot_bind_server_authority() -> None:
    p = ready_profile()
    assert "data_policy_not_bound" in {
        f.code
        for f in check_profile(
            p,
            trusted_profile_digest=p.digest,
            trusted_data_policy_digest="0" * 64,
            observed_account_id="123456789012",
            observed_role_arn=ROLE,
        )
    }


@pytest.mark.parametrize(
    "target",
    [
        {"Fn::Sub": "arn:aws:bedrock:${AWS::Region}::foundation-model/*"},
        {"Fn::If": ["Wide", "*", MODEL]},
        {"Ref": "UnresolvedModels"},
    ],
)
def test_dynamic_model_permission_cannot_hide_unapproved_scope(target: object) -> None:
    t = {
        "Resources": {
            "Role": {
                "Type": "AWS::IAM::Role",
                "Properties": {
                    "Policies": [
                        {
                            "PolicyDocument": {
                                "Statement": [
                                    {
                                        "Effect": "Allow",
                                        "Action": "bedrock:InvokeModel",
                                        "Resource": target,
                                    }
                                ]
                            }
                        }
                    ]
                },
            }
        }
    }
    assert "model_permission_unbounded" in template_codes(t)


@pytest.mark.parametrize("conditional_resource", [True, False])
def test_conditional_tls_deny_is_not_guaranteed(conditional_resource: bool) -> None:
    t = bucket_template()
    if conditional_resource:
        t["Resources"]["TLS"]["Condition"] = "EnableTLS"
    else:
        document = t["Resources"]["TLS"]["Properties"]["PolicyDocument"]
        document["Statement"] = {"Fn::If": ["EnableTLS", document["Statement"], []]}
    t["Conditions"] = {"EnableTLS": {"Fn::Equals": ["off", "on"]}}
    assert "s3_tls_deny_required" in template_codes(t)


def test_allow_notprincipal_is_a_public_complement_grant() -> None:
    t = bucket_template()
    t["Resources"]["TLS"]["Properties"]["PolicyDocument"]["Statement"].append(
        {
            "Effect": "Allow",
            "NotPrincipal": {"AWS": ROLE},
            "Action": "s3:GetObject",
            "Resource": {"Fn::Sub": "${Evidence.Arn}/*"},
        }
    )
    assert "public_principal_grant" in template_codes(t)


def test_queue_arn_cannot_impersonate_agentcore_inventory() -> None:
    data = ready_profile().model_dump(mode="json")
    data["resources"][0]["arn"] = "arn:aws:sqs:us-east-1:123456789012:not-a-runtime"
    assert "resource_kind_arn_mismatch" in codes(CompetitionProfile.model_validate(data))


def test_exact_parameter_is_still_checked_against_approved_model_scope() -> None:
    t = {
        "Resources": {
            "Role": {
                "Type": "AWS::IAM::Role",
                "Properties": {
                    "Policies": [
                        {
                            "PolicyDocument": {
                                "Statement": [
                                    {
                                        "Effect": "Allow",
                                        "Action": "bedrock:InvokeModel",
                                        "Resource": {"Ref": "Models"},
                                    }
                                ]
                            }
                        }
                    ]
                },
            }
        }
    }
    assert (
        check_template(t, parameters={"Models": MODEL}, approved_model_arns=frozenset({MODEL}))
        == ()
    )
    for value in ["*", MODEL + ",*", {"Fn::Sub": MODEL}]:
        assert "model_permission_unbounded" in {
            f.code
            for f in check_template(
                t, parameters={"Models": value}, approved_model_arns=frozenset({MODEL})
            )
        }
    assert "model_permission_not_approved" in {
        f.code
        for f in check_template(
            t,
            parameters={"Models": MODEL.replace("example.v1", "other.v1")},
            approved_model_arns=frozenset({MODEL}),
        )
    }
