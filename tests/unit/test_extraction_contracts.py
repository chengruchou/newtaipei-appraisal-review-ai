"""Offline contract admission, source binding and immutable snapshot consumer checks."""

import hashlib
import importlib.util
import json
import runpy
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from pydantic.json_schema import models_json_schema

from appraisal_review.adapters.local.synthetic import synthetic_material
from appraisal_review.domain.document_models import SourceDocument, SourcePage, SourceRegion
from appraisal_review.domain.evaluation_contracts import EvaluationManifest
from appraisal_review.domain.extraction_contracts import (
    AttemptTelemetry,
    ExecutionBudget,
    HandoffRequest,
    PageOutcome,
    PageRequest,
    ProviderTelemetry,
)
from appraisal_review.ports.document_extraction import AuthorizedSanitizedSnapshot

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "extraction_export", ROOT / "scripts/export_extraction_contracts.py"
)
assert SPEC and SPEC.loader
EXPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXPORT)


def payload(name="request.json"):
    return EXPORT.fixtures()[name].model_dump(mode="json")


def snapshot(request_data=None):
    request = PageRequest.model_validate(request_data or payload())
    source = SourceDocument(
        document_id=request.source.document.document_id,
        uri="file:///synthetic/sanitized.pdf",
        version="v1",
        content_hash=hashlib.sha256(EXPORT.SYNTHETIC_BYTES).hexdigest(),
        role=request.source.document.purpose,
        pages=[
            SourcePage(
                number=n,
                width=100,
                height=100,
                crop_box=(0, 0, 100, 100),
                has_text=True,
                regions=[SourceRegion(id="region-1", kind="text", bbox=(1, 1, 10, 10), text="0")],
            )
            for n in (1, 2)
        ],
    )
    return AuthorizedSanitizedSnapshot(
        request.source,
        EXPORT.SYNTHETIC_BYTES,
        source.model_dump_json().encode(),
    )


def test_exported_bundles_and_fixtures_are_current_and_roundtrip():
    for name, value in EXPORT.artifacts().items():
        assert json.loads((ROOT / name).read_text(encoding="utf-8")) == value
    for name, model in EXPORT.fixtures().items():
        assert type(model).model_validate_json(model.model_dump_json()) == model
        bundle = "evaluation" if isinstance(model, EvaluationManifest) else "extraction"
        schema = json.loads((ROOT / f"schemas/{bundle}-v1.json").read_text(encoding="utf-8"))
        schema["$ref"] = f"#/$defs/{type(model).__name__}"
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(payload(name))


@pytest.mark.parametrize(
    "path,value",
    [
        (("schema_version",), "extraction-v2"),
        (("sanitized",), True),
        (("source", "document", "uri"), "file:///private.pdf"),
        (("source", "document", "purpose"), "template"),
        (("source", "document", "case_id"), "different"),
        (("source", "document", "content_hash"), "bad"),
        (("source", "privacy_manifest_digest"), "bad"),
        (("context", "task"), "propose_rules"),
        (("context", "case_context"), "private prose"),
        (("page",), 0),
        (("page",), 3),
        (("page",), True),
        (("page",), "1"),
    ],
)
def test_request_rejects_invalid_binding_or_extra_authority(path, value):
    data = payload()
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValidationError):
        PageRequest.model_validate(data)


def test_snapshot_checks_bytes_and_returns_detached_parser_data():
    item = snapshot()
    request = PageRequest.model_validate(payload())
    item.validate_request(request).pages[0].regions[0].text = "changed"
    assert item.source.pages[0].regions[0].text == "0"
    assert "Synthetic snapshot contract test bytes" not in repr(item)
    with pytest.raises(ValueError, match="byte digest"):
        AuthorizedSanitizedSnapshot(item.reference, b"replacement", item.source_json)
    with pytest.raises(ValueError, match="immutable bytes"):
        AuthorizedSanitizedSnapshot(
            item.reference, bytearray(EXPORT.SYNTHETIC_BYTES), item.source_json
        )
    other = payload()
    other["source"]["privacy_manifest_digest"] = "4" * 64
    with pytest.raises(ValueError, match="resolved snapshot"):
        item.validate_request(PageRequest.model_validate(other))


@pytest.mark.parametrize(
    "field,value", [("version", "v2"), ("role", "criteria"), ("document_id", "other")]
)
def test_snapshot_parser_identity_cannot_be_relabelled(field, value):
    item = snapshot()
    data = item.source.model_dump(mode="json")
    data[field] = value
    with pytest.raises(ValueError, match="parser identity"):
        AuthorizedSanitizedSnapshot(item.reference, item.content, json.dumps(data).encode())


def test_handoff_cannot_change_run_page_or_claim_completion():
    data = payload("handoff.json")
    data["locations"][0]["page"] = 2
    with pytest.raises(ValidationError):
        HandoffRequest.model_validate(data)
    data = payload("failed-page.json")
    data["handoffs"][0]["request"]["run"]["revision"]["revision_id"] = "other"
    with pytest.raises(ValidationError):
        PageOutcome.model_validate(data)
    for changes in ({"proposal": {}}, {"failure": None}, {"handoffs": []}, {"status": "completed"}):
        with pytest.raises(ValidationError):
            PageOutcome.model_validate(payload("failed-page.json") | changes)


def test_candidate_requires_returned_attempt_and_snapshot_resolves_handoff_regions():
    data = payload("candidate.json")
    data["telemetry"]["attempts"] = []
    with pytest.raises(ValidationError):
        PageOutcome.model_validate(data)
    data = payload("failed-page.json")
    data["handoffs"][0]["locations"][0]["region_id"] = "unknown"
    with pytest.raises(ValueError, match="Handoff region"):
        snapshot().validate_outcome(PageOutcome.model_validate(data))
    assert (
        snapshot().validate_outcome(PageOutcome.model_validate(payload("candidate.json"))).status
        == "candidate"
    )


def test_nullable_usage_and_timeout_cannot_start_replacement_attempt():
    data = payload("candidate.json")["telemetry"]
    assert ProviderTelemetry.model_validate(data).attempts[0].input_tokens is None
    data["attempts"][0].update(completion="unknown", failure="timeout")
    assert ProviderTelemetry.model_validate(data).attempts[0].completion == "unknown"
    data["attempts"].append(data["attempts"][0] | {"attempt": 2})
    data["elapsed_seconds"] = 2
    with pytest.raises(ValidationError, match="replacement"):
        ProviderTelemetry.model_validate(data)


@pytest.mark.parametrize(
    "changes",
    [
        {"attempt": 0},
        {"input_tokens": -1},
        {"output_tokens": True},
        {"elapsed_seconds": float("nan")},
        {"completion": "failed", "failure": None},
        {"completion": "unknown", "failure": "refused"},
        {"raw_response": "private"},
    ],
)
def test_telemetry_rejects_invalid_or_sensitive_fields(changes):
    with pytest.raises(ValidationError):
        AttemptTelemetry.model_validate(
            payload("candidate.json")["telemetry"]["attempts"][0] | changes
        )


@pytest.mark.parametrize(
    "path,value",
    [
        (("schema_version",), "evaluation-v2"),
        (("split",), "unknown"),
        (("golden_digest",), "bad"),
        (("adjudication",), "independent_reviewers"),
        (("repeats",), 3),
        (("budget", "max_calls"), 1),
        (("scoring", "minimum_iou"), 0.5),
        (("scoring", "numeric_absolute_tolerance"), "-1"),
        (("scoring", "unit_policy"), "versioned_conversion"),
        (("scoring", "missing_states"), "blank_equals_zero"),
        (("scoring", "denominator"), "successful_only"),
    ],
)
def test_evaluation_manifest_rejects_unreproducible_or_misleading_configuration(path, value):
    data = payload("evaluation.json")
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValidationError):
        EvaluationManifest.model_validate(data)


def test_evaluation_preserves_page_order_and_rejects_duplicates():
    data = payload("evaluation.json")
    assert EvaluationManifest.model_validate(data).inputs[0].pages == (2, 1)
    for pages in ([1, 1], [0], [3], []):
        data["inputs"][0]["pages"] = pages
        with pytest.raises(ValidationError):
            EvaluationManifest.model_validate(data)
    data = payload("evaluation.json")
    data["inputs"] *= 2
    with pytest.raises(ValidationError, match="Duplicate scheduled"):
        EvaluationManifest.model_validate(data)


def test_budget_rejects_unbounded_or_incoherent_limits():
    budget = payload("evaluation.json")["budget"]
    for changes in (
        {"max_calls": 0},
        {"max_concurrency": 5},
        {"max_attempts_per_page": 5},
        {"max_elapsed_seconds": float("inf")},
    ):
        with pytest.raises(ValidationError):
            ExecutionBudget.model_validate(budget | changes)


def candidate_with_citation():
    data = payload("candidate.json")
    document = data["request"]["source"]["document"]
    document["purpose"] = "criteria"
    data["request"]["context"]["task"] = "propose_rules"
    rule = synthetic_material().policy.rule_sets[0].rules.rules[0]
    data["proposal"]["rules"] = [
        {
            "scope": "individual",
            "rule": rule.model_dump(mode="json"),
            "evidence": [
                {
                    "document_id": document["document_id"],
                    "version": document["version"],
                    "content_hash": document["content_hash"],
                    "page": 1,
                    "region_id": "region-1",
                    "bbox": [1, 1, 10, 10],
                    "excerpt": "0",
                }
            ],
        }
    ]
    return data


@pytest.mark.parametrize(
    "field,value",
    [
        ("document_id", "other"),
        ("version", "v2"),
        ("content_hash", "0" * 64),
        ("page", 2),
    ],
)
def test_proposal_cannot_cross_source_version_or_page(field, value):
    data = candidate_with_citation()
    data["proposal"]["rules"][0]["evidence"][0][field] = value
    with pytest.raises(ValidationError, match="requested source"):
        PageOutcome.model_validate(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("bbox", [1, 1, 9, 9]),
        ("excerpt", "invented"),
        ("region_id", "other"),
    ],
)
def test_same_identity_still_requires_canonical_geometry_and_excerpt(field, value):
    data = candidate_with_citation()
    assert (
        snapshot(data["request"]).validate_outcome(PageOutcome.model_validate(data)).proposal
        is not None
    )
    data["proposal"]["rules"][0]["evidence"][0][field] = value
    with pytest.raises(ValueError, match="does not resolve"):
        snapshot(data["request"]).validate_outcome(PageOutcome.model_validate(data))


def test_mutated_nested_candidate_is_revalidated_at_snapshot_boundary():
    outcome = PageOutcome.model_validate(candidate_with_citation())
    outcome.proposal.rules[0].evidence[0].version = "forged"
    with pytest.raises(ValidationError, match="requested source"):
        snapshot(outcome.request.model_dump(mode="json")).validate_outcome(outcome)


def test_rule_proposal_cannot_use_forms_even_with_resolving_citations():
    data = candidate_with_citation()
    data["request"]["source"]["document"]["purpose"] = "forms"
    data["request"]["context"]["task"] = "propose_case"
    with pytest.raises(ValidationError, match="criteria source purpose"):
        PageOutcome.model_validate(data)


def test_contract_imports_cannot_construct_or_discover_sdk_clients():
    script = """
import sys
class NoSDK:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'boto3', 'botocore', 'bedrock_agentcore'}:
            raise AssertionError('SDK import is forbidden in contract consumers')
sys.meta_path.insert(0, NoSDK())
import appraisal_review.domain.evaluation_contracts
import appraisal_review.ports.document_extraction
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_manifest_cost_and_measurement_policies_remain_explicit():
    data = payload("evaluation.json")
    assert EvaluationManifest.model_validate(data).cost_policy is None
    data["scoring"].update(
        localization="region_iou",
        minimum_iou=0.8,
        unit_policy="versioned_conversion",
        unit_policy_digest="4" * 64,
    )
    data["cost_policy"] = {
        "currency": "USD",
        "rates_date": "2026-09-10",
        "rates_source": "synthetic-pricing-v1",
        "rates_digest": "5" * 64,
        "input_per_million": "1",
        "output_per_million": "2",
        "estimated_cost_ceiling": "0.10",
    }
    result = EvaluationManifest.model_validate(data)
    assert result.scoring.minimum_iou == 0.8
    assert result.scoring.acceptance_thresholds_digest is None
    data["cost_policy"]["input_per_million"] = "NaN"
    with pytest.raises(ValidationError):
        EvaluationManifest.model_validate(data)


def test_telemetry_sequence_totals_and_routing_cannot_contradict_records():
    data = payload("candidate.json")["telemetry"]
    for changes in ({"elapsed_seconds": 0}, {"attempts": [data["attempts"][0] | {"attempt": 2}]}):
        with pytest.raises(ValidationError):
            ProviderTelemetry.model_validate(data | changes)
    data["configuration"]["routing_regions"] *= 2
    with pytest.raises(ValidationError, match="Duplicate routing"):
        ProviderTelemetry.model_validate(data)


def test_unknown_privacy_contract_and_duplicate_handoff_locations_fail():
    data = payload()
    data["source"]["privacy_contract_version"] = "privacy-v999"
    with pytest.raises(ValidationError):
        PageRequest.model_validate(data)
    data = payload("handoff.json")
    data["locations"] *= 2
    with pytest.raises(ValidationError, match="Duplicate handoff"):
        HandoffRequest.model_validate(data)


def test_legacy_service_schema_bundles_remain_unchanged_without_posix_path_execution():
    from appraisal_review.adapters.local.service import LocalServiceConfiguration

    exporter = runpy.run_path(str(ROOT / "scripts/export_service_contracts.py"))
    _, schema = models_json_schema(
        [(model, "serialization") for model in exporter["MODELS"]], title="Service v1"
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    assert schema == json.loads((ROOT / "schemas/service-v1.json").read_text())
    assert LocalServiceConfiguration.model_json_schema(mode="serialization") == json.loads(
        (ROOT / "schemas/local-service-v1.json").read_text()
    )
