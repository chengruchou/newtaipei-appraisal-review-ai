"""Generate local synthetic PDFs and emit aggregate scan results, never original text."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from appraisal_review.adapters.local.privacy.ocr import TesseractConfig, TesseractOCR
from appraisal_review.adapters.local.privacy.process import ScanFailure
from appraisal_review.adapters.local.privacy.scanner import LocalPrivacyScanner
from appraisal_review.adapters.local.privacy.source import IsolatedPrivacyPDF, LocalSnapshotStore


def create_fixture(path: Path, kind: str) -> None:
    import pymupdf

    with pymupdf.open() as image_document:
        page = image_document.new_page(width=400, height=300)
        page.insert_text((35, 100), "synthetic@example.invalid", fontsize=18)
        page.insert_text((35, 140), "合成姓名", fontname="china-t", fontsize=18)
        raster = page.get_pixmap(dpi=144).tobytes("png")
    with pymupdf.open() as document:
        page = document.new_page(width=400, height=300)
        if kind in {"scanned", "mixed"}:
            page.insert_image(page.rect, stream=raster)
        if kind in {"native", "mixed"}:
            page.insert_text((35, 50), "Name: Synthetic Example", fontsize=14)
        document.save(path)


def smoke(work_directory: Path, ocr_config: Path | None = None) -> dict[str, object]:
    """Run only generated fixtures in a new ignored directory within this repository."""
    artifact_root = Path(__file__).resolve().parents[1] / "artifacts"
    work_directory = work_directory.resolve()
    if not work_directory.is_relative_to(artifact_root) or work_directory == artifact_root:
        raise ValueError("Smoke output must be a new repository artifact directory")
    config = TesseractConfig.model_validate_json(ocr_config.read_bytes()) if ocr_config else None
    work_directory.mkdir(parents=True, exist_ok=False)
    store = LocalSnapshotStore(work_directory, IsolatedPrivacyPDF(work_directory))
    ocr = TesseractOCR(config, work_directory) if config is not None else None
    scanner = LocalPrivacyScanner(store, ocr)
    results = []
    for kind in ("native", "scanned", "mixed"):
        path = work_directory / f"synthetic-{kind}.pdf"
        create_fixture(path, kind)
        before = hashlib.sha256(path.read_bytes()).digest()
        snapshot = store.capture(path.name, case_id=uuid4())
        report = asyncio.run(scanner.scan(snapshot))
        ocr_text = "".join(
            o.text for p in report.pages for o in p.observations if o.origin == "ocr"
        )
        canary = (
            None
            if kind == "native"
            else ("synthetic@example.invalid" in ocr_text and "合成姓名" in ocr_text)
        )
        results.append(
            {
                "fixture": kind,
                "status": report.status,
                "candidate_count": len(report.candidates),
                "issues": sorted({issue.value for p in report.pages for issue in p.issues}),
                "original_unchanged": before == hashlib.sha256(path.read_bytes()).digest(),
                "ocr_canary_passed": canary,
            }
        )
        store.forget(snapshot)
    return {"capabilities": scanner.capabilities().model_dump(mode="json"), "results": results}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--ocr-config", type=Path)
    args = parser.parse_args()
    try:
        report = smoke(args.work_dir, args.ocr_config)
    except (OSError, ValueError, ImportError, ScanFailure):
        print(json.dumps({"status": "blocked", "code": "privacy_smoke_failed"}))
        return 2
    print(json.dumps(report, indent=2))
    results = report["results"]
    assert isinstance(results, list)
    return (
        2
        if any(
            r["status"] == "blocked"
            or r["ocr_canary_passed"] is False
            or not r["original_unchanged"]
            for r in results
        )
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
