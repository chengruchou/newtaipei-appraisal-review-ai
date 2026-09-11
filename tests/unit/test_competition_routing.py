"""Per-model discovery through the guarded SDK and localhost; no AWS access."""

from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

import boto3
import pytest
from moto import mock_aws
from test_competition_runtime import budget_seed_item, endpoint, factory, runtime_profile
from test_extraction_preflight import policy

from appraisal_review.adapters.aws.competition_clients import CompetitionClientFault
from appraisal_review.adapters.aws.extraction_preflight import preflight
from appraisal_review.domain.competition_profile import CompetitionProfile
from appraisal_review.ports.document_extraction import ExtractionBoundaryError
from appraisal_review.ports.model_dispatch import DispatchGuard, dispatch_guard

EAST = "arn:aws:bedrock:us-east-1::foundation-model/example.v1"
WEST = EAST.replace("us-east-1", "us-west-2")
PROFILE_A = "us.profile-a"
PROFILE_B = "us.profile-b"


def routing_profile() -> CompetitionProfile:
    data = runtime_profile().model_dump(mode="json")
    original = data["models"][0]
    data["models"] = [
        {
            **original,
            "model_id": identifier,
            "kind": "system_profile",
            "destinations": [{"arn": arn}],
        }
        for identifier, arn in ((PROFILE_A, EAST), (PROFILE_B, WEST))
    ]
    data["allow_cross_region"] = True
    data["routing_approval_reference"] = "synthetic-routing-only"
    data["iam_actions"].append("sts:GetCallerIdentity")
    invocation = data["budget"]["operation_reservations"][0]
    reservations = []
    for identifier in (PROFILE_A, PROFILE_B):
        reservations.append({**invocation, "model_id": identifier})
        reservations.append({**invocation, "operation": "CountTokens", "model_id": identifier})
        reservations.append(
            {
                **invocation,
                "operation": "GetInferenceProfile",
                "model_id": identifier,
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": "0",
            }
        )
    reservations.append(
        {
            **invocation,
            "operation": "GetFoundationModel",
            "model_id": "example.v1",
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": "0",
        }
    )
    data["budget"]["operation_reservations"] = reservations
    return CompetitionProfile.model_validate(data)


def extraction_policy(identifier=PROFILE_A, kind="system_profile"):
    return policy(
        model_id=identifier,
        model_kind=kind,
        role_name="competition",
        allowed_foundation_models=["example.v1"],
        allowed_regions=["us-east-1", "us-west-2"],
        allow_cross_region=True,
        timeout_seconds=60,
        max_output_tokens=10,
    )


@contextmanager
def metadata_endpoint(discovered, *, kind="SYSTEM_DEFINED", failures=0, on_failure=None):
    requests = []
    active = [PROFILE_A, 0]

    class Handler(BaseHTTPRequestHandler):
        def handle_request(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            path = unquote(self.path)
            requests.append((self.command, path))
            status = 200
            if b"Action=GetCallerIdentity" in body:
                data = (
                    b'<GetCallerIdentityResponse xmlns="https://sts.amazonaws.com/doc/2011-06-15/">'
                    b"<GetCallerIdentityResult><Arn>arn:aws:sts::123456789012:assumed-role/"
                    b"competition/synthetic</Arn><Account>123456789012</Account>"
                    b"<UserId>synthetic</UserId></GetCallerIdentityResult></GetCallerIdentityResponse>"
                )
                content_type = "text/xml"
            elif path.startswith("/inference-profiles/"):
                identifier = path.rsplit("/", 1)[-1]
                active[:] = [identifier, 0]
                value = {
                    "inferenceProfileArn": (
                        "arn:aws:bedrock:us-east-1:123456789012:"
                        + (
                            "application-inference-profile/"
                            if kind == "APPLICATION"
                            else "inference-profile/"
                        )
                        + identifier
                    ),
                    "inferenceProfileId": identifier,
                    "status": "ACTIVE",
                    "type": kind,
                    "models": [{"modelArn": arn} for arn in discovered[identifier]],
                }
                data, content_type = json.dumps(value).encode(), "application/json"
            elif path.startswith("/foundation-models/"):
                identifier = path.rsplit("/", 1)[-1]
                arn = discovered[active[0]][active[1]]
                active[1] += 1
                value = {
                    "modelDetails": {
                        "modelId": identifier,
                        "modelArn": arn,
                        "inputModalities": ["TEXT", "IMAGE"],
                        "outputModalities": ["TEXT"],
                        "inferenceTypesSupported": ["ON_DEMAND"],
                        "modelLifecycle": {"status": "ACTIVE"},
                    }
                }
                data, content_type = json.dumps(value).encode(), "application/json"
            else:
                data = json.dumps(
                    {
                        "stopReason": "end_turn",
                        "output": {"message": {"role": "assistant", "content": [{"text": "{}"}]}},
                        "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
                        "metrics": {"latencyMs": 1},
                    }
                ).encode()
                content_type = "application/json"
                if sum(p.endswith("/converse") for _, p in requests) <= failures:
                    status = 500
                    data = b'{"message":"synthetic retry"}'
                    if on_failure is not None:
                        on_failure()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_POST = do_GET = handle_request

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@contextmanager
def composition(tmp_path, discovered, *, profile=None, metadata_options=None, **factory_options):
    profile = profile or routing_profile()
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name="us-east-1")
        ddb.create_table(
            TableName="shared",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        ddb.put_item(TableName="shared", Item=budget_seed_item(profile))
        with (
            endpoint(ddb=ddb) as (ddb_url, _),
            metadata_endpoint(discovered, **(metadata_options or {})) as (url, requests),
        ):
            clients, _, _ = factory(
                tmp_path, url, profile=profile, ddb_url=ddb_url, **factory_options
            )
            # The already validated synthetic loopback also serves the STS fixture.
            clients._test_endpoints["sts"] = url
            yield clients, requests


def test_profile_cannot_borrow_another_profiles_destination(tmp_path):
    with composition(tmp_path, {PROFILE_A: [EAST, WEST], PROFILE_B: [WEST]}) as (clients, calls):
        with pytest.raises(ExtractionBoundaryError):
            preflight(clients, extraction_policy())
        assert not any(path.endswith("/converse") for _, path in calls)
        assert not any(path.startswith("/foundation-models/") for _, path in calls)


def test_matching_profile_reaches_real_guarded_invocation(tmp_path):
    with composition(tmp_path, {PROFILE_A: [EAST], PROFILE_B: [WEST]}) as (clients, calls):
        runtime = preflight(clients, extraction_policy())
        with dispatch_guard(DispatchGuard(time.monotonic() + 30, lambda: True)):
            response = runtime.converse(
                modelId=PROFILE_A,
                messages=[{"role": "user", "content": [{"text": "synthetic"}]}],
                inferenceConfig={"maxTokens": 8},
            )
        assert response["stopReason"] == "end_turn"
        assert sum(path.endswith("/converse") for _, path in calls) == 1


def converse(runtime, identifier=PROFILE_A, *, max_sends=1):
    with dispatch_guard(DispatchGuard(time.monotonic() + 30, lambda: True, max_sends=max_sends)):
        return runtime.converse(
            modelId=identifier,
            messages=[{"role": "user", "content": [{"text": "synthetic"}]}],
            inferenceConfig={"maxTokens": 8},
        )


@pytest.mark.parametrize(
    "destinations", [[], [EAST, EAST], [WEST], [EAST.replace("example.v1", "unknown.v1")]]
)
def test_non_exact_discovery_is_rejected_before_foundation_lookup(tmp_path, destinations):
    with composition(tmp_path, {PROFILE_A: destinations, PROFILE_B: [WEST]}) as (clients, calls):
        with pytest.raises(ExtractionBoundaryError):
            preflight(clients, extraction_policy())
        assert not any(path.startswith("/foundation-models/") for _, path in calls)
        assert not any(path.endswith("/converse") for _, path in calls)


def test_observed_drift_revokes_old_client_until_fresh_exact_preflight(tmp_path):
    discovered = {PROFILE_A: [EAST], PROFILE_B: [WEST]}
    with composition(tmp_path, discovered) as (clients, calls):
        original = preflight(clients, extraction_policy())
        discovered[PROFILE_A] = [EAST, WEST]
        with pytest.raises(ExtractionBoundaryError):
            preflight(clients, extraction_policy())
        with pytest.raises(CompetitionClientFault, match="competition_model_routing_expired"):
            converse(original)
        assert not any(path.endswith("/converse") for _, path in calls)
        discovered[PROFILE_A] = [EAST]
        fresh = preflight(clients, extraction_policy())
        assert converse(fresh)["stopReason"] == "end_turn"
        with pytest.raises(CompetitionClientFault, match="competition_model_routing_expired"):
            converse(original)
        assert sum(path.endswith("/converse") for _, path in calls) == 1


def test_two_preflights_cannot_replace_each_others_runtime_scope(tmp_path):
    with composition(tmp_path, {PROFILE_A: [EAST], PROFILE_B: [WEST]}) as (clients, calls):
        runtime_a = preflight(clients, extraction_policy())
        runtime_b = preflight(clients, extraction_policy(PROFILE_B))
        assert converse(runtime_a)["stopReason"] == "end_turn"
        assert converse(runtime_b, PROFILE_B)["stopReason"] == "end_turn"
        with pytest.raises(CompetitionClientFault, match="competition_model_not_approved"):
            converse(runtime_a, PROFILE_B)
        assert sum(path.endswith("/converse") for _, path in calls) == 2


def test_scoped_metadata_cannot_borrow_another_models_region(tmp_path):
    with composition(tmp_path, {PROFILE_A: [EAST], PROFILE_B: [WEST]}) as (clients, calls):
        scoped = clients.for_model(PROFILE_A, "system_profile", time.monotonic() + 30)
        scoped.validate_destinations((EAST,))
        with pytest.raises(CompetitionClientFault, match="competition_model_not_approved"):
            scoped.client("bedrock", "us-west-2", 5).get_foundation_model(
                modelIdentifier="example.v1"
            )
        assert calls == []


def test_raw_dynamic_runtime_cannot_reuse_a_prior_preflight(tmp_path):
    with composition(tmp_path, {PROFILE_A: [EAST], PROFILE_B: [WEST]}) as (clients, calls):
        preflight(clients, extraction_policy())
        with pytest.raises(CompetitionClientFault, match="competition_model_routing_unverified"):
            converse(clients.client("bedrock-runtime", "us-east-1", 5))
        assert not any(path.endswith("/converse") for _, path in calls)


@pytest.mark.parametrize("change", ["authority", "deadline", "pricing"])
def test_bound_runtime_rechecks_current_authority_deadline_and_budget(
    tmp_path, monkeypatch, change
):
    with composition(tmp_path, {PROFILE_A: [EAST], PROFILE_B: [WEST]}) as (clients, calls):
        runtime = preflight(clients, extraction_policy())
        if change == "authority":
            clients.current_authority = lambda: False
            code = "competition_authority_revoked"
        elif change == "deadline":
            deadline = time.monotonic() + 61
            monkeypatch.setattr(time, "monotonic", lambda: deadline)
            code = "competition_model_routing_expired"
        else:
            data = clients.profile.model_dump(mode="json")
            data["models"][0]["destination_snapshot_sha256"] = "b" * 64
            clients.profile = CompetitionProfile.model_validate(data)
            clients.trusted_profile_pin = clients.profile.digest
            code = "competition_budget_profile_changed"
        with pytest.raises(CompetitionClientFault, match=code):
            converse(runtime)
        assert not any(path.endswith("/converse") for _, path in calls)


def test_matching_application_profile_reaches_native_runtime(tmp_path):
    data = routing_profile().model_dump(mode="json")
    data["models"][0]["kind"] = "application_profile"
    profile = CompetitionProfile.model_validate(data)
    with composition(
        tmp_path,
        {PROFILE_A: [EAST], PROFILE_B: [WEST]},
        profile=profile,
        metadata_options={"kind": "APPLICATION"},
    ) as (clients, calls):
        runtime = preflight(clients, extraction_policy(kind="application_profile"))
        assert converse(runtime)["stopReason"] == "end_turn"
        assert sum(path.endswith("/converse") for _, path in calls) == 1


@pytest.mark.parametrize("revoke", [False, True])
def test_native_retry_retains_model_scope_and_rechecks_revocation(tmp_path, revoke):
    authority = [True]
    with composition(
        tmp_path,
        {PROFILE_A: [EAST], PROFILE_B: [WEST]},
        attempts=2,
        authority=lambda: authority[0],
        metadata_options={
            "failures": 1,
            "on_failure": lambda: authority.__setitem__(0, not revoke),
        },
    ) as (clients, calls):
        runtime = preflight(clients, extraction_policy())
        if revoke:
            with pytest.raises(CompetitionClientFault, match="competition_authority_revoked"):
                converse(runtime, max_sends=2)
        else:
            assert converse(runtime, max_sends=2)["stopReason"] == "end_turn"
        assert sum(path.endswith("/converse") for _, path in calls) == (1 if revoke else 2)


def test_matching_foundation_reaches_native_runtime_without_profile_lookup(tmp_path):
    data = runtime_profile().model_dump(mode="json")
    data["iam_actions"].append("sts:GetCallerIdentity")
    bound = data["budget"]["operation_reservations"][0]
    data["budget"]["operation_reservations"].append(
        {
            **bound,
            "operation": "GetFoundationModel",
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": "0",
        }
    )
    profile = CompetitionProfile.model_validate(data)
    identifier = profile.models[0].model_id
    with composition(tmp_path, {PROFILE_A: [EAST]}, profile=profile) as (clients, calls):
        runtime = preflight(clients, extraction_policy(identifier, "foundation"))
        assert converse(runtime, identifier)["stopReason"] == "end_turn"
        assert not any(path.startswith("/inference-profiles/") for _, path in calls)
        assert sum(path.startswith("/foundation-models/") for _, path in calls) == 1
        sts = [
            parts
            for parts in clients.admission.parts
            if any(
                p.part_id == "aws.request.operation"
                and json.loads(p.content)["operation"] == "GetCallerIdentity"
                for p in parts
            )
        ]
        assert len(sts) == 1
        assert any(p.content == b"Action=GetCallerIdentity&Version=2011-06-15" for p in sts[0])


def test_sdk_retry_cannot_send_after_an_observed_routing_change(tmp_path):
    discovered = {PROFILE_A: [EAST], PROFILE_B: [WEST]}

    def install_retry_observation(client):
        if client.meta.service_model.service_name == "bedrock-runtime":

            def observe(attempts, **kwargs):
                if attempts == 1:
                    discovered[PROFILE_A] = [EAST, WEST]
                    with pytest.raises(ExtractionBoundaryError):
                        preflight(clients, extraction_policy())

            client.meta.events.register("needs-retry.bedrock-runtime.Converse", observe)

    with composition(
        tmp_path,
        discovered,
        attempts=2,
        hook=install_retry_observation,
        metadata_options={"failures": 1},
    ) as (clients, calls):
        runtime = preflight(clients, extraction_policy())
        with pytest.raises(CompetitionClientFault, match="competition_model_routing_expired"):
            converse(runtime, max_sends=2)
        assert sum(path.endswith("/converse") for _, path in calls) == 1
