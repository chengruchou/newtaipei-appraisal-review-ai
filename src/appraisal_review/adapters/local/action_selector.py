"""Deterministic local baseline for the controlled-action selector port."""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID, uuid4

from pydantic import ValidationError

from appraisal_review.application.service_guards import ServiceFault, admit_action
from appraisal_review.domain.service_contracts import (
    ActionArguments,
    ActionKind,
    ActionProposal,
    ActorReference,
    DeterministicReviewArguments,
    ExtractPageArguments,
    InspectReferenceArguments,
    RequestHumanReviewArguments,
    SelectorInput,
)
from appraisal_review.ports.action_selection import ActionSelectionError, SelectorErrorCode


class DeterministicActionSelector:
    """Choose the sole advertised action and derive arguments from trusted input."""

    def __init__(
        self,
        *,
        actor_id: str = "deterministic-action-selector",
        proposal_id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        self._actor = ActorReference(actor_id=actor_id, kind="system")
        self._proposal_id_factory = proposal_id_factory

    @property
    def actor(self) -> ActorReference:
        return self._actor

    async def select(self, selector_input: SelectorInput) -> ActionProposal:
        current = SelectorInput.model_validate_json(selector_input.model_dump_json())
        if not current.allowed_actions.actions:
            raise ActionSelectionError(SelectorErrorCode.NO_ALLOWED_ACTION)
        if any(action.proposer_kinds != ("system",) for action in current.allowed_actions.actions):
            raise ActionSelectionError(SelectorErrorCode.INVALID_PROPOSAL)
        if len(current.allowed_actions.actions) != 1:
            raise ActionSelectionError(SelectorErrorCode.AMBIGUOUS_ACTION)
        allowed = current.allowed_actions.actions[0]
        arguments = self._arguments(current, allowed.action)
        try:
            proposal = ActionProposal(
                proposal_id=self._proposal_id_factory(),
                run=current.snapshot.run,
                action_id=allowed.action_id,
                action=allowed.action,
                policy_version=current.allowed_actions.policy_version,
                snapshot_digest=current.allowed_actions.snapshot_digest,
                proposer=self._actor,
                arguments=arguments,
                proposer_rationale="Deterministic baseline selected the sole allowed action.",
            )
            admit_action(
                proposal,
                proposer=self._actor,
                executor=ActorReference(actor_id="selector-preflight", kind="system"),
                snapshot=current.snapshot,
                allowed=current.allowed_actions,
            )
        except (ServiceFault, ValidationError) as error:
            raise ActionSelectionError(SelectorErrorCode.INVALID_PROPOSAL) from error
        return proposal

    @staticmethod
    def _arguments(selector_input: SelectorInput, action: ActionKind) -> ActionArguments:
        snapshot = selector_input.snapshot
        allowed = selector_input.allowed_actions.actions[0]
        if action == ActionKind.REVIEW:
            return DeterministicReviewArguments(
                revision=snapshot.revision.reference,
                rules=snapshot.revision.rules,
            )
        if action == ActionKind.HUMAN:
            if not snapshot.unresolved_blockers:
                raise ActionSelectionError(SelectorErrorCode.MISSING_ARGUMENT_SOURCE)
            blocker = snapshot.unresolved_blockers[0]
            return RequestHumanReviewArguments(
                reason_code=blocker.reason_code,
                question=f"Review the unresolved {blocker.reason_code} blocker.",
                affected_subject_ids=blocker.affected_subject_ids,
                evidence=blocker.evidence,
            )
        documents = {
            (document.document_id, document.version, document.content_hash): document
            for document in snapshot.revision.documents
            if document.purpose in allowed.permitted_document_purposes
        }
        citation = next(
            (
                item
                for item in selector_input.evidence
                if (item.document_id, item.version, item.content_hash) in documents
            ),
            None,
        )
        if citation is None:
            raise ActionSelectionError(SelectorErrorCode.MISSING_ARGUMENT_SOURCE)
        document = documents[(citation.document_id, citation.version, citation.content_hash)]
        if action == ActionKind.EXTRACT:
            return ExtractPageArguments(document=document, page=citation.page, region=citation)
        if action == ActionKind.REFERENCE:
            return InspectReferenceArguments(document=document, page=citation.page)
        raise ActionSelectionError(SelectorErrorCode.INVALID_PROPOSAL)
