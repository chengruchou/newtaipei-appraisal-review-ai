"""Review expiry, bounded retention and explicit evidence-preserving restart."""

import hashlib
import json
from datetime import timedelta
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from test_privacy_bridge import bridge as bridge
from test_privacy_refill_review import confirm_all_explicitly, setup_review
from test_privacy_refill_review_http import confirm_stage, pending

from appraisal_review.domain.privacy_refill_review import OCRReviewConfirmation


@pytest.mark.parametrize("stage", ["published", "restored"])
@pytest.mark.parametrize("operation", ["view", "image", "confirm", "advance"])
@pytest.mark.parametrize("callback", ["authorize", "engine_identity"])
def test_slow_trust_checks_cannot_return_after_expiry(tmp_path, stage, operation, callback):
    review, *_ = setup_review(tmp_path)
    if stage == "restored":
        confirm_all_explicitly(review)
        assert review.advance() is None
    view = review.view()
    item = view.items[0]
    image = review.image(item.page)
    clock = [review.now()]
    review.now = lambda: clock[0]
    original_callback = getattr(review, callback)

    def checked_then_expired():
        result = original_callback()
        clock[0] = review.expires_at + timedelta(seconds=1)
        return result

    setattr(review, callback, checked_then_expired)
    args = {
        "view": (),
        "image": (item.page,),
        "advance": (),
        "confirm": (
            item.item_id,
            OCRReviewConfirmation(
                review_digest=view.review_digest,
                page_image_sha256=hashlib.sha256(image).hexdigest(),
                reading=item.expected_text or "Explicit synthetic reading",
            ),
        ),
    }
    with pytest.raises(ValueError, match="expired"):
        getattr(review, operation)(*args[operation])
    assert review._complete is None
    assert review._view.items == view.items


def test_oversize_candidate_refused_before_retention(tmp_path):
    review, *_ = setup_review(tmp_path)
    confirm_all_explicitly(review)
    before = review.retained_bytes
    review.max_retained_bytes = before + 1
    with pytest.raises(ValueError, match="retention limit"):
        review.advance()
    assert review._candidate is None
    assert review.retained_bytes == before
    assert review._stage_observations.keys() == {"published"}
    assert not list(tmp_path.glob("new-final*.pdf"))


def test_session_capacity_refuses_before_resolve_or_ocr(bridge):
    state, _, _ = pending(bridge)
    state.id = uuid4()
    second = bridge.client.post(f"/restore/{state.id}", json={})
    assert second.status_code == 409
    assert second.json()["code"] == "local_privacy_review_required"
    resolve = Mock(side_effect=AssertionError("Capacity must be checked before resolution"))
    bridge.owner.results.resolve = resolve
    third = bridge.client.post(f"/restore/{uuid4()}", json={})
    assert third.status_code == 409
    assert third.json()["code"] == "local_privacy_request_rejected"
    resolve.assert_not_called()
    assert len(bridge.owner._ocr_reviews) == len(bridge.owner._result_reviews) == 2


def test_expired_restart_archives_both_stages_before_fresh_identity(bridge):
    state, route, _ = pending(bridge)
    confirm_stage(bridge, route)
    assert bridge.client.post(f"/restore/{state.id}", json={}).status_code == 409
    identifier = UUID(route.rsplit("/", 1)[1])
    review = bridge.owner._ocr_reviews[identifier].review
    measurements = {
        stage: [o.model_dump(mode="json") for o in observations]
        for stage, observations in review._stage_observations.items()
    }
    pngs = {
        f"{stage}-page-{page}.png": png
        for stage, pages in review._stage_images.items()
        for page, png in pages.items()
    }
    receipts = [r.model_dump(mode="json") for r in review._receipts]
    original = bridge.config.sources[bridge.source_id].path.read_bytes()
    downloads = {p: p.read_bytes() for p in bridge.path.glob("authorized-download-*/published.pdf")}
    review.now = lambda: review.expires_at + timedelta(seconds=1)
    assert bridge.client.get(route).status_code == 409
    restarted = bridge.client.post(route + "/restart", json={})
    assert restarted.status_code == 200, restarted.text
    assert restarted.json() == {"state": "restarted", "result_id": str(state.id)}
    archive = bridge.path / "ocr-review-archive-0"
    evidence = json.loads((archive / "evidence.json").read_bytes())
    assert evidence["observations"] == measurements
    assert evidence["receipts"] == receipts
    assert evidence["views"].keys() == {"published", "restored"}
    assert all((archive / name).read_bytes() == png for name, png in pngs.items())
    assert all(path.stat().st_mode & 0o077 == 0 for path in archive.iterdir())
    assert review._revoked
    assert not bridge.owner._ocr_reviews and not bridge.owner._result_reviews
    assert bridge.client.get(route).status_code == 409
    assert bridge.client.post(route + "/restart", json={}).status_code == 409
    fresh = bridge.client.post(f"/restore/{state.id}", json={})
    assert fresh.status_code == 409
    assert fresh.json()["review_id"] != str(identifier)
    fresh_view = bridge.client.get("/restore-reviews/" + fresh.json()["review_id"]).json()
    assert fresh_view["stage"] == "published"
    assert fresh_view["receipts"] == []
    assert all(item["confirmed_reading"] is None for item in fresh_view["items"])
    assert bridge.config.sources[bridge.source_id].path.read_bytes() == original
    assert all(path.read_bytes() == data for path, data in downloads.items())
    assert not list(bridge.path.glob("restored-*/final-local.pdf"))


@pytest.mark.parametrize("fault", ["revoked", "completed", "archive-capacity", "other-session"])
def test_restart_cannot_bypass_authority_or_drop_evidence(bridge, fault):
    state, route, _ = pending(bridge)
    identifier = UUID(route.rsplit("/", 1)[1])
    review = bridge.owner._ocr_reviews[identifier].review
    if fault == "completed":
        confirm_stage(bridge, route)
        assert bridge.client.post(f"/restore/{state.id}", json={}).status_code == 409
        confirm_stage(bridge, route)
        assert bridge.client.post(f"/restore/{state.id}", json={}).status_code == 200
    elif fault == "revoked":
        state.active = False
    elif fault == "archive-capacity":
        for slot in range(4):
            (bridge.path / f"ocr-review-archive-{slot}").mkdir(mode=0o700)
    kwargs = (
        {"headers": {"Authorization": f"Bearer {bridge.other_token}"}}
        if fault == "other-session"
        else {}
    )
    response = bridge.client.post(route + "/restart", json={}, **kwargs)
    assert response.status_code == 409
    assert bridge.owner._ocr_reviews[identifier].review is review
    assert bridge.owner._result_reviews[state.id] == identifier
    assert not review._revoked
    assert not list(bridge.path.glob("ocr-review-archive-*/evidence.json"))
