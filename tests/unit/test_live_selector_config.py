"""Env-gated Converse client composition (gate A04 groundwork).

The gate must keep the synthetic default byte-identical, refuse to launch on
incomplete live configuration instead of silently falling back, and never leak
credential values through its diagnostics. No test performs a network call.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from appraisal_review.adapters.aws import live_selector
from appraisal_review.adapters.local.sqlite_review_store import SQLiteReviewStore
from appraisal_review.adapters.local.synthetic_workbench import (
    SyntheticConverseClient,
    compose_action_selector,
)


@pytest.fixture
def store(tmp_path: Path) -> SQLiteReviewStore:
    return SQLiteReviewStore(tmp_path / "state" / "review.sqlite3")


def test_unset_env_composes_the_synthetic_client(store: SQLiteReviewStore) -> None:
    selector = compose_action_selector(store, environ={})
    assert isinstance(selector._client, SyntheticConverseClient)
    assert selector._config.model_id == "fixed-synthetic-converse-v1"
    assert selector._config.attempts == 1


def test_explicit_synthetic_value_matches_the_default(store: SQLiteReviewStore) -> None:
    selector = compose_action_selector(store, environ={"REVIEW_MODEL_CLIENT": "synthetic"})
    assert isinstance(selector._client, SyntheticConverseClient)


def test_bedrock_without_model_id_refuses_to_launch(store: SQLiteReviewStore) -> None:
    with pytest.raises(ValueError, match="REVIEW_MODEL_ID"):
        compose_action_selector(
            store,
            environ={"REVIEW_MODEL_CLIENT": "bedrock", "REVIEW_MODEL_REGION": "us-west-2"},
        )


def test_bedrock_without_region_refuses_to_launch(store: SQLiteReviewStore) -> None:
    with pytest.raises(ValueError, match="REVIEW_MODEL_REGION"):
        compose_action_selector(
            store,
            environ={"REVIEW_MODEL_CLIENT": "bedrock", "REVIEW_MODEL_ID": "some.model-id"},
        )


def test_unknown_mode_refuses_to_launch_rather_than_falling_back(
    store: SQLiteReviewStore,
) -> None:
    with pytest.raises(ValueError, match="REVIEW_MODEL_CLIENT"):
        compose_action_selector(store, environ={"REVIEW_MODEL_CLIENT": "openai"})


def test_bedrock_mode_builds_the_live_selector_with_the_given_model_id(
    store: SQLiteReviewStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sentinel = object()
    seen: dict[str, Any] = {}

    def fake(model_id: str, region: str, **kwargs: Any) -> Any:
        seen.update(model_id=model_id, region=region, **kwargs)
        return sentinel

    monkeypatch.setattr(live_selector, "live_action_selector", fake)
    selector = compose_action_selector(
        store,
        environ={
            "REVIEW_MODEL_CLIENT": "bedrock",
            "REVIEW_MODEL_ID": "anthropic.claude-x-v1",
            "REVIEW_MODEL_REGION": "us-west-2",
        },
        dispatch_store_path=tmp_path / "dispatch.sqlite3",
    )
    assert selector is sentinel
    assert seen["model_id"] == "anthropic.claude-x-v1"
    assert seen["region"] == "us-west-2"
    assert seen["dispatch_store_path"] == tmp_path / "dispatch.sqlite3"


def test_config_error_message_contains_no_credential_values(
    store: SQLiteReviewStore,
) -> None:
    secret = "AKIAFAKEFAKEFAKEFAKE-not-a-real-credential"
    environ = {
        "REVIEW_MODEL_CLIENT": "bedrock",
        "AWS_ACCESS_KEY_ID": secret,
        "AWS_SECRET_ACCESS_KEY": secret,
        "AWS_SESSION_TOKEN": secret,
    }
    with pytest.raises(ValueError) as failure:
        compose_action_selector(store, environ=environ)
    assert secret not in str(failure.value)
    assert "REVIEW_MODEL_ID" in str(failure.value)


def test_live_factory_rejects_a_weakened_throttle_before_importing_boto3() -> None:
    with pytest.raises(ValueError, match=r"1\.2 seconds"):
        live_selector.live_action_selector("some.model", "us-west-2", 1.0)
    with pytest.raises(ValueError, match="model id"):
        live_selector.live_action_selector("  ", "us-west-2")
    with pytest.raises(ValueError, match="region"):
        live_selector.live_action_selector("some.model", " us-west-2")


def test_live_factory_installs_the_dispatch_guard_on_a_real_client(tmp_path: Path) -> None:
    """Offline construction only: a boto3 client is built, nothing is sent."""
    pytest.importorskip("boto3")
    selector = live_selector.live_action_selector(
        "anthropic.claude-x-v1",
        "us-west-2",
        dispatch_store_path=tmp_path / "dispatch.sqlite3",
    )
    assert selector._config.model_id == "anthropic.claude-x-v1"
    assert selector._config.attempts == 1
    transport = selector._client._endpoint.http_session  # type: ignore[attr-defined]
    assert type(transport).__name__ == "_DispatchTransport"
    # Without a reviewed competition admission the transport fails closed.
    assert transport.competition_admission is None
    assert transport.dispatcher.interval_seconds >= 1.2
