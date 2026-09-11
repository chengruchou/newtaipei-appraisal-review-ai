"""Source identity is necessary but its permitted use must also be established."""

from dataclasses import dataclass
from typing import Literal

from appraisal_review.domain.document_models import SourceCitation, SourceDocument, SourceRegistry
from appraisal_review.domain.factor_models import CaseFacts, ReviewPolicy
from appraisal_review.domain.rule_sources import RuleBundle

Purpose = Literal["case", "rule", "procedure"]


@dataclass(frozen=True)
class SourcePurposes:
    registry: SourceRegistry
    forms: SourceDocument
    criteria: SourceDocument
    bundle: RuleBundle | None = None

    @classmethod
    def selected(
        cls,
        registry: SourceRegistry,
        *,
        forms: SourceDocument | None = None,
        criteria: SourceDocument | None = None,
        bundle: RuleBundle | None = None,
    ) -> "SourcePurposes":
        def select(role: str, supplied: SourceDocument | None) -> SourceDocument:
            choices = [d for d in registry.documents if d.role == role]
            if supplied is not None:
                choices = [d for d in choices if d.same_identity_and_content(supplied)]
            if len(choices) != 1:
                raise ValueError("source_purpose: one current source per selected role required")
            return choices[0]

        if bundle is not None:
            chosen = [
                d
                for d in registry.documents
                if d.document_id == bundle.primary_criteria_document_id
            ]
            if len(chosen) != 1 or (
                criteria is not None and not criteria.same_identity_and_content(chosen[0])
            ):
                raise ValueError("source_purpose: primary criteria differs from selected bundle")
            criteria = chosen[0]
            for selected in bundle.sources:
                matches = [
                    d
                    for d in registry.documents
                    if (
                        (d.document_id, d.version, d.content_hash)
                        == (selected.document_id, selected.version, selected.content_hash)
                        and d.role
                        in (
                            {"criteria"}
                            if selected.role == "district_basis"
                            else {"criteria", "reference"}
                        )
                    )
                ]
                if len(matches) != 1 or any(
                    page > len(matches[0].pages) for page in selected.pages
                ):
                    raise ValueError("source_purpose: selected rule source is not current")
        return cls(registry, select("forms", forms), select("criteria", criteria), bundle)

    def allows(
        self,
        refs: list[SourceCitation],
        purpose: Purpose,
        *,
        scope: Literal["regional", "individual"] | None = None,
    ) -> bool:
        allowed = [self.forms] if purpose == "case" else [self.criteria]
        if purpose == "procedure":
            allowed = [
                self.forms,
                self.criteria,
                *[d for d in self.registry.documents if d.role == "reference"],
            ]
        if self.bundle is not None and purpose != "case":
            selected = [
                source
                for source in self.bundle.sources
                if scope in source.scopes
                and (purpose == "procedure" or source.use == "factor_rules")
            ]
            return bool(refs) and all(
                self.registry.resolves(ref)
                and any(
                    (ref.document_id, ref.version, ref.content_hash)
                    == (source.document_id, source.version, source.content_hash)
                    and ref.page in source.pages
                    for source in selected
                )
                for ref in refs
            )
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

        def check(
            id: str,
            refs: list[SourceCitation],
            purpose: Purpose,
            scope: Literal["regional", "individual"] | None = None,
        ) -> None:
            if not self.allows(refs, purpose, scope=scope):
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
        slots = {slot.id: slot for slot in policy.inventory.slots}
        for arithmetic in policy.inventory.checks:
            names = [*arithmetic.inputs, arithmetic.target]
            if any(name not in slots for name in names):
                invalid.append((f"arithmetic/{arithmetic.id}", arithmetic.evidence))
                continue
            for scope in {slots[name].context.scope for name in names}:
                check(f"arithmetic/{arithmetic.id}", arithmetic.evidence, "procedure", scope)
        for scoped in policy.rule_sets:
            check(scoped.context.key(), scoped.evidence, "rule", scoped.context.scope)
            source = scoped.rules.source_document
            bindings = [
                (source.document_id, source.content_hash, scoped.source_version, source.pages),
                *(
                    (s.document_id, s.content_hash, s.version, s.pages)
                    for s in scoped.additional_sources
                ),
            ]
            allowed = (
                [
                    (s.document_id, s.content_hash, s.version, s.pages)
                    for s in self.bundle.sources
                    if s.use == "factor_rules" and scoped.context.scope in s.scopes
                ]
                if self.bundle is not None
                else [
                    (
                        self.criteria.document_id,
                        self.criteria.content_hash,
                        self.criteria.version,
                        list(range(1, len(self.criteria.pages) + 1)),
                    )
                ]
            )
            if len({b[0] for b in bindings}) != len(bindings) or any(
                not any(b[:3] == a[:3] and set(b[3]) <= set(a[3]) for a in allowed)
                for b in bindings
            ):
                invalid.append((scoped.context.key(), scoped.evidence))
        return invalid
