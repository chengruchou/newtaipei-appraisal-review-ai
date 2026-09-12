"""Synthetic unit catalog boundaries; these are not real multi-district acceptance."""

from datetime import date

import pytest
from pydantic import ValidationError

from appraisal_review.adapters.local.synthetic import synthetic_material
from appraisal_review.application.document_review import assemble
from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.domain.case_review import CaseReviewer
from appraisal_review.domain.extraction_models import PageExtraction, PageProposal, ProposedRule
from appraisal_review.domain.factor_models import ReviewMaterial
from appraisal_review.domain.review_contracts import content_digest
from appraisal_review.domain.rule_sources import (
    CatalogRuleSource,
    RuleCatalog,
    resolve_rule_sources,
    verify_rule_bundle,
)
from appraisal_review.domain.source_purpose import SourcePurposes


@pytest.fixture
def catalog_fixture():
    material = synthetic_material()
    criteria = next(d for d in material.policy.registry.documents if d.role == "criteria")
    manual = criteria.model_copy(deep=True)
    manual.document_id, manual.uri, manual.role = (
        "unit-manual",
        "file:///unit/manual.pdf",
        "reference",
    )
    material.policy.registry.documents.append(manual)
    original_ref = material.policy.rule_sets[0].evidence[0]
    manual_ref = original_ref.model_copy(update={"document_id": manual.document_id})
    identity = material.policy.identity
    entries = []
    for document, role, use, citation in [
        (criteria, "district_basis", "factor_rules", original_ref),
        (manual, "general_rules", "procedure", manual_ref),
    ]:
        entries.append(
            CatalogRuleSource(
                entry_id=f"unit-{role}",
                entry_version="1",
                document_id=document.document_id,
                version=document.version,
                content_hash=document.content_hash,
                pages=[1],
                role=role,
                use=use,
                district=identity.district,
                zone=identity.zone,
                land_use_category=identity.land_use_category,
                scopes=[c.context.scope for c in material.policy.inventory.contexts],
                effective_from=date(2000, 1, 1),
                effective_to=date(2099, 12, 31),
                review_status="reviewed",
                evidence=[citation],
            )
        )
    return material, RuleCatalog(version="unit-catalog-1", entries=entries)


def resolution(material, catalog, *, confirmed=True):
    return resolve_rule_sources(
        catalog,
        material.policy.identity,
        [c.context for c in material.policy.inventory.contexts],
        material.policy.registry,
        conditions_confirmed=confirmed,
    )


def test_pins_independent_source_versions_and_full_catalog_hash(catalog_fixture):
    material, catalog = catalog_fixture
    selected = resolution(material, catalog)
    assert selected.status == "ready"
    assert selected.bundle.catalog_digest == content_digest(catalog)
    assert selected.bundle_id == content_digest(selected.bundle)
    assert {s.role for s in selected.bundle.sources} == {"general_rules", "district_basis"}
    assert {s.document_id for s in selected.bundle.sources} == {
        d.document_id for d in material.policy.registry.documents if d.role != "forms"
    }
    material.policy.rule_bundle = selected.bundle
    assert (
        CaseReviewer(None).review(material.policy, material.facts, material.policy.registry).status
        != "verified"
    )


@pytest.mark.parametrize("condition", ["district", "zone", "land_use_category", "effective_date"])
def test_no_district_latest_or_date_fallback(catalog_fixture, condition):
    material, catalog = catalog_fixture
    setattr(
        material.policy.identity,
        condition,
        date(2100, 1, 1) if condition == "effective_date" else "other",
    )
    result = resolution(material, catalog)
    assert result.status == "missing" and result.bundle is None


def test_duplicate_matching_version_is_ambiguous_not_latest(catalog_fixture):
    material, catalog = catalog_fixture
    other = catalog.entries[0].model_copy(deep=True)
    other.entry_version = "2"
    catalog.entries.append(other)
    result = resolution(material, catalog)
    assert result.status == "ambiguous" and result.bundle is None


def test_unknown_effectivity_and_conditions_remain_pending(catalog_fixture):
    material, catalog = catalog_fixture
    for source in catalog.entries:
        source.review_status = "candidate"
        source.effective_from = source.effective_to = None
    result = resolution(material, catalog, confirmed=False)
    assert result.status == "needs_review" and result.bundle is not None
    assert "case_conditions_not_confirmed" in result.reasons
    assert len([r for r in result.reasons if r.startswith("effective_period_not_confirmed")]) == 2


@pytest.mark.parametrize(
    "field,value", [("content_hash", "f" * 64), ("version", "stale"), ("pages", [999])]
)
def test_catalog_identity_and_actual_page_bounds_are_checked(catalog_fixture, field, value):
    material, catalog = catalog_fixture
    setattr(catalog.entries[1], field, value)
    assert resolution(material, catalog).status == "missing"


def test_only_explicit_selected_role_can_supply_rules_or_procedures(catalog_fixture):
    material, catalog = catalog_fixture
    selected = resolution(material, catalog).bundle
    purposes = SourcePurposes.selected(material.policy.registry, bundle=selected)
    manual = next(source for source in selected.sources if source.role == "general_rules")
    assert purposes.allows(manual.evidence, "procedure", scope=manual.scopes[0])
    assert not purposes.allows(manual.evidence, "rule", scope="individual")
    assert not purposes.allows(manual.evidence, "case")


def test_legacy_material_serialization_retains_exact_old_digest():
    material = synthetic_material()
    raw = material.model_dump(mode="json")
    assert "rule_bundle" not in raw["policy"]
    assert all("additional_sources" not in rule for rule in raw["policy"]["rule_sets"])
    assert content_digest(ReviewMaterial.model_validate(raw)) == content_digest(material)


def test_two_factor_sources_assemble_with_separate_exact_bindings(catalog_fixture):
    material, catalog = catalog_fixture
    catalog.entries[1].use = "factor_rules"
    selected = resolution(material, catalog).bundle
    forms = next(d for d in material.policy.registry.documents if d.role == "forms")
    pages = []
    for source in catalog.entries:
        rule = material.policy.rule_sets[0].rules.rules[0].model_copy(deep=True)
        rule.id = source.entry_id
        rule.factor_id = source.entry_id
        pages.append(
            PageExtraction(
                document_id=source.document_id,
                page=1,
                content_hash=source.content_hash,
                origin="native",
                model_id=None,
                region="local",
                input_tokens=0,
                output_tokens=0,
                elapsed_seconds=0,
                attempts=1,
                proposal=PageProposal(
                    rules=[
                        ProposedRule(scope=source.scopes[0], rule=rule, evidence=source.evidence)
                    ]
                ),
            )
        )
    pages.append(
        PageExtraction(
            document_id=forms.document_id,
            page=1,
            content_hash=forms.content_hash,
            origin="manual",
            model_id=None,
            region="local",
            input_tokens=0,
            output_tokens=0,
            elapsed_seconds=0,
            attempts=1,
            proposal=PageProposal(
                contexts=material.policy.inventory.contexts,
                pairs=material.facts.pairs,
                slots=material.policy.inventory.slots,
                observed=material.facts.observed,
            ),
        )
    )
    assembled = assemble(
        material.policy.identity, material.policy.registry, pages, rule_bundle=selected
    )
    scoped = assembled.policy.rule_sets[0]
    assert len(scoped.rules.rules) == 2 and len(scoped.additional_sources) == 1
    assert scoped.additional_sources[0].document_id == catalog.entries[1].document_id
    assert scoped.rules.version == selected.selection_id
    assert scoped.rules.status == "candidate"
    assert not SourcePurposes.selected(assembled.policy.registry, bundle=selected).violations(
        assembled.policy, assembled.facts
    )


def test_revision_rebinds_bundle_version_without_confirming_conditions(catalog_fixture):
    material, catalog = catalog_fixture
    material.policy.rule_bundle = resolution(material, catalog, confirmed=False).bundle
    before = material.policy.rule_bundle.model_copy(deep=True)
    parent = RevisionSnapshot.capture(material, "parent")
    child = parent.revise(parent.material, "child")
    bundle = child.material.policy.rule_bundle
    assert bundle.identity.version == child.material.policy.identity.version == "child"
    assert bundle.conditions_confirmed is False
    assert bundle.sources == before.sources and bundle.catalog_digest == before.catalog_digest
    assert parent.material.policy.rule_bundle == before


def test_model_origin_never_accepts_an_absent_model_identifier():
    with pytest.raises(ValidationError):
        PageExtraction(
            document_id="unit",
            page=1,
            content_hash="a" * 64,
            proposal=PageProposal(),
            origin="model",
            model_id=None,
            region="local",
            input_tokens=0,
            output_tokens=0,
            elapsed_seconds=0,
            attempts=1,
        )


def test_full_catalog_ambiguity_cannot_be_hidden_by_selected_members(catalog_fixture):
    material, catalog = catalog_fixture
    bundle = resolution(material, catalog).bundle
    conflicting = catalog.entries[0].model_copy(deep=True)
    conflicting.entry_version = "conflict"
    bundle.catalog.entries.append(conflicting)
    bundle.catalog_digest = content_digest(bundle.catalog)
    result = verify_rule_bundle(
        bundle,
        material.policy.identity,
        [c.context for c in material.policy.inventory.contexts],
        material.policy.registry,
    )
    assert result.status == "ambiguous"
    material.policy.rule_bundle = bundle
    reviewed = CaseReviewer(None).review(material.policy, material.facts, material.policy.registry)
    assert reviewed.status == "needs_review"
    assert not reviewed.comparisons
    assert any("ambiguous:" in f.trace for f in reviewed.findings)


def test_selected_members_must_equal_the_full_catalog_resolution(catalog_fixture):
    material, catalog = catalog_fixture
    bundle = resolution(material, catalog).bundle
    bundle.sources[0].entry_version = "unselected-version"
    result = verify_rule_bundle(
        bundle,
        material.policy.identity,
        [c.context for c in material.policy.inventory.contexts],
        material.policy.registry,
    )
    assert result.reasons == ["selected_catalog_members_mismatch"]


def test_scoped_sources_cannot_supply_other_scope_or_unspecified_use(catalog_fixture):
    material, catalog = catalog_fixture
    bundle = resolution(material, catalog).bundle
    source = next(s for s in bundle.sources if s.use == "factor_rules")
    scope = source.scopes[0]
    other = "individual" if scope == "regional" else "regional"
    purposes = SourcePurposes.selected(material.policy.registry, bundle=bundle)
    assert purposes.allows(source.evidence, "rule", scope=scope)
    assert not purposes.allows(source.evidence, "rule", scope=other)
    assert not purposes.allows(source.evidence, "rule")
    material.policy.rule_bundle = bundle
    material.policy.rule_sets[0].context.scope = other
    assert purposes.violations(material.policy, material.facts)


def test_old_bundle_bytes_stay_readable_but_missing_catalog_blocks_new_review(catalog_fixture):
    from appraisal_review.domain.rule_sources import RuleBundle

    material, catalog = catalog_fixture
    raw = resolution(material, catalog).bundle.model_dump()
    raw.pop("catalog")
    assert "condition_candidates" not in raw
    old = RuleBundle.model_validate(raw)
    assert old.model_dump() == raw
    result = verify_rule_bundle(
        old,
        material.policy.identity,
        [c.context for c in material.policy.inventory.contexts],
        material.policy.registry,
    )
    assert result.reasons == ["pinned_catalog_snapshot_missing"]


def test_bundle_snapshot_digest_and_page_integer_are_not_coerced(catalog_fixture):
    from appraisal_review.domain.rule_sources import RuleBundle, RuleDocumentVersion

    material, catalog = catalog_fixture
    raw = resolution(material, catalog).bundle.model_dump()
    raw["catalog_digest"] = "f" * 64
    with pytest.raises(ValidationError, match="catalog snapshot"):
        RuleBundle.model_validate(raw)
    for pages in [[True], ["1"], [1.0]]:
        with pytest.raises(ValidationError):
            RuleDocumentVersion(document_id="unit", version="1", content_hash="a" * 64, pages=pages)


def test_fact_only_revision_preserves_rule_selection_version(catalog_fixture):
    material, catalog = catalog_fixture
    bundle = resolution(material, catalog).bundle
    before = bundle.selection_id
    original_bundle = bundle.bundle_id
    bundle.identity.version = "fact-confirmation-r2"
    assert bundle.selection_id == before
    assert bundle.bundle_id != original_bundle


def test_unavailable_source_purpose_returns_blocked_result_without_exception(catalog_fixture):
    material, catalog = catalog_fixture
    bundle = resolution(material, catalog, confirmed=False).bundle
    material.policy.rule_bundle = bundle
    other_forms = next(
        d for d in material.policy.registry.documents if d.role == "forms"
    ).model_copy(deep=True)
    other_forms.document_id = "second-forms"
    material.policy.registry.documents.append(other_forms)
    result = CaseReviewer(None).review(material.policy, material.facts, material.policy.registry)
    assert result.status == "needs_review"
    assert any(f.kind == "source_purpose" for f in result.findings)
