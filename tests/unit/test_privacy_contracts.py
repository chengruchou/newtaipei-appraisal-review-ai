"""Synthetic contract and pure-admission tests; no OCR or real authority adapters."""

from __future__ import annotations

import json
import runpy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import jsonschema
import pytest
from pydantic import ValidationError

from appraisal_review.application.privacy_guards import PrivacyFault, check_export_admission
from appraisal_review.domain.privacy_models import (
    EncryptedMappingEnvelope,
    LocalPrivacyApproval,
    PrivacyManifest,
    PrivacyRegion,
    PrivacyReviewCommand,
    RehydrationPlan,
    placeholder_text,
    privacy_review_digest,
    public_manifest_json,
)
from appraisal_review.ports.privacy import PrivacyApprovalAuthority, SanitizedArtifactVerifier

ROOT = Path(__file__).resolve().parents[2]
EXPORT = runpy.run_path(str(ROOT / "scripts/export_privacy_contracts.py"))
NOW = datetime(2026, 9, 10, 0, 30, tzinfo=UTC)


@pytest.fixture
def values():
    return EXPORT["fixtures"]()


def test_schema_and_synthetic_consumers_roundtrip(tmp_path):
    EXPORT["export"](tmp_path)
    for path in (tmp_path / "schemas").glob("*privacy-v1.json"):
        assert path.read_bytes() == (ROOT / "schemas" / path.name).read_bytes()
        jsonschema.Draft202012Validator.check_schema(json.loads(path.read_text()))
    index_path = Path("examples/privacy-v1/index.json")
    assert (tmp_path / index_path).read_bytes() == (ROOT / index_path).read_bytes()
    index = json.loads((tmp_path / index_path).read_text())
    models = {m.__name__: m for m in (*EXPORT["LOCAL_MODELS"], *EXPORT["PUBLIC_MODELS"])}
    for relative, model_name in index.items():
        path = Path("examples/privacy-v1") / relative
        assert (tmp_path / path).read_bytes() == (ROOT / path).read_bytes()
        raw = (tmp_path / path).read_text()
        model = models[model_name].model_validate_json(raw)
        assert model.model_dump(mode="json") == json.loads(raw)
        schema_name = "local-privacy-v1" if relative.startswith("local/") else "privacy-v1"
        schema = json.loads((tmp_path / f"schemas/{schema_name}.json").read_text())
        jsonschema.validate(json.loads(raw), {**schema, "$ref": f"#/$defs/{model_name}"})
    public = (tmp_path / "schemas/privacy-v1.json").read_text()
    for forbidden in ("source_digest", "raw_text", "principal_id", "key_reference"):
        assert forbidden not in public
    assert not any(name.startswith("Local") for name in json.loads(public)["$defs"])


@pytest.mark.parametrize("field", ["verified", "state", "actor", "approved", "source_file", "uri"])
def test_consumer_cannot_assert_authority_or_choose_path(values, field):
    raw = values["review-command.json"].model_dump(mode="json")
    raw[field] = "verified"
    with pytest.raises(ValidationError):
        PrivacyReviewCommand.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("filename", "../original.pdf"),
        ("filename", "private-name.pdf"),
        ("schema_version", "privacy-v2"),
        ("byte_size", True),
        ("byte_size", "100"),
        ("case_id", "../../case"),
        ("case_id", "00000000-0000-1000-8000-000000000001"),
        ("sanitized_digest", "invalid"),
        ("source_digest", "a" * 64),
        ("raw_text", "SYNTHETIC-LOCAL-ONLY-VALUE"),
    ],
)
def test_public_manifest_rejects_non_allowlisted_input(values, field, value):
    raw = values["manifest.json"].model_dump(mode="json")
    raw[field] = value
    with pytest.raises(ValidationError):
        PrivacyManifest.model_validate_json(json.dumps(raw))


def test_public_serializer_does_not_accept_local_objects_or_subclasses(values):
    for name in ("source.json", "candidate.json", "review-command.json", "mapping-envelope.json"):
        with pytest.raises(ValueError, match="Expected public"):
            public_manifest_json(values[name])

    class ExtendedManifest(PrivacyManifest):
        secret: str

    extended = ExtendedManifest(**values["manifest.json"].model_dump(), secret="synthetic-secret")
    with pytest.raises(ValueError, match="Expected public"):
        public_manifest_json(extended)
    public = public_manifest_json(values["manifest.json"])
    for forbidden in ("SYNTHETIC-LOCAL-ONLY-VALUE", "a" * 64, "raw_text", "snapshot_id"):
        assert forbidden not in public


@pytest.mark.parametrize(
    "bbox",
    [
        (-1.0, 0.0, 2.0, 3.0),
        (0.0, 0.0, 0.0, 3.0),
        (0.0, 3.0, 1.0, 2.0),
        (0.0, 0.0, float("nan"), 3.0),
        (0.0, 0.0, float("inf"), 3.0),
    ],
)
def test_invalid_geometry_fails(bbox):
    with pytest.raises(ValidationError):
        PrivacyRegion(page=1, bbox=bbox)


@pytest.mark.parametrize("change", ["page", "bounds", "duplicate", "page_order"])
def test_occurrences_resolve_to_unique_valid_page_regions(values, change):
    raw = values["manifest.json"].model_dump(mode="json")
    if change == "page":
        raw["occurrences"][0]["region"]["page"] = 2
    elif change == "bounds":
        raw["occurrences"][0]["region"]["bbox"][2] = 601
    elif change == "duplicate":
        raw["occurrences"] *= 2
    else:
        raw["pages"][0]["number"] = 2
    with pytest.raises(ValidationError):
        PrivacyManifest.model_validate_json(json.dumps(raw))


def test_repeated_entity_with_distinct_occurrences_is_legal(values):
    raw = values["manifest.json"].model_dump(mode="json")
    second = json.loads(json.dumps(raw["occurrences"][0]))
    second["occurrence_id"] = str(EXPORT["fixture_id"](20))
    raw["occurrences"].append(second)
    assert len(PrivacyManifest.model_validate_json(json.dumps(raw)).occurrences) == 2
    assert placeholder_text(values["manifest.json"].occurrences[0].entity_id) == (
        "PT_00000000000040008000000000000005"
    )


def admit(values, **changes):
    args = {
        "command": values["review-command.json"],
        "approval": values["approval.json"],
        "manifest": values["manifest.json"],
        "now": NOW,
        "approval_authority": Mock(spec=PrivacyApprovalAuthority, permits=Mock(return_value=True)),
        "artifact_verifier": Mock(spec=SanitizedArtifactVerifier, permits=Mock(return_value=True)),
    }
    args.update(changes)
    check_export_admission(**args)
    return args


def test_pure_admission_requires_both_trusted_adapters(values):
    args = admit(values)
    args["approval_authority"].permits.assert_called_once()
    args["artifact_verifier"].permits.assert_called_once()


@pytest.mark.parametrize(
    "change", ["source", "snapshot", "case", "policy", "revision", "text", "region", "group"]
)
def test_any_review_material_change_invalidates_confirmation(values, change):
    raw = values["review-command.json"].model_dump(mode="json")
    if change == "source":
        raw["source"]["source_digest"] = "f" * 64
    elif change in {"snapshot", "case"}:
        raw["source"][f"{change}_id"] = str(EXPORT["fixture_id"](30))
    elif change == "policy":
        raw["policy_digest"] = "f" * 64
    elif change == "revision":
        raw["selection_revision"] = 2
    elif change == "text":
        raw["selections"][0]["candidate"]["raw_text"] = "synthetic-changed"
    elif change == "region":
        raw["selections"][0]["candidate"]["region"]["bbox"][0] = 11.0
    else:
        raw["selections"][0]["entity_id"] = str(EXPORT["fixture_id"](30))
    command = PrivacyReviewCommand.model_validate_json(json.dumps(raw))
    assert privacy_review_digest(command) != values["approval.json"].review_digest
    with pytest.raises(PrivacyFault, match="privacy_stale_confirmation"):
        admit(values, command=command)


@pytest.mark.parametrize("offset", [-31, 30])
def test_future_or_expired_approval_is_not_accepted(values, offset):
    with pytest.raises(PrivacyFault, match="privacy_stale_confirmation"):
        admit(values, now=NOW + timedelta(minutes=offset))


def test_zero_detections_still_requires_all_pages_reviewed(values):
    raw = values["review-command.json"].model_dump(mode="json")
    raw.update(selections=[], reviewed_pages=[])
    command = PrivacyReviewCommand.model_validate_json(json.dumps(raw))
    with pytest.raises(PrivacyFault, match="privacy_invalid_input"):
        admit(values, command=command)


@pytest.mark.parametrize("which", ["approval_authority", "artifact_verifier"])
@pytest.mark.parametrize("result", [False, "verified", RuntimeError("synthetic-secret")])
def test_claimed_authority_or_verification_cannot_replace_trusted_adapter(values, which, result):
    adapter = Mock()
    if isinstance(result, Exception):
        adapter.permits.side_effect = result
    else:
        adapter.permits.return_value = result
    with pytest.raises(PrivacyFault) as caught:
        admit(values, **{which: adapter})
    assert "synthetic-secret" not in str(caught.value)
    assert "synthetic-secret" not in caught.value.problem.model_dump_json()
    assert caught.value.__suppress_context__ or not isinstance(result, Exception)


@pytest.mark.parametrize("field", ["case_id", "document_id", "sanitized_digest"])
def test_wrong_artifact_identity_and_original_digest_are_rejected(values, field):
    raw = values["manifest.json"].model_dump(mode="json")
    raw[field] = "a" * 64 if field == "sanitized_digest" else str(EXPORT["fixture_id"](30))
    manifest = PrivacyManifest.model_validate_json(json.dumps(raw))
    with pytest.raises(PrivacyFault, match="privacy_verification_failed"):
        admit(values, manifest=manifest)


@pytest.mark.parametrize("change", ["missing", "extra", "unknown_entity", "page_count"])
def test_manifest_must_cover_exact_approved_entity_occurrences(values, change):
    raw = values["manifest.json"].model_dump(mode="json")
    if change == "missing":
        raw["occurrences"] = []
    elif change == "extra":
        second = json.loads(json.dumps(raw["occurrences"][0]))
        second["occurrence_id"] = str(EXPORT["fixture_id"](30))
        raw["occurrences"].append(second)
    elif change == "unknown_entity":
        raw["occurrences"][0]["entity_id"] = str(EXPORT["fixture_id"](30))
    else:
        second = dict(raw["pages"][0], number=2)
        raw["pages"].append(second)
    manifest = PrivacyManifest.model_validate_json(json.dumps(raw))
    with pytest.raises(PrivacyFault, match="privacy_verification_failed"):
        admit(values, manifest=manifest)


def test_naive_clock_does_not_create_approval_authority(values):
    with pytest.raises(PrivacyFault, match="privacy_invalid_input"):
        admit(values, now=NOW.replace(tzinfo=None))


def test_valid_empty_review_and_explicit_dismissal_need_current_approval(values):
    raw = values["review-command.json"].model_dump(mode="json")
    raw["selections"][0].update(
        disposition="dismiss", entity_id=None, dismissal_reason="Synthetic false positive reviewed"
    )
    command = PrivacyReviewCommand.model_validate_json(json.dumps(raw))
    approval = values["approval.json"].model_copy(
        update={"review_digest": privacy_review_digest(command)}
    )
    manifest = values["manifest.json"].model_copy(update={"occurrences": ()})
    admit(values, command=command, approval=approval, manifest=manifest)


@pytest.mark.parametrize(
    "change", ["rotation", "crop", "reviewed_page", "duplicate_page", "confidence"]
)
def test_source_geometry_and_review_evidence_validation(values, change):
    raw = values["review-command.json"].model_dump(mode="json")
    if change == "rotation":
        raw["source"]["pages"][0]["rotation"] = 45
    elif change == "crop":
        raw["source"]["pages"][0]["crop_box"][2] = 601.0
    elif change == "reviewed_page":
        raw["reviewed_pages"] = [2]
    elif change == "duplicate_page":
        raw["reviewed_pages"] = [1, 1]
    else:
        raw["selections"][0]["candidate"]["confidence"] = 1.1
    with pytest.raises(ValidationError):
        PrivacyReviewCommand.model_validate_json(json.dumps(raw))


def test_constructed_invalid_instance_is_revalidated(values):
    broken = values["manifest.json"].model_copy(update={"byte_size": -1})
    with pytest.raises(ValidationError):
        public_manifest_json(broken)
    with pytest.raises(PrivacyFault, match="privacy_invalid_input"):
        admit(values, manifest=broken)


@pytest.mark.parametrize(
    "change", ["missing_evidence", "dismissal", "duplicate", "unknown_category"]
)
def test_candidate_and_selection_require_explicit_evidence_and_decisions(values, change):
    raw = values["review-command.json"].model_dump(mode="json")
    if change == "missing_evidence":
        raw["selections"][0]["candidate"]["raw_text"] = None
    elif change == "dismissal":
        raw["selections"][0].update(disposition="dismiss", entity_id=None)
    elif change == "duplicate":
        raw["selections"] *= 2
    else:
        raw["selections"][0]["candidate"]["category"] = "unknown"
    with pytest.raises(ValidationError):
        PrivacyReviewCommand.model_validate_json(json.dumps(raw))


def test_approval_and_map_reject_invalid_time_and_crypto_shape(values):
    raw = values["approval.json"].model_dump(mode="json")
    raw["expires_at"] = raw["approved_at"]
    with pytest.raises(ValidationError):
        LocalPrivacyApproval.model_validate_json(json.dumps(raw))
    raw = values["mapping-envelope.json"].model_dump(mode="json")
    raw["nonce_hex"] = "00"
    with pytest.raises(ValidationError):
        EncryptedMappingEnvelope.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize("change", ["duplicate", "omission", "bounds"])
def test_refill_plan_is_explicit_and_bounded(values, change):
    raw = values["rehydration-plan.json"].model_dump(mode="json")
    if change == "duplicate":
        raw["fields"] *= 2
    elif change == "omission":
        raw["fields"][0]["operation"] = "omit"
    else:
        raw["fields"][0]["destination"]["bbox"][2] = 700
    with pytest.raises(ValidationError):
        RehydrationPlan.model_validate_json(json.dumps(raw))
