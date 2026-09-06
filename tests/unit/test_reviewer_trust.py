"""Isolated receipts and subprocesses test reviewer eligibility and platforms."""

import subprocess
import sys

import pytest

from appraisal_review.adapters.local.approval import LocalApprovalStore, current_reviewer
from appraisal_review.adapters.local.synthetic import synthetic_material
from appraisal_review.domain.confidence import confirm_side
from appraisal_review.domain.review_contracts import content_digest


def proposed_material():
    material = synthetic_material()
    for side in ("target", "comparable"):
        reliability = getattr(material.facts.pairs[0], f"{side}_reliability")
        reliability.method, reliability.confidence_kind, reliability.provenance = (
            "model_proposed",
            "localization_only",
            "parser_registry",
        )
        reliability.producer = "canonical-pdf-localization-v1"
        observation = getattr(material.facts.pairs[0].pair, side)
        observation.confidence = observation.evidence[0].confidence = 0
    return material


@pytest.mark.parametrize("confirmed", [[], ["target"]])
def test_every_side_must_be_eligible_before_any_receipt_is_created(tmp_path, confirmed):
    material = proposed_material()
    reviewer = current_reviewer()
    for side in confirmed:
        confirm_side(material.facts.pairs[0], side, reviewer=f"{reviewer.uid}:{reviewer.name}")
    store = LocalApprovalStore.initialize(tmp_path / "store", reviewer)
    before = {p.name: p.read_bytes() for p in store.root.iterdir()}
    with pytest.raises(ValueError, match="confirmation"):
        store.approve(material, expected_digest=content_digest(material))
    assert not store.permits(material)
    assert {p.name: p.read_bytes() for p in store.root.iterdir()} == before


def without_pwd(args):
    script = """
import sys, runpy
class MissingPwd:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'pwd':
            raise ModuleNotFoundError("No module named 'pwd'")
sys.modules.pop('pwd', None)
sys.meta_path.insert(0, MissingPwd())
sys.argv = ['documents', *sys.argv[1:]]
runpy.run_module('appraisal_review.document_cli', run_name='__main__')
"""
    return subprocess.run(
        [sys.executable, "-c", script, *map(str, args)], capture_output=True, text=True
    )


def test_cli_help_does_not_require_posix_reviewer_imports():
    result = without_pwd(["--help"])
    assert result.returncode == 0 and "confirm-facts" in result.stdout
    assert "Traceback" not in result.stderr


def test_unsupported_reviewer_operation_fails_cleanly_before_side_effects(tmp_path):
    root = tmp_path / "store"
    result = without_pwd(["init-store", "--store", root])
    assert result.returncode != 0
    assert "unsupported_reviewer_platform" in result.stderr and "Traceback" not in result.stderr
    assert not root.exists()


def both_confirmed(material):
    reviewer = current_reviewer()
    for pair in material.facts.pairs:
        for side in ("target", "comparable"):
            confirm_side(pair, side, reviewer=f"{reviewer.uid}:{reviewer.name}")
    return material


@pytest.mark.parametrize(
    "problem", ["multi-pair", "method-only", "unknown", "stale", "reviewer", "changed-method"]
)
def test_receipt_eligibility_fails_closed_for_every_pair_and_both_sides(tmp_path, problem):
    material = both_confirmed(proposed_material())
    pair = material.facts.pairs[0]
    if problem == "multi-pair":
        second = pair.model_copy(deep=True)
        second.context.comparable_id = "second"
        material.facts.pairs.append(second)
        both_confirmed(material)
        second.comparable_reliability.method = "model_proposed"
        second.comparable_reliability.confirmation = None
    elif problem == "method-only":
        pair.comparable_reliability.confirmation = None
    elif problem == "unknown":
        pair.comparable_reliability.provenance = "unknown"
    elif problem == "stale":
        pair.pair.comparable.confidence = 0.5
    elif problem == "reviewer":
        pair.comparable_reliability.confirmation.reviewer = "other"
    else:
        pair.comparable_reliability.method = "native_numeric"
        pair.comparable_reliability.confirmation = None
    store = LocalApprovalStore.initialize(tmp_path / "store", current_reviewer())
    before = {p.name: p.read_bytes() for p in store.root.iterdir()}
    with pytest.raises(ValueError, match="confirmation"):
        store.approve(material, expected_digest=content_digest(material))
    assert not store.permits(material)
    assert {p.name: p.read_bytes() for p in store.root.iterdir()} == before


@pytest.mark.parametrize("native", [False, True])
def test_eligible_confirmation_and_native_material_remain_usable(tmp_path, native):
    material = synthetic_material() if native else both_confirmed(proposed_material())
    store = LocalApprovalStore.initialize(tmp_path / "store", current_reviewer())
    before = material.model_dump_json()
    store.approve(material, expected_digest=content_digest(material))
    assert store.permits(material)
    assert material.model_dump_json() == before
    if native:
        # Receipt eligibility does not invent a second confidence threshold.
        material.facts.pairs[0].pair.target.evidence[0].confidence = 0.1
        store.approve(material, expected_digest=content_digest(material))
        assert store.permits(material)
        from appraisal_review.domain.case_review import CaseReviewer

        result = CaseReviewer(store, minimum_confidence=0.95).review(
            material.policy, material.facts, material.policy.registry
        )
        assert result.status.value == "needs_review"


@pytest.mark.parametrize("partial", [False, True])
def test_historical_signed_unconfirmed_receipt_is_rejected_without_rewriting(tmp_path, partial):
    import hashlib
    import hmac
    from datetime import UTC, datetime

    from appraisal_review.adapters.local.approval import ApprovalReceipt

    material = proposed_material()
    reviewer = current_reviewer()
    if partial:
        confirm_side(material.facts.pairs[0], "target", reviewer=f"{reviewer.uid}:{reviewer.name}")
    store = LocalApprovalStore.initialize(tmp_path / "store", reviewer)
    digest = content_digest(material)
    receipt = ApprovalReceipt(
        material_digest=digest,
        case_id=material.policy.identity.case_id,
        case_version=material.policy.identity.version,
        reviewer=reviewer,
        approved_at=datetime.now(UTC),
        signature="",
    )
    # Only this test's isolated key signs a receipt admitted by the previous code.
    receipt.signature = hmac.new(
        (store.root / "signing.key").read_bytes(), receipt.signed_bytes(), hashlib.sha256
    ).hexdigest()
    store._write_new(f"{digest}.json", receipt.model_dump_json().encode())
    before = {p.name: p.read_bytes() for p in store.root.iterdir()}
    assert not store.permits(material)
    assert {p.name: p.read_bytes() for p in store.root.iterdir()} == before


@pytest.mark.parametrize("command", ["confirm-facts", "approve", "review"])
def test_platform_check_precedes_material_access_or_confirmation(tmp_path, command):
    args = [command, "--material", tmp_path / "missing.json"]
    if command in {"confirm-facts", "approve"}:
        args += ["--expected-digest", "synthetic"]
    if command in {"approve", "review"}:
        args += ["--store", tmp_path / "store"]
    if command in {"confirm-facts", "review"}:
        args += ["--output", tmp_path / "output.json"]
    result = without_pwd(args)
    assert result.returncode == 2 and "unsupported_reviewer_platform" in result.stderr
    assert "Traceback" not in result.stderr
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("mode", ["platform", "getuid"])
def test_unsupported_platform_or_missing_identity_never_falls_back(tmp_path, mode):
    script = """
import os, sys
from appraisal_review.document_cli import main
mode, root = sys.argv[1:]
if mode == 'platform':
    sys.platform = 'win32'
else:
    del os.getuid
sys.argv = ['documents', 'init-store', '--store', root]
main()
"""
    result = subprocess.run(
        [sys.executable, "-c", script, mode, str(tmp_path / "store")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2 and "unsupported_reviewer_platform" in result.stderr
    assert "Traceback" not in result.stderr and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "uri",
    [
        "file:///C:/Users/reviewer/input.pdf",
        "file://server/share/input.pdf",
        "file:////server/share/input.pdf",
        "file:///C%3A/Users/input.pdf",
        "file:///C:%5CUsers%5Cinput.pdf",
    ],
)
def test_local_uri_helper_rejects_windows_drive_and_unc_paths(uri):
    from appraisal_review.document_cli import _file_path

    with pytest.raises(ValueError, match="unsupported_local_file_uri"):
        _file_path(uri)


def test_posix_identity_permissions_and_uri_remain_verified_on_host(tmp_path):
    import os
    import stat

    from appraisal_review.document_cli import _file_path

    reviewer = current_reviewer()
    assert reviewer.uid == os.getuid()
    store = LocalApprovalStore.initialize(tmp_path / "store", reviewer)
    assert stat.S_IMODE(store.root.stat().st_mode) == 0o700
    assert all(
        stat.S_IMODE(p.stat().st_mode) == 0o600 and p.stat().st_uid == reviewer.uid
        for p in store.root.iterdir()
    )
    assert (
        _file_path("file://localhost/synthetic/some%20file.pdf").as_posix()
        == "/synthetic/some file.pdf"
    )
