"""Local service transition tests with explicitly trusted synthetic human doubles."""

from __future__ import annotations

import asyncio
import json
import runpy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, Mock
from uuid import uuid4

import jsonschema
import pytest

from appraisal_review.adapters.local.privacy.review import LinuxTerminalConfirmation
from appraisal_review.application.privacy_guards import PrivacyFault
from appraisal_review.application.privacy_review import LocalPrivacyReviewService
from appraisal_review.domain.privacy_models import (
    LocalSourcePage,
    LocalSourceSnapshot,
    PrivacyRegion,
    SensitiveCandidate,
    SensitiveCategory,
    privacy_review_digest,
)
from appraisal_review.domain.privacy_review import (
    AddPrivacyRegion,
    ConfirmPrivacyReview,
    EditPrivacyRegion,
    PrivacyCropRequest,
    PrivacyPagePreview,
    RemovePrivacyRegion,
    ReviewPrivacyPage,
)
from appraisal_review.domain.privacy_scan import (
    PageScan,
    PrivacyScanReport,
    ScanIssue,
    TextObservation,
)


def source():
    return LocalSourceSnapshot(
        case_id=uuid4(),
        document_id=uuid4(),
        snapshot_id=uuid4(),
        source_revision=1,
        source_digest="a" * 64,
        byte_size=10,
        pages=tuple(
            LocalSourcePage(
                number=p,
                width=300.0,
                height=400.0,
                rotation=0,
                crop_box=(0.0, 0.0, 300.0, 400.0),
            )
            for p in (1, 2)
        ),
    )


def report(snapshot, blocked=False):
    region = PrivacyRegion(page=1, bbox=(10.0, 20.0, 80.0, 40.0))
    return PrivacyScanReport(
        source=snapshot,
        native_engine_version="synthetic",
        status="blocked" if blocked else "needs_review",
        pages=tuple(
            PageScan(
                page=p.number,
                mode="native",
                status="blocked" if blocked else "processed",
                issues=(ScanIssue.OCR_UNAVAILABLE,) if blocked else (),
                observations=(
                    TextObservation(
                        text="synthetic@example.invalid",
                        region=PrivacyRegion(page=p.number, bbox=region.bbox),
                        origin="native",
                        confidence=None,
                    ),
                ),
            )
            for p in snapshot.pages
        ),
        candidates=(
            SensitiveCandidate(
                candidate_id=uuid4(),
                category=SensitiveCategory.EMAIL,
                region=region,
                raw_text="synthetic@example.invalid",
                confidence=None,
                detector_id="synthetic",
                detector_version="1",
            ),
        ),
    )


class Scanner:
    blocked = False

    async def scan(self, snapshot):
        return report(snapshot, self.blocked)


@pytest.fixture
def session():
    scanner = Scanner()
    sources = Mock()
    sources.read.return_value = b"synthetic"
    previews = Mock()
    previews.preview.return_value = PrivacyPagePreview(600, 800, b"synthetic-png")
    human = Mock()
    human.confirm.return_value = "synthetic-test-reviewer"
    clock = Mock(return_value=datetime(2026, 9, 10, tzinfo=UTC))
    service = LocalPrivacyReviewService(
        scanner=scanner,
        sources=sources,
        previews=previews,
        human=human,
        clock=clock,
    )
    asyncio.run(service.rescan(source(), policy_digest="b" * 64))
    return service, scanner, sources, human, clock


def version(service):
    command = service.list().command
    return dict(
        case_id=command.source.case_id,
        snapshot_id=command.source.snapshot_id,
        revision=command.selection_revision,
    )


def review_all(service):
    for p in service.list().command.source.pages:
        request = ReviewPrivacyPage(**version(service), page=p.number)
        service.preview(request)
        service.review_page(request)


def confirm(service):
    return service.confirm(
        ConfirmPrivacyReview(
            **version(service),
            review_digest=privacy_review_digest(service.list().command),
        )
    )


def add(service):
    return service.add(
        AddPrivacyRegion(
            **version(service),
            category=SensitiveCategory.SIGNATURE,
            region=PrivacyRegion(page=2, bbox=(20.0, 30.0, 80.0, 60.0)),
        )
    )


def test_confirm_exact_owned_review_and_expiry(session):
    service, _, _, human, clock = session
    assert service.list().state == "awaiting_confirmation"
    review_all(service)
    command = service.list().command
    approval = confirm(service)
    assert service.list().state == "confirmed"
    human.confirm.assert_called_once_with(command)
    assert service.permits(approval, command, now=clock())
    clock.return_value += timedelta(minutes=15)
    assert not service.permits(approval, command, now=approval.approved_at)
    assert service.list().state == "awaiting_confirmation"


@pytest.mark.parametrize(
    "mutation", ["add", "edit", "remove", "rescan", "policy", "source", "page"]
)
def test_every_mutation_revokes_approval(session, mutation):
    service, _, _, _, clock = session
    review_all(service)
    command = service.list().command
    approval = confirm(service)
    candidate = command.selections[0].candidate
    if mutation == "add":
        add(service)
    elif mutation == "edit":
        service.edit(
            EditPrivacyRegion(
                **version(service),
                candidate_id=candidate.candidate_id,
                region=candidate.region,
                category=SensitiveCategory.CASE_CONTACT,
            )
        )
    elif mutation == "remove":
        service.remove(
            RemovePrivacyRegion(
                **version(service),
                candidate_id=candidate.candidate_id,
                reason="Synthetic false positive",
            )
        )
    elif mutation == "page":
        service.review_page(ReviewPrivacyPage(**version(service), page=1))
    else:
        asyncio.run(
            service.rescan(
                source() if mutation == "source" else command.source,
                policy_digest="c" * 64 if mutation == "policy" else "b" * 64,
            )
        )
    assert not service.permits(approval, command, now=clock())
    assert service.list().command.selection_revision > command.selection_revision
    if mutation != "page":
        assert service.list().command.reviewed_pages == ()


@pytest.mark.parametrize(
    "field",
    ["approval_id", "case_id", "snapshot_id", "principal_id", "review_digest", "expires_at"],
)
def test_forged_receipt_rejected(session, field):
    service, _, _, _, clock = session
    review_all(service)
    command = service.list().command
    approval = confirm(service)
    value = {
        "principal_id": "model",
        "review_digest": "c" * 64,
        "expires_at": approval.expires_at + timedelta(hours=1),
    }.get(field, uuid4())
    forged = approval.model_copy(update={field: value})
    assert not service.permits(forged, command, now=clock())


@pytest.mark.parametrize(
    "field,value",
    [
        ("actor", "human"),
        ("confirmed", True),
        ("approval", "model-authored"),
        ("principal_id", "human"),
    ],
)
def test_model_authored_confirmation_fields_rejected_without_prompt(session, field, value):
    service, _, _, human, _ = session
    review_all(service)
    request = dict(**version(service), review_digest=privacy_review_digest(service.list().command))
    request[field] = value
    with pytest.raises(PrivacyFault):
        service.confirm(request)
    human.confirm.assert_not_called()


@pytest.mark.parametrize("change", ["case_id", "snapshot_id", "revision", "review_digest"])
def test_stale_or_cross_case_confirmation_never_prompts(session, change):
    service, _, _, human, _ = session
    review_all(service)
    request = dict(**version(service), review_digest=privacy_review_digest(service.list().command))
    request[change] = {"revision": 100, "review_digest": "d" * 64}.get(change, uuid4())
    with pytest.raises(PrivacyFault):
        service.confirm(ConfirmPrivacyReview(**request))
    human.confirm.assert_not_called()


def test_all_pages_and_preview_required(session):
    service, _, _, human, _ = session
    with pytest.raises(PrivacyFault):
        service.review_page(ReviewPrivacyPage(**version(service), page=1))
    service.preview(ReviewPrivacyPage(**version(service), page=1))
    service.review_page(ReviewPrivacyPage(**version(service), page=1))
    with pytest.raises(PrivacyFault):
        confirm(service)
    human.confirm.assert_not_called()


def test_blocked_scan_cannot_confirm_even_with_manual_regions_and_page_attestations(session):
    service, scanner, _, human, _ = session
    scanner.blocked = True
    asyncio.run(service.rescan(service.list().command.source, policy_digest="b" * 64))
    add(service)
    review_all(service)
    with pytest.raises(PrivacyFault):
        confirm(service)
    human.confirm.assert_not_called()
    assert service.list().state == "blocked"


@pytest.mark.parametrize("result", [None, "", "   ", True])
def test_no_real_human_authorization_no_issuance(session, result):
    service, _, _, human, _ = session
    review_all(service)
    human.confirm.return_value = result
    with pytest.raises(PrivacyFault):
        confirm(service)
    assert service.list().state == "awaiting_confirmation"


def test_edit_retains_raw_evidence_and_dismissal_history(session):
    service, _, _, _, _ = session
    original = service.list().scan.candidates[0]
    view = service.edit(
        EditPrivacyRegion(
            **version(service),
            candidate_id=original.candidate_id,
            region=PrivacyRegion(page=2, bbox=original.region.bbox),
            category=SensitiveCategory.ADDRESS,
        )
    )
    assert view.command.selections[0].candidate.raw_text is None
    assert view.command.selections[0].candidate.crop_id is not None
    assert view.command.selections[0].candidate.confidence is None
    assert view.scan.candidates[0] == original
    assert view.events[-1].before.candidate == original
    view = service.remove(
        RemovePrivacyRegion(
            **version(service),
            candidate_id=original.candidate_id,
            reason="Reviewed synthetic false positive",
        )
    )
    assert len(view.command.selections) == 1
    assert view.command.selections[0].disposition == "dismiss"
    assert view.command.selections[0].dismissal_reason


@pytest.mark.parametrize("reason", ["", "   ", "\n"])
def test_dismissal_requires_nonblank_reason_and_safe_errors(session, reason):
    service, _, _, _, _ = session
    with pytest.raises(PrivacyFault) as error:
        service.remove(
            dict(
                **version(service),
                candidate_id=service.list().scan.candidates[0].candidate_id,
                reason=reason,
            )
        )
    assert str(error.value) == "privacy_invalid_input"


def test_manual_crop_bound_to_owned_source_and_revision(session):
    service, _, _, _, _ = session
    view = add(service)
    candidate = view.command.selections[-1].candidate
    request = PrivacyCropRequest(**version(service), crop_id=candidate.crop_id)
    crop = service.crop(request)
    assert crop.source == view.command.source and crop.region == candidate.region
    assert candidate.raw_text is None and candidate.confidence is None
    add(service)
    with pytest.raises(PrivacyFault):
        service.crop(request)


def test_unknown_group_or_outside_page_rejected(session):
    service, _, _, _, _ = session
    c = service.list().scan.candidates[0]
    with pytest.raises(PrivacyFault):
        service.edit(
            EditPrivacyRegion(
                **version(service),
                candidate_id=c.candidate_id,
                region=c.region,
                category=c.category,
                entity_id=uuid4(),
            )
        )
    with pytest.raises(PrivacyFault):
        service.add(
            AddPrivacyRegion(
                **version(service),
                category=c.category,
                region=PrivacyRegion(page=3, bbox=c.region.bbox),
            )
        )


def test_mutation_during_human_prompt_cannot_issue(session):
    service, _, _, human, _ = session
    review_all(service)
    human.confirm.side_effect = lambda _: (add(service), "synthetic-reviewer")[1]
    with pytest.raises(PrivacyFault):
        confirm(service)
    assert service.list().state == "awaiting_confirmation"


def test_failed_rescan_revokes_existing_approval(session):
    service, _, sources, _, clock = session
    review_all(service)
    command = service.list().command
    approval = confirm(service)
    sources.read.side_effect = ValueError("private canary")
    with pytest.raises(PrivacyFault) as error:
        asyncio.run(service.rescan(command.source, policy_digest="b" * 64))
    assert "private canary" not in str(error.value)
    assert not service.permits(approval, command, now=clock())


def test_overlapping_scans_do_not_restore_stale_state(session):
    service, scanner, _, _, _ = session
    first_source = service.list().command.source

    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()

        async def scan(snapshot):
            if snapshot == first_source:
                started.set()
                await release.wait()
            return report(snapshot)

        scanner.scan = scan
        first = asyncio.create_task(service.rescan(first_source, policy_digest="b" * 64))
        await started.wait()
        second = await service.rescan(source(), policy_digest="c" * 64)
        release.set()
        with pytest.raises(PrivacyFault):
            await first
        assert service.list().command == second.command

    asyncio.run(scenario())


def test_linux_terminal_never_uses_windows_fallback(monkeypatch):
    monkeypatch.setattr("appraisal_review.adapters.local.privacy.review.sys.platform", "win32")
    with pytest.raises(PrivacyFault):
        LinuxTerminalConfirmation(owner_uid=1000).confirm(Mock())


@pytest.mark.parametrize("mode", ["approve", "deny", "wrong-owner", "background", "not-tty"])
def test_terminal_adapter_requires_current_owner_foreground_tty_and_fresh_challenge(
    session,
    monkeypatch,
    mode,
):
    import appraisal_review.adapters.local.privacy.review as adapter

    service, _, _, _, _ = session
    review_all(service)
    monkeypatch.setattr(adapter.sys, "platform", "linux")
    reviewer = Mock(uid=1001 if mode == "wrong-owner" else 1000)
    monkeypatch.setattr(adapter, "current_reviewer", Mock(return_value=reviewer))
    monkeypatch.setattr(adapter.secrets, "token_hex", lambda _: "synthetic-challenge")
    monkeypatch.setattr(
        adapter.os, "tcgetpgrp", lambda _: 2 if mode == "background" else 1, raising=False
    )
    monkeypatch.setattr(adapter.os, "getpgrp", lambda: 1, raising=False)
    reader, writer = MagicMock(), MagicMock()
    for stream in (reader, writer):
        stream.__enter__.return_value = stream
        stream.isatty.return_value = mode != "not-tty"
    reader.readline.return_value = "CONFIRM synthetic-challenge\n" if mode != "deny" else "yes\n"
    opened = Mock(side_effect=[reader, writer])
    monkeypatch.setattr(adapter, "open", opened, raising=False)
    terminal = LinuxTerminalConfirmation(owner_uid=1000)
    if mode in {"wrong-owner", "background", "not-tty"}:
        with pytest.raises(PrivacyFault):
            terminal.confirm(service.list().command)
    else:
        assert terminal.confirm(service.list().command) == (
            "posix-uid:1000" if mode == "approve" else None
        )
        assert "synthetic@example.invalid" not in str(writer.write.call_args)
    if mode == "wrong-owner":
        opened.assert_not_called()


def test_close_and_new_session_never_accept_old_receipt(session):
    service, _, _, _, clock = session
    review_all(service)
    command = service.list().command
    approval = confirm(service)
    service.close()
    assert not service.permits(approval, command, now=clock())
    asyncio.run(service.rescan(command.source, policy_digest=command.policy_digest))
    review_all(service)
    assert not service.permits(approval, service.list().command, now=clock())


def test_superseded_confirmation_prompt_cannot_issue(session):
    service, _, _, human, _ = session
    review_all(service)

    def supersede(_):
        human.confirm.side_effect = None
        human.confirm.return_value = None
        with pytest.raises(PrivacyFault):
            confirm(service)
        return "synthetic-reviewer"

    human.confirm.side_effect = supersede
    with pytest.raises(PrivacyFault):
        confirm(service)
    assert service.list().state == "awaiting_confirmation"


def test_group_merge_and_split_use_only_service_issued_entities(session):
    service, _, _, _, _ = session
    view = add(service)
    first, second = view.command.selections
    request = dict(
        candidate_id=second.candidate.candidate_id,
        region=second.candidate.region,
        category=second.candidate.category,
    )
    view = service.edit(EditPrivacyRegion(**version(service), **request, entity_id=first.entity_id))
    assert view.command.selections[0].entity_id == view.command.selections[1].entity_id
    view = service.edit(EditPrivacyRegion(**version(service), **request))
    assert view.command.selections[0].entity_id != view.command.selections[1].entity_id


def test_lost_source_and_naive_time_fail_authority(session):
    service, _, sources, _, clock = session
    review_all(service)
    command = service.list().command
    approval = confirm(service)
    assert not service.permits(approval, command, now=datetime(2026, 9, 10))
    sources.read.side_effect = ValueError("synthetic private text")
    assert not service.permits(approval, command, now=clock())


def test_scan_error_or_foreign_report_remains_blocked(session):
    service, scanner, _, _, _ = session

    async def foreign(_):
        return report(source())

    scanner.scan = foreign
    with pytest.raises(PrivacyFault):
        asyncio.run(service.rescan(service.list().command.source, policy_digest="b" * 64))
    assert service.list().state == "blocked"


def test_review_schema_and_consumer_fixtures_are_reproducible(tmp_path):
    root = Path(__file__).resolve().parents[2]
    export = runpy.run_path(str(root / "scripts/export_privacy_review_contracts.py"))["export"]
    export(tmp_path)
    schema_path = Path("schemas/local-privacy-review-v1.json")
    schema = json.loads((tmp_path / schema_path).read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    assert (tmp_path / schema_path).read_bytes() == (root / schema_path).read_bytes()
    for name, model in {
        "add": AddPrivacyRegion,
        "review-page": ReviewPrivacyPage,
        "remove": RemovePrivacyRegion,
        "confirm-request": ConfirmPrivacyReview,
    }.items():
        path = Path(f"examples/privacy-review-v1/{name}.json")
        raw = (tmp_path / path).read_text()
        assert (tmp_path / path).read_bytes() == (root / path).read_bytes()
        jsonschema.validate(json.loads(raw), {**schema, "$ref": f"#/$defs/{model.__name__}"})
        assert model.model_validate_json(raw)
