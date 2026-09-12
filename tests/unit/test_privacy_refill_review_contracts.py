"""The loopback OCR-review schema is generated from the exact local-only DTOs."""

import importlib.util
from pathlib import Path


def test_local_ocr_review_schema_matches_export(tmp_path):
    root = Path(__file__).resolve().parents[2]
    script = root / "scripts/export_privacy_refill_review_contracts.py"
    spec = importlib.util.spec_from_file_location("export_ocr_review", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.export(tmp_path)
    relative = "schemas/local-privacy-refill-review-v1.json"
    assert (tmp_path / relative).read_bytes() == (root / relative).read_bytes()
