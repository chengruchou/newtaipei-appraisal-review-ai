"""Located candidate questions; downstream human-task persistence owns acceptance."""

from appraisal_review.domain.document_models import SourceCitation
from appraisal_review.domain.extraction_contracts import (
    HandoffLocation,
    HandoffRequest,
    PageOutcome,
)


def with_candidate_handoffs(outcome: PageOutcome) -> PageOutcome:
    proposal = outcome.proposal
    if proposal is None:
        return outcome
    handoffs = list(outcome.handoffs)

    def add(reason: str, refs: list[SourceCitation], field: str | None = None) -> None:
        locations = tuple(
            dict.fromkeys(
                HandoffLocation(page=ref.page, region_id=ref.region_id, field_id=field)
                for ref in refs
            )
        ) or (HandoffLocation(page=outcome.request.page, field_id=field),)
        handoff = HandoffRequest.model_validate(
            {"request": outcome.request, "reason": reason, "locations": locations}
        )
        if handoff not in handoffs:
            handoffs.append(handoff)

    if proposal.unsupported:
        add("unsupported", [])
    if proposal.unresolved:
        add("conflicting", [])
    for rule in proposal.rules:
        if rule.unresolved:
            add("conflicting", rule.evidence)
    for pair in proposal.pairs:
        for side in ("target", "comparable"):
            observation = getattr(pair.pair, side)
            reliability = getattr(pair, f"{side}_reliability")
            refs = getattr(pair, f"{side}_sources")
            # Self-reported confidence never changes authority or measured reliability.
            if observation.value is None:
                add("missing", refs)
            if reliability.unresolved or reliability.selection == "ambiguous":
                add("conflicting", refs)
            if reliability.method == "model_proposed" or observation.confidence < 0.8:
                add("low_confidence", refs)
    for context in proposal.contexts:
        for factor in context.factor_ids:
            if not any(
                p.context == context.context and p.pair.factor_id == factor for p in proposal.pairs
            ):
                add("missing", context.evidence)
    for slot in proposal.slots:
        observations = [o for o in proposal.observed if o.slot_id == slot.id]
        if not observations or any(
            o.state == "missing" or (o.state == "blank" and not slot.derivable_blank)
            for o in observations
        ):
            add("missing", slot.evidence)
    return PageOutcome.model_validate(outcome.model_dump() | {"handoffs": tuple(handoffs)})
