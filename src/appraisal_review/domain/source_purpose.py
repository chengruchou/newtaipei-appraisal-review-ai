"""Source identity is necessary but its permitted use must also be established."""

from dataclasses import dataclass
from typing import Literal

from appraisal_review.domain.document_models import SourceCitation, SourceDocument, SourceRegistry
from appraisal_review.domain.factor_models import CaseFacts, ReviewPolicy

Purpose = Literal["case", "rule", "procedure"]


@dataclass(frozen=True)
class SourcePurposes:
    registry: SourceRegistry
    forms: SourceDocument
    criteria: SourceDocument

    @classmethod
    def selected(
        cls,
        registry: SourceRegistry,
        *,
        forms: SourceDocument | None = None,
        criteria: SourceDocument | None = None,
    ) -> "SourcePurposes":
        def select(role: str, supplied: SourceDocument | None) -> SourceDocument:
            choices = [d for d in registry.documents if d.role == role]
            if supplied is not None:
                choices = [d for d in choices if d.same_identity_and_content(supplied)]
            if len(choices) != 1:
                raise ValueError("source_purpose: one current source per selected role required")
            return choices[0]

        return cls(registry, select("forms", forms), select("criteria", criteria))

    def allows(self, refs: list[SourceCitation], purpose: Purpose) -> bool:
        allowed = [self.forms] if purpose == "case" else [self.criteria]
        if purpose == "procedure":
            allowed = [
                self.forms,
                self.criteria,
                *[d for d in self.registry.documents if d.role == "reference"],
            ]
        return bool(refs) and all(
            self.registry.resolves(ref)
            and any(
                (ref.document_id, ref.content_hash, ref.version)
                == (d.document_id, d.content_hash, d.version)
                for d in allowed
            )
            for ref in refs
        )

    def violations(
        self, policy: ReviewPolicy, facts: CaseFacts
    ) -> list[tuple[str, list[SourceCitation]]]:
        invalid: list[tuple[str, list[SourceCitation]]] = []

        def check(id: str, refs: list[SourceCitation], purpose: Purpose) -> None:
            if not self.allows(refs, purpose):
                invalid.append((id, refs))

        for entry in policy.inventory.contexts:
            check(entry.context.key(), entry.evidence, "case")
        for slot in policy.inventory.slots:
            check(f"observed/{slot.id}", slot.evidence, "case")
        for observed in facts.observed:
            check(f"observed/{observed.slot_id}", observed.evidence, "case")
        for pair in facts.pairs:
            for refs in (pair.target_sources, pair.comparable_sources):
                check(f"{pair.context.key()}/factor/{pair.pair.factor_id}", refs, "case")
        for empty in policy.inventory.empty_columns:
            check(f"empty/{empty.id}", empty.evidence, "case")
        for arithmetic in policy.inventory.checks:
            check(f"arithmetic/{arithmetic.id}", arithmetic.evidence, "procedure")
        for scoped in policy.rule_sets:
            check(scoped.context.key(), scoped.evidence, "rule")
            source = scoped.rules.source_document
            if (source.document_id, source.content_hash, scoped.source_version) != (
                self.criteria.document_id,
                self.criteria.content_hash,
                self.criteria.version,
            ):
                invalid.append((scoped.context.key(), scoped.evidence))
        return invalid
