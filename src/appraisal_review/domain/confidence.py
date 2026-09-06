"""Explicit side confirmation binding; raw measurements are never promoted."""

import hashlib
import json
from typing import Literal

from appraisal_review.domain.factor_models import EvidencedPair
from appraisal_review.domain.review_contracts import FactConfirmation

Side = Literal["target", "comparable"]


def confirmation_digest(pair: EvidencedPair, side: Side) -> str:
    reliability = getattr(pair, f"{side}_reliability")
    data = {
        "context": pair.context.model_dump(mode="json"),
        "factor_id": pair.pair.factor_id,
        "side": side,
        "observation": getattr(pair.pair, side).model_dump(mode="json"),
        "sources": [r.model_dump(mode="json") for r in getattr(pair, f"{side}_sources")],
        "reliability": reliability.model_dump(mode="json", exclude={"method", "confirmation"}),
    }
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def confirm_side(pair: EvidencedPair, side: Side, *, reviewer: str) -> None:
    """Called by the trusted reviewer operation, never from model-controlled tools.

    This is a review assertion, not authorization. The final material still needs
    the separately injected exact-material authority.
    """
    observation = getattr(pair.pair, side)
    reliability = getattr(pair, f"{side}_reliability")
    if (
        observation.value is None
        or not observation.evidence
        or not getattr(pair, f"{side}_sources")
        or reliability.unresolved
        or reliability.selection == "ambiguous"
        or reliability.confidence_kind == "unknown"
        or reliability.provenance == "unknown"
    ):
        raise ValueError("Resolve missing facts, provenance and ambiguity before confirmation")
    reliability.confirmation = FactConfirmation(
        reviewer=reviewer, input_digest=confirmation_digest(pair, side)
    )
    reliability.method = "reviewer_confirmed"
