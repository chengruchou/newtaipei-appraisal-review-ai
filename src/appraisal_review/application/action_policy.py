"""Trusted, provider-neutral allowed-action derivation for controlled workflows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import TypeAdapter

from appraisal_review.domain.review_contracts import content_digest
from appraisal_review.domain.service_contracts import (
    ActionCost,
    ActionKind,
    ActionPrerequisite,
    AllowedAction,
    AllowedActionSet,
    OpaqueID,
    WorkflowSnapshot,
    WorkflowState,
)

ProposerKind = Literal["system", "model"]
DocumentPurpose = Literal["criteria", "forms", "reference", "brief", "template"]


@dataclass(frozen=True)
class ActionPolicyRule:
    """Static trusted policy registration, never selector- or request-authored."""

    action_id: str
    action: ActionKind
    states: tuple[WorkflowState, ...]
    prerequisites: tuple[ActionPrerequisite, ...]
    document_purposes: tuple[DocumentPurpose, ...] = ()
    retry_cost: int = 0
    requires_blockers: bool = False


DEFAULT_ACTION_RULES = (
    ActionPolicyRule(
        action_id="extract-criteria",
        action=ActionKind.EXTRACT,
        states=(WorkflowState.CRITERIA_PENDING,),
        prerequisites=(ActionPrerequisite.CRITERIA_DOCUMENT,),
        document_purposes=("criteria",),
    ),
    ActionPolicyRule(
        action_id="extract-forms",
        action=ActionKind.EXTRACT,
        states=(WorkflowState.FORMS_PENDING,),
        prerequisites=(
            ActionPrerequisite.CRITERIA_PARSED,
            ActionPrerequisite.FORMS_DOCUMENT,
            ActionPrerequisite.RULES_APPROVED,
        ),
        document_purposes=("forms",),
    ),
    ActionPolicyRule(
        action_id="retry-localized-extraction",
        action=ActionKind.EXTRACT,
        states=(WorkflowState.EXTRACTION_RETRYABLE,),
        prerequisites=(
            ActionPrerequisite.AUTHORIZED_SOURCE,
            ActionPrerequisite.EXTRACTION_RETRY_AVAILABLE,
        ),
        document_purposes=("criteria", "forms", "reference", "brief"),
        retry_cost=1,
    ),
    ActionPolicyRule(
        action_id="inspect-versioned-reference",
        action=ActionKind.REFERENCE,
        states=(WorkflowState.REFERENCE_AVAILABLE,),
        prerequisites=(
            ActionPrerequisite.AUTHORIZED_SOURCE,
            ActionPrerequisite.REFERENCE_AVAILABLE,
        ),
        document_purposes=("reference", "brief"),
    ),
    ActionPolicyRule(
        action_id="request-rule-review",
        action=ActionKind.HUMAN,
        states=(WorkflowState.RULES_AWAITING_APPROVAL,),
        prerequisites=(
            ActionPrerequisite.CRITERIA_PARSED,
            ActionPrerequisite.RULES_AVAILABLE,
        ),
        requires_blockers=True,
    ),
    ActionPolicyRule(
        action_id="request-evidence-review",
        action=ActionKind.HUMAN,
        states=(WorkflowState.EVIDENCE_NEEDS_REVIEW,),
        prerequisites=(ActionPrerequisite.FORMS_PARSED,),
        requires_blockers=True,
    ),
    ActionPolicyRule(
        action_id="deterministic-review",
        action=ActionKind.REVIEW,
        states=(WorkflowState.MATERIAL_READY,),
        prerequisites=(
            ActionPrerequisite.FORMS_PARSED,
            ActionPrerequisite.RULES_APPROVED,
            ActionPrerequisite.CRITICAL_EVIDENCE_AVAILABLE,
            ActionPrerequisite.MATERIAL_COMPLETE,
        ),
    ),
    ActionPolicyRule(
        action_id="request-review-failure",
        action=ActionKind.HUMAN,
        states=(WorkflowState.REVIEW_FAILED,),
        prerequisites=(ActionPrerequisite.MATERIAL_COMPLETE,),
        requires_blockers=True,
    ),
    ActionPolicyRule(
        action_id="request-unsupported-context-review",
        action=ActionKind.HUMAN,
        states=(WorkflowState.UNSUPPORTED_CONTEXTS,),
        prerequisites=(ActionPrerequisite.MATERIAL_COMPLETE,),
        requires_blockers=True,
    ),
)


class ControlledActionPolicy:
    """Derive action authority from trusted state without document or tool I/O."""

    def __init__(
        self,
        *,
        proposer_kind: ProposerKind,
        policy_version: str = "controlled-action-policy-v1",
        rules: tuple[ActionPolicyRule, ...] = DEFAULT_ACTION_RULES,
    ) -> None:
        if proposer_kind not in {"system", "model"}:
            raise ValueError("Unsupported action proposer kind")
        self.proposer_kind = proposer_kind
        self.policy_version = f"{policy_version}-{proposer_kind}"
        TypeAdapter(OpaqueID).validate_python(self.policy_version)
        self.rules = tuple(rules)
        self._validate_registry()

    def _validate_registry(self) -> None:
        action_ids = [rule.action_id for rule in self.rules]
        if len(set(action_ids)) != len(action_ids):
            raise ValueError("Action policy IDs must be unique")
        registrations: set[tuple[WorkflowState, ActionKind]] = set()
        for rule in self.rules:
            TypeAdapter(OpaqueID).validate_python(rule.action_id)
            if not isinstance(rule.action, ActionKind):
                raise ValueError("Action policy kinds must be typed")
            if any(not isinstance(state, WorkflowState) for state in rule.states):
                raise ValueError("Action policy states must be typed")
            if any(
                not isinstance(prerequisite, ActionPrerequisite)
                for prerequisite in rule.prerequisites
            ):
                raise ValueError("Action policy prerequisites must be typed")
            if not rule.states or len(set(rule.states)) != len(rule.states):
                raise ValueError("Action policy states must be non-empty and unique")
            if len(set(rule.prerequisites)) != len(rule.prerequisites):
                raise ValueError("Action policy prerequisites must be unique")
            if len(set(rule.document_purposes)) != len(rule.document_purposes):
                raise ValueError("Action policy document purposes must be unique")
            if type(rule.retry_cost) is not int or rule.retry_cost < 0:
                raise ValueError("Action policy retry cost cannot be negative")
            source_action = rule.action in {ActionKind.EXTRACT, ActionKind.REFERENCE}
            if source_action != bool(rule.document_purposes):
                raise ValueError("Only source action policy rules declare document purposes")
            if rule.action == ActionKind.REFERENCE and not set(rule.document_purposes) <= {
                "reference",
                "brief",
            }:
                raise ValueError("Reference policy rules require reference or brief material")
            if "template" in rule.document_purposes:
                raise ValueError("Templates cannot be controlled extraction sources")
            for state in rule.states:
                registration = state, rule.action
                if registration in registrations:
                    raise ValueError("Action policy contains an ambiguous state/action rule")
                registrations.add(registration)

    def derive(self, snapshot: WorkflowSnapshot) -> AllowedActionSet:
        """Return the only actions trusted policy permits for this exact snapshot."""

        current = WorkflowSnapshot.model_validate_json(snapshot.model_dump_json())
        snapshot_digest = content_digest(current)
        actions = tuple(
            action
            for rule in self.rules
            if (action := self._derive_rule(current, rule)) is not None
        )
        return AllowedActionSet(
            policy_version=self.policy_version,
            snapshot_digest=snapshot_digest,
            revision=current.revision.reference,
            documents=current.revision.documents,
            rules=current.revision.rules,
            actions=actions,
        )

    def _derive_rule(
        self, snapshot: WorkflowSnapshot, rule: ActionPolicyRule
    ) -> AllowedAction | None:
        if snapshot.state not in rule.states:
            return None
        if not set(rule.prerequisites) <= set(snapshot.satisfied_prerequisites):
            return None
        if rule.requires_blockers and not snapshot.unresolved_blockers:
            return None
        model_cost = 1 if self.proposer_kind == "model" else 0
        if (
            snapshot.budget.steps_remaining < 1
            or snapshot.budget.model_calls_remaining < model_cost
            or snapshot.budget.retries_remaining < rule.retry_cost
            or snapshot.budget.time_remaining_ms == 0
        ):
            return None
        available_purposes = {document.purpose for document in snapshot.revision.documents}
        permitted_purposes = tuple(
            purpose for purpose in rule.document_purposes if purpose in available_purposes
        )
        if rule.document_purposes and not permitted_purposes:
            return None
        return AllowedAction(
            action_id=rule.action_id,
            action=rule.action,
            permitted_states=rule.states,
            permitted_document_purposes=permitted_purposes,
            revision=snapshot.revision.reference,
            rules=snapshot.revision.rules,
            proposer_kinds=(self.proposer_kind,),
            prerequisites=rule.prerequisites,
            cost=ActionCost(steps=1, model_calls=model_cost, retries=rule.retry_cost),
        )
