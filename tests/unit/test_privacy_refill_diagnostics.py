"""Local diagnostics retain original confidence without exposing raw observations."""

import asyncio
import json
from dataclasses import replace
from unittest.mock import Mock
from uuid import UUID

import httpx
import pytest
from test_privacy_bridge import bridge as bridge
from test_privacy_bridge import prepared
from test_privacy_refill import scenario

from appraisal_review.adapters.local.privacy_bridge import (
    create_privacy_bridge,
    privacy_request_id,
)
from appraisal_review.application.privacy_refill import RefillOCRFailure


@pytest.mark.parametrize("stage", ["published", "restored"])
def test_low_score_diagnostic_is_bounded_and_never_changes_measurement(tmp_path, stage):
    executor, _, handle, plan, _, _, ocr = scenario(tmp_path)
    original = (ocr.before if stage == "published" else ocr.after)[0]
    measured = original.model_copy(update={"text": "private local canary", "confidence": 0.0})
    if stage == "published":
        ocr.before = (measured, original)
    else:
        ocr.after = (measured, original)
    writer = Mock(wraps=executor._processor.write)
    executor._processor.write = writer
    with pytest.raises(RefillOCRFailure) as raised:
        executor.execute(handle, plan)
    assert raised.value.diagnostic == {
        "stage": stage,
        "page": 1,
        "reason": "uncertain_ocr",
        "observation_count": 2,
        "low_confidence_count": 1,
    }
    assert measured.confidence == 0.0
    assert "private local canary" not in json.dumps(raised.value.diagnostic)
    assert "private local canary" not in str(raised.value)
    assert writer.call_count == (stage == "restored")
    assert not (tmp_path / "final-local.pdf").exists()


def test_http_diagnostic_matches_server_request_header_and_body_stays_generic(bridge):
    records = []
    seen_request_ids = []
    config = replace(bridge.config, diagnostic=records.append)
    app = create_privacy_bridge(config, {bridge.token: bridge.owner})

    def reject(actor, result):
        seen_request_ids.append(privacy_request_id())
        raise RefillOCRFailure(stage="published", page=1, reason="uncertain_ocr")

    prepared(bridge)
    bridge.owner.results.resolve = reject

    async def request():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 30001)),
            base_url="http://127.0.0.1",
        ) as client:
            return await client.post(
                "/local-privacy/restore/0d745d6c-e985-4055-9077-2cde64d15c29",
                headers={
                    "Host": config.authority,
                    "Origin": config.origin,
                    "Authorization": f"Bearer {bridge.token}",
                    "X-Privacy-Request-Id": "caller-supplied-untrusted-correlation",
                },
                json={},
            )

    response = asyncio.run(request())
    assert response.status_code == 409
    assert response.json() == {"code": "local_privacy_request_rejected"}
    identifier = response.headers["X-Privacy-Request-Id"]
    assert UUID(identifier).version == 4
    assert records[0]["request_id"] == identifier
    assert seen_request_ids == [identifier]
    assert records[0]["reason"] == "uncertain_ocr"
    assert records[0]["code"] == "privacy_verification_failed"
    assert "caller-supplied" not in json.dumps(records)
    assert privacy_request_id() is None


def test_completed_ocr_past_deadline_reports_timeout_and_cannot_write(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from appraisal_review.application import privacy_refill

    executor, _, handle, plan, _, _, ocr = scenario(tmp_path)
    original = ocr.before
    clock = iter((0.0, 0.0, 31.0))
    monkeypatch.setattr(privacy_refill, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    writer = Mock(wraps=executor._processor.write)
    executor._processor.write = writer
    with pytest.raises(RefillOCRFailure) as raised:
        executor.execute(handle, plan)
    assert raised.value.diagnostic["reason"] == "ocr_deadline"
    assert raised.value.diagnostic["observation_count"] == len(original)
    assert raised.value.diagnostic["low_confidence_count"] == 0
    assert ocr.before == original
    writer.assert_not_called()
