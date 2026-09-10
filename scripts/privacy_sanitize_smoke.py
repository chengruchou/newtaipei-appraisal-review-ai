"""Synthetic raster/OCR smoke only; never issue human approval or export a bundle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import uuid4

import pymupdf

from appraisal_review.adapters.local.privacy.ocr import TesseractConfig, TesseractOCR
from appraisal_review.adapters.local.privacy.sanitize import (
    IsolatedPrivacyRasterProcessor,
    TesseractPrivacyOutputOCR,
)
from appraisal_review.adapters.local.privacy.source import IsolatedPrivacyPDF, LocalSnapshotStore
from appraisal_review.application.privacy_bundle import LocalSanitizedVerifier
from appraisal_review.application.privacy_guards import PrivacyFault
from appraisal_review.domain.privacy_models import (
    PrivacyReviewCommand,
    ReviewSelection,
    SensitiveCandidate,
    SensitiveCategory,
)


def smoke(output: Path, ocr_config: Path | None = None) -> dict[str, object]:
    artifacts = (Path(__file__).resolve().parents[1] / "artifacts").resolve()
    output = output.resolve()
    if not output.is_relative_to(artifacts) or output == artifacts or output.exists():
        raise ValueError("A fresh repository artifact directory is required")
    output.mkdir(parents=True)
    path = output / "synthetic-source.pdf"
    with pymupdf.open() as document:
        page = document.new_page(width=320, height=420)
        page.insert_text((35, 60), "CANARY-PRIVATE-1234", fontsize=12)
        page.insert_text((35, 180), "Rate 1.25%  Total 987654.32", fontsize=12)
        page.draw_rect(pymupdf.Rect(25, 155, 290, 210))
        document.set_metadata({"title": "CANARY-PRIVATE-1234"})
        document.embfile_add("private.txt", b"CANARY-PRIVATE-1234")
        document.save(path)
    store = LocalSnapshotStore(output, IsolatedPrivacyPDF(output))
    source = store.capture(path.name, case_id=uuid4())
    original = store.read(source)
    observation = store.inspection(source).pages[0].observations[0]
    command = PrivacyReviewCommand(
        source=source,
        selection_revision=1,
        policy_digest="b" * 64,
        reviewed_pages=(1,),
        selections=(
            ReviewSelection(
                disposition="redact",
                entity_id=uuid4(),
                candidate=SensitiveCandidate(
                    candidate_id=uuid4(),
                    category=SensitiveCategory.NAME,
                    region=observation.region,
                    raw_text=observation.text,
                    detector_id="synthetic-fixture",
                    detector_version="1",
                ),
            ),
        ),
    )
    processor = IsolatedPrivacyRasterProcessor(output)
    draft = processor.build(command, original)
    previews = processor.verify(command, original, draft)
    # Explicit synthetic QA artifacts; the draft is not an authorized bundle.
    (output / "synthetic-draft.pdf").write_bytes(draft.pdf)
    (output / "synthetic-preview.png").write_bytes(previews[0].png)
    verified = False
    if ocr_config is not None:
        config = TesseractConfig.model_validate_json(ocr_config.read_bytes())
        verifier = LocalSanitizedVerifier(
            processor, TesseractPrivacyOutputOCR(TesseractOCR(config, output))
        )
        try:
            verifier.verify(command, original, draft)
            verified = True
        except PrivacyFault:
            pass
    result: dict[str, object] = {
        "mode": "synthetic-no-human",
        "structure_pixels_verified": True,
        "ocr_verified": verified,
        "original_unchanged": path.read_bytes() == original,
        "bundle_emitted": False,
    }
    (output / "report.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ocr-config", type=Path)
    args = parser.parse_args()
    result = smoke(args.output, args.ocr_config)
    print(json.dumps(result))
    raise SystemExit(0 if result["ocr_verified"] and result["original_unchanged"] else 2)
