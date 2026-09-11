"""Real immutable C2 bytes and native parser, with no provider or cloud access."""

import asyncio
import json
import runpy
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from appraisal_review.adapters.document_extraction import DocumentSnapshotResolver
from appraisal_review.application.extraction import AuthorizedExtractionService
from appraisal_review.domain.extraction_contracts import PageRequest
from appraisal_review.ports.document_extraction import ExtractionBoundaryError

ROOT = Path(__file__).resolve().parents[2]
synthetic_source = runpy.run_path(str(ROOT / "cloud_tests/extraction_smoke.py"))["synthetic_source"]


@pytest.fixture
def admitted(tmp_path):
    return synthetic_source(tmp_path)


def test_real_two_page_chinese_snapshot_resolves_without_source_paths(admitted):
    documents, principal, requests = admitted
    resolver = DocumentSnapshotResolver(documents)
    snapshots = [asyncio.run(resolver.resolve(principal, r)) for r in requests]
    assert snapshots[0].content == snapshots[1].content
    source = snapshots[0].source
    assert len(source.pages) == 2
    assert "合成測試" in source.model_dump_json()
    assert "10" in source.pages[0].model_dump_json()
    assert "5" in source.pages[1].model_dump_json()
    assert source.uri.startswith("urn:document:")
    assert "/Users/" not in source.model_dump_json()
    assert all(p.number == i for i, p in enumerate(source.pages, 1))


@pytest.mark.parametrize("change", ["principal", "version", "digest", "manifest", "purpose", "run"])
def test_invalid_binding_never_calls_provider(admitted, change):
    documents, principal, requests = admitted
    data = requests[0].model_dump(mode="json")
    if change == "principal":
        principal = replace(principal, case_ids=frozenset())
    elif change == "version":
        data["source"]["document"]["version"] = "wrong-version"
    elif change == "digest":
        data["source"]["document"]["content_hash"] = "f" * 64
    elif change == "manifest":
        data["source"]["privacy_manifest_digest"] = "f" * 64
    elif change == "purpose":
        data["source"]["document"]["purpose"] = "criteria"
        data["context"]["task"] = "propose_rules"
    else:
        data["run"]["revision"]["material_digest"] = "f" * 64
    backend = Mock()
    service = AuthorizedExtractionService(
        resolver=DocumentSnapshotResolver(documents), backend=backend
    )
    with pytest.raises(ExtractionBoundaryError):
        asyncio.run(service.extract(principal, PageRequest.model_validate(data)))
    backend.extract.assert_not_called()


def test_revocation_after_parse_is_rechecked(admitted, monkeypatch):
    documents, principal, requests = admitted
    original = documents.read_snapshot
    calls = []

    def read(*args):
        calls.append(True)
        if len(calls) == 2:
            documents.authorization.grants = ()
        return original(*args)

    monkeypatch.setattr(documents, "read_snapshot", read)
    with pytest.raises(ExtractionBoundaryError, match="unauthorized_source"):
        asyncio.run(DocumentSnapshotResolver(documents).resolve(principal, requests[0]))
    assert len(calls) == 2


def test_probe_cli_is_dry_by_default_and_opt_in_is_fail_closed():
    command = [sys.executable, str(ROOT / "cloud_tests/extraction_smoke.py")]
    dry = subprocess.run(command, capture_output=True, text=True, check=True)
    assert json.loads(dry.stdout) == {
        "mode": "dry_run",
        "provider_calls": 0,
        "ready_for_live": False,
    }
    missing = subprocess.run([*command, "--execute"], capture_output=True, text=True)
    assert missing.returncode == 1
    assert json.loads(missing.stdout)["code"] == "extraction_probe_failed"
