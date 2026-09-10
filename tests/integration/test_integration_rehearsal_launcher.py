"""Opaque restore handles are created only from actual current publications."""

import asyncio
import importlib
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from appraisal_review.adapters.local.synthetic_workbench import prepare_workbench
from appraisal_review.domain.artifact_publication import PublicationError


def test_restore_handle_requires_current_completed_owned_result(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    launcher = importlib.import_module("run_integration_rehearsal")

    async def scenario():
        context = await prepare_workbench(tmp_path / "core")
        await context.settle()

        def forms(case_id):
            fixture = context.by_case[case_id]
            reference = next(r for r in fixture.snapshot.revision.documents if r.purpose == "forms")
            return fixture.documents.read(context.principal, reference).metadata

        results = launcher.PublishedResults(context, forms)
        pending = UUID(context.state["job_ids"]["confirm"])
        with pytest.raises(ValueError):
            results.bind(pending)
        complete = UUID(context.state["job_ids"]["completed"])
        handle = results.bind(complete)
        assert results.bind(complete) == handle
        publication = results(context.principal.actor.actor_id, handle)
        assert publication.job_id == complete
        assert publication.pdf.startswith(b"%PDF")
        assert len(publication.artifact.contexts) == 2
        with pytest.raises(ValueError):
            results(str(uuid4()), handle)
        restored = launcher.PublishedResults(context, forms)
        assert restored(context.principal.actor.actor_id, handle).pdf == publication.pdf
        context.manifests.revoke(publication.run.revision.case_id)
        with pytest.raises(PublicationError):
            results(context.principal.actor.actor_id, handle)

    asyncio.run(scenario())


def test_private_ocr_recording_preserves_every_raw_observation(monkeypatch, tmp_path):
    import json

    from appraisal_review.domain.privacy_models import PrivacyPage, PrivacyRegion
    from appraisal_review.domain.privacy_review import PrivacyPagePreview
    from appraisal_review.domain.privacy_scan import TextObservation

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    launcher = importlib.import_module("run_integration_rehearsal")
    original = (
        TextObservation(
            text="unchanged raw OCR",
            region=PrivacyRegion(page=1, bbox=(1, 2, 30, 20)),
            origin="ocr",
            confidence=0.37,
        ),
    )

    class Delegate:
        def read(self, preview, page, *, timeout):
            return original

    recorder = launcher.RecordedOCR(Delegate(), tmp_path)
    returned = recorder.read(
        PrivacyPagePreview(width=100, height=100, png=b"private test input"),
        PrivacyPage(number=1, width=100, height=100),
        timeout=1,
    )
    assert returned is original
    records = list(tmp_path.glob("ocr-*.json"))
    assert len(records) == 1
    record = json.loads(records[0].read_bytes())
    assert record["observations"] == [item.model_dump(mode="json") for item in original]
    assert record["observations"][0]["confidence"] == 0.37
    assert record["status"] == "observed"
    assert records[0].stat().st_mode & 0o777 == 0o600


def test_private_ocr_recording_reraises_same_exception(monkeypatch, tmp_path):
    import json

    from appraisal_review.domain.privacy_models import PrivacyPage
    from appraisal_review.domain.privacy_review import PrivacyPagePreview

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    launcher = importlib.import_module("run_integration_rehearsal")
    failure = ValueError("private diagnostic must remain absent")
    preview = PrivacyPagePreview(width=100, height=100, png=b"private test input")
    page = PrivacyPage(number=1, width=100, height=100)

    class Delegate:
        def read(self, actual_preview, actual_page, *, timeout):
            assert actual_preview is preview
            assert actual_page is page
            assert timeout == 2.5
            raise failure

    recorder = launcher.RecordedOCR(Delegate(), tmp_path)
    with pytest.raises(ValueError) as raised:
        recorder.read(preview, page, timeout=2.5)
    assert raised.value is failure
    records = list(tmp_path.glob("ocr-*.json"))
    assert len(records) == 1
    assert records[0].stat().st_mode & 0o777 == 0o600
    record = json.loads(records[0].read_bytes())
    assert record["status"] == "failed"
    assert record["exception_type"] == "ValueError"
    assert "observations" not in record
    assert str(failure) not in records[0].read_text()
