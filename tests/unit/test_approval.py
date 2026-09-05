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


def test_old_receipt_is_not_reused_or_resigned_after_material_extension(tmp_path):
    import hashlib
    import hmac
    from datetime import UTC, datetime

    from appraisal_review.adapters.local.approval import ApprovalReceipt
    from appraisal_review.domain.factor_models import ReviewMaterial

    old = synthetic_material().model_dump(mode="json")
    for side in ("target", "comparable"):
        reliability = old["facts"]["pairs"][0][f"{side}_reliability"]
        for key in ("confidence_kind", "provenance", "producer", "confirmation"):
            reliability.pop(key)
        reliability["method"] = "reviewer_confirmed"
    old_digest = hashlib.sha256(
        json.dumps(old, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    store = LocalApprovalStore.initialize(tmp_path / "store", current_reviewer())
    # Construct a historical receipt only in this isolated synthetic test store.
    receipt = ApprovalReceipt(
        material_digest=old_digest,
        case_id=old["policy"]["identity"]["case_id"],
        case_version="1",
        reviewer=current_reviewer(),
        approved_at=datetime.now(UTC),
        signature="",
    )
    receipt.signature = hmac.new(
        (store.root / "signing.key").read_bytes(), receipt.signed_bytes(), hashlib.sha256
    ).hexdigest()
    store._write_new(f"{old_digest}.json", receipt.model_dump_json().encode())
    before = {p.name: p.read_bytes() for p in store.root.iterdir()}
    loaded = ReviewMaterial.model_validate(old)
    assert content_digest(loaded) != old_digest
    assert not store.permits(loaded)
    path, output = tmp_path / "old.json", tmp_path / "confirmed.json"
    path.write_text(json.dumps(old))
    result = cli(
        "confirm-facts",
        "--material",
        path,
        "--output",
        output,
        "--expected-digest",
        content_digest(loaded),
    )
    assert result.returncode != 0 and not output.exists()
    assert {p.name: p.read_bytes() for p in store.root.iterdir()} == before


def test_store_rejects_method_only_or_other_reviewer_confirmation(tmp_path):
    from appraisal_review.domain.confidence import confirm_side

    material = synthetic_material()
    store = LocalApprovalStore.initialize(tmp_path / "store", current_reviewer())
    material.facts.pairs[0].target_reliability.method = "reviewer_confirmed"
    with pytest.raises(ValueError, match="reviewer confirmation"):
        store.approve(material, expected_digest=content_digest(material))
    confirm_side(material.facts.pairs[0], "target", reviewer="other-reviewer")
    with pytest.raises(ValueError, match="reviewer confirmation"):
        store.approve(material, expected_digest=content_digest(material))
    assert not store.permits(material)
