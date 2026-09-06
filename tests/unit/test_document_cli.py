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


def test_extraction_capability_uses_explicit_identity_without_job_resources(monkeypatch):
    from argparse import Namespace

    boto = Mock()
    session = boto.Session.return_value
    sts, control, runtime = Mock(), Mock(), Mock()
    session.client.side_effect = lambda service, **kwargs: {
        "sts": sts,
        "bedrock": control,
        "bedrock-runtime": runtime,
    }[service]
    sts.get_caller_identity.return_value = {
        "Account": "123456789012",
        "Arn": "arn:aws:sts::123456789012:assumed-role/ProjectReviewer/session",
    }
    control.get_foundation_model.return_value = {
        "modelDetails": {"inputModalities": ["TEXT", "IMAGE"], "outputModalities": ["TEXT"]}
    }
    monkeypatch.setitem(sys.modules, "boto3", boto)
    # botocore is an optional runtime import and can be injected independently.
    monkeypatch.setitem(sys.modules, "botocore.config", Mock(Config=lambda **kwargs: kwargs))
    args = Namespace(
        profile="explicit-project",
        region="synthetic-region",
        expected_account="123456789012",
        expected_role="ProjectReviewer",
        model_id="synthetic-model",
        allow_cross_region=False,
        timeout=1,
    )
    assert bedrock_client(args) is runtime
    boto.Session.assert_called_once_with(
        profile_name="explicit-project", region_name="synthetic-region"
    )
    args.expected_account = "000000000000"
    with pytest.raises(PermissionError, match="account/role"):
        bedrock_client(args)
    args.expected_account = "123456789012"
    args.model_id = "global.synthetic-model"
    with pytest.raises(PermissionError, match="cross-region"):
        bedrock_client(args)
