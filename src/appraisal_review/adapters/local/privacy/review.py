"""Local preview and Linux controlling-terminal human confirmation adapters."""

from __future__ import annotations

import os
import secrets
import sys

from appraisal_review.adapters.local.approval import current_reviewer
from appraisal_review.adapters.local.privacy.source import LocalSnapshotStore
from appraisal_review.application.privacy_guards import PrivacyFault
from appraisal_review.domain.privacy_models import (
    LocalSourceSnapshot,
    PrivacyErrorCode,
    PrivacyReviewCommand,
    privacy_review_digest,
)
from appraisal_review.domain.privacy_review import PrivacyPagePreview


class LocalPrivacyPreviews:
    def __init__(self, store: LocalSnapshotStore) -> None:
        self._store = store

    def preview(self, source: LocalSourceSnapshot, page: int) -> PrivacyPagePreview:
        if type(page) is not int or not 1 <= page <= len(source.pages):
            raise PrivacyFault(PrivacyErrorCode.INVALID_INPUT)
        raster = self._store.pdf.render(
            self._store.read(source),
            page,
            timeout=self._store.pdf.limits.timeout,
        )
        return PrivacyPagePreview(raster.width, raster.height, raster.png)


class LinuxTerminalConfirmation:
    """Operator-owned process and OS account are trusted, including its terminal.

    No stdin/argument/environment approval fallback. This does not distinguish a
    human from software controlling the same trusted account or terminal. Never
    give an untrusted model process this account, terminal or composition access.
    """

    def __init__(self, *, owner_uid: int) -> None:
        if type(owner_uid) is not int or owner_uid < 0:
            raise ValueError("Explicit reviewer UID required")
        self._owner_uid = owner_uid

    def confirm(self, command: PrivacyReviewCommand) -> str | None:
        if sys.platform != "linux":
            raise PrivacyFault(PrivacyErrorCode.CAPABILITY_UNAVAILABLE)
        command = PrivacyReviewCommand.model_validate(command)
        reviewer = current_reviewer()
        if reviewer.uid != self._owner_uid:
            raise PrivacyFault(PrivacyErrorCode.UNAUTHORIZED)
        challenge = secrets.token_hex(8)
        # Separate non-seekable streams avoid TextIOWrapper read/write seeking.
        with (
            open("/dev/tty", encoding="utf-8") as reader,
            open("/dev/tty", "w", encoding="utf-8") as writer,
        ):
            if (
                not reader.isatty()
                or not writer.isatty()
                or (
                    os.tcgetpgrp(reader.fileno()) != os.getpgrp()
                    or os.tcgetpgrp(writer.fileno()) != os.getpgrp()
                )
            ):
                raise PrivacyFault(PrivacyErrorCode.UNAUTHORIZED)
            writer.write(
                f"Local privacy review: case {command.source.case_id}\n"
                f"Snapshot {command.source.snapshot_id}, revision {command.selection_revision}\n"
                f"Review digest {privacy_review_digest(command)}\n"
                f"Pages reviewed: {len(command.reviewed_pages)}/{len(command.source.pages)}\n"
                f"Regions: {len(command.selections)}\n"
                "Confirm only after inspecting all original page previews, every sensitive "
                "category, region, grouping and dismissal reason in the trusted local consumer.\n"
                f"To attest this exact review, type CONFIRM {challenge}: "
            )
            writer.flush()
            answer = reader.readline(128).strip()
        if answer != f"CONFIRM {challenge}" or current_reviewer() != reviewer:
            return None
        return f"posix-uid:{reviewer.uid}"
