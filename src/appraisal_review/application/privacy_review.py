"""Ephemeral local human review service. Never mount this service in a cloud app.

Composition, scanner, source reader, previewer, clock and human prompt are trusted.
Consumer DTOs cannot replace any of them or submit a principal/approval record.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import wraps
from threading import RLock
from typing import Literal, ParamSpec, TypeVar
from uuid import UUID, uuid4

from appraisal_review.application.privacy_guards import PrivacyFault
from appraisal_review.domain.privacy_models import (
    LocalPrivacyApproval,
    LocalSourceSnapshot,
    PrivacyErrorCode,
    PrivacyReviewCommand,
    ReviewSelection,
    SensitiveCandidate,
    privacy_review_digest,
    validate_region,
)
from appraisal_review.domain.privacy_review import (
    AddPrivacyRegion,
    ConfirmPrivacyReview,
    EditPrivacyRegion,
    PrivacyCropReference,
    PrivacyCropRequest,
    PrivacyPagePreview,
    PrivacyReviewEvent,
    PrivacyReviewView,
    RemovePrivacyRegion,
    ReviewPrivacyPage,
    ReviewVersion,
)
from appraisal_review.domain.privacy_scan import PrivacyScanReport
from appraisal_review.ports.privacy import (
    LocalPrivacyScan,
    LocalSnapshotReader,
    PrivacyHumanConfirmation,
    PrivacyPreviewProvider,
)

P = ParamSpec("P")
T = TypeVar("T")


def _safe_input(function: Callable[P, T]) -> Callable[P, T]:
    """Do not expose Pydantic input excerpts in service exceptions."""

    @wraps(function)
    def call(*args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return function(*args, **kwargs)
        except ValueError:
            raise PrivacyFault(PrivacyErrorCode.INVALID_INPUT) from None

    return call


class LocalPrivacyReviewService:
    """One active source per instance; all state is lost on process exit.

    Use one instance per trusted local review session. There is no HTTP, receipt
    import, persistent credential, model tool registration or upload method.
    """

    def __init__(
        self,
        *,
        scanner: LocalPrivacyScan,
        sources: LocalSnapshotReader,
        previews: PrivacyPreviewProvider,
        human: PrivacyHumanConfirmation,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._scanner = scanner
        self._sources = sources
        self._previews = previews
        self._human = human
        self._clock = clock
        self._lock = RLock()
        self._command: PrivacyReviewCommand | None = None
        self._scan: PrivacyScanReport | None = None
        self._approval: LocalPrivacyApproval | None = None
        self._events: list[PrivacyReviewEvent] = []
        self._previewed: set[int] = set()
        self._revision = 0
        self._confirmation_attempt: object | None = None

    def _current(self) -> PrivacyReviewCommand:
        if self._command is None:
            raise PrivacyFault(PrivacyErrorCode.INVALID_INPUT)
        return self._command

    def close(self) -> None:
        """Revoke and release review references; source store lifetime is separate."""
        with self._lock:
            self._command = None
            self._scan = None
            self._approval = None
            self._confirmation_attempt = None
            self._events.clear()
            self._previewed.clear()

    def _check(self, request: ReviewVersion) -> PrivacyReviewCommand:
        command = self._current()
        if (
            request.case_id != command.source.case_id
            or request.snapshot_id != command.source.snapshot_id
            or request.revision != command.selection_revision
        ):
            raise PrivacyFault(PrivacyErrorCode.STALE_CONFIRMATION)
        return command

    def _ready(self) -> None:
        if self._scan is None or self._scan.status == "blocked":
            raise PrivacyFault(PrivacyErrorCode.CAPABILITY_UNAVAILABLE)

    async def rescan(self, source: LocalSourceSnapshot, *, policy_digest: str) -> PrivacyReviewView:
        """Also used for source/policy replacement; invalidate before asynchronous work."""
        with self._lock:
            self._revision += 1
            self._approval = None
            self._confirmation_attempt = None
            self._scan = None
            self._command = None
            self._previewed.clear()
            try:
                command = PrivacyReviewCommand(
                    source=source,
                    policy_digest=policy_digest,
                    selection_revision=self._revision,
                    selections=(),
                    reviewed_pages=(),
                )
                self._sources.read(command.source)
            except Exception:
                raise PrivacyFault(PrivacyErrorCode.INVALID_INPUT) from None
            self._command = command
            self._events = [PrivacyReviewEvent(revision=command.selection_revision, action="scan")]
        try:
            scan = PrivacyScanReport.model_validate(await self._scanner.scan(command.source))
            if scan.source != command.source:
                raise ValueError("Different source")
        except Exception:
            raise PrivacyFault(PrivacyErrorCode.CAPABILITY_UNAVAILABLE) from None
        with self._lock:
            if self._command is not command:
                raise PrivacyFault(PrivacyErrorCode.STALE_CONFIRMATION)
            self._scan = scan
            self._command = PrivacyReviewCommand(
                **command.model_dump(exclude={"selections"}),
                selections=tuple(
                    ReviewSelection(candidate=c, disposition="redact", entity_id=uuid4())
                    for c in scan.candidates
                ),
            )
            return self.list()

    def list(self) -> PrivacyReviewView:
        with self._lock:
            command = self._current()
            state: Literal["blocked", "awaiting_confirmation", "confirmed"] = (
                "blocked"
                if self._scan is None or self._scan.status == "blocked"
                else (
                    "confirmed"
                    if self._approval is not None
                    and self.permits(self._approval, command, now=self._clock())
                    else "awaiting_confirmation"
                )
            )
            return PrivacyReviewView(
                state=state, command=command, scan=self._scan, events=tuple(self._events)
            )

    @_safe_input
    def preview(self, request: ReviewPrivacyPage) -> PrivacyPagePreview:
        request = ReviewPrivacyPage.model_validate(request)
        with self._lock:
            command = self._check(request)
            if request.page > len(command.source.pages):
                raise PrivacyFault(PrivacyErrorCode.INVALID_INPUT)
            try:
                self._sources.read(command.source)
                preview = self._previews.preview(command.source, request.page)
            except Exception:
                raise PrivacyFault(PrivacyErrorCode.CAPABILITY_UNAVAILABLE) from None
            self._previewed.add(request.page)
            return preview

    @_safe_input
    def crop(self, request: PrivacyCropRequest) -> PrivacyCropReference:
        request = PrivacyCropRequest.model_validate(request)
        with self._lock:
            command = self._check(request)
            for selection in command.selections:
                if selection.candidate.crop_id == request.crop_id:
                    self._sources.read(command.source)
                    return PrivacyCropReference(
                        crop_id=request.crop_id,
                        source=command.source,
                        region=selection.candidate.region,
                    )
            raise PrivacyFault(PrivacyErrorCode.INVALID_INPUT)

    def _change(
        self,
        selections: tuple[ReviewSelection, ...],
        event: PrivacyReviewEvent,
        *,
        reviewed_pages: tuple[int, ...] = (),
        clear_previews: bool = True,
    ) -> PrivacyReviewView:
        if len(self._events) >= 1000 or len(selections) > 10000:
            raise PrivacyFault(PrivacyErrorCode.CAPABILITY_UNAVAILABLE)
        command = self._current()
        self._revision += 1
        self._command = PrivacyReviewCommand(
            source=command.source,
            policy_digest=command.policy_digest,
            selection_revision=self._revision,
            selections=selections,
            reviewed_pages=reviewed_pages,
        )
        self._approval = None
        if clear_previews:
            self._previewed.clear()
        self._events.append(event)
        return self.list()

    @_safe_input
    def add(self, request: AddPrivacyRegion) -> PrivacyReviewView:
        request = AddPrivacyRegion.model_validate(request)
        with self._lock:
            command = self._check(request)
            validate_region(request.region, command.source.pages)
            selection = ReviewSelection(
                candidate=SensitiveCandidate(
                    candidate_id=uuid4(),
                    category=request.category,
                    region=request.region,
                    crop_id=uuid4(),
                    detector_id="local-manual-region",
                    detector_version="1",
                ),
                disposition="redact",
                entity_id=uuid4(),
            )
            return self._change(
                (*command.selections, selection),
                PrivacyReviewEvent(
                    revision=command.selection_revision + 1,
                    action="add",
                    after=selection,
                ),
            )

    def _selection(self, candidate_id: UUID) -> ReviewSelection:
        for selection in self._current().selections:
            if selection.candidate.candidate_id == candidate_id:
                return selection
        raise PrivacyFault(PrivacyErrorCode.INVALID_INPUT)

    @_safe_input
    def edit(self, request: EditPrivacyRegion) -> PrivacyReviewView:
        """Preserve known evidence when only the category or entity assignment changes."""
        request = EditPrivacyRegion.model_validate(request)
        with self._lock:
            command = self._check(request)
            before = self._selection(request.candidate_id)
            validate_region(request.region, command.source.pages)
            entity = request.entity_id or uuid4()
            if request.entity_id is not None and entity not in {
                s.entity_id for s in command.selections if s.disposition == "redact"
            }:
                raise PrivacyFault(PrivacyErrorCode.INVALID_INPUT)
            if request.region == before.candidate.region:
                candidate = before.candidate.model_copy(update={"category": request.category})
            else:
                # Text from the old geometry cannot authenticate a newly selected crop.
                candidate = SensitiveCandidate(
                    candidate_id=before.candidate.candidate_id,
                    region=request.region,
                    category=request.category,
                    crop_id=uuid4(),
                    detector_id="local-manual-region",
                    detector_version="1",
                )
            after = ReviewSelection(candidate=candidate, disposition="redact", entity_id=entity)
            return self._replace(before, after, "edit")

    @_safe_input
    def remove(self, request: RemovePrivacyRegion) -> PrivacyReviewView:
        """Dismiss with a reason, retaining the detection and its evidence."""
        request = RemovePrivacyRegion.model_validate(request)
        with self._lock:
            self._check(request)
            if not request.reason.strip():
                raise PrivacyFault(PrivacyErrorCode.INVALID_INPUT)
            before = self._selection(request.candidate_id)
            after = ReviewSelection(
                candidate=before.candidate,
                disposition="dismiss",
                dismissal_reason=request.reason,
            )
            return self._replace(before, after, "remove")

    def _replace(
        self,
        before: ReviewSelection,
        after: ReviewSelection,
        action: Literal["edit", "remove"],
    ) -> PrivacyReviewView:
        command = self._current()
        return self._change(
            tuple(
                after if s.candidate.candidate_id == before.candidate.candidate_id else s
                for s in command.selections
            ),
            PrivacyReviewEvent(
                revision=command.selection_revision + 1,
                action=action,
                before=before,
                after=after,
            ),
        )

    @_safe_input
    def review_page(self, request: ReviewPrivacyPage) -> PrivacyReviewView:
        request = ReviewPrivacyPage.model_validate(request)
        with self._lock:
            command = self._check(request)
            if request.page not in self._previewed:
                raise PrivacyFault(PrivacyErrorCode.INVALID_INPUT)
            return self._change(
                command.selections,
                PrivacyReviewEvent(
                    revision=command.selection_revision + 1,
                    action="review_page",
                    page=request.page,
                ),
                reviewed_pages=tuple(sorted(set(command.reviewed_pages) | {request.page})),
                clear_previews=False,
            )

    @_safe_input
    def confirm(self, request: ConfirmPrivacyReview) -> LocalPrivacyApproval:
        request = ConfirmPrivacyReview.model_validate(request)
        with self._lock:
            command = self._check(request)
            self._ready()
            if request.review_digest != privacy_review_digest(
                command
            ) or command.reviewed_pages != (tuple(p.number for p in command.source.pages)):
                raise PrivacyFault(PrivacyErrorCode.STALE_CONFIRMATION)
            self._approval = None
            attempt = object()
            self._confirmation_attempt = attempt
        try:
            principal = self._human.confirm(command)
            if principal is None or not principal.strip():
                raise ValueError("No human authorization")
        except Exception:
            raise PrivacyFault(PrivacyErrorCode.UNAUTHORIZED) from None
        with self._lock:
            if self._command is not command or self._confirmation_attempt is not attempt:
                raise PrivacyFault(PrivacyErrorCode.STALE_CONFIRMATION)
            self._ready()
            try:
                self._sources.read(command.source)
                now = self._clock()
                approval = LocalPrivacyApproval(
                    approval_id=uuid4(),
                    case_id=command.source.case_id,
                    snapshot_id=command.source.snapshot_id,
                    review_digest=privacy_review_digest(command),
                    principal_id=principal,
                    approved_at=now,
                    expires_at=now + timedelta(minutes=15),
                )
            except Exception:
                raise PrivacyFault(PrivacyErrorCode.UNAUTHORIZED) from None
            self._approval = approval
            return approval

    def permits(
        self,
        approval: LocalPrivacyApproval,
        command: PrivacyReviewCommand,
        *,
        now: datetime,
    ) -> bool:
        with self._lock:
            try:
                approval = LocalPrivacyApproval.model_validate(approval)
                command = PrivacyReviewCommand.model_validate(command)
                self._ready()
                self._sources.read(command.source)
                return (
                    self._approval == approval
                    and self._command == command
                    and approval.review_digest == privacy_review_digest(command)
                    and approval.approved_at <= now < approval.expires_at
                    and approval.approved_at <= self._clock() < approval.expires_at
                )
            except Exception:
                return False
