"""Synthetic routing-discovery tests; no AWS clients, credentials or invocation."""

from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from appraisal_review.adapters.aws.routing_discovery import RoutingSnapshot, discover_routing
from appraisal_review.application.competition_preflight import check_model_destinations
from appraisal_review.domain.competition_profile import (
    CompetitionProfile,
    ModelDestination,
    ModelUse,
)
from appraisal_review.ports.document_extraction import ExtractionBoundaryError

ACCOUNT = "123456789012"
OBSERVED = datetime(2026, 9, 12, 2, 0, tzinfo=UTC)
REGIONS = ("us-west-2", "us-east-1", "us-east-2")


def arn(region, name="vendor.synthetic-v1"):
    return f"arn:aws:bedrock:{region}::foundation-model/{name}"


def clients(regions=REGIONS):
    factory = Mock()
    mapping = {}
    for region in regions:
        item = Mock()
        item.meta.region_name = region
        item.get_foundation_model.return_value = {
            "modelDetails": {"modelId": "vendor.synthetic-v1", "modelArn": arn(region)}
        }
        mapping[region] = item
    factory.client.side_effect = lambda service, region, timeout: mapping[region]
    return factory, mapping


def profile_arn(identifier="us.vendor.synthetic", resource="inference-profile"):
    return f"arn:aws:bedrock:us-west-2:{ACCOUNT}:{resource}/{identifier}"


def profile_response(regions=REGIONS, identifier="us.vendor.synthetic", **changes):
    return {
        "inferenceProfileArn": profile_arn(identifier),
        "inferenceProfileId": identifier,
        "status": "ACTIVE",
        "type": "SYSTEM_DEFINED",
        "models": [{"modelArn": arn(region)} for region in regions],
    } | changes


def discover(factory, **changes):
    return discover_routing(
        factory,
        **{
            "model_id": "us.vendor.synthetic",
            "kind": "system_profile",
            "region": "us-west-2",
            "account_id": ACCOUNT,
            "observed_at": OBSERVED,
        }
        | changes,
    )


def test_profile_discovery_returns_the_complete_destination_set():
    factory, mapping = clients()
    mapping["us-west-2"].get_inference_profile.return_value = profile_response()
    snapshot = discover(factory)
    assert snapshot.destination_regions == REGIONS
    assert [d.arn for d in snapshot.destinations] == [arn(region) for region in REGIONS]
    assert snapshot.profile_status == "ACTIVE"
    for region in REGIONS:
        mapping[region].get_foundation_model.assert_called_once_with(
            modelIdentifier="vendor.synthetic-v1"
        )
    assert all(call.args[0] == "bedrock" for call in factory.client.call_args_list)


def test_discovery_never_creates_a_runtime_client_or_invokes_a_model():
    factory, mapping = clients()
    mapping["us-west-2"].get_inference_profile.return_value = profile_response()
    discover(factory)
    assert all(
        call.args[0] not in {"bedrock-runtime", "bedrock-agentcore"}
        for call in factory.client.call_args_list
    )
    for region in REGIONS:
        mapping[region].converse.assert_not_called()


def test_digest_pins_the_routing_set_and_ignores_observation_time():
    factory, mapping = clients()
    mapping["us-west-2"].get_inference_profile.return_value = profile_response()
    first = discover(factory)
    factory, mapping = clients()
    mapping["us-west-2"].get_inference_profile.return_value = profile_response()
    later = discover(factory, observed_at=datetime(2026, 9, 12, 9, 30, tzinfo=UTC))
    assert first.digest == later.digest and first.observed_at != later.observed_at


def test_digest_changes_when_a_destination_region_changes():
    factory, mapping = clients()
    mapping["us-west-2"].get_inference_profile.return_value = profile_response()
    complete = discover(factory)
    narrowed_regions = ("us-west-2", "us-east-1")
    factory, mapping = clients(narrowed_regions)
    mapping["us-west-2"].get_inference_profile.return_value = profile_response(narrowed_regions)
    narrowed = discover(factory)
    assert complete.digest != narrowed.digest
    assert narrowed.routes_outside(frozenset({"us-west-2", "us-east-1"})) == ()
    assert complete.routes_outside(frozenset({"us-west-2", "us-east-1"})) == ("us-east-2",)


def test_discovered_set_is_comparable_to_an_approved_profile():
    factory, mapping = clients()
    mapping["us-west-2"].get_inference_profile.return_value = profile_response()
    snapshot = discover(factory)
    approved = CompetitionProfile(
        profile_id="routing-comparison",
        primary_region="us-west-2",
        models=(
            ModelUse(
                model_id="us.vendor.synthetic",
                kind="system_profile",
                purpose="extractor",
                destinations=tuple(ModelDestination(arn=arn(r)) for r in REGIONS),
                destination_snapshot_sha256=snapshot.digest,
            ),
        ),
    )
    discovered = [d.arn for d in snapshot.destinations]
    assert check_model_destinations(approved, snapshot.model_id, discovered)
    # A profile that records only part of the observed routing must not pass.
    partial = approved.model_copy(
        update={
            "models": (
                approved.models[0].model_copy(
                    update={"destinations": (ModelDestination(arn=arn("us-west-2")),)}
                ),
            )
        }
    )
    assert not check_model_destinations(partial, snapshot.model_id, discovered)


def test_foundation_discovery_pins_one_same_region_destination():
    factory, mapping = clients(("us-west-2",))
    snapshot = discover(factory, model_id="vendor.synthetic-v1", kind="foundation")
    assert [d.arn for d in snapshot.destinations] == [arn("us-west-2")]
    assert snapshot.profile_arn is None and snapshot.profile_status is None
    mapping["us-west-2"].get_inference_profile.assert_not_called()


def test_foundation_arn_from_another_region_is_refused():
    factory, _mapping = clients(("us-west-2",))
    with pytest.raises(ExtractionBoundaryError):
        discover(factory, model_id=arn("eu-west-1"), kind="foundation")


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "INACTIVE"},
        {"type": "APPLICATION"},
        {"inferenceProfileId": "other"},
        {"inferenceProfileArn": profile_arn("other")},
        {"inferenceProfileArn": profile_arn(resource="application-inference-profile")},
    ],
)
def test_inconsistent_profile_identity_is_refused(changes):
    factory, mapping = clients()
    mapping["us-west-2"].get_inference_profile.return_value = profile_response(**changes)
    with pytest.raises(ExtractionBoundaryError):
        discover(factory)


@pytest.mark.parametrize(
    "routes",
    [[], [{"modelArn": arn(region)} for region in REGIONS] * 2, [{"modelArn": "not-an-arn"}], [{}]],
)
def test_unusable_destination_lists_are_refused(routes):
    factory, mapping = clients()
    mapping["us-west-2"].get_inference_profile.return_value = profile_response(models=routes)
    with pytest.raises(ExtractionBoundaryError):
        discover(factory)


def test_a_destination_that_does_not_resolve_in_its_region_is_refused():
    factory, mapping = clients()
    mapping["us-west-2"].get_inference_profile.return_value = profile_response()
    mapping["us-east-2"].get_foundation_model.return_value = {
        "modelDetails": {"modelId": "vendor.synthetic-v1", "modelArn": arn("us-east-1")}
    }
    with pytest.raises(ExtractionBoundaryError):
        discover(factory)


def test_a_client_bound_to_another_region_is_refused():
    factory, mapping = clients()
    mapping["us-west-2"].get_inference_profile.return_value = profile_response()
    mapping["us-east-1"].meta.region_name = "eu-west-1"
    with pytest.raises(ExtractionBoundaryError):
        discover(factory)


@pytest.mark.parametrize(
    "changes",
    [
        {"model_id": " "},
        {"model_id": "us.x "},
        {"kind": "unknown"},
        {"region": "US-WEST-2"},
        {"account_id": "12345"},
        {"timeout_seconds": 0},
    ],
)
def test_invalid_discovery_input_is_refused_without_sdk_calls(changes):
    factory, _mapping = clients()
    with pytest.raises(ExtractionBoundaryError):
        discover(factory, **changes)
    factory.client.assert_not_called()


def test_snapshot_requires_a_timezone_and_a_coherent_route():
    identity = {
        "model_id": "us.vendor.synthetic",
        "kind": "system_profile",
        "account_id": ACCOUNT,
        "calling_region": "us-west-2",
        "profile_arn": profile_arn(),
        "destinations": [{"arn": arn("us-west-2")}],
    }
    with pytest.raises(ValidationError):
        RoutingSnapshot.model_validate(identity | {"observed_at": "2026-09-12T02:00:00"})
    with pytest.raises(ValidationError):
        # Direct foundation routing cannot carry a profile ARN.
        RoutingSnapshot.model_validate(
            identity | {"kind": "foundation", "observed_at": OBSERVED.isoformat()}
        )
    with pytest.raises(ValidationError):
        RoutingSnapshot.model_validate(
            identity
            | {
                "destinations": [{"arn": arn("us-west-2")}, {"arn": arn("us-west-2")}],
                "observed_at": OBSERVED.isoformat(),
            }
        )
