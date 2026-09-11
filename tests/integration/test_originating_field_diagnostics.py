"""Current-source diagnostics preserve the reviewed whole-case authority ruling."""

from __future__ import annotations

from copy import deepcopy

import pytest

from appraisal_review.adapters.local.golden_cases import GoldenAuthorization, golden_fixtures
from appraisal_review.domain.case_review import CaseReviewer


def material_for(case_key="normal-complete"):
    return deepcopy(next(f.material for f in golden_fixtures() if f.case_key == case_key))


def review(material):
    return CaseReviewer(GoldenAuthorization(material)).review(
        material.policy, material.facts, material.policy.registry
    )


def rename_slot(material, old, new):
    for slot in material.policy.inventory.slots:
        if slot.id == old:
            slot.id = new
    for observation in material.facts.observed:
        if observation.slot_id == old:
            observation.slot_id = new
    for check in material.policy.inventory.checks:
        check.inputs = [new if value == old else value for value in check.inputs]
        if check.target == old:
            check.target = new


@pytest.mark.parametrize(
    "slot_id",
    [
        "regional/copied-total",
        "regional/\u8907\u88fd\u5408\u8a08",
        "",
        'field\n"name"\x00',
        "x" * 129,
    ],
)
def test_existing_inventory_identifier_contract_never_silently_loses_an_origin(slot_id):
    import json

    from appraisal_review.domain.factor_models import ReviewMaterial

    material = material_for()
    rename_slot(material, "regional-copied-total", slot_id)
    # Validate through the existing contract before comparing either review.
    material = ReviewMaterial.model_validate_json(material.model_dump_json())
    original = review(material)
    assert original.status == "verified"
    observation = next(value for value in material.facts.observed if value.slot_id == slot_id)
    observation.evidence = []
    observation.raw_text = "PRIVATE OBSERVATION BODY"
    blocked = review(material)
    assert blocked.status == "needs_review"
    assert blocked.comparisons == original.comparisons
    findings = [finding for finding in blocked.findings if finding.id.startswith("observed/")]
    assert findings and all(finding.status == "needs_review" for finding in findings)
    for finding in findings:
        assert finding.originating_field_ids == [slot_id]
        assert json.dumps(slot_id, ensure_ascii=False) in finding.trace
        assert "PRIVATE OBSERVATION BODY" not in finding.trace
        assert "\n" not in finding.trace


@pytest.mark.parametrize("mode", ["missing", "wrong_source", "stale_version"])
@pytest.mark.parametrize(
    "ids", [("regional-road-rate",), ("regional-road-rate", "regional-frontage-rate")]
)
def test_current_source_origins_are_exact_and_do_not_change_calculation(mode, ids):
    material = material_for()
    original_comparisons = review(material).comparisons
    for value in material.facts.observed:
        if value.slot_id in ids:
            value.raw_text = "PRIVATE ORIGIN TEXT MUST NOT ENTER DIAGNOSTICS"
            if mode == "missing":
                value.evidence = []
            elif mode == "wrong_source":
                value.evidence = deepcopy(material.policy.rule_sets[0].evidence)
            else:
                value.evidence = [
                    ref.model_copy(update={"version": "stale-version"}) for ref in value.evidence
                ]
    before = material.model_dump_json()
    result = review(material)
    assert result.status == "needs_review"
    assert result.comparisons == original_comparisons
    assert material.model_dump_json() == before
    blocked = [f for f in result.findings if f.id.startswith("observed/")]
    assert blocked and all(f.status == "needs_review" for f in blocked)
    for finding in blocked:
        assert getattr(finding, "originating_field_ids", []) == sorted(ids)
        assert "PRIVATE ORIGIN TEXT" not in finding.trace
        assert all(name in finding.trace for name in ids)


def test_missing_observation_golden_names_original_field_without_changing_expectations():
    from pathlib import Path

    from appraisal_review.adapters.local.golden_cases import load_golden_manifests
    from appraisal_review.domain.golden_validator import compare_case_review

    material = material_for("missing-observation")
    result = review(material)
    suite = load_golden_manifests(Path(__file__).parents[1] / "goldens")
    manifest = next(c for c in suite.cases if c.case_key == "missing-observation")
    assert compare_case_review(manifest, result).ok
    for finding in result.findings:
        if finding.id.startswith("observed/") and finding.status != "verified":
            assert getattr(finding, "originating_field_ids", []) == ["regional-copied-total"]


def test_repaired_revision_does_not_replay_old_origin_mapping():
    material = material_for()
    observation = next(v for v in material.facts.observed if v.slot_id == "regional-road-rate")
    evidence = deepcopy(observation.evidence)
    observation.evidence = []
    blocked = review(material)
    assert any(
        getattr(f, "originating_field_ids", []) == [observation.slot_id] for f in blocked.findings
    )
    observation.evidence = evidence
    repaired = review(material)
    assert repaired.status == "verified"
    assert all(getattr(f, "originating_field_ids", []) == [] for f in repaired.findings)


@pytest.mark.parametrize(
    "unknown_id", ["unknown-private-field", "unknown/\u6b04\u4f4d", "unknown\nfield"]
)
def test_unknown_observation_never_becomes_a_public_origin(unknown_id):
    material = material_for()
    material.facts.observed.append(
        material.facts.observed[0].model_copy(update={"slot_id": unknown_id, "evidence": []})
    )
    result = review(material)
    assert result.status == "failed"
    assert all(getattr(f, "originating_field_ids", []) == [] for f in result.findings)


@pytest.mark.parametrize("mode", ["missing", "wrong_source", "stale_version"])
@pytest.mark.parametrize(
    "ids",
    [
        ("regional-road-rate",),
        ("regional-road-rate", "regional-frontage-rate"),
        ("regional/copied-total", "regional/\u8907\u88fd\u5408\u8a08"),
    ],
)
def test_real_controller_legacy_http_invocation_and_committed_public_result(mode, ids):
    import asyncio
    import json
    from dataclasses import replace
    from uuid import uuid4

    from fastapi.testclient import TestClient

    from appraisal_review.adapters.aws.agentcore.runtime import invoke
    from appraisal_review.adapters.local.golden_cases import golden_adapters
    from appraisal_review.adapters.local.job_store import InMemoryJobStore, InMemoryResultStore
    from appraisal_review.adapters.local.service import public_verification
    from appraisal_review.api.app import create_app
    from appraisal_review.application.bootstrap import build_controller
    from appraisal_review.application.review_jobs import ReviewJobService
    from appraisal_review.application.revisions import RevisionSnapshot
    from appraisal_review.application.service_guards import Principal
    from appraisal_review.config import Settings
    from appraisal_review.domain.service_contracts import (
        ActorReference,
        ExecutionStatus,
        Permission,
        ReviewSubmission,
        RunReference,
        ServiceResult,
    )

    fixture = next(f for f in golden_fixtures() if f.case_key == "normal-complete")
    material = deepcopy(fixture.material)
    for old, new in zip(("regional-road-rate", "regional-frontage-rate"), ids, strict=False):
        rename_slot(material, old, new)
    for value in material.facts.observed:
        if value.slot_id in ids:
            value.raw_text = "PRIVATE ORIGIN TEXT MUST NOT ENTER DIAGNOSTICS"
            if mode == "missing":
                value.evidence = []
            elif mode == "wrong_source":
                value.evidence = deepcopy(material.policy.rule_sets[0].evidence)
            else:
                value.evidence = [
                    ref.model_copy(update={"version": "stale-version"}) for ref in value.evidence
                ]
    fixture = replace(fixture, material=material)
    writes = []

    class ForbiddenWriter:
        async def write_pdf(self, request):
            writes.append(request)
            raise AssertionError("Whole-case evidence failure must never reach a PDF writer")

    def factory():
        return build_controller(
            Settings.model_construct(
                runtime_mode="local", synthetic_demo=False, min_extraction_confidence=0.85
            ),
            adapters=golden_adapters(fixture, pdf_writer=ForbiddenWriter()),
        )

    request = fixture.request.model_dump(mode="json")
    with TestClient(create_app(controller_factory=factory)) as client:
        response = client.post("/v1/reviews", json=request)
    invoked = asyncio.run(invoke(request, controller_factory=factory))
    assert response.status_code == 200
    legacy = response.json()
    assert legacy["status"] == invoked["status"] == "needs_review"
    assert legacy["artifact_status"] == invoked["artifact_status"] == "not_requested"
    legacy_keys = {
        "id",
        "kind",
        "status",
        "context",
        "factor_id",
        "rule_id",
        "rule_version",
        "observed",
        "expected",
        "evidence",
        "trace",
    }
    for payload in (legacy, invoked):
        assert "PRIVATE ORIGIN TEXT" not in json.dumps(payload)
        for finding in payload["case_review"]["findings"]:
            assert set(finding) == legacy_keys
            if finding["id"].startswith("observed/") and finding["status"] != "verified":
                assert all(name in finding["trace"] for name in ids)

    run = asyncio.run(factory().review(fixture.request))
    snapshot = RevisionSnapshot.capture(material, "originating-fields-r1")
    principal = Principal(
        actor=ActorReference(actor_id="diagnostic-reviewer", kind="human"),
        case_ids=frozenset({material.policy.identity.case_id}),
        permissions=frozenset(Permission),
    )

    class Resolver:
        async def current_principal(self):
            return principal

    jobs = ReviewJobService(InMemoryJobStore(), InMemoryResultStore(), clock=lambda: 100)
    submitted = asyncio.run(
        jobs.submit(
            principal,
            ReviewSubmission(
                revision=snapshot.revision.reference,
                documents=snapshot.revision.documents,
                idempotency_key="diagnostic-http",
            ),
        )
    )
    job = submitted.status.job
    attempt = asyncio.run(
        jobs.claim(job_id=job.job_id, run_id=submitted.status.current_run.run_id, owner=uuid4())
    )
    assert attempt is not None
    result = ServiceResult(
        run=RunReference(
            run_id=attempt.run_id,
            attempt_id=attempt.attempt_id,
            revision=snapshot.revision.reference,
        ),
        result_version=1,
        execution_status=ExecutionStatus.SUCCEEDED,
        business_status=run.status,
        artifact_status=run.artifact_status,
        findings=tuple(run.case_review.findings),
        verification=public_verification(run.verification),
    )
    asyncio.run(jobs.publish(attempt, result))
    with TestClient(create_app(job_service=jobs, principal_resolver=Resolver())) as client:
        response = client.get(f"/v1/review-jobs/{job.job_id}/result")
    assert response.status_code == 200
    public = response.json()
    assert public["business_status"] == "needs_review" and public["artifacts"] == []
    assert "PRIVATE ORIGIN TEXT" not in response.text
    for finding in public["findings"]:
        if finding["id"].startswith("observed/") and finding["status"] != "verified":
            assert finding["originating_field_ids"] == sorted(ids)
    assert writes == []


def test_legacy_finding_and_result_digest_round_trip_omits_empty_metadata():
    import hashlib
    import json
    from uuid import UUID

    from appraisal_review.domain.review_contracts import ReviewFinding, content_digest
    from appraisal_review.domain.service_contracts import (
        RevisionReference,
        RunReference,
        ServiceResult,
    )

    legacy = {
        "id": "observed/field-a",
        "kind": "source_purpose",
        "status": "needs_review",
        "context": None,
        "factor_id": None,
        "rule_id": None,
        "rule_version": None,
        "observed": None,
        "expected": None,
        "evidence": [],
        "trace": "Source evidence required",
    }
    finding = ReviewFinding.model_validate(legacy)
    assert finding.originating_field_ids == []
    assert finding.model_dump(mode="json") == legacy
    result = ServiceResult(
        run=RunReference(
            run_id=UUID(int=1),
            revision=RevisionReference(
                case_id="case-1", revision_id="r1", material_digest="1" * 64
            ),
        ),
        result_version=1,
        execution_status="succeeded",
        business_status="needs_review",
        findings=(finding,),
    )
    old_payload = result.model_dump(mode="json")
    old_payload["findings"] = [legacy]
    old_digest = hashlib.sha256(
        json.dumps(old_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    restored = ServiceResult.model_validate(old_payload)
    assert content_digest(restored) == old_digest
    restored.findings[0].originating_field_ids = ["field-a"]
    assert content_digest(restored) != old_digest
    schema = ReviewFinding.model_json_schema(mode="serialization")
    assert schema["additionalProperties"] is False
    assert set(legacy) | {"originating_field_ids"} == set(schema["properties"])
    assert "originating_field_ids" not in schema["required"]


@pytest.mark.parametrize("origins", [["x", "x"], ["z", "a"], [1]])
def test_diagnostic_identifiers_require_strict_strings_sorted_and_unique(origins):
    from pydantic import ValidationError

    from appraisal_review.domain.review_contracts import ReviewFinding

    with pytest.raises(ValidationError):
        ReviewFinding(
            id="observed/a",
            kind="source_purpose",
            status="needs_review",
            trace="Review source evidence",
            originating_field_ids=origins,
        )


def test_duplicate_source_violations_name_the_original_slot_once():
    material = material_for()
    slot = next(s for s in material.policy.inventory.slots if s.id == "regional-road-rate")
    slot.evidence = [r.model_copy(update={"version": "stale"}) for r in slot.evidence]
    observed = next(v for v in material.facts.observed if v.slot_id == slot.id)
    observed.evidence = []
    result = review(material)
    violations = [
        f for f in result.findings if f.id == f"observed/{slot.id}" and f.kind == "source_purpose"
    ]
    assert len(violations) == 2
    assert all(f.originating_field_ids == [slot.id] for f in violations)


def test_missing_material_approval_does_not_invent_a_source_origin():
    material = material_for()
    result = CaseReviewer(None).review(material.policy, material.facts, material.policy.registry)
    assert result.status == "needs_review"
    assert all(f.originating_field_ids == [] for f in result.findings)
