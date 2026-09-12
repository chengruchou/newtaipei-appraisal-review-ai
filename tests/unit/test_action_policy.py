from __future__ import annotations

from dataclasses import replace
from typing import cast
from uuid import UUID

import pytest
from pydantic import ValidationError

from appraisal_review.adapters.local.synthetic import synthetic_material
from appraisal_review.application.action_policy import (
    DEFAULT_ACTION_RULES,
    ActionPolicyRule,
    ControlledActionPolicy,
)
from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.application.service_guards import ServiceFault, admit_action
from appraisal_review.domain.review_contracts import content_digest
from appraisal_review.domain.service_contracts import (
    ActionKind,
    ActionPrerequisite,
    ActionProposal,
    ActorReference,
    Budget,
    DeterministicReviewArguments,
    DocumentReference,
    MaterialRevision,
    RunReference,
    ServiceErrorCode,
    WorkflowBlocker,
    WorkflowSnapshot,
    WorkflowState,
)

STATE_INPUTS = {
    WorkflowState.CRITERIA_PENDING: ((ActionPrerequisite.CRITERIA_DOCUMENT,), False),
    WorkflowState.FORMS_PENDING: (
        (
            ActionPrerequisite.CRITERIA_PARSED,
            ActionPrerequisite.FORMS_DOCUMENT,
            ActionPrerequisite.RULES_APPROVED,
        ),
        False,
    ),
    WorkflowState.EXTRACTION_RETRYABLE: (
        (
            ActionPrerequisite.AUTHORIZED_SOURCE,
            ActionPrerequisite.EXTRACTION_RETRY_AVAILABLE,
        ),
        False,
    ),
    WorkflowState.REFERENCE_AVAILABLE: (
        (
            ActionPrerequisite.AUTHORIZED_SOURCE,
            ActionPrerequisite.REFERENCE_AVAILABLE,
        ),
        False,
    ),
    WorkflowState.RULES_AWAITING_APPROVAL: (
        (ActionPrerequisite.CRITERIA_PARSED, ActionPrerequisite.RULES_AVAILABLE),
        True,
    ),
    WorkflowState.EVIDENCE_NEEDS_REVIEW: ((ActionPrerequisite.FORMS_PARSED,), True),
    WorkflowState.MATERIAL_READY: (
        (
            ActionPrerequisite.FORMS_PARSED,
            ActionPrerequisite.RULES_APPROVED,
            ActionPrerequisite.CRITICAL_EVIDENCE_AVAILABLE,
            ActionPrerequisite.MATERIAL_COMPLETE,
        ),
        False,
    ),
    WorkflowState.REVIEW_FAILED: ((ActionPrerequisite.MATERIAL_COMPLETE,), True),
    WorkflowState.VERIFIED: ((), False),
    WorkflowState.UNSUPPORTED_CONTEXTS: ((ActionPrerequisite.MATERIAL_COMPLETE,), True),
    WorkflowState.WAITING_FOR_HUMAN: ((), False),
}

EXPECTED_ACTIONS = {
    WorkflowState.CRITERIA_PENDING: ("extract-criteria",),
    WorkflowState.FORMS_PENDING: ("extract-forms",),
    WorkflowState.EXTRACTION_RETRYABLE: ("retry-localized-extraction",),
    WorkflowState.REFERENCE_AVAILABLE: ("inspect-versioned-reference",),
    WorkflowState.RULES_AWAITING_APPROVAL: ("request-rule-review",),
    WorkflowState.EVIDENCE_NEEDS_REVIEW: ("request-evidence-review",),
    WorkflowState.MATERIAL_READY: ("deterministic-review",),
    WorkflowState.REVIEW_FAILED: ("request-review-failure",),
    WorkflowState.VERIFIED: (),
    WorkflowState.UNSUPPORTED_CONTEXTS: ("request-unsupported-context-review",),
    WorkflowState.WAITING_FOR_HUMAN: (),
}


def _revision(*, include_reference: bool = False) -> MaterialRevision:
    revision = RevisionSnapshot.capture(synthetic_material(), "policy-r1").revision
    if not include_reference:
        return revision
    reference = DocumentReference(
        case_id=revision.reference.case_id,
        document_id="policy-reference",
        version="reference-v1",
        content_hash="d" * 64,
        purpose="reference",
    )
    return MaterialRevision.model_validate(
        {**revision.model_dump(), "documents": (*revision.documents, reference)}
    )


def _snapshot(
    state: WorkflowState,
    *,
    prerequisites: tuple[ActionPrerequisite, ...] | None = None,
    with_blocker: bool | None = None,
    budget: Budget | None = None,
) -> WorkflowSnapshot:
    required, rule_needs_blocker = STATE_INPUTS[state]
    revision = _revision(include_reference=state == WorkflowState.REFERENCE_AVAILABLE)
    blockers: tuple[WorkflowBlocker, ...] = ()
    if rule_needs_blocker if with_blocker is None else with_blocker:
        blockers = (
            WorkflowBlocker(
                blocker_id="policy-blocker",
                reason_code="trusted-state-blocker",
                affected_subject_ids=("synthetic.subject",),
            ),
        )
    return WorkflowSnapshot(
        state_version=1,
        run=RunReference(run_id=UUID(int=20), revision=revision.reference),
        revision=revision,
        state=state,
        satisfied_prerequisites=required if prerequisites is None else prerequisites,
        unresolved_blockers=blockers,
        budget=budget
        or Budget(
            steps_remaining=2,
            model_calls_remaining=1,
            retries_remaining=1,
            time_remaining_ms=5_000,
        ),
    )


@pytest.mark.parametrize("state", tuple(WorkflowState))
def test_policy_state_table_is_explicit_and_fail_closed(state: WorkflowState) -> None:
    allowed = ControlledActionPolicy(proposer_kind="system").derive(_snapshot(state))
    assert tuple(action.action_id for action in allowed.actions) == EXPECTED_ACTIONS[state]


def test_policy_is_deterministic_detached_and_binds_exact_authority() -> None:
    snapshot = _snapshot(WorkflowState.MATERIAL_READY)
    before = snapshot.model_dump_json()
    system = ControlledActionPolicy(proposer_kind="system").derive(snapshot)
    repeated = ControlledActionPolicy(proposer_kind="system").derive(snapshot)
    model = ControlledActionPolicy(proposer_kind="model").derive(snapshot)

    assert system == repeated
    assert snapshot.model_dump_json() == before
    assert system.snapshot_digest == content_digest(snapshot)
    assert system.revision == snapshot.revision.reference
    assert system.documents == snapshot.revision.documents
    assert system.rules == snapshot.revision.rules
    assert system.policy_version == "controlled-action-policy-v1-system"
    assert model.policy_version == "controlled-action-policy-v1-model"
    assert system.actions[0].cost.model_calls == 0
    assert model.actions[0].cost.model_calls == 1


@pytest.mark.parametrize(
    "state",
    [
        WorkflowState.CRITERIA_PENDING,
        WorkflowState.FORMS_PENDING,
        WorkflowState.EXTRACTION_RETRYABLE,
        WorkflowState.MATERIAL_READY,
    ],
)
def test_policy_withholds_action_when_a_prerequisite_is_missing(
    state: WorkflowState,
) -> None:
    required, _ = STATE_INPUTS[state]
    snapshot = _snapshot(state, prerequisites=required[:-1])
    assert ControlledActionPolicy(proposer_kind="system").derive(snapshot).actions == ()


def test_policy_withholds_human_action_without_a_current_blocker() -> None:
    snapshot = _snapshot(WorkflowState.EVIDENCE_NEEDS_REVIEW, with_blocker=False)
    assert ControlledActionPolicy(proposer_kind="system").derive(snapshot).actions == ()


@pytest.mark.parametrize(
    "budget",
    [
        Budget(steps_remaining=0, model_calls_remaining=1, retries_remaining=1),
        Budget(steps_remaining=1, model_calls_remaining=0, retries_remaining=1),
        Budget(
            steps_remaining=1,
            model_calls_remaining=1,
            retries_remaining=1,
            time_remaining_ms=0,
        ),
    ],
)
def test_model_policy_withholds_actions_when_budget_is_exhausted(budget: Budget) -> None:
    snapshot = _snapshot(WorkflowState.MATERIAL_READY, budget=budget)
    assert ControlledActionPolicy(proposer_kind="model").derive(snapshot).actions == ()


def test_retry_action_requires_retry_budget() -> None:
    snapshot = _snapshot(
        WorkflowState.EXTRACTION_RETRYABLE,
        budget=Budget(steps_remaining=1, model_calls_remaining=1, retries_remaining=0),
    )
    assert ControlledActionPolicy(proposer_kind="system").derive(snapshot).actions == ()


def test_source_actions_expose_only_present_allowed_purposes() -> None:
    retry = ControlledActionPolicy(proposer_kind="system").derive(
        _snapshot(WorkflowState.EXTRACTION_RETRYABLE)
    )
    assert retry.actions[0].permitted_document_purposes == ("criteria", "forms")

    reference = ControlledActionPolicy(proposer_kind="system").derive(
        _snapshot(WorkflowState.REFERENCE_AVAILABLE)
    )
    assert reference.actions[0].permitted_document_purposes == ("reference",)


def test_reference_action_is_withheld_when_only_wrong_purpose_documents_exist() -> None:
    snapshot = _snapshot(WorkflowState.REFERENCE_AVAILABLE)
    revision = _revision()
    snapshot = WorkflowSnapshot.model_validate(
        {
            **snapshot.model_dump(),
            "run": snapshot.run.model_copy(update={"revision": revision.reference}),
            "revision": revision,
        }
    )
    assert ControlledActionPolicy(proposer_kind="system").derive(snapshot).actions == ()


@pytest.mark.parametrize(
    "rules",
    [
        (DEFAULT_ACTION_RULES[0], DEFAULT_ACTION_RULES[0]),
        (
            DEFAULT_ACTION_RULES[0],
            ActionPolicyRule(
                action_id="different-id",
                action=ActionKind.EXTRACT,
                states=(WorkflowState.CRITERIA_PENDING,),
                prerequisites=(ActionPrerequisite.CRITERIA_DOCUMENT,),
                document_purposes=("criteria",),
            ),
        ),
    ],
)
def test_policy_rejects_duplicate_or_ambiguous_registrations(
    rules: tuple[ActionPolicyRule, ...],
) -> None:
    with pytest.raises(ValueError):
        ControlledActionPolicy(proposer_kind="system", rules=rules)


@pytest.mark.parametrize(
    "rule",
    [
        ActionPolicyRule(
            action_id="bad-review-source",
            action=ActionKind.REVIEW,
            states=(WorkflowState.MATERIAL_READY,),
            prerequisites=(),
            document_purposes=("forms",),
        ),
        ActionPolicyRule(
            action_id="bad-reference-purpose",
            action=ActionKind.REFERENCE,
            states=(WorkflowState.REFERENCE_AVAILABLE,),
            prerequisites=(),
            document_purposes=("forms",),
        ),
        ActionPolicyRule(
            action_id="bad-template-source",
            action=ActionKind.EXTRACT,
            states=(WorkflowState.CRITERIA_PENDING,),
            prerequisites=(),
            document_purposes=("template",),
        ),
        ActionPolicyRule(
            action_id="bad-retry",
            action=ActionKind.EXTRACT,
            states=(WorkflowState.EXTRACTION_RETRYABLE,),
            prerequisites=(),
            document_purposes=("forms",),
            retry_cost=-1,
        ),
    ],
)
def test_policy_rejects_invalid_trusted_rule_shapes(rule: ActionPolicyRule) -> None:
    with pytest.raises(ValueError):
        ControlledActionPolicy(proposer_kind="system", rules=(rule,))


def test_policy_rejects_invalid_identity_and_runtime_types() -> None:
    with pytest.raises(ValidationError):
        ControlledActionPolicy(proposer_kind="system", policy_version="contains spaces")
    with pytest.raises(ValueError):
        ControlledActionPolicy(proposer_kind="worker")  # type: ignore[arg-type]
    invalid = ActionPolicyRule(
        action_id="bad-action-type",
        action="extract_page",  # type: ignore[arg-type]
        states=(WorkflowState.CRITERIA_PENDING,),
        prerequisites=(ActionPrerequisite.CRITERIA_DOCUMENT,),
        document_purposes=("criteria",),
    )
    with pytest.raises(ValueError):
        ControlledActionPolicy(proposer_kind="system", rules=(invalid,))


@pytest.mark.parametrize(
    "rule",
    [
        replace(DEFAULT_ACTION_RULES[0], states=()),
        replace(
            DEFAULT_ACTION_RULES[0],
            states=(WorkflowState.CRITERIA_PENDING, WorkflowState.CRITERIA_PENDING),
        ),
        replace(
            DEFAULT_ACTION_RULES[0],
            states=(cast(WorkflowState, "criteria_pending"),),
        ),
        replace(
            DEFAULT_ACTION_RULES[0],
            prerequisites=(
                ActionPrerequisite.CRITERIA_DOCUMENT,
                ActionPrerequisite.CRITERIA_DOCUMENT,
            ),
        ),
        replace(
            DEFAULT_ACTION_RULES[0],
            prerequisites=(cast(ActionPrerequisite, "criteria_document"),),
        ),
        replace(DEFAULT_ACTION_RULES[0], document_purposes=("criteria", "criteria")),
        replace(DEFAULT_ACTION_RULES[0], retry_cost=True),
    ],
)
def test_policy_rejects_noncanonical_registry_values(rule: ActionPolicyRule) -> None:
    with pytest.raises(ValueError):
        ControlledActionPolicy(proposer_kind="system", rules=(rule,))


def test_fresh_policy_output_is_rechecked_immediately_before_execution() -> None:
    snapshot = _snapshot(WorkflowState.MATERIAL_READY)
    policy = ControlledActionPolicy(proposer_kind="system")
    allowed = policy.derive(snapshot)
    action = allowed.actions[0]
    proposer = ActorReference(actor_id="trusted-selector", kind="system")
    executor = ActorReference(actor_id="trusted-executor", kind="system")
    proposal = ActionProposal(
        proposal_id=UUID(int=21),
        run=snapshot.run,
        action_id=action.action_id,
        action=action.action,
        policy_version=allowed.policy_version,
        snapshot_digest=allowed.snapshot_digest,
        proposer=proposer,
        arguments=DeterministicReviewArguments(
            revision=snapshot.revision.reference,
            rules=snapshot.revision.rules,
        ),
    )

    admit_action(
        proposal,
        proposer=proposer,
        executor=executor,
        snapshot=snapshot,
        allowed=allowed,
    )
    fresh_snapshot = snapshot.model_copy(update={"state_version": 2})
    with pytest.raises(ServiceFault) as error:
        admit_action(
            proposal,
            proposer=proposer,
            executor=executor,
            snapshot=fresh_snapshot,
            allowed=allowed,
        )
    assert error.value.problem.code == ServiceErrorCode.CONFLICT
