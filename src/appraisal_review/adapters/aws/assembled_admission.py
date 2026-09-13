"""Admission for model requests the trusted composition itself just assembled.

The pinned-record admission verifies pre-reviewed, byte-exact envelopes; a live
selector conversation can never be pinned in advance because run identifiers are
part of the payload. This admission closes that gap without weakening the guard:
the trusted selector *declares* the exact content it assembled - model id, system
prompt, user payload and the provenance of the case the payload was built from -
in a context variable that request code cannot reach, and the transport-side
check re-parses the actual outbound body and refuses anything that is not
field-for-field identical to the declaration. Only declared provenances admitted
at construction (tonight: the synthetic fixture cases) pass; an undeclared send,
a mismatched body, an extra message or a real-case payload all fail closed with
the same competition-data fault the pinned admission raises.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from appraisal_review.domain.competition_data import CompetitionDataFault, DataPart


@dataclass(frozen=True)
class OutboundModelIntent:
    """What trusted selector code is about to send, declared before the transport."""

    model_id: str
    system_text: str
    user_payload: str
    provenance: str


_intent: ContextVar[OutboundModelIntent | None] = ContextVar(
    "assembled_model_intent", default=None
)


@contextmanager
def declare_outbound_envelope(intent: OutboundModelIntent) -> Iterator[None]:
    token = _intent.set(intent)
    try:
        yield
    finally:
        _intent.reset(token)


class TrustedAssemblyAdmission:
    """Admit exactly the envelope the trusted composition declared, nothing else."""

    def __init__(self, allowed_provenances: frozenset[str]) -> None:
        if not allowed_provenances:
            raise ValueError("An admission that admits no provenance should not exist")
        self.allowed_provenances = allowed_provenances

    def check(self, parts: tuple[DataPart, ...]) -> None:
        intent = _intent.get()
        if intent is None or intent.provenance not in self.allowed_provenances:
            raise CompetitionDataFault("competition_data_unreviewed")
        by_id = {part.part_id: part for part in parts}
        body_part = by_id.get("bedrock.request.body")
        url_part = by_id.get("bedrock.request.url")
        if body_part is None or url_part is None:
            raise CompetitionDataFault("competition_data_unreviewed")
        # The Converse model id travels URL-encoded in the request path.
        url = url_part.content.decode("utf-8", errors="strict")
        if intent.model_id not in url and intent.model_id.replace(":", "%3A") not in url:
            raise CompetitionDataFault("competition_data_unreviewed")
        try:
            body = json.loads(body_part.content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise CompetitionDataFault("competition_data_unreviewed") from error
        if not isinstance(body, dict):
            raise CompetitionDataFault("competition_data_unreviewed")
        system = body.get("system")
        messages = body.get("messages")
        if system != [{"text": intent.system_text}]:
            raise CompetitionDataFault("competition_data_unreviewed")
        if messages != [{"role": "user", "content": [{"text": intent.user_payload}]}]:
            raise CompetitionDataFault("competition_data_unreviewed")
        recognized = {"system", "messages", "inferenceConfig", "additionalModelRequestFields"}
        extras = set(body) - recognized
        if extras or body.get("additionalModelRequestFields") not in (None, {}):
            # A field this check does not model could smuggle undeclared content.
            raise CompetitionDataFault("competition_data_unreviewed")
