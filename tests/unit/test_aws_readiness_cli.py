"""Readiness CLI tests with injected clients; no AWS session, credentials or invocation."""

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
# Documentation placeholder account and a synthetic role. No real identity belongs here.
ACCOUNT = "123456789012"


def load():
    spec = importlib.util.spec_from_file_location(
        "aws_readiness_cli", ROOT / "scripts/check_aws_readiness.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Dataclass field resolution reads the owning module from sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CLI = load()


def arn(region, name="vendor.synthetic-4-6"):
    return f"arn:aws:bedrock:{region}::foundation-model/{name}"


def account_clients(advertised=("us-west-2", "us-east-1", "us-east-2"), denied=()):
    mapping: dict[tuple[str, str], Mock] = {}

    def client(service, region, timeout):
        item = mapping.get((service, region))
        if item is None:
            item = Mock()
            item.meta.region_name = region
            mapping[service, region] = item
            _configure(item, service, region)
        return item

    def _configure(item, service, region):
        if service == "sts":
            item.get_caller_identity.return_value = {
                "Account": ACCOUNT,
                "Arn": f"arn:aws:sts::{ACCOUNT}:assumed-role/SyntheticParticipantRole/Participant",
            }
        if service == "bedrock":
            if region in denied:
                error = Exception("denied")
                error.response = {"Error": {"Code": "AccessDeniedException"}}
                item.get_foundation_model.side_effect = error
                item.get_foundation_model_availability.side_effect = error
                item.get_inference_profile.side_effect = error
                return
            item.get_foundation_model.return_value = {
                "modelDetails": {"modelId": "vendor.synthetic-4-6", "modelArn": arn(region)}
            }
            item.get_foundation_model_availability.return_value = {
                "agreementAvailability": {"status": "AVAILABLE"},
                "authorizationStatus": "AUTHORIZED",
                "entitlementAvailability": "AVAILABLE",
                "regionAvailability": "AVAILABLE",
            }
            item.get_inference_profile.return_value = {
                "inferenceProfileArn": (
                    f"arn:aws:bedrock:us-west-2:{ACCOUNT}:inference-profile/us.vendor.synthetic-4-6"
                ),
                "inferenceProfileId": "us.vendor.synthetic-4-6",
                "status": "ACTIVE",
                "type": "SYSTEM_DEFINED",
                "models": [{"modelArn": arn(r)} for r in advertised],
            }
        if service == "s3":
            item.list_buckets.return_value = {"Buckets": []}
        if service == "dynamodb":
            item.list_tables.return_value = {"TableNames": []}
        if service == "ecr":
            item.describe_repositories.return_value = {"repositories": []}
        if service == "bedrock-agentcore-control":
            item.list_agent_runtimes.return_value = {"agentRuntimes": []}

    return client, mapping


def run(monkeypatch, capsys, tmp_path, argv, client):
    monkeypatch.setattr(CLI.ThrottledClients, "client", lambda self, s, r, t: client(s, r, t))
    code = CLI.main([*argv, "--dispatch-state", str(tmp_path / "dispatch.sqlite3")])
    return code, json.loads(capsys.readouterr().out)


def checks(report):
    return {check["name"]: check for check in report["checks"]}


def test_report_never_claims_acceptance_or_data_admission(monkeypatch, capsys, tmp_path):
    client, _mapping = account_clients()
    _code, report = run(monkeypatch, capsys, tmp_path, ["--region", "us-west-2"], client)
    assert report["live_acceptance"] == "not_performed"
    assert report["data_admission"] == "not_granted"
    assert report["dispatch"] == {
        "scope": "team-wide",
        "interval_seconds": 1.1,
        "coordination": "host_local_sqlite",
    }


def test_identity_is_digested_unless_the_operator_asks_for_it(monkeypatch, capsys, tmp_path):
    client, _mapping = account_clients()
    _code, private = run(monkeypatch, capsys, tmp_path, ["--region", "us-west-2"], client)
    assert ACCOUNT not in json.dumps(private)
    assert set(private["identity"]) == {"account_id_sha256", "caller_arn_sha256"}
    _code, emitted = run(
        monkeypatch, capsys, tmp_path, ["--region", "us-west-2", "--emit-identity"], client
    )
    assert emitted["identity"]["account_id"] == ACCOUNT


def test_unpermitted_destination_region_blocks_the_report(monkeypatch, capsys, tmp_path):
    client, _mapping = account_clients()
    code, report = run(
        monkeypatch,
        capsys,
        tmp_path,
        [
            "--region",
            "us-west-2",
            "--permit-region",
            "us-east-1",
            "--model",
            "us.vendor.synthetic-4-6:system_profile",
        ],
        client,
    )
    advertised = checks(report)["model_advertised_routing[us.vendor.synthetic-4-6]"]
    assert code == 1 and report["status"] == "blocked"
    assert advertised["state"] == "blocked" and "us-east-2" in advertised["detail"]


def test_verified_routing_produces_a_pinnable_digest(monkeypatch, capsys, tmp_path):
    client, _mapping = account_clients(advertised=("us-west-2",))
    code, report = run(
        monkeypatch,
        capsys,
        tmp_path,
        ["--region", "us-west-2", "--model", "us.vendor.synthetic-4-6:system_profile"],
        client,
    )
    assert code == 0 and report["status"] == "observed"
    observation = report["routing"][0]
    assert observation["destinations"] == [arn("us-west-2")]
    assert len(observation["destination_snapshot_sha256"]) == 64
    assert observation["regions_outside_permitted"] == []


def test_a_denied_destination_region_blocks_verification(monkeypatch, capsys, tmp_path):
    client, _mapping = account_clients(denied=("us-east-2",))
    code, report = run(
        monkeypatch,
        capsys,
        tmp_path,
        [
            "--region",
            "us-west-2",
            "--permit-region",
            "us-east-1",
            "--permit-region",
            "us-east-2",
            "--model",
            "us.vendor.synthetic-4-6:system_profile",
        ],
        client,
    )
    routing = checks(report)["model_routing[us.vendor.synthetic-4-6]"]
    assert code == 1 and routing["state"] == "blocked"
    # A partially observable routing set is never reported as a pinned snapshot.
    assert report["routing"] == []


def test_role_permissions_and_absent_resources_are_reported_as_missing(
    monkeypatch, capsys, tmp_path
):
    client, _mapping = account_clients()
    _code, report = run(monkeypatch, capsys, tmp_path, ["--region", "us-west-2"], client)
    assert {
        "role_effective_permissions",
        "sanitized_bucket_posture",
        "shared_dispatch_store",
        "existing_buckets",
        "existing_tables",
        "existing_repositories",
        "existing_runtimes",
    } <= set(report["missing"])


def test_an_unintended_account_or_role_is_blocked(monkeypatch, capsys, tmp_path):
    client, _mapping = account_clients()
    _code, report = run(
        monkeypatch,
        capsys,
        tmp_path,
        ["--region", "us-west-2", "--expect-account", "000000000000"],
        client,
    )
    assert checks(report)["caller_identity"]["state"] == "blocked"
    _code, other = run(
        monkeypatch,
        capsys,
        tmp_path,
        [
            "--region",
            "us-west-2",
            "--expect-role-arn",
            f"arn:aws:iam::{ACCOUNT}:role/OtherRole",
        ],
        client,
    )
    assert checks(other)["caller_identity"]["state"] == "blocked"


def test_region_outside_the_permitted_primaries_is_blocked(monkeypatch, capsys, tmp_path):
    client, _mapping = account_clients()
    code, report = run(monkeypatch, capsys, tmp_path, ["--region", "eu-west-1"], client)
    assert code == 1 and checks(report)["primary_region_permitted"]["state"] == "blocked"


@pytest.mark.parametrize("value", ["us.model", "us.model:unknown", ":foundation"])
def test_model_arguments_require_an_explicit_kind(value):
    with pytest.raises(argparse.ArgumentTypeError):
        CLI.parse_model(value)


def test_sdk_diagnostics_are_reduced_to_an_error_code():
    error = Exception("bucket private-name and endpoint https://private.invalid")
    error.response = {"Error": {"Code": "AccessDeniedException", "Message": "private-name"}}
    assert CLI.error_code(error) == "AccessDeniedException"
    assert CLI.error_code(ValueError("private-name")) == "ValueError"
