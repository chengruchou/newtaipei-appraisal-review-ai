"""Competition configuration rejects coercion and never carries its own approval."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from appraisal_review.application.competition_preflight import check_profile, service_catalog
from appraisal_review.domain.competition_profile import (
    BudgetLimits,
    CompetitionProfile,
    ModelUse,
    ThrottlePolicy,
    iam_actions_for_api,
)

ROOT = Path(__file__).resolve().parents[2]


def test_pending_configuration_retains_unapproved_gates() -> None:
    profile = CompetitionProfile.model_validate_json(
        (ROOT / "config/competition-profile.pending.json").read_bytes()
    )
    assert profile.sources is not None and profile.sources.complete()
    assert profile.primary_region == "us-east-1"
    assert not profile.allow_cross_region and not profile.allow_global
    codes = {f.code for f in check_profile(profile)}
    assert {
        "profile_not_approved",
        "identity_not_approved",
        "models_not_approved",
        "budget_not_approved",
        "shared_dispatch_store_missing",
        "team_entrypoints_not_covered",
        "throttle_scope_not_approved",
        "invocation_auth_unverified",
        "resource_inventory_missing",
        "synthetic_financial_permission_unknown",
        "role_permissions_unverified",
    } <= codes


def test_profile_cannot_self_approve_and_digest_binds_every_field() -> None:
    p = CompetitionProfile(profile_id="test-pending")
    with pytest.raises(ValidationError):
        CompetitionProfile.model_validate({**p.model_dump(), "approved": True})
    updated = CompetitionProfile.model_validate({**p.model_dump(), "primary_region": "us-west-2"})
    assert updated.digest != p.digest
    assert p.digest == CompetitionProfile.model_validate_json(p.model_dump_json()).digest
    assert "profile_not_approved" in {
        f.code for f in check_profile(updated, trusted_profile_digest=p.digest)
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("primary_region", "eu-west-1"),
        ("allow_cross_region", "true"),
        ("allow_global", 1),
        ("iam_actions", ["bedrock:*"]),
        ("account_id", "123"),
    ],
)
def test_invalid_profile_values_fail(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        CompetitionProfile.model_validate({"profile_id": "test", field: value})


@pytest.mark.parametrize("interval", [0, 1.0, 1.099, float("inf"), float("nan")])
def test_interval_cannot_relax_conservative_limit(interval: float) -> None:
    with pytest.raises(ValidationError):
        ThrottlePolicy(interval_seconds=interval)


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_calls", 0),
        ("max_calls", True),
        ("max_input_tokens", -1),
        ("max_output_tokens", 0),
        ("max_cost_usd", "0"),
        ("max_cost_usd", "NaN"),
        ("window_seconds", 0),
    ],
)
def test_budget_limits_are_explicit_positive_values(field: str, value: object) -> None:
    data = {
        "window_seconds": 3600,
        "max_calls": 20,
        "max_input_tokens": 1000,
        "max_output_tokens": 1000,
        "max_cost_usd": "0.10",
        "pricing_evidence_sha256": "a" * 64,
    }
    with pytest.raises(ValidationError):
        BudgetLimits.model_validate({**data, field: value})


def test_iam_source_ceiling_uses_actions_not_sdk_names() -> None:
    assert iam_actions_for_api("Converse") == ("bedrock:InvokeModel",)
    assert iam_actions_for_api("ConverseStream") == ("bedrock:InvokeModelWithResponseStream",)
    with pytest.raises(ValueError, match="Unsupported API"):
        iam_actions_for_api("AssumedEquivalentOperation")
    catalog = service_catalog()
    assert catalog["bedrock"]["source_row"] == 46
    assert catalog["bedrock-agentcore"]["source_row"] == 47
    assert {"InvokeModel", "CountTokens", "GetInferenceProfile"} <= set(
        catalog["bedrock"]["actions"]
    )
    assert "Converse" not in catalog["bedrock"]["actions"]
    assert "InvokeAgentRuntime" in catalog["bedrock-agentcore"]["actions"]


def test_full_coverage_includes_all_sheets_and_notes() -> None:
    data = json.loads((ROOT / "config/competition-profile.pending.json").read_bytes())
    for sheet in data["sources"]["worksheets"]:
        sheet["rows_read"] -= 1
    profile = CompetitionProfile.model_validate(data)
    assert "source_coverage_incomplete" in {f.code for f in check_profile(profile)}


def test_profile_identity_and_destination_duplicates_fail() -> None:
    with pytest.raises(ValidationError, match="another account"):
        CompetitionProfile(
            profile_id="test",
            account_id="123456789012",
            role_arn="arn:aws:iam::999999999999:role/example",
        )
    destination = {"arn": "arn:aws:bedrock:us-east-1::foundation-model/example.v1"}
    with pytest.raises(ValidationError, match="Duplicate model destinations"):
        ModelUse.model_validate(
            {
                "model_id": "example.v1",
                "kind": "foundation",
                "purpose": "extractor",
                "destinations": [destination, destination],
            }
        )
