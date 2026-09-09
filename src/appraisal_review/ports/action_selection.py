"""Provider-neutral controlled-action selection boundary."""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from appraisal_review.domain.service_contracts import (
    ActionProposal,
    ActorReference,
    SelectionFailureEvent,
    SelectorInput,
)


class SelectorErrorCode(StrEnum):
    NO_ALLOWED_ACTION = "no_allowed_action"
    AMBIGUOUS_ACTION = "ambiguous_action"
    MISSING_ARGUMENT_SOURCE = "missing_argument_source"
    INVALID_PROPOSAL = "invalid_proposal"
    MALFORMED_OUTPUT = "malformed_output"
    TRUNCATED_OUTPUT = "truncated_output"
    REFUSED = "refused"
    TIMEOUT = "timeout"
    IN_FLIGHT = "in_flight"
    THROTTLED = "throttled"
    PROVIDER_ERROR = "provider_error"
    UNSUPPORTED_RESPONSE = "unsupported_response"


class ActionSelectionError(Exception):
    """Sanitized selector failure with optional adapter-measured metadata."""

    def __init__(
        self,
        code: SelectorErrorCode,
        *,
        model_id: str | None = None,
        prompt_version: str | None = None,
        attempts: int = 0,
        latency_ms: int = 0,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        attempts_known: bool = True,
    ) -> None:
        self.code = code
        self.model_id = model_id
        self.prompt_version = prompt_version
        self.attempts = attempts
        self.latency_ms = latency_ms
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.attempts_known = attempts_known
        self.event: SelectionFailureEvent | None = None
        super().__init__(code.value)


class ActionSelector(Protocol):
    @property
    def actor(self) -> ActorReference:
        """Trusted adapter identity; proposal bodies cannot choose it."""
        ...

    async def select(self, selector_input: SelectorInput) -> ActionProposal:
        """Return one proposal only; never invoke the selected tool."""
        ...
