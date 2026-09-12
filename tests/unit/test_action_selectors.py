from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from appraisal_review.adapters.aws.action_selector import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    BedrockActionSelector,
    ModelSelectorConfig,
)
from appraisal_review.adapters.local.action_selector import DeterministicActionSelector
from appraisal_review.adapters.local.synthetic import synthetic_material
from appraisal_review.application.action_policy import ControlledActionPolicy
from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.application.service_guards import admit_action
from appraisal_review.domain.service_contracts import (
    ActionCost,
    ActionKind,
    ActionPrerequisite,
    ActorReference,
    AllowedAction,
    Budget,
    DocumentReference,
    ExtractPageArguments,
    InspectReferenceArguments,
    MaterialRevision,
    RequestHumanReviewArguments,
    RunReference,
    SelectorInput,
    WorkflowBlocker,
    WorkflowSnapshot,
    WorkflowState,
)
from appraisal_review.ports.action_selection import (
    ActionSelectionError,
    ActionSelector,
    SelectorErrorCode,
)

ROOT = Path(__file__).resolve().parents[2]


class FakeClient:
    def __init__(self, *results: object) -> None:
        self.results = list(results)
        self.calls: list[dict[str, Any]] = []

    def converse(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        assert isinstance(result, dict)
        return result


class ProviderFailure(Exception):
    def __init__(self, code: str, secret: str = "private provider detail") -> None:
        self.response = {"Error": {"Code": code, "Message": secret}}
        super().__init__(secret)


class SlowClient:
    def converse(self, **kwargs: Any) -> dict[str, Any]:
        time.sleep(0.05)
        return _response("{}")


def _snapshot(state: WorkflowState) -> WorkflowSnapshot:
    material = synthetic_material()
    revision = RevisionSnapshot.capture(material, "selector-r1").revision
    if state == WorkflowState.REFERENCE_AVAILABLE:
        reference = DocumentReference(
            case_id=revision.reference.case_id,
            document_id="selector-reference",
            version="reference-v1",
            content_hash="e" * 64,
            purpose="reference",
        )
        revision = MaterialRevision.model_validate(
            {**revision.model_dump(), "documents": (*revision.documents, reference)}
        )
    evidence = tuple(material.facts.pairs[0].target_sources)
    prerequisites: tuple[ActionPrerequisite, ...]
    blockers: tuple[WorkflowBlocker, ...] = ()
    if state == WorkflowState.MATERIAL_READY:
        prerequisites = (
            ActionPrerequisite.FORMS_PARSED,
            ActionPrerequisite.RULES_APPROVED,
            ActionPrerequisite.CRITICAL_EVIDENCE_AVAILABLE,
            ActionPrerequisite.MATERIAL_COMPLETE,
        )
    elif state == WorkflowState.EVIDENCE_NEEDS_REVIEW:
        prerequisites = (ActionPrerequisite.FORMS_PARSED,)
        blockers = (
            WorkflowBlocker(
                blocker_id="selector-blocker",
                reason_code="low-confidence-evidence",
                affected_subject_ids=("synthetic.road-width.target",),
                evidence=evidence,
            ),
        )
    elif state == WorkflowState.CRITERIA_PENDING:
        prerequisites = (ActionPrerequisite.CRITERIA_DOCUMENT,)
    elif state == WorkflowState.REFERENCE_AVAILABLE:
        prerequisites = (
            ActionPrerequisite.AUTHORIZED_SOURCE,
            ActionPrerequisite.REFERENCE_AVAILABLE,
        )
    else:
        raise AssertionError("Unsupported selector test state")
    return WorkflowSnapshot(
        state_version=1,
        run=RunReference(run_id=UUID(int=40), revision=revision.reference),
        revision=revision,
        state=state,
        satisfied_prerequisites=prerequisites,
        unresolved_blockers=blockers,
        budget=Budget(
            steps_remaining=2,
            model_calls_remaining=3,
            retries_remaining=1,
            time_remaining_ms=5_000,
        ),
    )


def _input(state: WorkflowState, *, proposer_kind: str = "model") -> SelectorInput:
    snapshot = _snapshot(state)
    policy = ControlledActionPolicy(proposer_kind=proposer_kind)  # type: ignore[arg-type]
    evidence = tuple(
        citation for blocker in snapshot.unresolved_blockers for citation in blocker.evidence
    )
    if state in {WorkflowState.CRITERIA_PENDING, WorkflowState.REFERENCE_AVAILABLE}:
        purpose = "criteria" if state == WorkflowState.CRITERIA_PENDING else "reference"
        criteria = next(
            document for document in snapshot.revision.documents if document.purpose == purpose
        )
        evidence = (
            synthetic_material()
            .facts.pairs[0]
            .target_sources[0]
            .model_copy(
                update={
                    "document_id": criteria.document_id,
                    "version": criteria.version,
                    "content_hash": criteria.content_hash,
                }
            ),
        )
    return SelectorInput(
        snapshot=snapshot,
        allowed_actions=policy.derive(snapshot),
        evidence=evidence,
        budget=snapshot.budget,
    )


def _selection(selector_input: SelectorInput) -> str:
    action = selector_input.allowed_actions.actions[0]
    arguments: dict[str, Any]
    if action.action == ActionKind.REVIEW:
        arguments = {
            "kind": "deterministic_review",
            "revision": selector_input.snapshot.revision.reference.model_dump(mode="json"),
            "rules": [
                rule.model_dump(mode="json") for rule in selector_input.snapshot.revision.rules
            ],
        }
    elif action.action == ActionKind.HUMAN:
        blocker = selector_input.snapshot.unresolved_blockers[0]
        arguments = {
            "kind": "request_human_review",
            "reason_code": blocker.reason_code,
            "question": "Review the supplied blocker.",
            "affected_subject_ids": list(blocker.affected_subject_ids),
            "evidence": [item.model_dump(mode="json") for item in blocker.evidence],
        }
    elif action.action == ActionKind.EXTRACT:
        citation = selector_input.evidence[0]
        document = next(
            item
            for item in selector_input.snapshot.revision.documents
            if item.document_id == citation.document_id
        )
        arguments = {
            "kind": "extract_page",
            "document": document.model_dump(mode="json"),
            "page": citation.page,
            "region": citation.model_dump(mode="json"),
        }
    elif action.action == ActionKind.REFERENCE:
        citation = selector_input.evidence[0]
        document = next(
            item
            for item in selector_input.snapshot.revision.documents
            if item.document_id == citation.document_id
        )
        arguments = {
            "kind": "inspect_reference",
            "document": document.model_dump(mode="json"),
            "page": citation.page,
        }
    else:
        raise AssertionError("Unsupported model selector test action")
    return json.dumps(
        {
            "action_id": action.action_id,
            "action": action.action,
            "arguments": arguments,
            "rationale": "Untrusted model rationale.",
        }
    )


def _response(
    text: str,
    *,
    stop_reason: str = "end_turn",
    usage: object = None,
) -> dict[str, Any]:
    return {
        "stopReason": stop_reason,
        "output": {"message": {"content": [{"text": text}]}},
        "usage": {"inputTokens": 31, "outputTokens": 17} if usage is None else usage,
    }


def _model_selector(client: Any, *, attempts: int = 1, timeout: float = 1) -> BedrockActionSelector:
    return BedrockActionSelector(
        client,
        ModelSelectorConfig(
            model_id="fixture-selector-model",
            attempts=attempts,
            timeout_seconds=timeout,
            retry_backoff_seconds=0,
        ),
        proposal_id_factory=lambda: UUID(int=41),
    )


def _admit(proposal: Any, selector_input: SelectorInput) -> None:
    admit_action(
        proposal,
        proposer=proposal.proposer,
        executor=ActorReference(actor_id="test-executor", kind="system"),
        snapshot=selector_input.snapshot,
        allowed=selector_input.allowed_actions,
    )


def test_model_selector_valid_output_is_adapter_bound_and_admitted() -> None:
    selector_input = _input(WorkflowState.MATERIAL_READY)
    client = FakeClient(_response(_selection(selector_input)))
    selector: ActionSelector = _model_selector(client)

    proposal = asyncio.run(selector.select(selector_input))
    _admit(proposal, selector_input)

    assert proposal.proposer.kind == "model"
    assert proposal.model_id == "fixture-selector-model"
    assert proposal.prompt_version == PROMPT_VERSION
    assert (proposal.input_tokens, proposal.output_tokens) == (31, 17)
    assert proposal.attempt_count == 1
    assert proposal.latency_ms is not None
    assert proposal.action == ActionKind.REVIEW
    sent = client.calls[0]
    assert sent["system"] == [{"text": SYSTEM_PROMPT}]
    advertised = json.loads(sent["messages"][0]["content"][0]["text"])
    assert advertised["allowed_actions"] == selector_input.allowed_actions.model_dump(mode="json")
    assert advertised["budget"] == selector_input.budget.model_dump(mode="json")
    assert "storage_uri" not in json.dumps(advertised)


def test_versioned_prompt_document_matches_runtime_instruction() -> None:
    document = (ROOT / "docs/prompts/controlled-action-selector-v1.md").read_text()
    documented_prompt = document.split("```text\n", 1)[1].split("\n```", 1)[0]
    assert documented_prompt == SYSTEM_PROMPT.strip()


@pytest.mark.parametrize(
    "state", [WorkflowState.MATERIAL_READY, WorkflowState.EVIDENCE_NEEDS_REVIEW]
)
def test_model_selection_changes_only_to_the_permitted_state_branch(state: WorkflowState) -> None:
    selector_input = _input(state)
    selector = _model_selector(FakeClient(_response(_selection(selector_input))))
    proposal = asyncio.run(selector.select(selector_input))
    _admit(proposal, selector_input)
    expected = ActionKind.REVIEW if state == WorkflowState.MATERIAL_READY else ActionKind.HUMAN
    assert proposal.action == expected


def test_deterministic_baseline_uses_same_admission_without_model_claims() -> None:
    selector_input = _input(WorkflowState.MATERIAL_READY, proposer_kind="system")
    selector: ActionSelector = DeterministicActionSelector(proposal_id_factory=lambda: UUID(int=42))
    proposal = asyncio.run(selector.select(selector_input))
    _admit(proposal, selector_input)
    assert proposal.action == ActionKind.REVIEW
    assert proposal.proposer.kind == "system"
    assert proposal.model_id is proposal.prompt_version is None
    assert proposal.attempt_count is proposal.latency_ms is None


def test_deterministic_baseline_derives_human_arguments_from_blocker() -> None:
    selector_input = _input(WorkflowState.EVIDENCE_NEEDS_REVIEW, proposer_kind="system")
    proposal = asyncio.run(DeterministicActionSelector().select(selector_input))
    _admit(proposal, selector_input)
    blocker = selector_input.snapshot.unresolved_blockers[0]
    assert proposal.action == ActionKind.HUMAN
    assert isinstance(proposal.arguments, RequestHumanReviewArguments)
    assert proposal.arguments.affected_subject_ids == blocker.affected_subject_ids


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: {**value, "action_id": "unadvertised-action"},
        lambda value: {**value, "action": "request_human_review"},
    ],
)
def test_model_selector_rejects_out_of_set_actions(mutate: Any) -> None:
    selector_input = _input(WorkflowState.MATERIAL_READY)
    value = json.loads(_selection(selector_input))
    selector = _model_selector(FakeClient(_response(json.dumps(mutate(value)))))
    with pytest.raises(ActionSelectionError) as error:
        asyncio.run(selector.select(selector_input))
    assert error.value.code == SelectorErrorCode.INVALID_PROPOSAL


@pytest.mark.parametrize(
    "text",
    [
        "I would choose the review action.",
        "{}",
        json.dumps(
            {
                "action_id": "deterministic-review",
                "action": "deterministic_review",
                "arguments": {"kind": "deterministic_review"},
            }
        ),
    ],
)
def test_model_selector_rejects_prose_or_malformed_arguments(text: str) -> None:
    selector = _model_selector(FakeClient(_response(text)))
    with pytest.raises(ActionSelectionError) as error:
        asyncio.run(selector.select(_input(WorkflowState.MATERIAL_READY)))
    assert error.value.code == SelectorErrorCode.MALFORMED_OUTPUT


@pytest.mark.parametrize("claim", [{"proposer": {"kind": "system"}}, {"budget": {"steps": 99}}])
def test_model_output_cannot_claim_identity_or_budget(claim: dict[str, object]) -> None:
    selector_input = _input(WorkflowState.MATERIAL_READY)
    payload = {**json.loads(_selection(selector_input)), **claim}
    selector = _model_selector(FakeClient(_response(json.dumps(payload))))
    with pytest.raises(ActionSelectionError) as error:
        asyncio.run(selector.select(selector_input))
    assert error.value.code == SelectorErrorCode.MALFORMED_OUTPUT


def test_model_selector_rejects_a_page_not_present_in_sanitized_evidence() -> None:
    selector_input = _input(WorkflowState.CRITERIA_PENDING)
    payload = json.loads(_selection(selector_input))
    payload["arguments"]["page"] += 1
    payload["arguments"]["region"] = None
    selector = _model_selector(FakeClient(_response(json.dumps(payload))))
    with pytest.raises(ActionSelectionError) as error:
        asyncio.run(selector.select(selector_input))
    assert error.value.code == SelectorErrorCode.INVALID_PROPOSAL


@pytest.mark.parametrize(
    ("stop_reason", "code"),
    [
        ("max_tokens", SelectorErrorCode.TRUNCATED_OUTPUT),
        ("model_context_window_exceeded", SelectorErrorCode.TRUNCATED_OUTPUT),
        ("refusal", SelectorErrorCode.REFUSED),
        ("content_filtered", SelectorErrorCode.REFUSED),
        ("unknown", SelectorErrorCode.UNSUPPORTED_RESPONSE),
    ],
)
def test_model_selector_rejects_noncomplete_responses(
    stop_reason: str, code: SelectorErrorCode
) -> None:
    selector = _model_selector(FakeClient(_response("{}", stop_reason=stop_reason)))
    with pytest.raises(ActionSelectionError) as error:
        asyncio.run(selector.select(_input(WorkflowState.MATERIAL_READY)))
    assert error.value.code == code
    assert error.value.attempts == 1
    assert (error.value.input_tokens, error.value.output_tokens) == (31, 17)


def test_model_selector_timeout_is_sanitized_and_not_retried() -> None:
    selector = _model_selector(SlowClient(), attempts=2, timeout=0.001)
    with pytest.raises(ActionSelectionError) as error:
        asyncio.run(selector.select(_input(WorkflowState.MATERIAL_READY)))
    assert error.value.code == SelectorErrorCode.TIMEOUT
    assert error.value.attempts == 1
    assert "private" not in str(error.value)


def test_model_selector_retries_throttling_then_reports_sanitized_failure() -> None:
    client = FakeClient(
        ProviderFailure("ThrottlingException"),
        ProviderFailure("ThrottlingException"),
    )
    selector = _model_selector(client, attempts=2)
    with pytest.raises(ActionSelectionError) as error:
        asyncio.run(selector.select(_input(WorkflowState.MATERIAL_READY)))
    assert error.value.code == SelectorErrorCode.THROTTLED
    assert error.value.attempts == 2
    assert len(client.calls) == 2
    assert "private provider detail" not in str(error.value)


def test_model_selector_records_attempt_after_throttled_retry_succeeds() -> None:
    selector_input = _input(WorkflowState.MATERIAL_READY)
    client = FakeClient(
        ProviderFailure("ServiceUnavailableException"),
        _response(_selection(selector_input)),
    )
    proposal = asyncio.run(_model_selector(client, attempts=2).select(selector_input))
    assert proposal.attempt_count == 2
    assert len(client.calls) == 2


def test_model_selector_retries_never_exceed_snapshot_model_budget() -> None:
    original = _snapshot(WorkflowState.MATERIAL_READY)
    snapshot = original.model_copy(
        update={"budget": original.budget.model_copy(update={"model_calls_remaining": 1})}
    )
    allowed = ControlledActionPolicy(proposer_kind="model").derive(snapshot)
    selector_input = SelectorInput(
        snapshot=snapshot,
        allowed_actions=allowed,
        budget=snapshot.budget,
    )
    client = FakeClient(
        ProviderFailure("ThrottlingException"),
        ProviderFailure("ThrottlingException"),
    )
    with pytest.raises(ActionSelectionError) as error:
        asyncio.run(_model_selector(client, attempts=3).select(selector_input))
    assert error.value.code == SelectorErrorCode.THROTTLED
    assert error.value.attempts == 1
    assert len(client.calls) == 1


def test_model_selector_retries_never_exceed_snapshot_retry_budget() -> None:
    original = _snapshot(WorkflowState.MATERIAL_READY)
    snapshot = original.model_copy(
        update={"budget": original.budget.model_copy(update={"retries_remaining": 0})}
    )
    allowed = ControlledActionPolicy(proposer_kind="model").derive(snapshot)
    selector_input = SelectorInput(
        snapshot=snapshot,
        allowed_actions=allowed,
        budget=snapshot.budget,
    )
    client = FakeClient(
        ProviderFailure("ThrottlingException"),
        ProviderFailure("ThrottlingException"),
    )
    with pytest.raises(ActionSelectionError) as error:
        asyncio.run(_model_selector(client, attempts=3).select(selector_input))
    assert error.value.code == SelectorErrorCode.THROTTLED
    assert error.value.attempts == 1
    assert len(client.calls) == 1


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (ProviderFailure("ValidationException"), SelectorErrorCode.PROVIDER_ERROR),
        (RuntimeError("private credential value"), SelectorErrorCode.PROVIDER_ERROR),
    ],
)
def test_model_selector_preserves_provider_failures_as_sanitized_codes(
    failure: Exception, code: SelectorErrorCode
) -> None:
    selector = _model_selector(FakeClient(failure))
    with pytest.raises(ActionSelectionError) as error:
        asyncio.run(selector.select(_input(WorkflowState.MATERIAL_READY)))
    assert error.value.code == code
    assert str(error.value) == code.value
    assert error.value.model_id == "fixture-selector-model"


def test_provider_timeout_class_and_unstructured_metadata_are_sanitized() -> None:
    read_timeout = type("ReadTimeoutError", (Exception,), {})
    selector = _model_selector(FakeClient(read_timeout("private timeout")))
    with pytest.raises(ActionSelectionError) as error:
        asyncio.run(selector.select(_input(WorkflowState.MATERIAL_READY)))
    assert error.value.code == SelectorErrorCode.TIMEOUT

    provider = ProviderFailure("ignored")
    provider.response = "private response"  # type: ignore[assignment]
    selector = _model_selector(FakeClient(provider))
    with pytest.raises(ActionSelectionError) as error:
        asyncio.run(selector.select(_input(WorkflowState.MATERIAL_READY)))
    assert error.value.code == SelectorErrorCode.PROVIDER_ERROR


def test_selector_does_not_call_model_when_policy_advertises_no_action() -> None:
    snapshot = _snapshot(WorkflowState.MATERIAL_READY).model_copy(
        update={"state": WorkflowState.VERIFIED}
    )
    allowed = ControlledActionPolicy(proposer_kind="model").derive(snapshot)
    selector_input = SelectorInput(
        snapshot=snapshot,
        allowed_actions=allowed,
        budget=snapshot.budget,
    )
    client = FakeClient()
    with pytest.raises(ActionSelectionError) as error:
        asyncio.run(_model_selector(client).select(selector_input))
    assert error.value.code == SelectorErrorCode.NO_ALLOWED_ACTION
    assert client.calls == []


def test_model_selector_rejects_system_policy_without_calling_client() -> None:
    selector_input = _input(WorkflowState.MATERIAL_READY, proposer_kind="system")
    client = FakeClient()
    with pytest.raises(ActionSelectionError) as error:
        asyncio.run(_model_selector(client).select(selector_input))
    assert error.value.code == SelectorErrorCode.INVALID_PROPOSAL
    assert client.calls == []


def test_deterministic_selector_rejects_model_policy() -> None:
    selector_input = _input(WorkflowState.MATERIAL_READY)
    with pytest.raises(ActionSelectionError) as error:
        asyncio.run(DeterministicActionSelector().select(selector_input))
    assert error.value.code == SelectorErrorCode.INVALID_PROPOSAL


def test_deterministic_selector_rejects_empty_and_ambiguous_sets() -> None:
    snapshot = _snapshot(WorkflowState.MATERIAL_READY)
    empty = ControlledActionPolicy(proposer_kind="system").derive(
        snapshot.model_copy(update={"state": WorkflowState.VERIFIED})
    )
    empty_input = SelectorInput(
        snapshot=snapshot.model_copy(update={"state": WorkflowState.VERIFIED}),
        allowed_actions=empty,
        budget=snapshot.budget,
    )
    with pytest.raises(ActionSelectionError) as error:
        asyncio.run(DeterministicActionSelector().select(empty_input))
    assert error.value.code == SelectorErrorCode.NO_ALLOWED_ACTION

    selector_input = _input(WorkflowState.MATERIAL_READY, proposer_kind="system")
    review = selector_input.allowed_actions.actions[0]
    human = AllowedAction(
        action_id="also-request-human-review",
        action=ActionKind.HUMAN,
        permitted_states=(WorkflowState.MATERIAL_READY,),
        revision=review.revision,
        rules=review.rules,
        proposer_kinds=("system",),
        prerequisites=review.prerequisites,
        cost=ActionCost(),
    )
    ambiguous = selector_input.allowed_actions.model_copy(update={"actions": (review, human)})
    ambiguous_input = SelectorInput.model_validate(
        {**selector_input.model_dump(), "allowed_actions": ambiguous}
    )
    with pytest.raises(ActionSelectionError) as error:
        asyncio.run(DeterministicActionSelector().select(ambiguous_input))
    assert error.value.code == SelectorErrorCode.AMBIGUOUS_ACTION


def test_deterministic_source_selection_requires_located_evidence() -> None:
    selector_input = _input(WorkflowState.CRITERIA_PENDING, proposer_kind="system").model_copy(
        update={"evidence": ()}
    )
    with pytest.raises(ActionSelectionError) as error:
        asyncio.run(DeterministicActionSelector().select(selector_input))
    assert error.value.code == SelectorErrorCode.MISSING_ARGUMENT_SOURCE


def test_deterministic_source_selection_copies_located_page() -> None:
    selector_input = _input(WorkflowState.CRITERIA_PENDING, proposer_kind="system")
    proposal = asyncio.run(DeterministicActionSelector().select(selector_input))
    _admit(proposal, selector_input)
    assert proposal.action == ActionKind.EXTRACT
    assert isinstance(proposal.arguments, ExtractPageArguments)
    assert proposal.arguments.page == selector_input.evidence[0].page


def test_deterministic_reference_selection_copies_located_page() -> None:
    selector_input = _input(WorkflowState.REFERENCE_AVAILABLE, proposer_kind="system")
    proposal = asyncio.run(DeterministicActionSelector().select(selector_input))
    _admit(proposal, selector_input)
    assert isinstance(proposal.arguments, InspectReferenceArguments)
    assert proposal.arguments.page == selector_input.evidence[0].page


def test_deterministic_selector_sanitizes_invalid_proposal_identity() -> None:
    selector_input = _input(WorkflowState.MATERIAL_READY, proposer_kind="system")
    selector = DeterministicActionSelector(proposal_id_factory=lambda: "invalid")  # type: ignore[arg-type,return-value]
    with pytest.raises(ActionSelectionError) as error:
        asyncio.run(selector.select(selector_input))
    assert error.value.code == SelectorErrorCode.INVALID_PROPOSAL


def test_selector_input_rejects_forged_budget_and_unbound_evidence() -> None:
    selector_input = _input(WorkflowState.EVIDENCE_NEEDS_REVIEW)
    with pytest.raises(ValueError):
        SelectorInput.model_validate(
            {
                **selector_input.model_dump(),
                "budget": selector_input.budget.model_copy(update={"steps_remaining": 1}),
            }
        )
    citation = selector_input.evidence[0].model_copy(update={"document_id": "unknown"})
    with pytest.raises(ValueError):
        SelectorInput.model_validate({**selector_input.model_dump(), "evidence": [citation]})


def test_valid_model_usage_is_optional_but_never_partially_recorded() -> None:
    selector_input = _input(WorkflowState.MATERIAL_READY)
    selector = _model_selector(
        FakeClient(_response(_selection(selector_input), usage={"inputTokens": 4}))
    )
    proposal = asyncio.run(selector.select(selector_input))
    assert proposal.input_tokens is proposal.output_tokens is None

    selector = _model_selector(
        FakeClient(_response(_selection(selector_input), usage="invalid usage"))
    )
    proposal = asyncio.run(selector.select(selector_input))
    assert proposal.input_tokens is proposal.output_tokens is None


class TestJsonBody:
    """Fence-tolerant extraction keeps the schema contract, only trimming decoration."""

    def test_bare_object_unchanged(self) -> None:
        from appraisal_review.adapters.aws.action_selector import _json_body

        assert _json_body('{"a": 1}') == '{"a": 1}'

    def test_fenced_object_extracted(self) -> None:
        from appraisal_review.adapters.aws.action_selector import _json_body

        fenced = '```json\n{"action": "review", "why": "fence"}\n```'
        assert _json_body(fenced) == '{"action": "review", "why": "fence"}'

    def test_prefixed_object_extracted(self) -> None:
        from appraisal_review.adapters.aws.action_selector import _json_body

        assert _json_body('Here is the JSON:\n{"a": {"b": 2}}') == '{"a": {"b": 2}}'

    def test_no_object_left_honest(self) -> None:
        from appraisal_review.adapters.aws.action_selector import _json_body

        assert _json_body("no json here") == "no json here"
