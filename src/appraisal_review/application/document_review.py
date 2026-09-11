"""Assemble reviewable page candidates and inject them into the existing Controller."""

from __future__ import annotations

from collections import defaultdict

from appraisal_review.adapters.local.pdf_parser import LocalPDFParser
from appraisal_review.application.bootstrap import ReviewAdapters
from appraisal_review.domain.document_models import SourceRegistry
from appraisal_review.domain.extraction_models import PageExtraction, ProposedRule
from appraisal_review.domain.factor_models import (
    CaseFacts,
    FactorRuleSet,
    ReviewMaterial,
    ReviewPolicy,
    RuleApplicability,
    RuleSource,
    ScopedRules,
)
from appraisal_review.domain.review_contracts import CaseIdentity, InventoryContext, ReviewInventory
from appraisal_review.domain.rule_sources import RuleBundle, RuleDocumentVersion
from appraisal_review.domain.source_purpose import SourcePurposes
from appraisal_review.ports.approval import ReviewAuthorization
from appraisal_review.ports.workflow import ParsedDocument


def assemble(
    identity: CaseIdentity,
    registry: SourceRegistry,
    extractions: list[PageExtraction],
    *,
    rule_bundle: RuleBundle | None = None,
) -> ReviewMaterial:
    """Page continuations merge inventory only; duplicate facts/rules never silently overwrite."""
    purposes = SourcePurposes.selected(registry, bundle=rule_bundle)
    if rule_bundle is not None and rule_bundle.identity != identity:
        raise ValueError("Rule bundle belongs to a different case revision")
    seen = set()
    contexts: dict[str, InventoryContext] = {}
    rules: dict[str, list[ProposedRule]] = defaultdict(list)
    pages: dict[str, list[int]] = defaultdict(list)
    tables: dict[str, list[str]] = defaultdict(list)
    unresolved: list[str] = []
    for extraction in extractions:
        key = extraction.document_id, extraction.page
        source = next((d for d in registry.documents if d.document_id == key[0]), None)
        if key in seen or source is None or extraction.content_hash != source.content_hash:
            raise ValueError("Duplicate extraction page or changed source")
        if not 1 <= extraction.page <= len(source.pages):
            raise ValueError("Extraction page is outside source")
        seen.add(key)
        pages[key[0]].append(key[1])
        proposal = extraction.proposal
        if not source.same_identity_and_content(purposes.forms) and any(
            (
                proposal.contexts,
                proposal.pairs,
                proposal.observed,
                proposal.slots,
                proposal.empty_columns,
            )
        ):
            unresolved.append("source_purpose: case proposals require the selected forms page")
        tables[key[0]].extend(proposal.accounted_table_ids)
        for context in proposal.contexts:
            id = context.context.key()
            if id not in contexts:
                contexts[id] = context.model_copy(deep=True)
            else:
                current = contexts[id]
                current.factor_ids = list(dict.fromkeys([*current.factor_ids, *context.factor_ids]))
                current.evidence.extend(context.evidence)
        for candidate in proposal.rules:
            if not purposes.allows(candidate.evidence, "rule", scope=candidate.scope):
                unresolved.append(
                    "Reference/background rules require explicit applicability review"
                )
                continue
            rules[candidate.scope].append(candidate)
            unresolved.extend(candidate.unresolved)
        unresolved.extend(proposal.unresolved)
    scoped = []
    for context in contexts.values():
        proposed = rules[context.context.scope]
        if not proposed:
            unresolved.append(f"No supported rules for {context.context.scope}")
            continue
        contributing = {
            ref.document_id: next(d for d in registry.documents if d.document_id == ref.document_id)
            for candidate in proposed
            for ref in candidate.evidence
        }
        source = (
            contributing.get(purposes.criteria.document_id) or contributing[sorted(contributing)[0]]
        )
        if rule_bundle is None and (len(contributing) != 1 or source != purposes.criteria):
            raise ValueError("Multiple rule sources require an explicit bundle")
        version = rule_bundle.selection_id if rule_bundle is not None else source.version
        scoped.append(
            ScopedRules(
                context=context.context,
                source_version=source.version,
                zone=identity.zone,
                evidence=[ref for rule in proposed for ref in rule.evidence],
                additional_sources=[
                    RuleDocumentVersion(
                        document_id=d.document_id,
                        version=d.version,
                        content_hash=d.content_hash,
                        pages=sorted(
                            {
                                ref.page
                                for candidate in proposed
                                for ref in candidate.evidence
                                if ref.document_id == d.document_id
                            }
                        ),
                    )
                    for d in contributing.values()
                    if d.document_id != source.document_id
                ],
                rules=FactorRuleSet(
                    rule_set_id=f"{source.document_id}.{context.context.scope}",
                    version=version,
                    status="candidate",
                    applicability=RuleApplicability(
                        jurisdiction=identity.district,
                        land_use_category=identity.land_use_category,
                        effective_from=identity.effective_date,
                        effective_to=identity.effective_date,
                    ),
                    source_document=RuleSource(
                        document_id=source.document_id,
                        content_hash=source.content_hash,
                        pages=sorted(
                            {
                                ref.page
                                for rule in proposed
                                for ref in rule.evidence
                                if ref.document_id == source.document_id
                            }
                        ),
                    ),
                    rules=[rule.rule for rule in proposed],
                ),
            )
        )
    # The configured case date bounds this candidate to that date; reviewer must
    # verify applicability from the criteria. No universal effective date is inferred.
    proposals = [e.proposal for e in extractions]
    policy = ReviewPolicy(
        identity=identity,
        registry=registry,
        rule_sets=scoped,
        rule_bundle=rule_bundle,
        inventory=ReviewInventory(
            contexts=list(contexts.values()),
            slots=[slot for p in proposals for slot in p.slots],
            checks=[check for p in proposals for check in p.checks],
            empty_columns=[empty for p in proposals for empty in p.empty_columns],
            inspected_pages={doc: sorted(numbers) for doc, numbers in pages.items()},
            inspected_tables=dict(tables),
            unresolved=unresolved,
            unsupported=[u for p in proposals for u in p.unsupported],
        ),
    )
    material = ReviewMaterial(
        policy=policy,
        facts=CaseFacts(
            identity=identity,
            pairs=[pair for p in proposals for pair in p.pairs],
            observed=[value for p in proposals for value in p.observed],
        ),
    )

    for id, _refs in purposes.violations(material.policy, material.facts):
        material.policy.inventory.unresolved.append(f"source_purpose: invalid evidence use at {id}")
    return material


class MaterialProvider:
    """Server-configured review material, never accepted as an HTTP approval flag."""

    def __init__(self, material: ReviewMaterial) -> None:
        self.material = material.model_copy(deep=True)

    async def load_or_build_rules(self, criteria: ParsedDocument) -> ReviewPolicy:
        expected = [d for d in self.material.policy.registry.documents if d.role == "criteria"]
        bundle = self.material.policy.rule_bundle
        if bundle is not None:
            SourcePurposes.selected(self.material.policy.registry, bundle=bundle)
            expected = [d for d in expected if d.document_id == bundle.primary_criteria_document_id]
        if len(expected) != 1 or criteria.source != expected[0]:
            raise ValueError("Criteria source changed since candidate preparation")
        return self.material.policy

    async def extract_facts(self, document: ParsedDocument, *, case_id: str) -> CaseFacts:
        expected = [d for d in self.material.policy.registry.documents if d.role == "forms"]
        if (
            len(expected) != 1
            or document.source != expected[0]
            or case_id != self.material.facts.identity.case_id
        ):
            raise ValueError("Case source changed since candidate preparation")
        return self.material.facts


def document_adapters(
    parser: LocalPDFParser, material: ReviewMaterial, authorization: ReviewAuthorization | None
) -> ReviewAdapters:
    provider = MaterialProvider(material)
    return ReviewAdapters(
        mode="local",
        parser=parser,
        fact_extractor=provider,
        rule_provider=provider,
        authorization=authorization,
    )
