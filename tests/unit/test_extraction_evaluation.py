"""Offline scorer and real CLI tests; reviewed #23 files are consumed read-only."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import subprocess
import sys
from datetime import date
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from uuid import UUID

import pytest
from jsonschema import Draft202012Validator

from appraisal_review.adapters.local.golden_cases import golden_fixtures, load_golden_manifests
from appraisal_review.application.extraction_evaluation import (
    UNIT_CONVERSION_POLICY,
    EvaluationError,
    _value_match,
    canonical_digest,
    dataset_digest,
    english_summary,
    golden_digest,
    run_evaluation,
    score_evaluation,
    scoring_policy_digest,
    split_digest,
)
from appraisal_review.domain.evaluation_contracts import (
    CostPolicy,
    EvaluationInput,
    EvaluationManifest,
    ScoringPolicy,
)
from appraisal_review.domain.evaluation_report import (
    EvaluationReplay,
    EvaluationReport,
    EvaluationVersions,
    GoldenBinding,
    RepeatedOutcome,
    TokenPriceEvidence,
)
from appraisal_review.domain.extraction_contracts import (
    AttemptTelemetry,
    ExtractionContext,
    HandoffLocation,
    HandoffRequest,
    PageOutcome,
    PageRequest,
    ProviderTelemetry,
    SanitizedSourceReference,
)
from appraisal_review.domain.extraction_models import PageProposal
from appraisal_review.domain.golden_contract import GoldenSuite
from appraisal_review.domain.review_contracts import ObservedValue
from appraisal_review.domain.service_contracts import (
    DocumentReference,
    RevisionReference,
    RunReference,
)
from appraisal_review.ports.document_extraction import ExtractionBoundaryError

ROOT = Path(__file__).resolve().parents[2]
GOLDENS = ROOT / "tests/goldens"
CLI = ROOT / "scripts/evaluate_extraction.py"
CANARY = "PRIVATE-RAW-CONTENT-CANARY"


@lru_cache(maxsize=1)
def fixture_materials():
    # Source fixtures produce test proposals. Expected answers are separately loaded from disk.
    return {fixture.case_key: fixture.material for fixture in golden_fixtures()}


@lru_cache(maxsize=1)
def reviewed():
    return load_golden_manifests(GOLDENS)


def repin(manifest, replay, **changes):
    manifest = EvaluationManifest.model_validate(manifest.model_dump(mode="json") | changes)
    replay = EvaluationReplay.model_validate(
        replay.model_dump(mode="json") | {"manifest_digest": canonical_digest(manifest)}
    )
    return manifest, replay


def setup(case_key="normal-complete", *, repeats=1, split="development", prices=False):
    goldens = reviewed()
    case = next(case for case in goldens.cases if case.case_key == case_key)
    material = fixture_materials()[case_key]
    bindings = (GoldenBinding(case_id=case.identity.case_id, golden_case_key=case_key),)
    inputs = tuple(
        EvaluationInput(
            source=SanitizedSourceReference(
                document=DocumentReference(
                    case_id=case.identity.case_id,
                    document_id=source.document_id,
                    version=source.version,
                    content_hash=source.content_hash,
                    purpose=source.role,
                ),
                privacy_contract_version="privacy-v1",
                privacy_manifest_digest="a" * 64,
                page_count=source.page_count,
            ),
            pages=tuple(range(1, source.page_count + 1)),
        )
        for source in case.fixture.documents
        if source.role == "forms"
    )
    payload = json.loads((ROOT / "examples/extraction-v1/evaluation.json").read_text())
    policy = ScoringPolicy.model_validate(
        payload["scoring"]
        | {
            "localization": "region_iou",
            "minimum_iou": 0.8,
        }
    )
    policy = policy.model_copy(update={"policy_digest": scoring_policy_digest(policy)})
    payload.update(
        inputs=[i.model_dump(mode="json") for i in inputs],
        dataset_digest=dataset_digest(inputs),
        golden_digest=golden_digest(goldens),
        golden_schema_version=goldens.schema_version,
        split=split,
        split_digest=split_digest(split, bindings),
        repeats=repeats,
        scoring=policy.model_dump(mode="json"),
        execution_kind="replay",
    )
    payload["budget"].update(max_pages=100, max_calls=100, max_output_tokens=100000)
    manifest = EvaluationManifest.model_validate(payload)
    outcomes = []
    for repeat in range(1, repeats + 1):
        for item in inputs:
            for page in item.pages:
                request = PageRequest(
                    run=RunReference(
                        run_id=UUID(int=repeat),
                        revision=RevisionReference(
                            case_id=case.identity.case_id,
                            revision_id=case.identity.version,
                            material_digest=case.fixture.material_digest,
                        ),
                    ),
                    source=item.source,
                    page=page,
                    context=ExtractionContext(task="propose_case", language="en"),
                )
                slots = [
                    slot.model_copy(deep=True)
                    for slot in material.policy.inventory.slots
                    if any(e.page == page for e in slot.evidence)
                ]
                slot_ids = {s.id for s in slots}
                observed = [
                    value.model_copy(deep=True)
                    for value in material.facts.observed
                    if value.slot_id in slot_ids
                ]
                outcomes.append(
                    RepeatedOutcome(
                        repeat=repeat,
                        outcome=PageOutcome(
                            request=request,
                            status="candidate",
                            proposal=PageProposal(slots=slots, observed=observed),
                            failure=None,
                            telemetry=ProviderTelemetry(
                                configuration=manifest.configuration,
                                prompt_version=manifest.prompt_version,
                                prompt_digest=manifest.prompt_digest,
                                elapsed_seconds=0.5,
                                attempts=(
                                    AttemptTelemetry(
                                        attempt=1,
                                        completion="returned",
                                        failure=None,
                                        input_tokens=100,
                                        output_tokens=20,
                                        elapsed_seconds=0.25,
                                        backoff_seconds=0,
                                    ),
                                ),
                            ),
                            handoffs=(),
                        ),
                    )
                )
    replay = EvaluationReplay(
        manifest_digest=canonical_digest(manifest),
        bindings=bindings,
        versions=EvaluationVersions(
            parser_version="fixture-parser-v1",
            parser_digest="b" * 64,
            normalizer_version="fixture-normalizer-v1",
            normalizer_digest="c" * 64,
        ),
        outcomes=tuple(outcomes),
    )
    if prices:
        evidence = TokenPriceEvidence(
            configuration_digest=canonical_digest(manifest.configuration),
            currency="USD",
            rates_date=date(2026, 9, 1),
            rates_source="synthetic-rate-record",
            input_per_million=Decimal("2"),
            output_per_million=Decimal("10"),
        )
        policy = CostPolicy(
            **evidence.model_dump(exclude={"configuration_digest", "scope"}),
            rates_digest=canonical_digest(evidence),
            estimated_cost_ceiling=Decimal("1"),
        )
        manifest, replay = repin(manifest, replay, cost_policy=policy.model_dump(mode="json"))
        replay = replay.model_copy(update={"pricing_evidence": evidence})
    return manifest, replay, goldens


def run_cli(tmp_path, manifest, replay, *, goldens=GOLDENS):
    manifest_path, replay_path = tmp_path / "manifest.json", tmp_path / "outcomes.json"
    manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")
    replay_path.write_text(replay.model_dump_json(), encoding="utf-8")
    output = tmp_path / "report"
    result = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--manifest",
            str(manifest_path),
            "--outcomes",
            str(replay_path),
            "--goldens",
            str(goldens),
            "--output-dir",
            str(output),
        ],
        cwd=ROOT,
        env=os.environ | {"PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    return result, output


def test_exact_existing_manifest_type_and_full_case_positive_cli(tmp_path):
    manifest, replay, goldens = setup()
    before = {p.name: p.read_bytes() for p in GOLDENS.glob("*.json")}
    assert type(manifest) is EvaluationManifest
    result, output = run_cli(tmp_path, manifest, replay)
    assert result.returncode == 0, result.stderr
    report = EvaluationReport.model_validate_json((output / "report.json").read_bytes())
    assert report == score_evaluation(manifest, replay, goldens)
    assert report.summary.counts["expected_fields"] == 13
    assert report.summary.counts["scheduled_pages"] == 2
    for name in (
        "precision",
        "recall",
        "type_accuracy",
        "value_accuracy",
        "source_accuracy",
        "localization_accuracy",
        "case_completeness",
        "case_accuracy",
    ):
        assert report.summary.metrics[name].value == 1
    assert report.summary.usage.input_tokens.total == 200
    assert report.summary.cost.amount is None
    assert (output / "summary.txt").read_text() == result.stdout
    assert before == {p.name: p.read_bytes() for p in GOLDENS.glob("*.json")}
    assert (output / "report.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("case_key", [case.case_key for case in reviewed().cases])
def test_independent_readonly_goldens_all_representative_cases(case_key):
    manifest, replay, goldens = setup(case_key)
    report = score_evaluation(manifest, replay, goldens)
    case = next(case for case in goldens.cases if case.case_key == case_key)
    assert report.summary.counts["expected_fields"] == len(case.expected_slots)
    assert report.summary.metrics["value_accuracy"].value == 1
    assert report.summary.metrics["recall"].value == 1
    assert report.field_scope == "golden_observed_slots"


def test_predictions_are_scored_against_printed_conflict_not_derived_answer():
    manifest, replay, goldens = setup("conflicting-sources")
    slot = next(s for s in goldens.cases if s.case_key == "conflicting-sources").expected_slots[-1]
    assert slot.observed.value != slot.independent.value
    actual = replay.outcomes[-1].outcome.proposal.observed[-1]
    assert actual.value == slot.observed.value
    actual.value = slot.independent.value
    score = score_evaluation(manifest, replay, goldens).summary
    assert score.counts["value_correct"] == 12
    assert score.field_errors["value_mismatch"] == 1


@pytest.mark.parametrize("keep,expected_pages,expected_fields", [(0, 0, 0), (1, 1, 9)])
def test_missing_pages_cases_and_predictions_stay_in_denominators(
    keep, expected_pages, expected_fields
):
    manifest, replay, goldens = setup()
    replay = replay.model_copy(update={"outcomes": replay.outcomes[:keep]})
    score = score_evaluation(manifest, replay, goldens).summary
    assert score.metrics["page_completeness"].denominator == 2
    assert score.metrics["recall"].denominator == 13
    assert score.metrics["recall"].numerator == expected_fields
    assert score.counts["observed_pages"] == expected_pages
    assert score.counts["missing_cases"] == (1 if keep == 0 else 0)
    assert score.metrics["case_completeness"].value == 0
    assert score.usage.total_attempts is None
    assert score.usage.input_tokens.total is None
    assert score.usage.page_elapsed_seconds.total is None
    if keep == 0:
        assert score.metrics["precision"].value is None
        assert score.usage.mean_observed_page_seconds is None


def test_complete_empty_proposals_do_not_equal_successful_extraction():
    manifest, replay, goldens = setup()
    replay = replay.model_copy(
        update={
            "outcomes": tuple(
                row.model_copy(
                    update={"outcome": row.outcome.model_copy(update={"proposal": PageProposal()})}
                )
                for row in replay.outcomes
            )
        }
    )
    score = score_evaluation(manifest, replay, goldens).summary
    assert score.metrics["page_completeness"].value == 1
    assert score.metrics["recall"].value == 0
    assert score.metrics["precision"].value is None
    assert score.metrics["case_accuracy"].value == 0
    assert score.state_confusion["present"]["no_prediction"] == 13


def test_wrong_context_type_duplicate_and_unknown_fields_are_not_hidden():
    manifest, replay, goldens = setup("multi-context")
    proposal = replay.outcomes[0].outcome.proposal
    proposal.slots[0].context.comparable_id = "other-context"
    proposal.slots[1].value = "total"
    proposal.observed.append(proposal.observed[0].model_copy(deep=True))
    proposal.observed.append(proposal.observed[0].model_copy(update={"slot_id": "unknown-field"}))
    score = score_evaluation(manifest, replay, goldens).summary
    assert score.counts["expected_fields"] == 26
    assert score.counts["predictions"] == 28
    assert score.counts["type_correct"] == 24
    assert score.field_errors["duplicate_prediction"] == 1
    assert score.field_errors["unknown_field"] == 1
    assert score.metrics["precision"].value == 24 / 28


def test_first_duplicate_consumes_match_without_cherry_picking():
    manifest, replay, goldens = setup()
    proposal = replay.outcomes[0].outcome.proposal
    incorrect = proposal.observed[0].model_copy(update={"value": "wrong"})
    proposal.observed.insert(0, incorrect)
    score = score_evaluation(manifest, replay, goldens).summary
    assert score.counts["correct_fields"] == 12
    assert score.field_errors["duplicate_prediction"] == 1
    assert score.counts["false_positives"] == 2


@pytest.mark.parametrize(
    "state,value,unit",
    [
        ("missing", None, None),
        ("present", "0", "percent_points"),
        ("not_present", None, None),
        ("not_applicable", None, None),
    ],
)
def test_blank_never_equals_missing_not_applicable_or_zero(state, value, unit):
    manifest, replay, goldens = setup("blank-derived")
    observed = replay.outcomes[-1].outcome.proposal.observed[-1]
    assert observed.state == "blank"
    observed.state, observed.value, observed.unit = state, value, unit
    score = score_evaluation(manifest, replay, goldens).summary
    assert score.expected_states["blank"] == 1
    assert score.state_confusion["blank"][state] == 1
    assert score.counts["correct_fields"] == 12


@pytest.mark.parametrize(
    "number,actual,tolerance,expected",
    [
        ("0", "0", "0", True),
        ("0", "0.0001", "0", False),
        ("10", "10.1", "0.1", True),
        ("10", "10.1001", "0.1", False),
        ("10", "NaN", "1", False),
        ("10", "Infinity", "1", False),
    ],
)
def test_decimal_zero_finite_numbers_and_absolute_boundary(number, actual, tolerance, expected):
    manifest, _, goldens = setup()
    slot = copy.deepcopy(
        next(c for c in goldens.cases if c.case_key == "normal-complete").expected_slots[-1]
    )
    # Independently authored scalar example; no reviewed file is changed.
    slot.observed.value = number
    policy = manifest.scoring.model_copy(update={"numeric_absolute_tolerance": Decimal(tolerance)})
    value = ObservedValue(
        slot_id=slot.slot_id, state="present", value=actual, unit="percent_points", raw_text=""
    )
    assert _value_match(slot, value, policy) is expected


def test_relative_tolerance_and_versioned_unit_conversion():
    manifest, replay, goldens = setup()
    proposal = replay.outcomes[-1].outcome.proposal
    proposal.observed[-1].value, proposal.observed[-1].unit = "0.09", "ratio"
    assert score_evaluation(manifest, replay, goldens).summary.counts["correct_fields"] == 12
    policy = manifest.scoring.model_copy(
        update={
            "unit_policy": "versioned_conversion",
            "unit_policy_digest": canonical_digest(UNIT_CONVERSION_POLICY),
            "numeric_relative_tolerance": Decimal("0.01"),
        }
    )
    policy = policy.model_copy(update={"policy_digest": scoring_policy_digest(policy)})
    manifest, replay = repin(manifest, replay, scoring=policy.model_dump(mode="json"))
    assert score_evaluation(manifest, replay, goldens).summary.counts["correct_fields"] == 13
    proposal = replay.outcomes[-1].outcome.proposal
    proposal.observed[-1].value = "0.0909"
    assert score_evaluation(manifest, replay, goldens).summary.counts["correct_fields"] == 13
    proposal.observed[-1].value = "0.09091"
    assert score_evaluation(manifest, replay, goldens).summary.counts["correct_fields"] == 12


@pytest.mark.parametrize(
    "mode", ["absent", "neighbor", "extra", "geometry", "invalid_box", "wrong_page"]
)
def test_evidence_and_cross_page_binding_have_all_field_denominators(mode):
    manifest, replay, goldens = setup()
    proposal = replay.outcomes[0].outcome.proposal
    observed = proposal.observed[0]
    if mode == "absent":
        observed.evidence = []
    elif mode == "neighbor":
        observed.evidence = copy.deepcopy(proposal.observed[1].evidence)
    elif mode == "extra":
        observed.evidence += copy.deepcopy(proposal.observed[1].evidence)
    elif mode in {"geometry", "invalid_box"}:
        observed.evidence[0].bbox = (0, 0, 1, 1) if mode == "geometry" else (2, 2, 1, 1)
    else:
        # Valid page-1 citation attached to the identity independently expected on page 2.
        observed.slot_id = replay.outcomes[-1].outcome.proposal.observed[-1].slot_id
    score = score_evaluation(manifest, replay, goldens).summary
    assert score.metrics["localization_accuracy"].denominator == 13
    assert score.metrics["localization_accuracy"].value < 1
    assert score.metrics["recall"].value < 1


def test_unmeasured_localization_is_null_not_perfect():
    manifest, replay, goldens = setup()
    policy = manifest.scoring.model_copy(
        update={"localization": "not_measured", "minimum_iou": None}
    )
    policy = policy.model_copy(update={"policy_digest": scoring_policy_digest(policy)})
    manifest, replay = repin(manifest, replay, scoring=policy.model_dump(mode="json"))
    metric = score_evaluation(manifest, replay, goldens).summary.metrics["localization_accuracy"]
    assert metric.value is None and metric.denominator == 13 and metric.numerator == 0


def failed_page(row, failure="refused"):
    outcome = row.outcome
    attempt = outcome.telemetry.attempts[0].model_copy(
        update={
            "completion": "unknown" if failure == "timeout" else "failed",
            "failure": failure,
            "input_tokens": None,
            "output_tokens": None,
        }
    )
    return row.model_copy(
        update={
            "outcome": outcome.model_copy(
                update={
                    "status": "failed",
                    "proposal": None,
                    "failure": failure,
                    "telemetry": outcome.telemetry.model_copy(update={"attempts": (attempt,)}),
                    "handoffs": (
                        HandoffRequest(
                            request=outcome.request,
                            reason="page_failed",
                            locations=(HandoffLocation(page=outcome.request.page),),
                        ),
                    ),
                }
            )
        }
    )


@pytest.mark.parametrize("failure", ["refused", "timeout", "access_denied", "malformed_output"])
def test_failures_human_refusals_and_unknown_usage_preserve_denominator(failure):
    manifest, replay, goldens = setup(prices=True)
    replay = replay.model_copy(
        update={"outcomes": (failed_page(replay.outcomes[0], failure), replay.outcomes[1])}
    )
    score = score_evaluation(manifest, replay, goldens).summary
    assert score.metrics["error_rate"].value == 0.5
    assert score.metrics["human_intervention_rate"].value == 0.5
    assert score.metrics["refusal_rate"].value == (0.5 if failure == "refused" else 0)
    assert score.page_failures == {failure: 1} and score.attempt_failures == {failure: 1}
    assert score.usage.input_tokens.known == 100 and score.usage.input_tokens.total is None
    assert score.cost.amount is None and score.cost.known_usage_subtotal == Decimal("0.0004")
    assert score.cost.reason == "unknown_usage"


def test_attempts_retries_and_all_failed_attempt_tokens_cost_are_accounted():
    manifest, replay, goldens = setup(prices=True)
    row = replay.outcomes[0]
    failed = AttemptTelemetry(
        attempt=1,
        completion="failed",
        failure="throttled",
        input_tokens=5,
        output_tokens=2,
        elapsed_seconds=0.1,
        backoff_seconds=0.1,
    )
    final = row.outcome.telemetry.attempts[0].model_copy(update={"attempt": 2})
    row = row.model_copy(
        update={
            "outcome": row.outcome.model_copy(
                update={
                    "telemetry": row.outcome.telemetry.model_copy(
                        update={"attempts": (failed, final)}
                    ),
                }
            )
        }
    )
    replay = replay.model_copy(update={"outcomes": (row, replay.outcomes[1])})
    score = score_evaluation(manifest, replay, goldens).summary
    assert score.usage.total_attempts == 3 and score.usage.total_retries == 1
    assert score.usage.input_tokens.total == 205 and score.usage.output_tokens.total == 42
    assert score.usage.retry_input_tokens.total == 100
    assert score.usage.backoff_seconds.total == pytest.approx(0.1)
    assert score.cost.amount == Decimal("0.00083")
    assert score.attempt_failures == {"throttled": 1} and score.page_failures == {}


@pytest.mark.parametrize("mode", ["no_evidence", "missing_page", "unknown_usage", "known_zero"])
def test_cost_and_unknown_usage_real_cli(tmp_path, mode):
    manifest, replay, goldens = setup(prices=True)
    if mode == "no_evidence":
        replay = replay.model_copy(update={"pricing_evidence": None})
    elif mode == "missing_page":
        replay = replay.model_copy(update={"outcomes": replay.outcomes[:1]})
    else:
        for row in replay.outcomes:
            attempt = row.outcome.telemetry.attempts[0]
            changed = attempt.model_copy(
                update={"input_tokens": 0 if mode == "known_zero" else None}
            )
            object.__setattr__(row.outcome.telemetry, "attempts", (changed,))
    result, output = run_cli(tmp_path, manifest, replay)
    assert result.returncode == 0, result.stderr
    report = EvaluationReport.model_validate_json((output / "report.json").read_bytes())
    assert report == score_evaluation(manifest, replay, goldens)
    if mode == "known_zero":
        assert report.summary.usage.input_tokens.total == 0
        assert report.summary.cost.amount == Decimal("0.0004")
    else:
        assert report.summary.cost.amount is None


def test_repeats_compare_actual_differences_not_output_order_or_bitwise_claim():
    manifest, replay, goldens = setup(repeats=3)
    replay.outcomes[2].outcome.proposal.observed.reverse()
    replay.outcomes[4].outcome.proposal.observed[0].value = "wrong"
    replay = replay.model_copy(update={"outcomes": replay.outcomes[:-1]})
    report = score_evaluation(manifest, replay, goldens)
    assert report.summary.counts["expected_fields"] == 39
    assert len(report.differences) == 3
    assert report.differences[0].changed_pages == 0
    assert report.differences[1].changes["field_value"] == 1
    assert report.differences[1].changes["availability"] == 1
    assert report.differences[1].missing_right == 1
    assert report.differences[1].metric_differences["recall"] < 0
    assert report.determinism_claim == "none"


def test_reports_never_copy_raw_values_excerpts_context_or_handoff_ids(tmp_path):
    manifest, replay, goldens = setup()
    proposal = replay.outcomes[0].outcome.proposal
    proposal.observed[0].value = proposal.observed[0].raw_text = CANARY
    proposal.observed[0].evidence[0].excerpt = CANARY
    proposal.slots[0].context.target_id = CANARY
    proposal.unresolved.append(CANARY)
    result, output = run_cli(tmp_path, manifest, replay)
    assert result.returncode == 0
    assert CANARY not in result.stdout + result.stderr
    report = score_evaluation(manifest, replay, goldens)
    assert CANARY not in english_summary(report) + report.model_dump_json()
    assert CANARY not in (output / "report.json").read_text()
    assert "golden-target" not in result.stdout


@pytest.mark.parametrize(
    "mode,code",
    [
        ("manifest", "manifest_mismatch"),
        ("dataset", "dataset_mismatch"),
        ("split", "split_mismatch"),
        ("golden_digest", "golden_digest_mismatch"),
        ("golden_version", "golden_version_mismatch"),
        ("golden_case", "golden_case_mismatch"),
        ("missing_case_binding", "case_denominator_mismatch"),
        ("golden_source", "golden_source_mismatch"),
        ("golden_page_count", "golden_source_mismatch"),
        ("page_denominator", "page_denominator_mismatch"),
        ("outcome_case", "outcome_outside_schedule"),
        ("outcome_page", "duplicate_page_outcome"),
        ("outcome_version", "invalid_contract"),
        ("outcome_hash", "invalid_contract"),
        ("citation_page", "invalid_contract"),
        ("citation_version", "invalid_contract"),
        ("configuration", "outcome_configuration_mismatch"),
        ("prompt", "outcome_configuration_mismatch"),
        ("revision", "outcome_revision_mismatch"),
        ("material", "outcome_revision_mismatch"),
        ("duplicate", "duplicate_page_outcome"),
        ("repeat", "repeat_outside_schedule"),
        ("rate", "rate_evidence_mismatch"),
    ],
)
def test_real_cli_rejects_wrong_bindings_without_leaking_values(tmp_path, mode, code):
    manifest, replay, _ = setup(prices=True)
    m, r = manifest.model_dump(mode="json"), replay.model_dump(mode="json")
    first = r["outcomes"][0]["outcome"]
    if mode == "manifest":
        r["manifest_digest"] = "0" * 64
    elif mode in {"dataset", "split", "golden_digest"}:
        m[mode if mode == "golden_digest" else f"{mode}_digest"] = "0" * 64
    elif mode == "golden_version":
        m["golden_schema_version"] = "golden-2"
    elif mode == "golden_case":
        r["bindings"][0]["case_id"] = "wrong-case"
        m["split_digest"] = split_digest(
            m["split"], [GoldenBinding.model_validate(r["bindings"][0])]
        )
    elif mode == "missing_case_binding":
        m["inputs"][0]["source"]["document"]["case_id"] = "wrong-case"
    elif mode == "golden_source":
        m["inputs"][0]["source"]["document"]["version"] = "wrong-version"
    elif mode == "golden_page_count":
        m["inputs"][0]["source"]["page_count"] = 3
    elif mode == "page_denominator":
        m["inputs"][0]["pages"] = [1]
    elif mode == "outcome_case":
        first["request"]["source"]["document"]["case_id"] = "wrong-case"
        first["request"]["run"]["revision"]["case_id"] = "wrong-case"
    elif mode == "outcome_page":
        r["outcomes"][0] = copy.deepcopy(r["outcomes"][1])
    elif mode in {"outcome_version", "outcome_hash"}:
        first["request"]["source"]["document"][
            "version" if mode == "outcome_version" else "content_hash"
        ] = "wrong-version" if mode == "outcome_version" else "0" * 64
    elif mode in {"citation_page", "citation_version"}:
        first["proposal"]["observed"][0]["evidence"][0][
            "page" if mode == "citation_page" else "version"
        ] = 2 if mode == "citation_page" else "wrong-version"
    elif mode == "configuration":
        first["telemetry"]["configuration"]["model_id"] = CANARY
    elif mode == "prompt":
        first["telemetry"]["prompt_digest"] = "0" * 64
    elif mode in {"revision", "material"}:
        first["request"]["run"]["revision"][
            "revision_id" if mode == "revision" else "material_digest"
        ] = "wrong-revision" if mode == "revision" else "0" * 64
    elif mode == "duplicate":
        r["outcomes"].append(copy.deepcopy(r["outcomes"][0]))
    elif mode == "repeat":
        r["outcomes"][0]["repeat"] = 2
    elif mode == "rate":
        r["pricing_evidence"]["input_per_million"] = "0"
    if mode in {"missing_case_binding", "golden_source", "golden_page_count", "page_denominator"}:
        m["dataset_digest"] = dataset_digest(
            [EvaluationInput.model_validate(i) for i in m["inputs"]]
        )
    manifest = EvaluationManifest.model_validate(m)
    if mode != "manifest":
        r["manifest_digest"] = canonical_digest(manifest)
    # Serialize deliberately malformed payloads without sanitizing them through model validation.
    (tmp_path / "manifest.json").write_text(manifest.model_dump_json())
    (tmp_path / "outcomes.json").write_text(json.dumps(r))
    result = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--manifest",
            str(tmp_path / "manifest.json"),
            "--outcomes",
            str(tmp_path / "outcomes.json"),
            "--goldens",
            str(GOLDENS),
            "--output-dir",
            str(tmp_path / "report"),
        ],
        cwd=ROOT,
        env=os.environ | {"PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert result.stderr == f"Evaluation rejected: {code}.\n"
    assert CANARY not in result.stderr + result.stdout
    assert not (tmp_path / "report").exists()


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("page", 3, "golden_evidence_page_mismatch"),
        ("version", "stale", "golden_evidence_source_mismatch"),
        ("content_hash", "0" * 64, "golden_evidence_source_mismatch"),
    ],
)
def test_golden_wrong_page_version_digest_rejected_even_with_recomputed_bundle_hash(
    field, value, code
):
    manifest, replay, goldens = setup()
    goldens = goldens.model_copy(deep=True)
    case = next(c for c in goldens.cases if c.case_key == "normal-complete")
    setattr(case.expected_slots[0].observed.citation, field, value)
    manifest, replay = repin(manifest, replay, golden_digest=golden_digest(goldens))
    with pytest.raises(EvaluationError, match=code):
        score_evaluation(manifest, replay, goldens)


def test_report_schema_is_additive_and_matches_runtime_model():
    schema = json.loads((ROOT / "schemas/evaluation-report-v1.json").read_text())
    assert schema == EvaluationReport.model_json_schema(mode="serialization")
    Draft202012Validator.check_schema(schema)
    manifest, replay, goldens = setup()
    Draft202012Validator(schema).validate(
        score_evaluation(manifest, replay, goldens).model_dump(mode="json")
    )


def test_development_and_held_out_splits_are_distinct_pinned_inputs():
    dev_manifest, dev_replay, goldens = setup()
    held_manifest, held_replay, _ = setup(split="held_out")
    assert dev_manifest.split_digest != held_manifest.split_digest
    assert canonical_digest(dev_manifest) != canonical_digest(held_manifest)
    assert score_evaluation(held_manifest, held_replay, goldens).split == "held_out"
    with pytest.raises(EvaluationError, match="manifest_mismatch"):
        score_evaluation(held_manifest, dev_replay, goldens)


def test_wholly_missing_second_case_keeps_its_pages_fields_and_usage_unknown():
    manifest, replay, goldens = setup()
    other = copy.deepcopy(next(c for c in goldens.cases if c.case_key == "normal-complete"))
    other.case_key = "second-case"
    other.identity.case_id = "second-case"
    goldens = GoldenSuite(cases=(*goldens.cases, other))
    item = manifest.inputs[0].model_dump(mode="json")
    item["source"]["document"]["case_id"] = "second-case"
    inputs = (*manifest.inputs, EvaluationInput.model_validate(item))
    bindings = (
        *replay.bindings,
        GoldenBinding(case_id="second-case", golden_case_key="second-case"),
    )
    replay = replay.model_copy(update={"bindings": bindings})
    manifest, replay = repin(
        manifest,
        replay,
        inputs=[i.model_dump(mode="json") for i in inputs],
        dataset_digest=dataset_digest(inputs),
        split_digest=split_digest(manifest.split, bindings),
        golden_digest=golden_digest(goldens),
    )
    score = score_evaluation(manifest, replay, goldens).summary
    assert score.counts["scheduled_cases"] == 2 and score.counts["missing_cases"] == 1
    assert score.metrics["recall"].numerator == 13 and score.metrics["recall"].denominator == 26
    assert score.metrics["page_completeness"].denominator == 4
    assert score.metrics["case_completeness"].value == 0.5
    assert score.usage.total_attempts is None


def execution_setup(*, repeats=1):
    manifest, replay, goldens = setup(repeats=repeats)
    prototypes = {row.outcome.request.page: row.outcome for row in replay.outcomes}
    manifest, replay = repin(manifest, replay, execution_kind="mocked")
    seed = replay.model_copy(update={"outcomes": ()})
    return manifest, seed, goldens, prototypes


def test_runner_composes_provider_over_frozen_schedule_and_retains_repeats():
    manifest, seed, goldens, prototypes = execution_setup(repeats=2)
    called = []

    async def extract(request):
        called.append(request)
        return prototypes[request.page].model_copy(update={"request": request}, deep=True)

    replay, report = asyncio.run(run_evaluation(manifest, seed, goldens, extract))
    assert [request.page for request in called] == [1, 2, 1, 2]
    assert all(request.context.language == "zh-Hant" for request in called)
    assert called[0].run == called[1].run and called[2].run != called[0].run
    assert len(replay.outcomes) == 4 and replay.manifest_digest == canonical_digest(manifest)
    assert report.summary.metrics["recall"].value == 1
    assert report.differences[0].changed_pages == 0


@pytest.mark.parametrize("mode", ["raised_boundary", "raised_private", "unknown_usage", "timeout"])
def test_runner_stops_on_uncertain_spend_and_keeps_missing_denominators(mode):
    manifest, seed, goldens, prototypes = execution_setup()
    if mode == "timeout":
        budget = manifest.budget.model_dump(mode="json") | {"max_elapsed_seconds": 0.01}
        manifest, seed = repin(manifest, seed, budget=budget)
    called = []

    async def extract(request):
        called.append(request)
        if mode == "raised_boundary":
            raise ExtractionBoundaryError("access_denied")
        if mode == "raised_private":
            raise RuntimeError(CANARY)
        if mode == "timeout":
            await asyncio.Event().wait()
        result = prototypes[request.page].model_copy(update={"request": request}, deep=True)
        attempt = result.telemetry.attempts[0].model_copy(update={"output_tokens": None})
        return result.model_copy(
            update={"telemetry": result.telemetry.model_copy(update={"attempts": (attempt,)})}
        )

    replay, report = asyncio.run(run_evaluation(manifest, seed, goldens, extract))
    assert len(called) == 1
    assert report.summary.metrics["recall"].denominator == 13
    assert report.summary.usage.total_attempts is None
    assert report.summary.usage.output_tokens.total is None
    if mode == "unknown_usage":
        assert len(replay.outcomes) == 1 and not replay.execution_errors
    else:
        assert not replay.outcomes and len(replay.execution_errors) == 1
        assert report.summary.counts["runner_error_pages"] == 1
        assert report.summary.metrics["error_rate"].value == 0.5
    assert CANARY not in report.model_dump_json() + replay.model_dump_json()


def test_runner_validates_manifest_before_provider_call():
    from unittest.mock import AsyncMock

    manifest, seed, goldens, _ = execution_setup()
    callback = AsyncMock()
    seed = seed.model_copy(update={"manifest_digest": "0" * 64})
    with pytest.raises(EvaluationError, match="manifest_mismatch"):
        asyncio.run(run_evaluation(manifest, seed, goldens, callback))
    callback.assert_not_called()


@pytest.mark.parametrize("execute", [True, False])
def test_real_cli_orchestrates_explicit_provider_without_aws(tmp_path, execute):
    manifest, seed, _, prototypes = execution_setup()
    (tmp_path / "manifest.json").write_text(manifest.model_dump_json())
    (tmp_path / "seed.json").write_text(seed.model_dump_json())
    (tmp_path / "prototypes.json").write_text(
        json.dumps({page: outcome.model_dump(mode="json") for page, outcome in prototypes.items()})
    )
    (tmp_path / "evaluation_test_provider.py").write_text("""
import json
import os
from pathlib import Path
from appraisal_review.domain.extraction_contracts import PageOutcome

def compose(manifest):
    assert manifest.execution_kind == "mocked"
    prototypes = json.loads(Path(os.environ["EVALUATION_TEST_PROTOTYPES"]).read_text())
    async def extract(request):
        result = PageOutcome.model_validate(prototypes[str(request.page)])
        return result.model_copy(update={"request": request})
    return extract
""")
    args = [
        sys.executable,
        str(CLI),
        "--manifest",
        str(tmp_path / "manifest.json"),
        "--outcomes",
        str(tmp_path / "seed.json"),
        "--goldens",
        str(GOLDENS),
        "--output-dir",
        str(tmp_path / "reports"),
        "--provider-factory",
        "evaluation_test_provider:compose",
    ]
    if execute:
        args.append("--execute")
    result = subprocess.run(
        args,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=os.environ
        | {
            "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(tmp_path)]),
            "EVALUATION_TEST_PROTOTYPES": str(tmp_path / "prototypes.json"),
        },
    )
    if execute:
        assert result.returncode == 0, result.stderr
        report = EvaluationReport.model_validate_json(
            (tmp_path / "reports/report.json").read_bytes()
        )
        replay = EvaluationReplay.model_validate_json(
            (tmp_path / "reports/private-outcomes.json").read_bytes()
        )
        assert report.summary.metrics["recall"].value == 1 and len(replay.outcomes) == 2
        assert replay.manifest_digest == canonical_digest(manifest)
        assert "golden-target" not in result.stdout
    else:
        assert result.returncode == 2
        assert result.stderr == "Evaluation rejected: execution_opt_in_required.\n"
        assert not (tmp_path / "reports").exists()


def budget_execution_setup(boundary):
    manifest, seed, goldens, prototypes = execution_setup(repeats=2 if boundary == "repeat" else 1)
    if boundary == "case":
        other = copy.deepcopy(next(c for c in goldens.cases if c.case_key == "normal-complete"))
        other.case_key = "budget-second-case"
        other.identity.case_id = "budget-second-case"
        goldens = GoldenSuite(cases=(*goldens.cases, other))
        second = manifest.inputs[0].model_dump(mode="json")
        second["source"]["document"]["case_id"] = other.identity.case_id
        inputs = (*manifest.inputs, EvaluationInput.model_validate(second))
        bindings = (
            *seed.bindings,
            GoldenBinding(
                case_id=other.identity.case_id,
                golden_case_key=other.case_key,
            ),
        )
        seed = seed.model_copy(update={"bindings": bindings})
        manifest, seed = repin(
            manifest,
            seed,
            inputs=[i.model_dump(mode="json") for i in inputs],
            dataset_digest=dataset_digest(inputs),
            split_digest=split_digest(manifest.split, bindings),
            golden_digest=golden_digest(goldens),
        )
    return manifest, seed, goldens, prototypes


def budget_outcome(prototype, request, outputs):
    attempts = tuple(
        AttemptTelemetry(
            attempt=index + 1,
            completion="returned" if index == len(outputs) - 1 else "failed",
            failure=None if index == len(outputs) - 1 else "throttled",
            input_tokens=100,
            output_tokens=tokens,
            elapsed_seconds=0.1,
            backoff_seconds=0,
        )
        for index, tokens in enumerate(outputs)
    )
    return prototype.model_copy(
        update={
            "request": request,
            "telemetry": prototype.telemetry.model_copy(
                update={
                    "attempts": attempts,
                    "elapsed_seconds": len(attempts) * 0.2,
                }
            ),
        },
        deep=True,
    )


@pytest.mark.parametrize("boundary", ["case", "repeat"])
@pytest.mark.parametrize("resource", ["calls", "output_tokens"])
@pytest.mark.parametrize("shortfall,expected_calls", [(1, 2), (0, 3)])
def test_global_worst_case_reservation_survives_fresh_run_ledgers(
    boundary,
    resource,
    shortfall,
    expected_calls,
):
    manifest, seed, goldens, prototypes = budget_execution_setup(boundary)
    budget = manifest.budget.model_dump(mode="json") | {"max_attempts_per_page": 3}
    if resource == "calls":
        # Two known calls leave either exactly three attempts or one fewer.
        budget["max_calls"] = 2 + 3 - shortfall
    else:
        # Two known 20-token outputs leave a 3 * per-attempt reservation or one fewer.
        budget["max_output_tokens"] = 40 + 3 * manifest.configuration.max_output_tokens - shortfall
    manifest, seed = repin(manifest, seed, budget=budget)
    called = []
    run_calls = {}

    async def extract(request):
        called.append(request)
        run_calls[request.run.run_id] = run_calls.get(request.run.run_id, 0) + 1
        return budget_outcome(prototypes[request.page], request, [20])

    replay, report = asyncio.run(run_evaluation(manifest, seed, goldens, extract))
    assert len(called) == expected_calls
    assert called[0].run == called[1].run
    if expected_calls == 3:
        assert called[2].run != called[0].run and len(run_calls) == 2
    else:
        assert len(run_calls) == 1
    assert len(replay.outcomes) == expected_calls and not replay.execution_errors
    assert all(row.outcome.status == "candidate" for row in replay.outcomes)
    score = report.summary
    assert score.counts["scheduled_pages"] == 4
    assert score.counts["scheduled_cases"] == 2
    assert score.metrics["recall"].denominator == 26
    assert score.counts["missing_pages"] == 4 - expected_calls
    assert score.counts["missing_cases"] == (1 if expected_calls == 2 else 0)
    assert score.usage.observed_attempts == expected_calls
    assert score.usage.output_tokens.known == 20 * expected_calls
    assert score.usage.output_tokens.unobserved_pages == 4 - expected_calls
    assert score.usage.output_tokens.total is None and score.usage.total_attempts is None
    assert score.page_failures == {} and score.attempt_failures == {}


def test_worst_case_exact_fit_allows_every_reserved_attempt_then_stops():
    manifest, seed, goldens, prototypes = execution_setup()
    budget = manifest.budget.model_dump(mode="json") | {
        "max_calls": 3,
        "max_attempts_per_page": 3,
        "max_output_tokens": 3 * manifest.configuration.max_output_tokens,
    }
    manifest, seed = repin(manifest, seed, budget=budget)
    called = []

    async def extract(request):
        called.append(request)
        return budget_outcome(prototypes[request.page], request, [1000, 1000, 1000])

    replay, report = asyncio.run(run_evaluation(manifest, seed, goldens, extract))
    assert len(called) == 1 and len(replay.outcomes) == 1
    assert replay.outcomes[0].outcome.status == "candidate"
    assert report.summary.usage.observed_attempts == 3
    assert report.summary.usage.output_tokens.known == 3000
    assert report.summary.counts["missing_pages"] == 1
    assert report.summary.metrics["recall"].denominator == 13
    assert report.summary.usage.output_tokens.total is None


def test_no_provider_call_when_initial_output_budget_cannot_fit_worst_case():
    from unittest.mock import AsyncMock

    manifest, seed, goldens, _ = execution_setup()
    budget = manifest.budget.model_dump(mode="json") | {
        "max_attempts_per_page": 3,
        "max_output_tokens": 2999,
    }
    manifest, seed = repin(manifest, seed, budget=budget)
    callback = AsyncMock()
    replay, report = asyncio.run(run_evaluation(manifest, seed, goldens, callback))
    callback.assert_not_called()
    assert replay.outcomes == replay.execution_errors == ()
    assert report.summary.counts["missing_pages"] == 2
    assert report.summary.counts["missing_cases"] == 1
    assert report.summary.metrics["recall"].denominator == 13
    assert report.summary.usage.output_tokens.known == 0
    assert report.summary.usage.output_tokens.total is None
    assert report.summary.usage.total_attempts is None
    assert report.summary.page_failures == {}


@pytest.mark.parametrize(
    "violation",
    [
        "page_attempts",
        "attempt_output_tokens",
        "global_calls",
        "global_output_tokens",
        "unknown_usage_with_excess_attempts",
    ],
)
def test_callback_budget_violation_fails_page_without_erasing_billed_evidence(violation):
    manifest, seed, goldens, prototypes = execution_setup()
    budget = manifest.budget.model_dump(mode="json")
    outputs = (
        [20, 20, 20]
        if violation
        in {
            "page_attempts",
            "global_calls",
            "unknown_usage_with_excess_attempts",
        }
        else [1001]
    )
    if violation == "global_calls":
        budget["max_calls"] = 2
    elif violation == "global_output_tokens":
        budget["max_output_tokens"] = 2000
        outputs = [1000, 1001]
    elif violation == "unknown_usage_with_excess_attempts":
        outputs = [20, None, 20]
    manifest, seed = repin(manifest, seed, budget=budget)
    returned = []

    async def extract(request):
        result = budget_outcome(prototypes[request.page], request, outputs)
        returned.append(result)
        return result

    replay, report = asyncio.run(run_evaluation(manifest, seed, goldens, extract))
    assert len(returned) == 1 and len(replay.outcomes) == 1
    original, rejected = returned[0], replay.outcomes[0].outcome
    assert original.status == "candidate" and original.proposal is not None
    assert rejected.status == "failed" and rejected.failure == "budget_exhausted"
    assert rejected.proposal is None
    assert rejected.telemetry == original.telemetry
    assert rejected.telemetry.attempts[-1].failure is None
    assert any(h.reason == "page_failed" and h.locations[0].page == 1 for h in rejected.handoffs)
    assert not replay.execution_errors  # An actual outcome exists; no fabricated unknown attempt.
    score = report.summary
    assert score.page_failures == {"budget_exhausted": 1}
    assert score.metrics["error_rate"].value == 0.5
    assert score.metrics["case_accuracy"].value == 0
    assert score.metrics["recall"].value == 0 and score.metrics["recall"].denominator == 13
    assert score.usage.observed_attempts == len(outputs)
    assert score.usage.input_tokens.known == len(outputs) * 100
    assert score.usage.output_tokens.known == sum(value for value in outputs if value is not None)
    assert score.usage.output_tokens.unknown_attempts == outputs.count(None)
    assert score.usage.output_tokens.total is None
    assert score.counts["missing_pages"] == 1
    assert "budget_exhausted" in english_summary(report)


def test_last_page_budget_violation_retains_known_totals_and_blocks_case_accuracy():
    manifest, seed, goldens, prototypes = execution_setup()
    called = []

    async def extract(request):
        called.append(request)
        return budget_outcome(
            prototypes[request.page], request, [20 if request.page == 1 else 1001]
        )

    replay, report = asyncio.run(run_evaluation(manifest, seed, goldens, extract))
    assert len(called) == 2 and len(replay.outcomes) == 2
    assert replay.outcomes[0].outcome.status == "candidate"
    assert replay.outcomes[1].outcome.failure == "budget_exhausted"
    assert report.summary.counts["missing_pages"] == 0
    assert report.summary.usage.total_attempts == 2
    assert report.summary.usage.output_tokens.total == 1021
    assert report.summary.metrics["case_completeness"].value == 1
    assert report.summary.metrics["case_accuracy"].value == 0


@pytest.mark.parametrize("resource", ["calls", "output_tokens"])
def test_replay_global_budget_violation_reports_failure_and_preserves_original(resource):
    manifest, replay, goldens = setup(repeats=2)
    budget = manifest.budget.model_dump(mode="json")
    outcomes = list(replay.outcomes)
    if resource == "calls":
        budget["max_calls"] = 4
        original = outcomes[0].outcome
        outcomes[0] = outcomes[0].model_copy(
            update={
                "outcome": budget_outcome(original, original.request, [20, 20]),
            }
        )
    else:
        budget["max_output_tokens"] = 3000
        outcomes = [
            row.model_copy(
                update={
                    "outcome": budget_outcome(row.outcome, row.outcome.request, [1000]),
                }
            )
            for row in outcomes
        ]
    manifest, replay = repin(manifest, replay, budget=budget)
    replay = replay.model_copy(update={"outcomes": tuple(outcomes)})
    original_bytes = replay.model_dump_json()
    report = score_evaluation(manifest, replay, goldens)
    assert replay.model_dump_json() == original_bytes
    assert all(row.outcome.status == "candidate" for row in replay.outcomes)
    assert report.summary.page_failures == {"budget_exhausted": 1}
    assert report.summary.counts["failed_pages"] == 1
    assert report.summary.counts["missing_pages"] == 0
    assert report.summary.metrics["case_accuracy"].value == 0.5
    if resource == "calls":
        assert report.summary.usage.total_attempts == 5
    else:
        assert report.summary.usage.output_tokens.total == 4000
