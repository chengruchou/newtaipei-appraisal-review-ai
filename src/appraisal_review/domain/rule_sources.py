"""Pinned local rule selection, independent of storage and formal rule approval."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal

from pydantic import Field, SerializerFunctionWrapHandler, model_serializer, model_validator

from appraisal_review.domain.document_models import (
    Digest,
    DocumentModel,
    SourceCitation,
    SourceRegistry,
)
from appraisal_review.domain.review_contracts import CaseIdentity, ComparisonContext, content_digest


class RuleDocumentVersion(DocumentModel):
    document_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    content_hash: Digest
    pages: list[Annotated[int, Field(strict=True, ge=1)]] = Field(min_length=1)

    @model_validator(mode="after")
    def numbered_pages(self) -> RuleDocumentVersion:
        if self.pages != sorted(set(self.pages)) or self.pages[0] < 1:
            raise ValueError("Rule source pages must be unique, ascending and one-based")
        return self


class CatalogRuleSource(RuleDocumentVersion):
    entry_id: str = Field(min_length=1)
    entry_version: str = Field(min_length=1)
    role: Literal["general_rules", "district_basis"]
    use: Literal["factor_rules", "procedure"]
    district: str = Field(min_length=1)
    zone: str = Field(min_length=1)
    land_use_category: str = Field(min_length=1)
    scopes: list[Literal["regional", "individual"]] = Field(min_length=1)
    effective_from: date | None
    effective_to: date | None
    review_status: Literal["candidate", "reviewed"] = "candidate"
    evidence: list[SourceCitation] = Field(min_length=1)
    unresolved: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def exact_metadata(self) -> CatalogRuleSource:
        if len(self.scopes) != len(set(self.scopes)):
            raise ValueError("Duplicate catalog scope")
        if self.effective_from and self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("Invalid catalog effective period")
        if any(
            (e.document_id, e.version, e.content_hash)
            != (self.document_id, self.version, self.content_hash)
            or e.page not in self.pages
            for e in self.evidence
        ):
            raise ValueError("Catalog evidence must bind the selected original pages")
        if self.review_status == "reviewed" and (
            self.effective_from is None or self.effective_to is None or self.unresolved
        ):
            raise ValueError(
                "Reviewed catalog metadata requires explicit period and resolved issues"
            )
        return self


class CaseConditionCandidate(DocumentModel):
    """A source-backed proposed condition, never an assertion of applicability."""

    field: Literal[
        "case_id",
        "district",
        "zone",
        "land_use_category",
        "effective_date",
        "current_use",
        "regulatory_zone",
        "target_id",
        "comparable_id",
    ]
    value: str = Field(min_length=1)
    method: Literal["native_proposed", "manual_proposed"] = "manual_proposed"
    evidence: list[SourceCitation] = Field(min_length=1)
    interpretation: str = Field(min_length=1)


class RuleCatalog(DocumentModel):
    version: str = Field(min_length=1)
    entries: list[CatalogRuleSource]

    @model_validator(mode="after")
    def unique_entries(self) -> RuleCatalog:
        if len({(e.entry_id, e.entry_version) for e in self.entries}) != len(self.entries):
            raise ValueError("Duplicate catalog entry version")
        return self


class RuleBundle(DocumentModel):
    """A source selection, never a grant of authority to execute or publish."""

    catalog_version: str = Field(min_length=1)
    catalog_digest: Digest
    primary_criteria_document_id: str = Field(min_length=1)
    identity: CaseIdentity
    contexts: list[ComparisonContext] = Field(min_length=1)
    sources: list[CatalogRuleSource] = Field(min_length=2)
    conditions_confirmed: bool = False
    catalog: RuleCatalog | None = None
    condition_candidates: list[CaseConditionCandidate] = Field(default_factory=list)

    @model_validator(mode="after")
    def independent_sources(self) -> RuleBundle:
        if self.catalog is not None and (
            self.catalog.version != self.catalog_version
            or content_digest(self.catalog) != self.catalog_digest
        ):
            raise ValueError("Bundle catalog snapshot differs from its pinned identity")
        if len({s.document_id for s in self.sources}) != len(self.sources):
            raise ValueError("Each original rule document has one explicit role")
        if {s.role for s in self.sources} != {"general_rules", "district_basis"}:
            raise ValueError("A bundle requires both general and district sources")
        if len({c.key() for c in self.contexts}) != len(self.contexts):
            raise ValueError("Duplicate bundle context")
        if not any(
            s.document_id == self.primary_criteria_document_id
            and s.role == "district_basis"
            and s.use == "factor_rules"
            for s in self.sources
        ):
            raise ValueError("The primary criteria must be an explicit district factor source")
        return self

    @model_serializer(mode="wrap")
    def optional_snapshots(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        if self.catalog is None:
            data.pop("catalog", None)
        if not self.condition_candidates:
            data.pop("condition_candidates", None)
        return data

    @property
    def selection_id(self) -> str:
        """Rule-selection version is stable across fact-only material revisions."""
        data = self.model_dump(mode="json", exclude={"catalog"})
        data["identity"].pop("version")
        import hashlib
        import json

        return hashlib.sha256(
            json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @property
    def bundle_id(self) -> str:
        return content_digest(self)


class RuleResolution(DocumentModel):
    status: Literal["ready", "needs_review", "missing", "ambiguous"]
    reasons: list[str]
    bundle: RuleBundle | None = None
    bundle_id: Digest | None = None


def resolve_rule_sources(
    catalog: RuleCatalog,
    identity: CaseIdentity,
    contexts: list[ComparisonContext],
    registry: SourceRegistry,
    *,
    conditions_confirmed: bool = False,
    condition_candidates: list[CaseConditionCandidate] | None = None,
) -> RuleResolution:
    """Require an exact role/scope match; never choose newest or a district fallback.

    Unknown effective periods remain candidate selections with explicit blockers.
    Storage must independently revalidate original bytes; metadata is not admission.
    """
    if not contexts:
        return RuleResolution(status="missing", reasons=["comparison_context_missing"])
    chosen: dict[str, CatalogRuleSource] = {}
    missing: list[str] = []
    ambiguous: list[str] = []
    for role in ("general_rules", "district_basis"):
        for scope in sorted({c.scope for c in contexts}):
            matches = [
                entry
                for entry in catalog.entries
                if (
                    entry.role == role
                    and entry.district == identity.district
                    and entry.zone == identity.zone
                    and entry.land_use_category == identity.land_use_category
                    and scope in entry.scopes
                    and (
                        entry.effective_from is None
                        or entry.effective_from <= identity.effective_date
                    )
                    and (
                        entry.effective_to is None or identity.effective_date <= entry.effective_to
                    )
                )
            ]
            if len(matches) != 1:
                (missing if not matches else ambiguous).append(f"{role}:{scope}")
            else:
                entry = matches[0]
                prior = chosen.get(entry.document_id)
                if prior is not None and prior != entry:
                    ambiguous.append(f"conflicting_document_binding:{entry.document_id}")
                chosen[entry.document_id] = entry
    if ambiguous or missing:
        return RuleResolution(
            status="ambiguous" if ambiguous else "missing",
            reasons=[f"ambiguous:{s}" for s in ambiguous] + [f"missing:{s}" for s in missing],
        )
    reasons = [] if conditions_confirmed else ["case_conditions_not_confirmed"]
    for source in chosen.values():
        registered = [
            d
            for d in registry.documents
            if (
                d.document_id == source.document_id
                and d.version == source.version
                and d.content_hash == source.content_hash
            )
        ]
        if (
            len(registered) != 1
            or registered[0].role
            not in ({"criteria"} if source.role == "district_basis" else {"criteria", "reference"})
            or any(page > len(registered[0].pages) for page in source.pages)
            or not all(registry.resolves(e) for e in source.evidence)
        ):
            return RuleResolution(status="missing", reasons=["source_identity_or_purpose_mismatch"])
        if source.review_status != "reviewed":
            reasons.append(f"catalog_metadata_not_reviewed:{source.entry_id}")
        if source.effective_from is None or source.effective_to is None:
            reasons.append(f"effective_period_not_confirmed:{source.entry_id}")
        reasons.extend(source.unresolved)
    primary = [
        s.document_id
        for s in chosen.values()
        if s.role == "district_basis" and s.use == "factor_rules"
    ]
    if len(primary) != 1:
        return RuleResolution(status="ambiguous", reasons=["primary_criteria_selection_required"])
    bundle = RuleBundle(
        catalog=catalog,
        condition_candidates=condition_candidates or [],
        catalog_version=catalog.version,
        catalog_digest=content_digest(catalog),
        primary_criteria_document_id=primary[0],
        identity=identity,
        contexts=contexts,
        sources=sorted(chosen.values(), key=lambda e: e.entry_id),
        conditions_confirmed=conditions_confirmed,
    )
    return RuleResolution(
        status="needs_review" if reasons else "ready",
        reasons=reasons,
        bundle=bundle,
        bundle_id=bundle.bundle_id,
    )


def verify_rule_bundle(
    bundle: RuleBundle,
    identity: CaseIdentity,
    contexts: list[ComparisonContext],
    registry: SourceRegistry,
) -> RuleResolution:
    """Re-resolve the full pinned catalog; selected members cannot hide conflicts."""
    if bundle.identity != identity or {c.key() for c in bundle.contexts} != {
        c.key() for c in contexts
    }:
        return RuleResolution(status="needs_review", reasons=["bundle_case_binding_mismatch"])
    if bundle.catalog is None:
        return RuleResolution(status="needs_review", reasons=["pinned_catalog_snapshot_missing"])
    if (
        bundle.catalog.version != bundle.catalog_version
        or content_digest(bundle.catalog) != bundle.catalog_digest
    ):
        return RuleResolution(status="needs_review", reasons=["catalog_snapshot_identity_mismatch"])
    if any(
        not registry.resolves(ref)
        for candidate in bundle.condition_candidates
        for ref in candidate.evidence
    ):
        return RuleResolution(status="needs_review", reasons=["condition_source_binding_mismatch"])
    selection = resolve_rule_sources(
        bundle.catalog,
        identity,
        contexts,
        registry,
        conditions_confirmed=bundle.conditions_confirmed,
        condition_candidates=bundle.condition_candidates,
    )
    if selection.bundle is not None and (
        selection.bundle.sources != bundle.sources
        or selection.bundle.primary_criteria_document_id != bundle.primary_criteria_document_id
    ):
        return RuleResolution(status="needs_review", reasons=["selected_catalog_members_mismatch"])
    return selection
