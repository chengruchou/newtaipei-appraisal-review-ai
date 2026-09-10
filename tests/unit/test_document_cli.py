"""CLI capability and actual local preparation checks without cloud credentials."""

import json
import runpy
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from appraisal_review.document_cli import bedrock_client, main


def test_parse_and_golden_commands_use_allowlisted_manifest(tmp_path, monkeypatch):
    import hashlib

    make = runpy.run_path(str(Path(__file__).with_name("test_pdf_parser.py")))["make_pdf"]
    docs = []
    for role in ["criteria", "forms"]:
        path = tmp_path / f"{role}.pdf"
        make(path)
        docs.append(
            {
                "path": str(path),
                "document_id": role,
                "version": "1",
                "role": role,
                "expected_hash": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "identity": {
                    "case_id": "synthetic",
                    "version": "1",
                    "district": "synthetic",
                    "zone": "synthetic",
                    "land_use_category": "synthetic",
                    "effective_date": "2026-09-05",
                },
                "documents": docs,
            }
        )
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["documents", "parse", "--manifest", str(manifest), "--output", str(tmp_path / "parsed")],
    )
    main()
    registry = json.loads((tmp_path / "parsed/registry.json").read_text())
    forms = next(d for d in registry["documents"] if d["role"] == "forms")
    region = next(
        r for r in forms["pages"][0]["regions"] if r["kind"] == "cell" and r["text"] == "18"
    )
    golden = tmp_path / "golden.json"
    golden.write_text(
        json.dumps(
            {
                "description": "Synthetic manual expectation",
                "fields": [
                    {
                        "name": "width",
                        "document_id": "forms",
                        "content_hash": forms["content_hash"],
                        "page": 1,
                        "region_id": region["id"],
                        "expected_text": "18",
                    }
                ],
            }
        )
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "documents",
            "check-golden",
            "--manifest",
            str(manifest),
            "--golden",
            str(golden),
            "--output",
            str(tmp_path / "metrics"),
        ],
    )
    main()
    metrics = json.loads((tmp_path / "metrics/golden-metrics.json").read_text())
    assert metrics["field_exact_match"] == {"correct": 1, "total": 1}
    assert metrics["selection_exact_match"] == {"correct": 0, "total": 0}


def test_legacy_client_factory_cannot_discover_credentials(monkeypatch):
    from argparse import Namespace

    from appraisal_review.ports.document_extraction import ExtractionBoundaryError

    boto = Mock()
    monkeypatch.setitem(sys.modules, "boto3", boto)
    with pytest.raises(ExtractionBoundaryError, match="privacy_unavailable"):
        bedrock_client(Namespace())
    boto.Session.assert_not_called()
