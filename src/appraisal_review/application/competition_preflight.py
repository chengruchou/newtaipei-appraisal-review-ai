"""Offline competition checks; no SDK calls, resource mutations or data admission."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from importlib.resources import files
from typing import Any

from appraisal_review.domain.competition_profile import (
    ENTRYPOINTS,
    CompetitionProfile,
    exact_account_resource,
    foundation_arn_matches,
    iam_actions_for_api,
    resource_kind_matches,
)

CATALOG_SHA256 = "ecc58147c19bbae8e0ec1107f30914376936e4f4c496d0ea45ff0d5796d5a141"
QUOTAS_SHA256 = "9ef38f979b28d4d8dcb3a540b8dc0af31be4f50ab9ab855270562636046faced"


@dataclass(frozen=True)
class CompetitionFinding:
    code: str
    location: str
    source: str


def service_catalog() -> dict[str, Any]:
    raw = files("appraisal_review").joinpath("data/competition/services-20260722.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != CATALOG_SHA256:
        raise ValueError("Competition IAM catalog digest mismatch")
    result: dict[str, Any] = json.loads(raw)
    services: dict[str, Any] = result["services"]
    return services


def sagemaker_quota(resource_key: str, region: str) -> float | None:
    """Compare the exact workload key; endpoint capacity never authorizes training.

    All matching limits apply conservatively. An unknown key/region is not a zero
    or an unlimited quota and requires organizer evidence. No quota API is called.
    """
    raw = files("appraisal_review").joinpath("data/competition/quotas-20260722.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != QUOTAS_SHA256:
        raise ValueError("Competition quota catalog digest mismatch")
    data = json.loads(raw)
    if data.get("source_sha256") != (
        "378eb61dba941647748f03b16ea4fb5f37ceba5610dd0f013dd759b0f5e0ccae"
    ):
        raise ValueError("Competition quota source mismatch")
    limits = [
        float(row["limit"])
        for row in data["quotas"]
        if row["resource_key"] == resource_key
        and ("*" in row["regions"] or region in row["regions"])
    ]
    return min(limits) if limits else None


def check_profile(
    profile: CompetitionProfile,
    *,
    trusted_profile_digest: str | None = None,
    trusted_data_policy_digest: str | None = None,
    observed_account_id: str | None = None,
    observed_role_arn: str | None = None,
) -> tuple[CompetitionFinding, ...]:
    """A trusted server/operator pin is external to the serialized profile.

    Identity arguments are from a trusted identity adapter or an explicitly
    labelled offline fixture, never request JSON. Passing offline checks neither
    performs nor proves live IAM authorization or model/data acceptance.
    """
    profile = CompetitionProfile.model_validate(profile)
    issues: list[CompetitionFinding] = []

    def fail(code: str, location: str, source: str = "PDF p1; operator approval") -> None:
        issues.append(CompetitionFinding(code, location, source))

    if profile.sources is None or not profile.sources.complete():
        fail("source_coverage_incomplete", "sources", "PDF pp1-2; all workbook sheets")
    if not trusted_profile_digest or trusted_profile_digest != profile.digest:
        fail("profile_not_approved", "profile_id")
    if profile.account_id is None or profile.role_arn is None:
        fail("identity_not_approved", "account_id")
    elif (observed_account_id, observed_role_arn) != (profile.account_id, profile.role_arn):
        fail("identity_not_verified", "role_arn")
    if profile.role_permissions_evidence_sha256 is None:
        fail("role_permissions_unverified", "role_permissions_evidence_sha256")
    if profile.budget is None:
        fail("budget_not_approved", "budget")
    elif (
        not profile.budget.window_id
        or profile.budget.window_start is None
        or not (profile.budget.operation_reservations)
    ):
        fail("budget_reservation_bounds_missing", "budget")
    if profile.budget is not None:
        for reservation in profile.budget.operation_reservations:
            uses = tuple(m for m in profile.models if m.model_id == reservation.model_id)
            if not reservation.covers_worst_case(uses):
                fail("budget_conservative_basis_missing", "budget.operation_reservations")
    if (
        profile.data_policy_digest is None
        or profile.data_policy_digest != trusted_data_policy_digest
    ):
        fail("data_policy_not_bound", "data_policy_digest", "PDF p1 general rule2")
    if profile.synthetic_financial_organizer_reference is None:
        fail("synthetic_financial_permission_unknown", "synthetic_financial_organizer_reference")
    if (
        profile.allow_cross_region or profile.allow_global
    ) and not profile.routing_approval_reference:
        fail("routing_not_approved", "routing_approval_reference", "PDF p1 general rule6")
    if profile.allow_global and not profile.allow_cross_region:
        fail("global_requires_cross_region_approval", "allow_global")
    catalog = service_catalog()
    for action in profile.iam_actions:
        ns, name = action.split(":")
        if ns not in catalog or name not in catalog[ns]["actions"]:
            fail("iam_action_not_listed", "iam_actions", "Services List A:B")
    operations = {"Converse", "CountTokens", "GetFoundationModel", "InvokeAgentRuntime"}
    if any(m.kind != "foundation" for m in profile.models):
        operations.add("GetInferenceProfile")
    required = {action for operation in operations for action in iam_actions_for_api(operation)}
    if not required <= set(profile.iam_actions):
        fail("required_iam_actions_missing", "iam_actions", "Services List rows46-47")
    if not profile.models:
        fail("models_not_approved", "models", "PDF p1 Bedrock rules2-3")
    for index, model in enumerate(profile.models):
        location = f"models.{index}"
        if not model.destinations or model.destination_snapshot_sha256 is None:
            fail("model_destinations_unverified", location)
        if not foundation_arn_matches(model, profile.primary_region):
            fail("foundation_destination_mismatch", location)
        if model.kind != "foundation" and model.model_id.startswith("arn:"):
            kind = (
                "inference-profile"
                if model.kind == "system_profile"
                else ("application-inference-profile")
            )
            prefix = f"arn:aws:bedrock:{profile.primary_region}:{profile.account_id}:{kind}/"
            if not model.model_id.startswith(prefix):
                fail("inference_profile_identity_mismatch", location)
        if model.model_id.split("/")[-1].startswith("global.") and not profile.allow_global:
            fail("global_routing_disabled", location)
        if any(d.region != profile.primary_region for d in model.destinations) and not (
            profile.allow_cross_region
        ):
            fail("cross_region_routing_disabled", location)
    throttle = profile.throttle
    if not throttle.includes_control_plane or not throttle.includes_count_tokens:
        fail("conservative_api_scope_required", "throttle", "PDF p1 Bedrock rule1")
    if not set(throttle.connected_entrypoints) >= ENTRYPOINTS:
        fail("team_entrypoints_not_covered", "throttle.connected_entrypoints")
    if not throttle.integration_evidence_sha256 or not throttle.scope_approval_reference:
        fail("throttle_scope_not_approved", "throttle")
    if not throttle.central_store_arn:
        fail("shared_dispatch_store_missing", "throttle.central_store_arn")
    elif not re.fullmatch(
        rf"arn:aws:dynamodb:{re.escape(profile.primary_region)}:{profile.account_id}:table/[^/]+",
        throttle.central_store_arn,
    ):
        fail("shared_dispatch_store_outside_scope", "throttle.central_store_arn")
    if not profile.invocation_principal_arns or not profile.invocation_auth_evidence_sha256:
        fail("invocation_auth_unverified", "invocation_auth")
    for principal in profile.invocation_principal_arns:
        if principal.split(":")[4] != profile.account_id:
            fail("invocation_principal_outside_account", "invocation_principal_arns")
    if not profile.resources:
        fail("resource_inventory_missing", "resources", "PDF p1 general rule5")
    expected_stop = {
        "runtime": "disable_invocation_and_stop_sessions",
        "bucket": "retain_evidence",
        "table": "retain_evidence",
        "queue": "retain_evidence",
        "dashboard": "no_compute",
        "worker": "disable_event_source_and_zero_concurrency",
        "schedule": "disable_schedule",
        "model": "deny_exact_model_invocation",
    }
    arns: set[str] = set()
    for index, resource in enumerate(profile.resources):
        location = f"resources.{index}"
        if resource.mode == "reuse" and resource.arn is None:
            fail("reuse_arn_missing", location)
        if resource.arn is not None:
            if not resource_kind_matches(resource.kind, resource.arn):
                fail("resource_kind_arn_mismatch", location)
            if resource.arn in arns:
                fail("duplicate_resource_arn", location)
            arns.add(resource.arn)
            if not exact_account_resource(
                resource.arn, profile.account_id or "", profile.primary_region
            ):
                fail("resource_outside_scope", location)
        if resource.stop_method != expected_stop[resource.kind]:
            fail("stop_method_invalid", location)
        if not resource.stop_verification_reference:
            fail("stop_verification_missing", location)
    if not any(r.kind == "runtime" for r in profile.resources):
        fail("agentcore_runtime_missing", "resources")
    return tuple(issues)


def check_model_destinations(
    profile: CompetitionProfile, model_id: str, discovered_arns: Iterable[str]
) -> bool:
    """Compare the complete discovered routing set, never a matching subset."""
    discovered = tuple(discovered_arns)
    matches = [m for m in profile.models if m.model_id == model_id]
    return (
        bool(matches)
        and bool(discovered)
        and len(set(discovered)) == len(discovered)
        and all(set(discovered) == {d.arn for d in model.destinations} for model in matches)
    )


def _walk(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _variants(value: Any) -> list[Any]:
    if isinstance(value, dict) and "Fn::If" in value:
        condition = value["Fn::If"]
        if isinstance(condition, list) and len(condition) == 3:
            return _variants(condition[1]) + _variants(condition[2])
    return [value]


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def _unbounded_cidr(value: Any) -> bool:
    if not isinstance(value, str):
        return True
    try:
        return ipaddress.ip_network(value, strict=False).prefixlen == 0
    except ValueError:
        return True


def _public_allow(statement: dict[str, Any]) -> bool:
    if statement.get("Effect") != "Allow":
        return False
    if "NotPrincipal" in statement:
        return True
    principal = statement.get("Principal")
    if principal == "*":
        return True
    return isinstance(principal, dict) and any(
        v == "*" for values in principal.values() for v in _list(values)
    )


def _bucket_tls(resources: dict[str, Any], name: str, bucket: dict[str, Any]) -> bool:
    bucket_arns: list[Any] = [{"Fn::GetAtt": [name, "Arn"]}]
    object_arns: list[Any] = [
        {"Fn::Sub": f"${{{name}.Arn}}/*"},
        {"Fn::Join": ["", [{"Fn::GetAtt": [name, "Arn"]}, "/*"]]},
    ]
    bucket_name = bucket.get("BucketName")
    if isinstance(bucket_name, str):
        bucket_arns.append(f"arn:aws:s3:::{bucket_name}")
        object_arns.append(f"arn:aws:s3:::{bucket_name}/*")
    required = {"s3:GetObject", "s3:GetObjectVersion", "s3:PutObject", "s3:DeleteObjectVersion"}
    for resource in resources.values():
        if resource.get("Type") != "AWS::S3::BucketPolicy":
            continue
        if "Condition" in resource:
            continue
        document = resource.get("Properties", {}).get("PolicyDocument", {})
        if not isinstance(document, dict):
            continue
        # Descendant conditional denies cannot establish an unconditional guard.
        for statement in _list(document.get("Statement", [])):
            if not isinstance(statement, dict):
                continue
            if statement.get("Effect") != "Deny" or statement.get("Principal") not in (
                "*",
                {"AWS": "*"},
            ):
                continue
            condition = statement.get("Condition")
            if condition not in (
                {"Bool": {"aws:SecureTransport": "false"}},
                {"Bool": {"aws:SecureTransport": False}},
            ):
                continue
            actions = _list(statement.get("Action"))
            if not (
                "s3:*" in actions
                or "*" in actions
                or required <= set(a for a in actions if isinstance(a, str))
            ):
                continue
            targets = _list(statement.get("Resource"))
            if "*" in targets or (
                any(a in targets for a in bucket_arns) and any(a in targets for a in object_arns)
            ):
                return True
    return False


def check_template(
    template: dict[str, Any],
    *,
    primary_region: str | None = None,
    parameters: dict[str, Any] | None = None,
    approved_model_arns: frozenset[str] | None = None,
) -> tuple[CompetitionFinding, ...]:
    """Conservative static checks of every branch; not a CloudFormation evaluator.

    Unsupported dynamic security settings remain findings. PUBLIC AgentCore
    networking uses an authenticated service endpoint; its live authorization
    evidence is a separate profile gate, not inferred from network mode.
    """
    issues: list[CompetitionFinding] = []

    def fail(code: str, name: str, source: str = "PDF p1 general rules1,3-5") -> None:
        issues.append(CompetitionFinding(code, f"Resources.{name}", source))

    resources = template.get("Resources")
    if not isinstance(resources, dict) or not resources:
        return (CompetitionFinding("template_resources_missing", "Resources", "template schema"),)
    catalog = service_catalog()
    if template.get("Transform"):
        fail("template_transform_requires_expansion", "Transform", "template coverage")
    for name, resource in resources.items():
        if not isinstance(resource, dict) or not isinstance(resource.get("Properties", {}), dict):
            fail("resource_schema_invalid", name)
            continue
        kind = resource.get("Type")
        props = resource.get("Properties", {})
        if kind == "AWS::CloudFormation::Stack":
            fail("nested_template_requires_inspection", name, "template coverage")
        if kind == "AWS::S3::Bucket":
            public = props.get("PublicAccessBlockConfiguration", {})
            if not isinstance(public, dict) or any(
                public.get(k) is not True
                for k in (
                    "BlockPublicAcls",
                    "IgnorePublicAcls",
                    "BlockPublicPolicy",
                    "RestrictPublicBuckets",
                )
            ):
                fail("s3_public_access_not_blocked", name)
            if props.get("AccessControl", "Private") != "Private":
                fail("s3_acl_not_private", name)
            if props.get("VersioningConfiguration", {}).get("Status") != "Enabled":
                fail("s3_versioning_required", name)
            encryption = props.get("BucketEncryption", {}).get("ServerSideEncryptionConfiguration")
            if (
                not isinstance(encryption, list)
                or not encryption
                or any(
                    not isinstance(e, dict)
                    or e.get("ServerSideEncryptionByDefault", {}).get("SSEAlgorithm")
                    not in ("AES256", "aws:kms", "aws:kms:dsse")
                    for e in encryption
                )
            ):
                fail("s3_encryption_required", name)
            if (
                resource.get("DeletionPolicy") != "Retain"
                or resource.get("UpdateReplacePolicy") != "Retain"
            ):
                fail("evidence_retention_required", name)
            for rule in _walk(props.get("LifecycleConfiguration", {})):
                if (
                    any(
                        key in rule
                        for key in (
                            "NoncurrentVersionExpiration",
                            "NoncurrentVersionExpirationInDays",
                            "ExpirationInDays",
                            "ExpirationDate",
                        )
                    )
                    and rule.get("Status") != "Disabled"
                ):
                    fail("pinned_versions_may_expire", name)
            if not _bucket_tls(resources, name, props):
                fail("s3_tls_deny_required", name)
        if kind in {"AWS::EC2::SecurityGroup", "AWS::EC2::SecurityGroupIngress"}:
            rules = (
                props.get("SecurityGroupIngress", []) if kind.endswith("SecurityGroup") else props
            )
            for rule in _walk(rules):
                for key in ("CidrIp", "CidrIpv6"):
                    if key in rule and any(_unbounded_cidr(v) for v in _variants(rule[key])):
                        fail("security_group_public_ingress", name)
        if kind in {"AWS::RDS::DBInstance", "AWS::RDS::DBCluster"} and any(
            v is not False for v in _variants(props.get("PubliclyAccessible"))
        ):
            fail("rds_private_access_unverified", name)
        if kind == "AWS::EMR::Cluster":
            # VisibleToAllUsers controls IAM user visibility, not network isolation.
            fail("emr_private_subnet_evidence_required", name)
        if kind in {"AWS::EC2::Instance", "AWS::EC2::LaunchTemplate"}:
            settings = props.get("LaunchTemplateData", props)
            instance = settings.get("InstanceType")
            if not isinstance(instance, str):
                fail("ec2_instance_quota_unverified", name, "EC2 rows2-11")
            elif re.match(r"^(?:g\d|vt\d|p\d)", instance):
                fail("ec2_gpu_zero_quota", name, "EC2 rows4,8")
        if kind == "AWS::SageMaker::EndpointConfig":
            counts: dict[str, int] = {}
            variants = props.get("ProductionVariants", []) + props.get(
                "ShadowProductionVariants", []
            )
            if not variants:
                fail("sagemaker_quota_unverified", name, "SageMaker AI A:C")
            for variant in variants:
                instance = variant.get("InstanceType") if isinstance(variant, dict) else None
                count = variant.get("InitialInstanceCount") if isinstance(variant, dict) else None
                if not isinstance(instance, str) or type(count) is not int or count < 1:
                    fail("sagemaker_quota_unverified", name, "SageMaker AI A:C")
                else:
                    counts[instance] = counts.get(instance, 0) + count
            for instance, count in counts.items():
                quota = sagemaker_quota(f"endpoint/{instance}", primary_region or "")
                if primary_region is None or quota is None:
                    fail("sagemaker_quota_unverified", name, "SageMaker AI A:C")
                elif count > quota:
                    fail("sagemaker_endpoint_quota_exceeded", name, "SageMaker AI A:C")
        if kind == "AWS::BedrockAgentCore::Runtime":
            if not props.get("RoleArn"):
                fail("runtime_execution_role_missing", name)
            auth = props.get("AuthorizerConfiguration")
            if auth is not None:
                jwt = auth.get("CustomJWTAuthorizer", {}) if isinstance(auth, dict) else {}
                if (
                    not isinstance(jwt.get("DiscoveryUrl"), str)
                    or not jwt["DiscoveryUrl"].startswith("https://")
                    or not (jwt.get("AllowedClients") or jwt.get("AllowedAudience"))
                ):
                    fail("runtime_authorizer_unverified", name)
        for statement in _walk(props):
            if _public_allow(statement):
                fail("public_principal_grant", name)
            if statement.get("Effect") != "Allow":
                continue
            if "NotAction" in statement:
                fail("iam_not_action_unbounded", name, "Services List A:B")
            for action in _list(statement.get("Action", [])):
                if not isinstance(action, str) or action.count(":") != 1:
                    fail("iam_action_unverified", name, "Services List A:B")
                    continue
                namespace, operation = action.split(":")
                if action in {"bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"}:
                    targets = _resolved_model_targets(statement.get("Resource"), parameters or {})
                    if not targets or any(not resource_kind_matches("model", t) for t in targets):
                        fail("model_permission_unbounded", name, "PDF p1 Bedrock rule2")
                    elif (
                        approved_model_arns is not None and not set(targets) <= approved_model_arns
                    ):
                        fail("model_permission_not_approved", name, "PDF p1 Bedrock rule2")
                if namespace not in catalog or operation not in catalog[namespace]["actions"]:
                    fail("iam_action_not_listed", name, "Services List A:B")
    return tuple(dict.fromkeys(issues))


def _resolved_model_targets(value: Any, parameters: dict[str, Any]) -> list[str] | None:
    """Resolve explicit parameter values; unsupported expressions fail closed."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        result = []
        for part in value:
            targets = _resolved_model_targets(part, parameters)
            if targets is None:
                return None
            result.extend(targets)
        return result
    if isinstance(value, dict) and set(value) == {"Ref"} and isinstance(value["Ref"], str):
        resolved = parameters.get(value["Ref"])
        if isinstance(resolved, str):
            return resolved.split(",")
        if isinstance(resolved, list) and all(isinstance(x, str) for x in resolved):
            return resolved
    # Fn::Sub/Fn::If/joins need a reviewed expanded template. Never select just a
    # convenient descendant or guess an unresolved value.
    return None
