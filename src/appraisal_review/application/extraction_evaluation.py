"""Offline evaluation of exact extraction outcomes against independently authored goldens."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
import uuid
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from itertools import combinations
from typing import Any, Literal

from pydantic import BaseModel

from appraisal_review.domain.document_models import Box, SourceCitation
from appraisal_review.domain.evaluation_contracts import (
    EvaluationInput,
    EvaluationManifest,
    ScoringPolicy,
)
from appraisal_review.domain.evaluation_report import (
    CostEstimate,
    EvaluationExecutionError,
    EvaluationReplay,
    EvaluationReport,
    GoldenBinding,
    Metric,
    Quantity,
    RepeatDifference,
    RepeatedOutcome,
    ScoreSummary,
    TokenQuantity,
    UsageReport,
)
from appraisal_review.domain.extraction_contracts import (
    ExtractionContext,
    HandoffLocation,
    HandoffRequest,
    PageOutcome,
    PageRequest,
)
from appraisal_review.domain.golden_contract import ExpectedSlot, GoldenCase, GoldenSuite
from appraisal_review.domain.review_contracts import ObservedValue, ReviewSlot
from appraisal_review.domain.service_contracts import RevisionReference, RunReference
from appraisal_review.ports.document_extraction import ExtractionBoundaryError

SCORER_VERSION = "observed-slots-v1"
UNIT_CONVERSION_POLICY = {"version": "ratio-percent-points-v1", "ratio_to_percent_points": "100"}
PageKey = tuple[str, str, int]
FieldKey = tuple[str, str]


class EvaluationError(ValueError):
    """Only fixed reason codes may cross the CLI boundary."""


def canonical_digest(value: Any) -> str:
    """SHA-256 of UTF-8, sorted-key compact JSON, with model JSON serialization."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def dataset_digest(inputs: Sequence[EvaluationInput]) -> str:
    return canonical_digest([item.model_dump(mode="json") for item in inputs])


def split_digest(split: str, bindings: Sequence[GoldenBinding]) -> str:
    return canonical_digest(
        {"split": split, "bindings": [b.model_dump(mode="json") for b in bindings]}
    )


def golden_digest(goldens: GoldenSuite) -> str:
    return canonical_digest(
        {
            "schema_version": goldens.schema_version,
            "cases": [
                c.model_dump(mode="json") for c in sorted(goldens.cases, key=lambda c: c.case_key)
            ],
        }
    )


def scoring_policy_digest(policy: ScoringPolicy) -> str:
    return canonical_digest(policy.model_dump(mode="json", exclude={"policy_digest"}))


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise EvaluationError(code)


def _page_key(outcome: PageOutcome) -> PageKey:
    document = outcome.request.source.document
    return document.case_id, document.document_id, outcome.request.page


def _known_output_tokens(outcome: PageOutcome) -> int:
    return sum(
        attempt.output_tokens
        for attempt in outcome.telemetry.attempts
        if attempt.output_tokens is not None
    )


def _check_recorded_budget(
    outcome: PageOutcome,
    manifest: EvaluationManifest,
    used_calls: int,
    used_output_tokens: int,
) -> tuple[PageOutcome, bool]:
    """Reject an over-budget result without discarding any recorded billed attempt."""
    attempts = outcome.telemetry.attempts
    exceeded = (
        len(attempts) > manifest.budget.max_attempts_per_page
        or any(
            attempt.output_tokens is not None
            and attempt.output_tokens > manifest.configuration.max_output_tokens
            for attempt in attempts
        )
        or used_calls + len(attempts) > manifest.budget.max_calls
        or used_output_tokens + _known_output_tokens(outcome) > manifest.budget.max_output_tokens
    )
    if not exceeded:
        return outcome, False
    handoffs = outcome.handoffs
    if not any(handoff.reason == "page_failed" for handoff in handoffs):
        handoffs = (
            *handoffs,
            HandoffRequest(
                request=outcome.request,
                reason="page_failed",
                locations=(HandoffLocation(page=outcome.request.page),),
            ),
        )
    failed = PageOutcome.model_validate(
        outcome.model_dump()
        | {
            "status": "failed",
            "proposal": None,
            "failure": "budget_exhausted",
            "handoffs": handoffs,
        }
    )
    return failed, True


@dataclass(frozen=True)
class _Schedule:
    pages: dict[PageKey, EvaluationInput]
    cases: dict[str, GoldenCase]
    expected: dict[FieldKey, ExpectedSlot]
    outcomes: dict[int, dict[PageKey, PageOutcome]]
    execution_errors: dict[int, dict[PageKey, EvaluationExecutionError]]


def _schedule(
    manifest: EvaluationManifest, replay: EvaluationReplay, goldens: GoldenSuite
) -> _Schedule:
    _require(replay.manifest_digest == canonical_digest(manifest), "manifest_mismatch")
    _require(manifest.dataset_digest == dataset_digest(manifest.inputs), "dataset_mismatch")
    _require(manifest.golden_schema_version == goldens.schema_version, "golden_version_mismatch")
    _require(manifest.golden_digest == golden_digest(goldens), "golden_digest_mismatch")
    _require(
        manifest.split_digest == split_digest(manifest.split, replay.bindings), "split_mismatch"
    )
    _require(manifest.dataset_kind == "synthetic", "unsupported_golden_origin")
    _require(
        manifest.scoring.policy_digest == scoring_policy_digest(manifest.scoring),
        "scoring_policy_mismatch",
    )
    if manifest.scoring.unit_policy == "versioned_conversion":
        _require(
            manifest.scoring.unit_policy_digest == canonical_digest(UNIT_CONVERSION_POLICY),
            "unsupported_unit_policy",
        )
    cases_by_key = {case.case_key: case for case in goldens.cases}
    cases: dict[str, GoldenCase] = {}
    for binding in replay.bindings:
        _require(binding.case_id not in cases, "duplicate_case_binding")
        case = cases_by_key.get(binding.golden_case_key)
        _require(case is not None, "unknown_golden_case")
        assert case is not None
        _require(case.identity.case_id == binding.case_id, "golden_case_mismatch")
        cases[binding.case_id] = case
    _require(
        set(cases) == {item.source.document.case_id for item in manifest.inputs},
        "case_denominator_mismatch",
    )
    pages: dict[PageKey, EvaluationInput] = {}
    for item in manifest.inputs:
        document = item.source.document
        case = cases[document.case_id]
        source = next(
            (d for d in case.fixture.documents if d.document_id == document.document_id), None
        )
        _require(source is not None, "golden_document_mismatch")
        assert source is not None
        _require(
            (source.version, source.content_hash, source.role, source.page_count)
            == (document.version, document.content_hash, document.purpose, item.source.page_count),
            "golden_source_mismatch",
        )
        # Whole-case goldens cannot silently become a successful-page subset benchmark.
        _require(
            set(item.pages) == set(range(1, source.page_count + 1)), "page_denominator_mismatch"
        )
        for page in item.pages:
            pages[document.case_id, document.document_id, page] = item
    expected: dict[FieldKey, ExpectedSlot] = {}
    for case_id, case in cases.items():
        for provenance in case.fixture.documents:
            if provenance.role == "forms":
                _require(
                    all(
                        (case_id, provenance.document_id, p) in pages
                        for p in range(1, provenance.page_count + 1)
                    ),
                    "field_denominator_mismatch",
                )
        for slot in case.expected_slots:
            ref = slot.observed.citation
            if ref is not None:
                cited_input = pages.get((case_id, ref.document_id, ref.page))
                _require(cited_input is not None, "golden_evidence_page_mismatch")
                assert cited_input is not None
                document = cited_input.source.document
                _require(
                    (ref.version, ref.content_hash) == (document.version, document.content_hash)
                    and _valid_box(ref.bbox),
                    "golden_evidence_source_mismatch",
                )
            if slot.observed.unit in {"ratio", "percent_points"}:
                _require(_number(slot.observed.value) is not None, "invalid_golden_number")
            expected[case_id, slot.slot_id] = slot
    outcomes: dict[int, dict[PageKey, PageOutcome]] = {
        repeat: {} for repeat in range(1, manifest.repeats + 1)
    }
    run_bindings: dict[tuple[int, str], str] = {}
    run_owners: dict[str, tuple[int, str]] = {}
    for record in replay.outcomes:
        _require(record.repeat in outcomes, "repeat_outside_schedule")
        outcome = record.outcome
        key = _page_key(outcome)
        _require(key in pages, "outcome_outside_schedule")
        _require(key not in outcomes[record.repeat], "duplicate_page_outcome")
        _require(outcome.request.source == pages[key].source, "outcome_source_mismatch")
        case_id = key[0]
        revision = outcome.request.run.revision
        _require(
            revision.revision_id == cases[case_id].identity.version
            and revision.material_digest == cases[case_id].fixture.material_digest,
            "outcome_revision_mismatch",
        )
        telemetry = outcome.telemetry
        _require(
            telemetry.configuration == manifest.configuration
            and telemetry.prompt_digest == manifest.prompt_digest
            and telemetry.prompt_version == manifest.prompt_version,
            "outcome_configuration_mismatch",
        )
        owner = record.repeat, case_id
        run_id = str(outcome.request.run.run_id)
        _require(run_bindings.get(owner, run_id) == run_id, "inconsistent_case_run")
        _require(run_owners.get(run_id, owner) == owner, "reused_repeat_run")
        run_bindings[owner], run_owners[run_id] = run_id, owner
        outcomes[record.repeat][key] = outcome
    execution_errors: dict[int, dict[PageKey, EvaluationExecutionError]] = {
        repeat: {} for repeat in outcomes
    }
    for error in replay.execution_errors:
        _require(error.repeat in outcomes, "repeat_outside_schedule")
        _require(error.input_index < len(manifest.inputs), "error_outside_schedule")
        item = manifest.inputs[error.input_index]
        error_key = item.source.document.case_id, item.source.document.document_id, error.page
        _require(error_key in pages, "error_outside_schedule")
        _require(
            error_key not in outcomes[error.repeat]
            and error_key not in execution_errors[error.repeat],
            "duplicate_page_outcome",
        )
        execution_errors[error.repeat][error_key] = error
    _validate_prices(manifest, replay)
    # Replays use the same declared-limit gate, in fixed manifest/repeat order.
    # Keep the caller's original replay untouched; only the scoring projection fails.
    used_calls = used_output_tokens = 0
    for repeated in outcomes.values():
        for scheduled_key in pages:
            recorded = repeated.get(scheduled_key)
            if recorded is None:
                continue
            repeated[scheduled_key], _ = _check_recorded_budget(
                recorded, manifest, used_calls, used_output_tokens
            )
            used_calls += len(recorded.telemetry.attempts)
            used_output_tokens += _known_output_tokens(recorded)
    return _Schedule(pages, cases, expected, outcomes, execution_errors)


def _validate_prices(manifest: EvaluationManifest, replay: EvaluationReplay) -> None:
    evidence, policy = replay.pricing_evidence, manifest.cost_policy
    if evidence is None:
        return
    _require(policy is not None, "unexpected_rate_evidence")
    assert policy is not None
    _require(
        canonical_digest(evidence) == policy.rates_digest
        and evidence.configuration_digest == canonical_digest(manifest.configuration)
        and all(
            getattr(evidence, key) == getattr(policy, key)
            for key in (
                "currency",
                "rates_date",
                "rates_source",
                "input_per_million",
                "output_per_million",
            )
        ),
        "rate_evidence_mismatch",
    )


def _metric(numerator: int, denominator: int, *, measured: bool = True) -> Metric:
    return Metric(
        numerator=numerator,
        denominator=denominator,
        value=numerator / denominator if denominator and measured else None,
    )


def _number(value: str | Decimal | None) -> Decimal | None:
    if value is None:
        return None
    try:
        result = Decimal(value)
        return result if result.is_finite() else None
    except InvalidOperation:
        return None


def _valid_box(box: Box) -> bool:
    x0, y0, x1, y1 = box
    return all(math.isfinite(n) for n in box) and 0 <= x0 < x1 and 0 <= y0 < y1


def _iou(left: Box, right: Box) -> float:
    if not _valid_box(left) or not _valid_box(right):
        return 0.0
    area = max(0.0, min(left[2], right[2]) - max(left[0], right[0])) * max(
        0.0, min(left[3], right[3]) - max(left[1], right[1])
    )
    union = (
        (left[2] - left[0]) * (left[3] - left[1])
        + (right[2] - right[0]) * (right[3] - right[1])
        - area
    )
    return area / union


def _source_match(expected: SourceCitation, actual: SourceCitation) -> bool:
    return (
        expected.document_id,
        expected.content_hash,
        expected.version,
        expected.page,
        expected.region_id,
    ) == (
        actual.document_id,
        actual.content_hash,
        actual.version,
        actual.page,
        actual.region_id,
    ) and _valid_box(actual.bbox)


def _same_unit(expected: str | None, actual: str | None, policy: ScoringPolicy) -> bool:
    return expected == actual or (
        policy.unit_policy == "versioned_conversion"
        and {expected, actual} == {"percent_points", "ratio"}
    )


def _value_match(slot: ExpectedSlot, actual: ObservedValue, policy: ScoringPolicy) -> bool:
    observed = slot.observed
    if observed.state != actual.state or not _same_unit(observed.unit, actual.unit, policy):
        return False
    if observed.state != "present":
        return actual.value is None and actual.unit is None
    if observed.unit == "grade":
        return isinstance(actual.value, str) and observed.value == actual.value
    left, right = _number(observed.value), _number(actual.value)
    if left is None or right is None:
        return False
    if observed.unit != actual.unit:
        right *= Decimal(100) if actual.unit == "ratio" else Decimal("0.01")
    return abs(left - right) <= max(
        policy.numeric_absolute_tolerance, policy.numeric_relative_tolerance * abs(left)
    )


def _type_match(
    expected: ExpectedSlot,
    actual: ObservedValue,
    declarations: list[ReviewSlot],
    policy: ScoringPolicy,
) -> bool:
    if len(declarations) != 1:
        return False
    declared = declarations[0]
    if (declared.value, declared.context, declared.factor_id) != (
        expected.slot_value,
        expected.context,
        expected.factor_id,
    ) or not _same_unit(expected.observed.unit, actual.unit, policy):
        return False
    if actual.state != "present":
        return actual.value is None and actual.unit is None
    if actual.unit == "grade":
        return isinstance(actual.value, str)
    return _number(actual.value) is not None


def _quantity(values: Sequence[float], missing: int) -> Quantity:
    known = sum(values)
    return Quantity(known=known, total=None if missing else known, unknown_records=missing)


def _usage(
    outcomes: list[PageOutcome],
    missing_pages: int,
    callback_errors: Sequence[EvaluationExecutionError],
) -> UsageReport:
    attempts = [a for o in outcomes for a in o.telemetry.attempts]
    retries = [a for a in attempts if a.attempt > 1]

    def tokens(key: str, *, retry: bool = False) -> TokenQuantity:
        values = [getattr(a, key) for a in (retries if retry else attempts)]
        known = sum(v for v in values if v is not None)
        unknown = sum(v is None for v in values)
        return TokenQuantity(
            known=known,
            total=None if unknown or missing_pages else known,
            unknown_attempts=unknown,
            unobserved_pages=missing_pages,
        )

    elapsed = sorted(o.telemetry.elapsed_seconds for o in outcomes)

    def percentile(fraction: float) -> float | None:
        return elapsed[max(0, math.ceil(len(elapsed) * fraction) - 1)] if elapsed else None

    return UsageReport(
        observed_attempts=len(attempts),
        total_attempts=None if missing_pages else len(attempts),
        observed_retries=len(retries),
        total_retries=None if missing_pages else len(retries),
        unknown_completion_attempts=sum(a.completion == "unknown" for a in attempts),
        input_tokens=tokens("input_tokens"),
        output_tokens=tokens("output_tokens"),
        retry_input_tokens=tokens("input_tokens", retry=True),
        retry_output_tokens=tokens("output_tokens", retry=True),
        page_elapsed_seconds=_quantity(elapsed, missing_pages),
        attempt_elapsed_seconds=_quantity([a.elapsed_seconds for a in attempts], missing_pages),
        backoff_seconds=_quantity([a.backoff_seconds for a in attempts], missing_pages),
        retry_elapsed_seconds=_quantity(
            [a.elapsed_seconds + a.backoff_seconds for a in retries], missing_pages
        ),
        callback_error_elapsed_seconds=sum(e.elapsed_seconds for e in callback_errors),
        mean_observed_page_seconds=sum(elapsed) / len(elapsed) if elapsed else None,
        p50_observed_page_seconds=percentile(0.5),
        p95_observed_page_seconds=percentile(0.95),
    )


def _cost(
    manifest: EvaluationManifest, replay: EvaluationReplay, usage: UsageReport
) -> CostEstimate:
    policy = manifest.cost_policy
    if policy is None or replay.pricing_evidence is None:
        return CostEstimate(
            amount=None,
            known_usage_subtotal=None,
            currency=policy.currency if policy else None,
            evidence_digest=None,
            reason="no_rate_evidence" if policy else "no_policy",
            exceeds_ceiling=None,
        )
    known = (
        Decimal(usage.input_tokens.known) * policy.input_per_million
        + Decimal(usage.output_tokens.known) * policy.output_per_million
    ) / Decimal(1_000_000)
    complete = usage.input_tokens.total is not None and usage.output_tokens.total is not None
    return CostEstimate(
        amount=known if complete else None,
        known_usage_subtotal=known,
        currency=policy.currency,
        evidence_digest=policy.rates_digest,
        reason="estimated" if complete else "unknown_usage",
        exceeds_ceiling=known > policy.estimated_cost_ceiling if complete else None,
    )


def _score(
    manifest: EvaluationManifest,
    replay: EvaluationReplay,
    schedule: _Schedule,
    repeats: Sequence[int],
) -> ScoreSummary:
    counts: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    expected_states: Counter[str] = Counter()
    predicted_states: Counter[str] = Counter()
    confusion: dict[str, Counter[str]] = defaultdict(Counter)
    page_failures: Counter[str] = Counter()
    attempt_failures: Counter[str] = Counter()
    handoffs: Counter[str] = Counter()
    unscored: Counter[str] = Counter()
    all_outcomes: list[PageOutcome] = []
    policy = manifest.scoring
    for repeat in repeats:
        outputs = schedule.outcomes[repeat]
        counts["scheduled_pages"] += len(schedule.pages)
        counts["scheduled_cases"] += len(schedule.cases)
        counts["observed_pages"] += len(outputs)
        counts["missing_pages"] += len(schedule.pages) - len(outputs)
        seen: set[FieldKey] = set()
        correct: set[FieldKey] = set()
        review_cases: set[str] = set()
        for case_id in schedule.cases:
            keys = [key for key in schedule.pages if key[0] == case_id]
            present = [outputs[key] for key in keys if key in outputs]
            counts["observed_cases"] += bool(present)
            counts["missing_cases"] += not present
            counts["complete_case_outcomes"] += len(present) == len(keys)
            counts["all_candidate_cases"] += len(present) == len(keys) and all(
                o.status == "candidate" for o in present
            )
            if len(present) != len(keys):
                review_cases.add(case_id)
        for key in schedule.pages:
            outcome = outputs.get(key)
            if outcome is None:
                counts["review_required_pages"] += 1
                callback_error = schedule.execution_errors[repeat].get(key)
                if callback_error is not None:
                    counts["runner_error_pages"] += 1
                    counts["failed_pages"] += 1
                    counts["refused_pages"] += callback_error.failure == "refused"
                    page_failures[callback_error.failure] += 1
                continue
            all_outcomes.append(outcome)
            case_id = key[0]
            counts["candidate_pages"] += outcome.status == "candidate"
            counts["failed_pages"] += outcome.status == "failed"
            counts["refused_pages"] += outcome.failure == "refused"
            counts["handoff_pages"] += bool(outcome.handoffs)
            if outcome.failure:
                page_failures[outcome.failure] += 1
            for attempt in outcome.telemetry.attempts:
                if attempt.failure:
                    attempt_failures[attempt.failure] += 1
            for handoff in outcome.handoffs:
                handoffs[handoff.reason] += 1
            proposal = outcome.proposal
            review = (
                outcome.status == "failed"
                or bool(outcome.handoffs)
                or (proposal is not None and bool(proposal.unresolved or proposal.unsupported))
            )
            counts["review_required_pages"] += review
            if review:
                review_cases.add(case_id)
            if proposal is None:
                continue
            for name in ("rules", "pairs", "contexts", "checks", "empty_columns"):
                unscored[name] += len(getattr(proposal, name))
            counts["unresolved_items"] += len(proposal.unresolved)
            counts["unsupported_items"] += len(proposal.unsupported)
            declarations: dict[str, list[ReviewSlot]] = defaultdict(list)
            for declaration in proposal.slots:
                declarations[declaration.id].append(declaration)
            observed_ids = {v.slot_id for v in proposal.observed}
            counts["unobserved_slot_declarations"] += sum(
                s.id not in observed_ids for s in proposal.slots
            )
            for actual in proposal.observed:
                counts["predictions"] += 1
                predicted_states[actual.state] += 1
                field_key = case_id, actual.slot_id
                expected = schedule.expected.get(field_key)
                if expected is None:
                    errors["unknown_field"] += 1
                    continue
                if field_key in seen:
                    errors["duplicate_prediction"] += 1
                    continue
                seen.add(field_key)
                counts["identity_matches"] += 1
                confusion[expected.observed.state][actual.state] += 1
                state_ok = expected.observed.state == actual.state
                type_ok = _type_match(expected, actual, declarations[actual.slot_id], policy)
                value_ok = _value_match(expected, actual, policy)
                counts["state_correct"] += state_ok
                counts["type_correct"] += type_ok
                counts["value_correct"] += value_ok
                if not state_ok:
                    errors["state_mismatch"] += 1
                if not type_ok:
                    errors["type_mismatch"] += 1
                if not value_ok:
                    errors["value_mismatch"] += 1
                ref = expected.observed.citation
                source_ok = ref is None and actual.state not in {"present", "blank"}
                localized = False
                if ref is not None:
                    # Extra unrelated evidence must not hide behind one correct citation.
                    source_ok = bool(actual.evidence) and all(
                        _source_match(ref, evidence) for evidence in actual.evidence
                    )
                    localized = source_ok and all(
                        _iou(ref.bbox, evidence.bbox) > 0
                        and _iou(ref.bbox, evidence.bbox) >= (policy.minimum_iou or 0)
                        for evidence in actual.evidence
                    )
                    counts["source_correct"] += source_ok
                    if policy.localization == "region_iou":
                        counts["localization_correct"] += localized
                    if not source_ok:
                        errors["source_mismatch"] += 1
                    if policy.localization == "region_iou" and not localized:
                        errors["localization_mismatch"] += 1
                if (
                    type_ok
                    and value_ok
                    and source_ok
                    and (ref is None or policy.localization == "not_measured" or localized)
                ):
                    correct.add(field_key)
        counts["correct_fields"] += len(correct)
        counts["missing_predictions"] += len(schedule.expected) - len(seen)
        errors["missing_prediction"] += len(schedule.expected) - len(seen)
        for field_key, expected in schedule.expected.items():
            counts["expected_fields"] += 1
            counts["expected_cited_fields"] += expected.observed.citation is not None
            expected_states[expected.observed.state] += 1
            if field_key not in seen:
                confusion[expected.observed.state]["no_prediction"] += 1
        for case_id in schedule.cases:
            case_expected = {key for key in schedule.expected if key[0] == case_id}
            if not case_expected <= correct:
                review_cases.add(case_id)
            complete = all(key in outputs for key in schedule.pages if key[0] == case_id)
            counts["fully_correct_cases"] += (
                bool(case_expected)
                and case_expected <= correct
                and complete
                and all(
                    outputs[key].status == "candidate"
                    for key in schedule.pages
                    if key[0] == case_id
                )
            )
        counts["review_required_cases"] += len(review_cases)
    # Emit zero counts explicitly so a run with no outcomes still has the whole denominator.
    for count_name in (
        "scheduled_pages",
        "scheduled_cases",
        "observed_pages",
        "missing_pages",
        "observed_cases",
        "missing_cases",
        "complete_case_outcomes",
        "all_candidate_cases",
        "candidate_pages",
        "failed_pages",
        "refused_pages",
        "handoff_pages",
        "review_required_pages",
        "predictions",
        "expected_fields",
        "expected_cited_fields",
        "identity_matches",
        "correct_fields",
        "state_correct",
        "type_correct",
        "value_correct",
        "source_correct",
        "localization_correct",
        "missing_predictions",
        "fully_correct_cases",
        "review_required_cases",
        "runner_error_pages",
    ):
        counts.setdefault(count_name, 0)
    counts["false_positives"] = counts["predictions"] - counts["correct_fields"]
    counts["false_negatives"] = counts["expected_fields"] - counts["correct_fields"]
    expected_n, predicted_n = counts["expected_fields"], counts["predictions"]
    scheduled_n = counts["scheduled_pages"]
    metrics = {
        "precision": _metric(counts["correct_fields"], predicted_n),
        "recall": _metric(counts["correct_fields"], expected_n),
        "identity_precision": _metric(counts["identity_matches"], predicted_n),
        "identity_recall": _metric(counts["identity_matches"], expected_n),
        "type_accuracy": _metric(counts["type_correct"], expected_n),
        "value_accuracy": _metric(counts["value_correct"], expected_n),
        "state_accuracy": _metric(counts["state_correct"], expected_n),
        "source_accuracy": _metric(counts["source_correct"], counts["expected_cited_fields"]),
        "localization_accuracy": _metric(
            counts["localization_correct"],
            counts["expected_cited_fields"],
            measured=policy.localization == "region_iou",
        ),
        "page_completeness": _metric(counts["observed_pages"], scheduled_n),
        "candidate_page_rate": _metric(counts["candidate_pages"], scheduled_n),
        "case_completeness": _metric(counts["complete_case_outcomes"], counts["scheduled_cases"]),
        "case_accuracy": _metric(counts["fully_correct_cases"], counts["scheduled_cases"]),
        "error_rate": _metric(counts["failed_pages"], scheduled_n),
        "refusal_rate": _metric(counts["refused_pages"], scheduled_n),
        "human_intervention_rate": _metric(counts["handoff_pages"], scheduled_n),
        "review_required_page_rate": _metric(counts["review_required_pages"], scheduled_n),
        "review_required_case_rate": _metric(
            counts["review_required_cases"], counts["scheduled_cases"]
        ),
    }
    usage = _usage(
        all_outcomes,
        counts["missing_pages"],
        [error for repeat in repeats for error in schedule.execution_errors[repeat].values()],
    )
    return ScoreSummary(
        counts=dict(counts),
        metrics=metrics,
        expected_states=dict(expected_states),
        predicted_states=dict(predicted_states),
        state_confusion=dict(confusion),
        field_errors=dict(errors),
        page_failures=dict(page_failures),
        attempt_failures=dict(attempt_failures),
        handoff_reasons=dict(handoffs),
        unscored_proposals=dict(unscored),
        usage=usage,
        cost=_cost(manifest, replay, usage),
    )


def _signature(outcome: PageOutcome) -> dict[str, Any]:
    """Private comparison projections, never serialized into the report."""
    proposal = outcome.proposal
    observations = proposal.observed if proposal else []

    def multiset(values: Any) -> Counter[str]:
        return Counter(canonical_digest(value) for value in values)

    return {
        "status": (outcome.status, outcome.failure),
        "field_identity": multiset(o.slot_id for o in observations),
        "field_state": multiset((o.slot_id, o.state) for o in observations),
        "field_value": multiset((o.slot_id, str(o.value), o.unit) for o in observations),
        "field_type": multiset(
            (s.id, s.value, s.factor_id, s.context.model_dump(mode="json"))
            for s in (proposal.slots if proposal else [])
        ),
        "field_evidence": multiset(
            (o.slot_id, [e.model_dump(mode="json", exclude={"excerpt"}) for e in o.evidence])
            for o in observations
        ),
        "handoffs": multiset(
            (h.reason, [location.model_dump(mode="json") for location in h.locations])
            for h in outcome.handoffs
        ),
        "usage": [
            (a.attempt, a.completion, a.failure, a.input_tokens, a.output_tokens)
            for a in outcome.telemetry.attempts
        ],
        "timing": (
            outcome.telemetry.elapsed_seconds,
            [(a.elapsed_seconds, a.backoff_seconds) for a in outcome.telemetry.attempts],
        ),
        "other_proposals": proposal.model_dump(
            mode="json", exclude={"observed", "slots", "accounted_table_ids"}
        )
        if proposal
        else None,
    }


def _differences(
    schedule: _Schedule, scores: tuple[ScoreSummary, ...]
) -> tuple[RepeatDifference, ...]:
    differences = []
    for left, right in combinations(schedule.outcomes, 2):
        lhs, rhs = schedule.outcomes[left], schedule.outcomes[right]
        changes: Counter[str] = Counter()
        changed_pages = 0
        for key in schedule.pages:
            if key not in lhs or key not in rhs:
                lerror = schedule.execution_errors[left].get(key)
                rerror = schedule.execution_errors[right].get(key)
                error_changed = ((lerror.failure, lerror.elapsed_seconds) if lerror else None) != (
                    (rerror.failure, rerror.elapsed_seconds) if rerror else None
                )
                if error_changed:
                    changes["execution_failure"] += 1
                if (key in lhs) != (key in rhs):
                    changes["availability"] += 1
                if error_changed or (key in lhs) != (key in rhs):
                    changed_pages += 1
                continue
            lvalues, rvalues = _signature(lhs[key]), _signature(rhs[key])
            changed = [name for name in lvalues if lvalues[name] != rvalues[name]]
            changes.update(changed)
            changed_pages += bool(changed)
        deltas: dict[str, float | None] = {}
        for name, metric in scores[left - 1].metrics.items():
            lvalue, rvalue = metric.value, scores[right - 1].metrics[name].value
            deltas[name] = rvalue - lvalue if lvalue is not None and rvalue is not None else None
        differences.append(
            RepeatDifference(
                left_repeat=left,
                right_repeat=right,
                scheduled_pages=len(schedule.pages),
                observed_in_both=len(lhs.keys() & rhs.keys()),
                missing_left=len(schedule.pages) - len(lhs),
                missing_right=len(schedule.pages) - len(rhs),
                changed_pages=changed_pages,
                changes=dict(changes),
                metric_differences=deltas,
            )
        )
    return tuple(differences)


def score_evaluation(
    manifest: EvaluationManifest, replay: EvaluationReplay, goldens: GoldenSuite
) -> EvaluationReport:
    """Score replay or captured live outcomes without invoking a provider or changing inputs.

    The caller supplies exactly the existing EvaluationManifest and independently loaded
    GoldenSuite. Missing outcomes stay in denominators. Invalid bindings fail closed.
    """
    try:
        manifest = EvaluationManifest.model_validate(manifest)
        replay = EvaluationReplay.model_validate(replay)
        goldens = GoldenSuite.model_validate(goldens)
    except (ValueError, TypeError):
        raise EvaluationError("invalid_evaluation_input") from None
    schedule = _schedule(manifest, replay, goldens)
    repeats = tuple(_score(manifest, replay, schedule, [n]) for n in schedule.outcomes)
    summary = _score(manifest, replay, schedule, list(schedule.outcomes))
    limitations = [
        "Scores cover independently authored observed form slots, not derived review answers.",
        "Rule proposals, measurements, arithmetic checks and review approval are not scored.",
        "Synthetic goldens do not establish Chinese or production-case extraction accuracy.",
        "Execution labels are supplied provenance, not independently verified service receipts.",
        "Human intervention counts requested handoff pages, not completed human decisions.",
        "Failure and handoff rates are observed lower bounds when scheduled outcomes are absent.",
        "Elapsed totals sum page or attempt durations; they are not concurrent-run wall time.",
        "Rate evidence is locally supplied; estimates exclude non-token charges and discounts.",
        "Repeat differences compare recorded projections and make no bitwise determinism claim.",
        "No acceptance decision is made from an acceptance-threshold digest alone.",
    ]
    if manifest.scoring.localization == "not_measured":
        limitations.append("Region IoU was not measured; localization accuracy is null.")
    return EvaluationReport(
        manifest_digest=canonical_digest(manifest),
        replay_digest=canonical_digest(replay),
        golden_digest=manifest.golden_digest,
        dataset_digest=manifest.dataset_digest,
        split_digest=manifest.split_digest,
        dataset_version=manifest.dataset_version,
        dataset_kind=manifest.dataset_kind,
        execution_kind=manifest.execution_kind,
        split=manifest.split,
        pipeline_version=manifest.pipeline_version,
        prompt_version=manifest.prompt_version,
        prompt_digest=manifest.prompt_digest,
        proposal_schema_digest=manifest.proposal_schema_digest,
        rendering_configuration_digest=manifest.rendering_configuration_digest,
        configuration_digest=canonical_digest(manifest.configuration),
        scoring_policy_digest=manifest.scoring.policy_digest,
        versions=replay.versions,
        timing_scope=manifest.timing_scope,
        summary=summary,
        repeats=repeats,
        differences=_differences(schedule, repeats),
        limitations=tuple(limitations),
    )


async def run_evaluation(
    manifest: EvaluationManifest,
    seed: EvaluationReplay,
    goldens: GoldenSuite,
    extract: Callable[[PageRequest], Awaitable[PageOutcome]],
) -> tuple[EvaluationReplay, EvaluationReport]:
    """Execute the frozen schedule through the caller's authorized provider composition.

    No provider receives goldens. The injected callback owns authorization, rendering,
    provider retries and usage capture. This loop is sequential, bounds total elapsed
    time and stops on uncertain completion or usage instead of issuing replacement calls.
    Callback exceptions retain a classified failure with unknown attempts and tokens.
    Every callback must fit its worst-case attempts and output tokens in the global
    remaining budget. Returned budget violations fail the page with telemetry intact.
    """
    score_evaluation(manifest, seed, goldens)
    _require(manifest.execution_kind in {"live", "mocked"}, "execution_kind_mismatch")
    _require(not seed.outcomes and not seed.execution_errors, "execution_seed_not_empty")
    schedule = _schedule(manifest, seed, goldens)
    records: list[RepeatedOutcome] = []
    failures: list[EvaluationExecutionError] = []
    started = time.monotonic()
    used_calls = used_output_tokens = 0
    attempt_reservation = manifest.budget.max_attempts_per_page
    output_reservation = attempt_reservation * manifest.configuration.max_output_tokens
    stopped = False
    for repeat in range(1, manifest.repeats + 1):
        runs = {case_id: uuid.uuid4() for case_id in schedule.cases}
        for input_index, item in enumerate(manifest.inputs):
            case = schedule.cases[item.source.document.case_id]
            for page in item.pages:
                remaining = manifest.budget.max_elapsed_seconds - (time.monotonic() - started)
                if (
                    stopped
                    or remaining <= 0
                    or used_calls + attempt_reservation > manifest.budget.max_calls
                    or used_output_tokens + output_reservation > manifest.budget.max_output_tokens
                ):
                    stopped = True
                    break
                purpose = item.source.document.purpose
                task: Literal["propose_case", "propose_rules", "inspect_reference"] = (
                    "propose_case"
                    if purpose == "forms"
                    else ("propose_rules" if purpose == "criteria" else "inspect_reference")
                )
                request = PageRequest(
                    run=RunReference(
                        run_id=runs[item.source.document.case_id],
                        revision=RevisionReference(
                            case_id=case.identity.case_id,
                            revision_id=case.identity.version,
                            material_digest=case.fixture.material_digest,
                        ),
                    ),
                    source=item.source,
                    page=page,
                    context=ExtractionContext(language="zh-Hant", task=task),
                )
                call_started = time.monotonic()
                try:
                    outcome = PageOutcome.model_validate(
                        await asyncio.wait_for(extract(request), timeout=remaining)
                    )
                    if outcome.request != request:
                        raise ExtractionBoundaryError("invalid_source_reference")
                    telemetry = outcome.telemetry
                    if (
                        telemetry.configuration != manifest.configuration
                        or telemetry.prompt_version != manifest.prompt_version
                        or telemetry.prompt_digest != manifest.prompt_digest
                    ):
                        raise ExtractionBoundaryError("configuration_error")
                except Exception as error:
                    code = (
                        error.code
                        if isinstance(error, ExtractionBoundaryError)
                        else ("timeout" if isinstance(error, TimeoutError) else "provider_error")
                    )
                    failures.append(
                        EvaluationExecutionError(
                            repeat=repeat,
                            input_index=input_index,
                            page=page,
                            failure=code,
                            elapsed_seconds=time.monotonic() - call_started,
                        )
                    )
                    # The callback supplied no telemetry; further spending is not knowable.
                    stopped = True
                    break
                outcome, budget_exceeded = _check_recorded_budget(
                    outcome, manifest, used_calls, used_output_tokens
                )
                records.append(RepeatedOutcome(repeat=repeat, outcome=outcome))
                attempts = outcome.telemetry.attempts
                used_calls += len(attempts)
                used_output_tokens += _known_output_tokens(outcome)
                stopped = budget_exceeded or any(
                    a.completion == "unknown" or a.input_tokens is None or a.output_tokens is None
                    for a in attempts
                )
            if stopped:
                break
        if stopped:
            break
    replay = EvaluationReplay.model_validate(
        seed.model_dump(mode="json")
        | {
            "outcomes": [r.model_dump(mode="json") for r in records],
            "execution_errors": [e.model_dump(mode="json") for e in failures],
        }
    )
    return replay, score_evaluation(manifest, replay, goldens)


def english_summary(report: EvaluationReport) -> str:
    """English companion to the typed machine report, without extracted values or locations."""
    summary = report.summary
    lines = [
        "Extraction evaluation",
        "",
        f"Dataset: {report.dataset_kind}; execution: {report.execution_kind}; "
        f"split: {report.split}.",
        f"Manifest digest: {report.manifest_digest}",
        f"Pipeline: {report.pipeline_version}; prompt: {report.prompt_version}.",
        f"Parser: {report.versions.parser_version}; "
        f"normalizer: {report.versions.normalizer_version}.",
        f"Model and provider configuration digest: {report.configuration_digest}",
        f"Scored field scope: {report.field_scope}.",
        "All scheduled cases, pages and repeats remain in their denominators.",
        "",
    ]
    for name, metric in summary.metrics.items():
        value = "unknown / not measured" if metric.value is None else f"{metric.value:.6f}"
        lines.append(f"{name}: {value} ({metric.numerator}/{metric.denominator}).")
    lines.extend(["", "Counts: " + json.dumps(summary.counts, sort_keys=True)])
    for name in ("field_errors", "page_failures", "attempt_failures", "handoff_reasons"):
        lines.append(f"{name}: {json.dumps(getattr(summary, name), sort_keys=True)}")
    lines.extend(
        [
            "",
            "Usage (null means unknown): " + summary.usage.model_dump_json(),
            "Token cost estimate: " + summary.cost.model_dump_json(),
            "",
        ]
    )
    for difference in report.differences:
        lines.append(
            f"Repeat {difference.left_repeat} vs {difference.right_repeat}: "
            f"{difference.changed_pages}/{difference.scheduled_pages} pages differ; "
            f"missing {difference.missing_left}/{difference.missing_right}; "
            + json.dumps(difference.changes, sort_keys=True)
        )
    lines.extend(["", *report.limitations, ""])
    return "\n".join(lines)
