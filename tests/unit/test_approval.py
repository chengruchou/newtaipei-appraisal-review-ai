"""Real local store and CLI execution using isolated synthetic review material."""

import json
import os
import subprocess
import sys

import pytest

from appraisal_review.adapters.local.approval import LocalApprovalStore, Reviewer, current_reviewer
from appraisal_review.adapters.local.synthetic import synthetic_material
from appraisal_review.domain.review_contracts import content_digest


def cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "appraisal_review.document_cli", *map(str, args)],
        capture_output=True,
        text=True,
    )


def test_cli_inspect_and_exact_approval_with_tamper_detection(tmp_path):
    material = synthetic_material()
    path, review, root = tmp_path / "material.json", tmp_path / "review.md", tmp_path / "store"
    path.write_text(material.model_dump_json())
    assert cli("inspect", "--material", path, "--output", review).returncode == 0
    assert content_digest(material) in review.read_text()
    assert cli("init-store", "--store", root).returncode == 0
    assert (
        cli("approve", "--material", path, "--store", root, "--expected-digest", "wrong").returncode
        != 0
    )
    assert (
        cli(
            "approve",
            "--material",
            path,
            "--store",
            root,
            "--expected-digest",
            content_digest(material),
        ).returncode
        == 0
    )
    store = LocalApprovalStore(root)
    assert store.permits(material)
    receipt_path = root / f"{content_digest(material)}.json"
    receipt = json.loads(receipt_path.read_text())
    assert receipt["reviewer"]["name"] == current_reviewer().name
    receipt["case_version"] = "forged"
    receipt_path.write_text(json.dumps(receipt))
    assert not store.permits(material)


@pytest.mark.parametrize("change", ["rule", "source", "case", "fact", "inventory"])
def test_other_material_cannot_reuse_receipt(tmp_path, change):
    material = synthetic_material()
    store = LocalApprovalStore.initialize(tmp_path / "store", current_reviewer())
    store.approve(material, expected_digest=content_digest(material))
    if change == "rule":
        material.policy.rule_sets[0].rules.version = "2"
    elif change == "source":
        material.policy.registry.documents[0].version = "2"
    elif change == "case":
        material.policy.identity.case_id = "other-case"
    elif change == "fact":
        material.facts.observed[0].value = "9"
    else:
        material.policy.inventory.inspected_pages = {}
    assert not store.permits(material)


def test_unauthorized_identity_permissions_and_unsigned_receipt_rejected(tmp_path):
    root = tmp_path / "store"
    with pytest.raises(PermissionError):
        LocalApprovalStore.initialize(
            root, Reviewer(uid=os.getuid() + 1, name="synthetic-reviewer")
        )
    store = LocalApprovalStore.initialize(root, current_reviewer())
    material = synthetic_material()
    receipt = root / f"{content_digest(material)}.json"
    receipt.write_text('{"approved": true, "reviewer": "synthetic-reviewer"}')
    receipt.chmod(0o600)
    assert not store.permits(material)
    (root / "signing.key").chmod(0o644)
    with pytest.raises(PermissionError):
        store.approve(material, expected_digest=content_digest(material))
    assert not store.permits(material)


def test_confirmation_is_explicit_and_does_not_clear_uncertainty(tmp_path):
    material = synthetic_material()
    path = tmp_path / "material.json"
    path.write_text(material.model_dump_json())
    assert (
        cli(
            "confirm-facts",
            "--material",
            path,
            "--output",
            tmp_path / "confirmed.json",
            "--expected-digest",
            content_digest(material),
        ).returncode
        == 0
    )
    material.facts.pairs[0].target_reliability.selection = "ambiguous"
    path.write_text(material.model_dump_json())
    assert (
        cli(
            "confirm-facts",
            "--material",
            path,
            "--output",
            tmp_path / "invalid.json",
            "--expected-digest",
            content_digest(material),
        ).returncode
        != 0
    )
    assert not (tmp_path / "invalid.json").exists()
