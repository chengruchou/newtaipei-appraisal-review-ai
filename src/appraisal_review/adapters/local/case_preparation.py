"""Controlled local candidate preparation, with no approval or publication capability.

The configuration is an operator-selected proposal, not an extraction result. Native
rules are rebuilt from the current parser registry. Original PDFs are read-only and
reparsed to detect forged/stale registry regions; OCR is never run by this adapter.
Native PDF localization has no measured semantic confidence, so proposed facts keep
confidence zero. Verified anchors establish localization_only, as in ADR 0008;
semantic confirmation and exact-material approval remain separate operations.
A diagnostic agreement between observed numbers is not acceptance.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from decimal import Decimal
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from appraisal_review.adapters.local.document_manifest import InputManifest
from appraisal_review.adapters.local.native_candidates import NativeCandidate, native_candidates
from appraisal_review.adapters.local.pdf_parser import DocumentInput, LocalPDFParser
from appraisal_review.application.document_review import assemble as assemble_document_review
from appraisal_review.domain.case_review import CaseReviewer
from appraisal_review.domain.document_models import (
    Digest,
    DocumentModel,
    SourceCitation,
    SourceDocument,
    SourceRegistry,
)
from appraisal_review.domain.extraction_models import PageExtraction, PageProposal
from appraisal_review.domain.factor_models import (
    EvidencedPair,
    FactorObservation,
    FactorPair,
    NormalizedValue,
    ReviewMaterial,
)
from appraisal_review.domain.fill_candidates import percent
from appraisal_review.domain.models import EvidenceRef
from appraisal_review.domain.review_contracts import (
    ArithmeticCheck,
    ComparisonContext,
    InventoryContext,
    ObservedValue,
    Reliability,
    ReviewSlot,
    content_digest,
)
from appraisal_review.domain.rule_engine import calculate
from appraisal_review.domain.rule_sources import (
    CaseConditionCandidate,
    RuleCatalog,
    RuleResolution,
    resolve_rule_sources,
)
from appraisal_review.domain.source_purpose import SourcePurposes

ProposalMethod = Literal["native_proposed", "manual_proposed"]


ConditionCandidate = CaseConditionCandidate


class NativeRuleSelection(DocumentModel):
    document_id: str
    factor_id: str
    candidate_digest: Digest
    evidence: list[SourceCitation] = Field(min_length=1)


class SelectedFact(DocumentModel):
    """A proposed normalization and its exact raw value anchor, never a confirmation."""

    value: NormalizedValue | None
    source: SourceCitation
    unit_evidence: list[SourceCitation] = Field(default_factory=list)
    method: ProposalMethod = "manual_proposed"
    interpretation: str = Field(min_length=1)
    unresolved: list[str] = Field(default_factory=list)


class SelectedPair(DocumentModel):
    context: ComparisonContext
    factor_id: str
    target: SelectedFact
    comparable: SelectedFact


class SelectedObservation(DocumentModel):
    """Read a whole numeric cell; a caller cannot supply a replacement result."""

    slot: ReviewSlot
    source: SourceCitation
    unit: Literal["percent_points", "ratio"]
    unit_evidence: list[SourceCitation] = Field(default_factory=list)


class CandidateSelections(DocumentModel):
    schema_version: Literal["local-case-preparation-v1"] = "local-case-preparation-v1"
    version: str = Field(min_length=1)
    registry_digest: Digest
    catalog_digest: Digest
    conditions: list[ConditionCandidate]
    contexts: list[InventoryContext] = Field(min_length=1)
    native_rules: list[NativeRuleSelection] = Field(min_length=1)
    pairs: list[SelectedPair] = Field(default_factory=list)
    observations: list[SelectedObservation] = Field(min_length=1)
    checks: list[ArithmeticCheck] = Field(default_factory=list)
    unresolved: list[str] = Field(min_length=1)


@dataclass(frozen=True)
class PreparedCase:
    material: ReviewMaterial | None
    resolution: RuleResolution
    extractions: list[PageExtraction]
    native: list[NativeCandidate]
    source_inventory: list[dict[str, object]]
    condition_evidence: dict[str, object]
    coverage: dict[str, object]
    arithmetic_observations: list[dict[str, object]]
    input_digests: dict[str, str]


def _unique(values: list[str], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"Duplicate {label}")


def _exact_source(registry: SourceRegistry, ref: SourceCitation) -> SourceDocument:
    if not registry.resolves(ref):
        raise ValueError("Source anchor does not resolve in the current parser registry")
    doc = next(d for d in registry.documents if d.document_id == ref.document_id)
    region = next(r for r in doc.pages[ref.page - 1].regions if r.id == ref.region_id)
    if ref.excerpt != region.text:
        raise ValueError("Source anchor must preserve the whole raw region text")
    return doc


def _decimal_cell(text: str) -> Decimal:
    compact = re.sub(r"\s+", "", text)
    if not re.fullmatch(r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?[%\uFF05]?", compact):
        raise ValueError("Source cell is not one unambiguous numeric value")
    return Decimal(compact.rstrip("%\uff05").replace(",", ""))


def _verify_sources(
    manifest: InputManifest,
    registry: SourceRegistry,
    roots: list[Path],
    *,
    max_bytes: int,
    max_pages: int,
) -> list[dict[str, object]]:
    if not roots or max_bytes <= 0 or max_pages <= 0:
        raise ValueError("Explicit authorized source roots and positive limits are required")
    roots = [r.resolve(strict=True) for r in roots]
    if any(not r.is_dir() for r in roots):
        raise ValueError("An authorized source root must be a directory")
    _unique([s.document_id for s in manifest.documents], "manifest document")
    if {s.document_id for s in manifest.documents} != {d.document_id for d in registry.documents}:
        raise ValueError("Manifest and registry must contain the same original documents")
    if sum(s.role == "forms" for s in manifest.documents) != 1:
        raise ValueError("One original forms document is required")
    parser = LocalPDFParser([], max_bytes=max_bytes, max_pages=max_pages)
    inventory: list[dict[str, object]] = []
    paths: list[str] = []
    for spec in manifest.documents:
        path = spec.path.resolve(strict=True)
        if (
            not spec.path.is_absolute()
            or spec.path != path
            or not path.is_file()
            or path.suffix.lower() != ".pdf"
            or not any(path.is_relative_to(root) for root in roots)
        ):
            raise ValueError(
                "Source must be a regular PDF inside an authorized root, without links"
            )
        paths.append(str(path))
        with path.open("rb") as stream:
            data = stream.read(max_bytes + 1)
        if len(data) > max_bytes or not data.startswith(b"%PDF-"):
            raise ValueError("Unsupported PDF or size limit")
        if hashlib.sha256(data).hexdigest() != spec.expected_hash:
            raise ValueError("Original source hash changed")
        current = next(d for d in registry.documents if d.document_id == spec.document_id)
        if (current.version, current.content_hash, current.role, current.document_date) != (
            spec.version,
            spec.expected_hash,
            spec.role,
            spec.document_date,
        ) or current.uri != path.as_uri():
            raise ValueError("Manifest source identity differs from parsed registry")
        # Only native parsing: no OCR, external model, approval store, or writer.
        parsed = parser.parse_bytes(
            data, DocumentInput(**spec.model_dump()), uri=path.as_uri()
        ).source
        if parsed is None or not parsed.same_identity_and_content(current):
            raise ValueError(
                "Parsed registry differs from current native parser; rebuild selections"
            )
        inventory.append(
            {
                "document_id": current.document_id,
                "version": current.version,
                "sha256": current.content_hash,
                "role": current.role,
                "bytes": len(data),
                "pages": len(current.pages),
                "page_inventory": [p.number for p in current.pages],
                "native_text_pages": [p.number for p in current.pages if p.has_text],
                "native_text_missing_pages": [p.number for p in current.pages if not p.has_text],
                "registry_reparsed_and_matched": True,
                "original_modified": False,
            }
        )
    _unique(paths, "original source path")
    return inventory


def _fact(
    selection: SelectedFact,
    registry: SourceRegistry,
    version: str,
    *,
    expected_unit: str | None,
) -> tuple[FactorObservation, Reliability]:
    doc = _exact_source(registry, selection.source)
    if doc.role != "forms":
        raise ValueError("Case facts require the original forms source")
    for ref in selection.unit_evidence:
        if _exact_source(registry, ref).document_id != doc.document_id:
            raise ValueError("Fact units must be grounded in the same forms document")
    value = selection.value
    unresolved = list(selection.unresolved)
    if value is None:
        unresolved.append("Proposed fact value is missing")
    if value is not None:
        if value.type == "number" and expected_unit is not None and value.unit is None:
            unresolved.append("Required fact unit is missing")
        if not selection.source.excerpt.strip():
            raise ValueError("A blank anchor cannot support a proposed present fact")
        if selection.method == "native_proposed":
            if value.type == "number":
                if _decimal_cell(selection.source.excerpt) != Decimal(str(value.value)):
                    raise ValueError("Native numeric proposal changes the original value")
            elif value.type in {"text", "category"}:
                if str(value.value) != selection.source.excerpt.strip():
                    raise ValueError("Native text proposal changes the original value")
            else:
                raise ValueError("Boolean interpretation requires an explicit manual proposal")
        if value.unit is not None:
            units = {r.excerpt.strip().casefold() for r in selection.unit_evidence}
            if value.unit.casefold() not in units:
                raise ValueError("Fact unit requires a separate exact unit anchor")
            if units != {value.unit.casefold()}:
                unresolved.append("Source unit anchors conflict")
    refs = [selection.source, *selection.unit_evidence]
    observation = FactorObservation(
        raw_text="\n".join(ref.excerpt for ref in refs),
        value=value,
        confidence=0,
        evidence=[
            EvidenceRef(
                document_id=doc.document_id,
                source_file=doc.uri,
                page=ref.page,
                block_ids=[ref.region_id],
                confidence=0,
                bounding_box=ref.bbox,
                coordinate_system="pdf_bottom_left",
            )
            for ref in refs
        ],
    )
    regions = [
        next(r for r in doc.pages[ref.page - 1].regions if r.id == ref.region_id) for ref in refs
    ]
    states = {region.selection for region in regions if region.selection is not None}
    ambiguous = "ambiguous" in states or {"checked", "unchecked"} <= states
    if ambiguous:
        unresolved.append("Source selection marks are ambiguous")
    # Called only after _verify_sources re-parses and matches the complete registry.
    # This describes proven location, never measured accuracy or reviewed semantics.
    reliability = Reliability(
        method=selection.method,
        selection="ambiguous" if ambiguous else regions[0].selection or "not_applicable",
        confidence_kind="unknown" if unresolved else "localization_only",
        provenance="parser_registry",
        producer=f"controlled-selection:{version}",
        model_confidence=None,
        unresolved=list(dict.fromkeys(unresolved)),
    )
    return observation, reliability


def _observation(selection: SelectedObservation, registry: SourceRegistry) -> ObservedValue:
    if selection.slot.value in {"target_grade", "comparable_grade"}:
        raise ValueError("This controlled observation reader supports numeric slots only")
    if selection.source not in selection.slot.evidence:
        raise ValueError("Observed source must be an inventory slot anchor")
    for ref in [*selection.slot.evidence, *selection.unit_evidence]:
        if _exact_source(registry, ref).role != "forms":
            raise ValueError("Observed values require original forms evidence")
    if selection.slot.derivable_blank:
        raise ValueError("Preparation cannot approve blank derivation")
    text = selection.source.excerpt
    if not text.strip():
        return ObservedValue(
            slot_id=selection.slot.id,
            state="blank",
            raw_text=text,
            evidence=[selection.source],
        )
    number = _decimal_cell(text)
    has_percent = any(
        "%" in t or "\uff05" in t for t in [text, *(r.excerpt for r in selection.unit_evidence)]
    )
    if selection.unit == "percent_points" and not has_percent:
        raise ValueError("Percentage points need a source-backed percent unit")
    if selection.unit == "ratio" and has_percent:
        raise ValueError("A percentage cannot silently become a ratio")
    return ObservedValue(
        slot_id=selection.slot.id,
        state="present",
        value=number,
        unit=selection.unit,
        raw_text=text,
        evidence=[selection.source],
    )


def _arithmetic_observations(material: ReviewMaterial) -> list[dict[str, object]]:
    """Compare original observed numbers only; never promote dependencies or approvals."""
    observed = {o.slot_id: o for o in material.facts.observed}
    results: list[dict[str, object]] = []
    for check in material.policy.inventory.checks:
        record: dict[str, object] = {
            "check_id": check.id,
            "kind": check.kind,
            "status": "needs_review",
            "diagnostic_only": True,
            "approval": "pending",
            "inputs": check.inputs,
            "target": check.target,
            "rule_evidence": [e.model_dump(mode="json") for e in check.evidence],
            "quantum": str(check.quantum),
            "tolerance": str(check.tolerance),
        }
        try:
            values = [percent(observed[name]) for name in check.inputs]
            actual = percent(observed[check.target])
            expected = calculate(check.kind, values, quantum=check.quantum)
            record.update(
                {
                    "input_values": [str(v) for v in values],
                    "observed": str(actual),
                    "computed": str(expected),
                    "agrees": abs(actual - expected) <= check.tolerance,
                    "trace": "Original observed candidates only; ROUND_HALF_UP; no trusted reuse",
                    "value_evidence": [
                        e.model_dump(mode="json")
                        for name in [*check.inputs, check.target]
                        for e in observed[name].evidence
                    ],
                }
            )
        except (KeyError, ValueError, ArithmeticError):
            record["trace"] = "Missing or non-numeric observed candidate; no result invented"
        results.append(record)
    return results


def prepare_case(
    manifest: InputManifest,
    registry: SourceRegistry,
    catalog: RuleCatalog,
    selections: CandidateSelections,
    *,
    source_roots: list[Path],
    max_bytes: int = 100_000_000,
    max_pages: int = 200,
) -> PreparedCase:
    """Verify original bytes and native registry, then assemble unapproved material."""
    # Revalidate instances, including callers that used mutable model_copy updates.
    manifest = InputManifest.model_validate(manifest.model_dump())
    registry = SourceRegistry.model_validate(registry.model_dump())
    catalog = RuleCatalog.model_validate(catalog.model_dump())
    selections = CandidateSelections.model_validate(selections.model_dump())
    bindings = {
        "manifest": content_digest(manifest),
        "registry": content_digest(registry),
        "catalog": content_digest(catalog),
        "selections": content_digest(selections),
    }
    if selections.registry_digest != content_digest(registry):
        raise ValueError("Selections are bound to another parsed registry")
    if selections.catalog_digest != content_digest(catalog):
        raise ValueError("Selections are bound to another rule catalog")
    sources = _verify_sources(
        manifest, registry, source_roots, max_bytes=max_bytes, max_pages=max_pages
    )
    _unique([c.context.key() for c in selections.contexts], "comparison context")
    _unique([f"{p.context.key()}/{p.factor_id}" for p in selections.pairs], "fact pair")
    _unique([s.slot.id for s in selections.observations], "observed slot")
    _unique([c.id for c in selections.checks], "arithmetic check")
    _unique([c.target for c in selections.checks], "arithmetic target")
    _unique([f"{r.document_id}/{r.factor_id}" for r in selections.native_rules], "native rule")
    contexts = {c.context.key(): c for c in selections.contexts}
    all_refs: list[SourceCitation] = []
    for condition in selections.conditions:
        all_refs.extend(condition.evidence)
    for context in selections.contexts:
        _unique(context.factor_ids, "context factor")
        all_refs.extend(context.evidence)
        if any(_exact_source(registry, r).role != "forms" for r in context.evidence):
            raise ValueError("Comparison context requires original forms evidence")
    for entry in catalog.entries:
        all_refs.extend(entry.evidence)
    for ref in all_refs:
        _exact_source(registry, ref)
    required_conditions = {
        "case_id",
        "district",
        "zone",
        "land_use_category",
        "effective_date",
        "target_id",
        "comparable_id",
    }
    condition_evidence: dict[str, object] = {
        "status": "needs_review",
        "conditions_confirmed": False,
        "configured_identity": manifest.identity.model_dump(mode="json"),
        "selection_version": selections.version,
        "candidates": [c.model_dump(mode="json") for c in selections.conditions],
        "missing_fields": sorted(required_conditions - {c.field for c in selections.conditions}),
        "unresolved": selections.unresolved,
        "confidence": 0,
        "confidence_kind": "unknown",
        "note": "Configured identity is a resolver query, not confirmation of applicability",
    }
    resolution = resolve_rule_sources(
        catalog,
        manifest.identity,
        [c.context for c in selections.contexts],
        registry,
        conditions_confirmed=False,
        condition_candidates=selections.conditions,
    )
    native = [
        candidate
        for doc in registry.documents
        if doc.role in {"criteria", "reference"}
        for candidate in native_candidates(doc)
    ]
    coverage: dict[str, object] = {
        "semantic_inventory_complete": False,
        "unresolved": selections.unresolved,
        "detected_native_candidates": len(native),
        "supported_native_candidates": sum(c.candidate is not None for c in native),
        "selected_native_candidates": 0,
    }
    if resolution.bundle is None:
        return PreparedCase(
            None, resolution, [], native, sources, condition_evidence, coverage, [], bindings
        )
    purposes = SourcePurposes.selected(registry, bundle=resolution.bundle)
    proposals: dict[tuple[str, int], PageProposal] = {}
    manual_pages: set[tuple[str, int]] = set()

    def page(ref: SourceCitation, *, manual: bool = False) -> PageProposal:
        _exact_source(registry, ref)
        key = ref.document_id, ref.page
        all_refs.append(ref)
        if manual:
            manual_pages.add(key)
        return proposals.setdefault(key, PageProposal())

    chosen = []
    for requested in selections.native_rules:
        matches = [
            c.candidate
            for c in native
            if c.candidate is not None
            and c.candidate.rule.factor_id == requested.factor_id
            and c.candidate.evidence[0].document_id == requested.document_id
        ]
        if len(matches) != 1:
            raise ValueError("Selected native rule is missing, unsupported, or ambiguous")
        candidate = matches[0]
        if content_digest(candidate) != requested.candidate_digest:
            raise ValueError("Selected native candidate content changed")
        if any(r not in candidate.evidence for r in requested.evidence):
            raise ValueError("Rule selection anchors differ from its native candidate")
        if not purposes.allows(candidate.evidence, "rule", scope=candidate.scope):
            raise ValueError("Native rule is outside selected source purpose or pages")
        for ref in candidate.evidence:
            _exact_source(registry, ref)
        all_refs.extend(candidate.evidence)
        page(candidate.evidence[0]).rules.append(candidate)
        chosen.append(candidate)
    for context in selections.contexts:
        available = {c.rule.factor_id for c in chosen if c.scope == context.context.scope}
        if not set(context.factor_ids) <= available:
            raise ValueError("Context contains an unselected or unsupported factor")
        page(context.evidence[0], manual=True).contexts.append(context)
    if any(
        not any(
            c.rule.factor_id in x.factor_ids and c.scope == x.context.scope
            for x in selections.contexts
        )
        for c in chosen
    ):
        raise ValueError("Selected rule has no configured comparison context")
    for pair in selections.pairs:
        pair_context = contexts.get(pair.context.key())
        if pair_context is None or pair.factor_id not in pair_context.factor_ids:
            raise ValueError("Fact pair is outside the controlled context inventory")
        matching_rules = [
            candidate.rule
            for candidate in chosen
            if candidate.scope == pair.context.scope and candidate.rule.factor_id == pair.factor_id
        ]
        if len(matching_rules) != 1:
            raise ValueError("Fact requires one selected rule to identify required units")
        target, target_reliability = _fact(
            pair.target, registry, selections.version, expected_unit=matching_rules[0].unit
        )
        comparable, comparable_reliability = _fact(
            pair.comparable, registry, selections.version, expected_unit=matching_rules[0].unit
        )
        all_refs.extend(
            [
                pair.target.source,
                pair.comparable.source,
                *pair.target.unit_evidence,
                *pair.comparable.unit_evidence,
            ]
        )
        page(pair.target.source, manual=True).pairs.append(
            EvidencedPair(
                context=pair.context,
                pair=FactorPair(factor_id=pair.factor_id, target=target, comparable=comparable),
                target_sources=[pair.target.source, *pair.target.unit_evidence],
                comparable_sources=[pair.comparable.source, *pair.comparable.unit_evidence],
                target_reliability=target_reliability,
                comparable_reliability=comparable_reliability,
            )
        )
    for selection in selections.observations:
        slot_context = contexts.get(selection.slot.context.key())
        factor_bound = selection.slot.value in {
            "target_grade",
            "comparable_grade",
            "adjustment_percent",
        }
        if slot_context is None or (
            selection.slot.factor_id not in slot_context.factor_ids
            if factor_bound
            else selection.slot.factor_id is not None
        ):
            raise ValueError("Observed slot has an invalid context or factor binding")
        observed = _observation(selection, registry)
        proposal = page(selection.source, manual=True)
        proposal.slots.append(selection.slot)
        proposal.observed.append(observed)
        all_refs.extend([*selection.slot.evidence, *selection.unit_evidence])
    slot_ids = {s.slot.id for s in selections.observations}
    try:
        tuple(
            TopologicalSorter({c.target: set(c.inputs) for c in selections.checks}).static_order()
        )
    except CycleError as error:
        raise ValueError("Arithmetic candidate dependencies contain a cycle") from error
    for check in selections.checks:
        if not set([*check.inputs, check.target]) <= slot_ids:
            raise ValueError("Arithmetic check references unknown observed slots")
        check_scopes = {
            selection.slot.context.scope
            for selection in selections.observations
            if selection.slot.id in [*check.inputs, check.target]
        }
        if not all(
            purposes.allows(check.evidence, "procedure", scope=scope) for scope in check_scopes
        ):
            raise ValueError("Arithmetic evidence is outside selected procedure source pages")
        for ref in check.evidence:
            _exact_source(registry, ref)
        all_refs.extend(check.evidence)
        page(check.evidence[0], manual=True).checks.append(check)
    first = next(iter(proposals.values()))
    first.unresolved.extend(
        [
            "Partial controlled preparation; full inventory and approval pending",
            *selections.unresolved,
            *resolution.reasons,
        ]
    )
    first.unsupported.extend(
        f"Native candidate unresolved: {c.evidence[0].document_id} page {c.evidence[0].page} "
        f"region {c.evidence[0].region_id}"
        for c in native
        if c.candidate is None and c.evidence
    )
    extractions = [
        PageExtraction(
            document_id=doc,
            page=number,
            content_hash=next(d.content_hash for d in registry.documents if d.document_id == doc),
            proposal=proposal,
            origin="manual" if (doc, number) in manual_pages else "native",
            model_id=None,
            region="local",
            input_tokens=0,
            output_tokens=0,
            elapsed_seconds=0,
            attempts=1,
        )
        for (doc, number), proposal in sorted(proposals.items())
    ]
    material = assemble_document_review(
        manifest.identity, registry, extractions, rule_bundle=resolution.bundle
    )
    if purposes.violations(material.policy, material.facts):
        raise ValueError("Assembled material violates original source purposes")
    coverage["selected_native_candidates"] = len(chosen)
    coverage["documents"] = [
        {
            "document_id": d.document_id,
            "parsed_pages": [p.number for p in d.pages],
            "candidate_anchor_pages": sorted(
                {r.page for r in all_refs if r.document_id == d.document_id}
            ),
            "pages_without_selected_anchors": [
                p.number
                for p in d.pages
                if not any(r.document_id == d.document_id and r.page == p.number for r in all_refs)
            ],
            "fully_reviewed_pages": [],
        }
        for d in registry.documents
    ]
    return PreparedCase(
        material,
        resolution,
        extractions,
        native,
        sources,
        condition_evidence,
        coverage,
        _arithmetic_observations(material),
        bindings,
    )


def read_local_json(path: Path, *, max_bytes: int = 64_000_000) -> bytes:
    if not path.is_file():
        raise ValueError("Configuration must be a local regular file")
    with path.open("rb") as stream:
        data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("Local JSON size limit exceeded")
    return data


def write_prepared_case(
    output: Path,
    *,
    workspace: Path,
    prepared: PreparedCase,
    manifest: InputManifest,
    registry: SourceRegistry,
    catalog: RuleCatalog,
    selections: CandidateSelections,
) -> dict[str, str]:
    """Exclusive private outputs, confined to this workspace's artifacts directory."""
    if prepared.input_digests != {
        "manifest": content_digest(manifest),
        "registry": content_digest(registry),
        "catalog": content_digest(catalog),
        "selections": content_digest(selections),
    }:
        raise ValueError("Preparation inputs changed before output; prepare the revision again")
    root = workspace.resolve(strict=True)
    artifacts = root / "artifacts"
    destination = output.absolute()
    if (
        destination != destination.resolve()
        or not destination.is_relative_to(artifacts)
        or destination == artifacts
        or artifacts.resolve() != artifacts
    ):
        raise ValueError("Output must be inside this workspace's artifacts without symlinks")
    if destination.exists():
        raise ValueError("Preparation output already exists; select a new private directory")
    payloads: dict[str, object] = {
        "preparation-manifest.json": {
            "schema_version": "local-case-preparation-v1",
            "status": "needs_review",
            "identity": manifest.identity.model_dump(mode="json"),
            "input_manifest_digest": content_digest(manifest),
            "registry_digest": content_digest(registry),
            "catalog_digest": content_digest(catalog),
            "selections_digest": content_digest(selections),
            "selection_version": selections.version,
            "material_digest": content_digest(prepared.material) if prepared.material else None,
            "bundle_id": prepared.resolution.bundle_id,
            "conditions_confirmed": False,
            "approval_issued": False,
            "ocr_executed": False,
            "model_executed": False,
            "source_verification": "Original bytes reparsed with current LocalPDFParser",
            "assembly": "appraisal_review.application.document_review.assemble",
            "arithmetic": "appraisal_review.domain.rule_engine.calculate",
            "scope": "Partial controlled candidates; observed arithmetic is diagnostic only",
        },
        "input-manifest.json": manifest,
        "registry.json": registry,
        "rule-catalog.json": catalog,
        "candidate-selections.json": selections,
        "rule-resolution.json": prepared.resolution,
        "condition-evidence.json": prepared.condition_evidence,
        "source-inventory.json": prepared.source_inventory,
        "coverage.json": prepared.coverage,
        "native-candidates.json": [c.model_dump(mode="json") for c in prepared.native],
        "page-extractions.json": [e.model_dump(mode="json") for e in prepared.extractions],
        "arithmetic-observations.json": prepared.arithmetic_observations,
        "template-gap.json": {
            "status": "needs_review",
            "output_pdf": None,
            "reason": "No admitted template and field map were supplied to this preparation",
            "required": [
                "Template source/version/hash and field-map review",
                "Case conditions and rule applicability confirmation",
                "Complete inventory and exact-material authority before writer use",
            ],
        },
    }
    if prepared.material is not None:
        payloads["material.json"] = prepared.material
        payloads["review.json"] = CaseReviewer(None).review(
            prepared.material.policy, prepared.material.facts, registry
        )
    serialized = {
        name: json.dumps(
            value.model_dump(mode="json") if isinstance(value, BaseModel) else value,
            ensure_ascii=False,
            indent=2,
        ).encode()
        + b"\n"
        for name, value in payloads.items()
    }
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in serialized.items()}
    serialized["hashes.json"] = json.dumps(hashes, indent=2).encode() + b"\n"
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination.mkdir(mode=0o700)
    for name, data in serialized.items():
        fd = os.open(destination / name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
    return hashes
