"""Synthetic local SDK integration harness; deliberately cannot issue approval."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from uuid import uuid4

import pymupdf

from appraisal_review.adapters.local.privacy.review import LocalPrivacyPreviews
from appraisal_review.adapters.local.privacy.scanner import LocalPrivacyScanner
from appraisal_review.adapters.local.privacy.source import IsolatedPrivacyPDF, LocalSnapshotStore
from appraisal_review.application.privacy_guards import PrivacyFault
from appraisal_review.application.privacy_review import LocalPrivacyReviewService
from appraisal_review.domain.privacy_models import PrivacyReviewCommand, privacy_review_digest
from appraisal_review.domain.privacy_review import ConfirmPrivacyReview, ReviewPrivacyPage


class NoHumanInHarness:
    def confirm(self, command: PrivacyReviewCommand) -> None:
        return None


async def run(root: Path) -> dict[str, str | int | bool]:
    artifacts = Path(__file__).resolve().parents[1] / "artifacts"
    root = root.resolve()
    if not root.is_relative_to(artifacts.resolve()) or root == artifacts.resolve() or root.exists():
        raise ValueError("A fresh repository artifacts subdirectory is required")
    root.mkdir(parents=True)
    original = root / "synthetic.pdf"
    with pymupdf.open() as document:
        page = document.new_page(width=300, height=400)
        page.insert_text((30, 60), "Synthetic: demo@example.invalid")
        document.save(original)
    original_bytes = original.read_bytes()
    store = LocalSnapshotStore(root, IsolatedPrivacyPDF(root))
    source = store.capture(original.name, case_id=uuid4())
    service = LocalPrivacyReviewService(
        scanner=LocalPrivacyScanner(store),
        sources=store,
        previews=LocalPrivacyPreviews(store),
        human=NoHumanInHarness(),
    )
    view = await service.rescan(source, policy_digest="b" * 64)
    request = ReviewPrivacyPage(
        case_id=source.case_id,
        snapshot_id=source.snapshot_id,
        revision=view.command.selection_revision,
        page=1,
    )
    preview = service.preview(request)
    view = service.review_page(request)
    denied = False
    try:
        service.confirm(
            ConfirmPrivacyReview(
                case_id=source.case_id,
                snapshot_id=source.snapshot_id,
                revision=view.command.selection_revision,
                review_digest=privacy_review_digest(view.command),
            )
        )
    except PrivacyFault as error:
        denied = str(error) == "privacy_unauthorized"
    result: dict[str, str | int | bool] = {
        "mode": "synthetic-no-human",
        "state": service.list().state,
        "candidate_count": len(view.command.selections),
        "preview_png": preview.png.startswith(b"\x89PNG\r\n\x1a\n"),
        "original_unchanged": original.read_bytes() == original_bytes,
        "confirmation_denied": denied,
    }
    (root / "report.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(run(args.output))
    print(json.dumps(result))
    raise SystemExit(
        0
        if all(
            result[k] is True
            for k in (
                "preview_png",
                "original_unchanged",
                "confirmation_denied",
            )
        )
        else 2
    )
