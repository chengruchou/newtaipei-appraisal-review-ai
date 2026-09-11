"""Real local crop writing with explicit synthetic OCR and per-region review commands."""

import hashlib
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pymupdf
import pytest
from test_privacy_refill import scenario

from appraisal_review.application.privacy_refill import RefillOCRFailure
from appraisal_review.application.privacy_refill_review import LocalRefillReview, _digest
from appraisal_review.domain.privacy_models import PrivacyRegion, placeholder_text
from appraisal_review.domain.privacy_refill_review import OCRReviewConfirmation


def setup_review(tmp_path, *, before_kind="low-score"):
    executor, mapping, handle, plan, publisher, authority, ocr = scenario(
        tmp_path, operation="restore_original_crop"
    )
    before = tuple(o.model_copy(update={"confidence": 0.0}) for o in ocr.before)
    after = tuple(o.model_copy(update={"confidence": 0.42}) for o in ocr.after)
    if before_kind == "missing":
        before = (
            before[0].model_copy(
                update={
                    "text": "Visible source text",
                    "confidence": 0.99,
                    "region": PrivacyRegion(page=1, bbox=(10.0, 10.0, 100.0, 20.0)),
                }
            ),
        )
    elif before_kind == "unexpected":
        before += (
            before[0].model_copy(
                update={
                    "text": placeholder_text(uuid4()),
                    "confidence": 0.99,
                    "region": PrivacyRegion(page=1, bbox=(10.0, 10.0, 100.0, 20.0)),
                }
            ),
        )
    state = SimpleNamespace(active=True, engine="a" * 64, written=False)
    ocr.read = lambda *args, **kwargs: after if state.written else before
    original = (tmp_path / "synthetic-original.pdf").read_bytes()
    downloaded = (tmp_path / "synthetic-cloud.pdf").read_bytes()
    real_write = executor._processor.write

    def write(*args):
        result = real_write(*args)
        state.written = True
        return result

    executor._processor.write = Mock(side_effect=write)
    # The existing automatic path remains a failing reproduction with the
    # original confidence zero and never calls the writer.
    with pytest.raises(RefillOCRFailure):
        executor.execute(handle, plan)
    executor._processor.write.assert_not_called()

    def authorize():
        assert authority.permits(plan)
        if not state.active:
            raise ValueError("Synthetic current publication revoked")
        if (tmp_path / "synthetic-original.pdf").read_bytes() != original:
            raise ValueError("Synthetic source changed")
        if (tmp_path / "synthetic-cloud.pdf").read_bytes() != downloaded:
            raise ValueError("Synthetic download changed")

    review = LocalRefillReview(
        principal_id="synthetic-local-reviewer",
        mapping=mapping,
        plan=plan,
        artifact=publisher.artifact,
        original=original,
        processor=executor._processor,
        ocr=ocr,
        engine_identity=lambda: state.engine,
        authorize=authorize,
        destination=str(tmp_path / "new-final-local.pdf"),
    )
    return review, state, before, after, original, downloaded


def confirm_all_explicitly(review):
    view = review.view()
    for item in view.items:
        image = review.image(item.page)
        command = OCRReviewConfirmation(
            review_digest=view.review_digest,
            page_image_sha256=hashlib.sha256(image).hexdigest(),
            reading=item.expected_text or "Measured synthetic region visually reviewed",
        )
        review.confirm(item.item_id, command)


def test_two_stages_keep_all_measurements_and_require_exact_separate_receipts(tmp_path):
    review, state, before, after, original, download = setup_review(tmp_path)
    first = review.view()
    assert first.observations[0].confidence == 0.0
    assert review.advance() is None
    assert not state.written
    confirm_all_explicitly(review)
    assert len(review.view().receipts) == 1
    assert review.advance() is None
    assert state.written
    assert not list(tmp_path.glob("new-final*.pdf"))
    final_stage = review.view()
    assert final_stage.stage == "restored"
    assert final_stage.observations[0].confidence == 0.42
    assert review.history("published").observations == first.observations
    assert review._stage_observations == {"published": before, "restored": after}
    confirm_all_explicitly(review)
    final = review.advance()
    assert final is not None and review.permits_completed(final.manifest.final_digest)
    assert len(review.view().receipts) == 2
    for receipt in review.view().receipts:
        measured = review._stage_observations[receipt.stage]
        assert receipt.measurements_digest == _digest([o.model_dump(mode="json") for o in measured])
        assert receipt.page_image_sha256 == tuple(
            hashlib.sha256(png).hexdigest() for png in review._stage_images[receipt.stage].values()
        )
        assert receipt.binding_digest == review.binding_digest
        assert receipt.business_authority == "unchanged"
    with pymupdf.open(stream=final.pdf, filetype="pdf") as document:
        assert len(document) == 1
    assert (tmp_path / "synthetic-original.pdf").read_bytes() == original
    assert (tmp_path / "synthetic-cloud.pdf").read_bytes() == download
    assert review.view().observations == final_stage.observations


@pytest.mark.parametrize(
    "fault", ["unviewed", "wrong-image", "wrong-stage", "wrong-item", "wrong-token"]
)
def test_confirmation_cannot_invent_viewing_or_binding(tmp_path, fault):
    review, state, *_ = setup_review(tmp_path)
    view = review.view()
    item = view.items[0]
    digest = view.pages[0].image_sha256
    if fault != "unviewed":
        review.image(item.page)
    command = OCRReviewConfirmation(
        review_digest="b" * 64 if fault == "wrong-stage" else view.review_digest,
        page_image_sha256="b" * 64 if fault == "wrong-image" else digest,
        reading="wrong token" if fault == "wrong-token" else item.expected_text,
    )
    with pytest.raises(ValueError):
        review.confirm(uuid4() if fault == "wrong-item" else item.item_id, command)
    assert not state.written
    assert not review.view().receipts
    assert all(i.confirmed_reading is None for i in review.view().items)


@pytest.mark.parametrize("stage", ["published", "restored", "completed"])
@pytest.mark.parametrize(
    "fault", ["revoked", "engine", "expired", "image", "measurement", "source", "download"]
)
def test_every_stage_rechecks_authority_and_immutable_evidence(tmp_path, stage, fault):
    review, state, *_ = setup_review(tmp_path)
    if stage != "published":
        confirm_all_explicitly(review)
        assert review.advance() is None
    if stage == "completed":
        confirm_all_explicitly(review)
        assert review.advance() is not None
    if fault == "revoked":
        state.active = False
    elif fault == "engine":
        state.engine = "b" * 64
    elif fault == "expired":
        now = review.now()
        review.now = lambda: now + timedelta(hours=1)
    elif fault == "image":
        review._images[1] = b"changed image"
    elif fault == "measurement":
        value = review._view.observations[0].model_copy(update={"confidence": 1.0})
        review._view = review._view.model_copy(update={"observations": (value,)})
    elif fault == "source":
        (tmp_path / "synthetic-original.pdf").write_bytes(b"changed original")
    else:
        (tmp_path / "synthetic-cloud.pdf").write_bytes(b"changed download")
    with pytest.raises(ValueError):
        review.view()
    with pytest.raises(ValueError):
        review.advance()


def test_published_receipt_cannot_confirm_final_candidate_or_changed_candidate(tmp_path):
    review, *_ = setup_review(tmp_path)
    old = review.view()
    confirm_all_explicitly(review)
    assert review.advance() is None
    current = review.view()
    item = current.items[0]
    review.image(item.page)
    with pytest.raises(ValueError):
        review.confirm(
            item.item_id,
            OCRReviewConfirmation(
                review_digest=old.review_digest,
                page_image_sha256=current.pages[0].image_sha256,
                reading="Exact visible text",
            ),
        )
    confirm_all_explicitly(review)
    review._candidate += b"changed"
    with pytest.raises(ValueError):
        review.advance()


def test_missing_token_has_explicit_cell_without_invented_ocr(tmp_path):
    review, state, before, *_ = setup_review(tmp_path, before_kind="missing")
    view = review.view()
    item = next(i for i in view.items if i.kind == "placeholder")
    assert item.observation_ids == ()
    assert len(view.observations) == len(before) == 1
    assert view.observations[0].raw_text == before[0].text
    assert review.advance() is None
    assert not state.written
    confirm_all_explicitly(review)
    assert review.advance() is None
    assert state.written


def test_unexpected_token_cannot_be_transcribed_as_authorized_placeholder(tmp_path):
    review, state, before, *_ = setup_review(tmp_path, before_kind="unexpected")
    view = review.view()
    item = next(i for i in view.items if i.kind == "observation")
    assert len(view.observations) == len(before) == 2
    image = review.image(item.page)
    with pytest.raises(ValueError):
        review.confirm(
            item.item_id,
            OCRReviewConfirmation(
                review_digest=view.review_digest,
                page_image_sha256=hashlib.sha256(image).hexdigest(),
                reading=before[-1].text,
            ),
        )
    assert review.view().items == view.items
    assert review.advance() is None
    assert not state.written


@pytest.mark.parametrize(
    "fault", ["history-image", "history-measurement", "history-view", "receipt"]
)
def test_original_stage_evidence_cannot_change_during_final_review(tmp_path, fault):
    review, *_ = setup_review(tmp_path)
    confirm_all_explicitly(review)
    assert review.advance() is None
    if fault == "history-image":
        review._stage_images["published"][1] = b"changed original review image"
    elif fault == "history-measurement":
        original = review._stage_observations["published"][0]
        review._stage_observations["published"] = (original.model_copy(update={"confidence": 1.0}),)
    elif fault == "history-view":
        view = review._history["published"]
        row = view.observations[0].model_copy(update={"raw_text": "changed evidence"})
        review._history["published"] = view.model_copy(update={"observations": (row,)})
    else:
        receipt = review._receipts[0]
        review._receipts[0] = receipt.model_copy(update={"binding_digest": "b" * 64})
    with pytest.raises(ValueError):
        review.advance()
