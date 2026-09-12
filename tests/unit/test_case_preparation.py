"""Self-authored synthetic regression fixtures; never real-case acceptance evidence."""

import hashlib
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import pymupdf
import pytest
from pydantic import ValidationError

from appraisal_review.adapters.local.case_preparation import (
    CandidateSelections,
    NativeRuleSelection,
    SelectedFact,
    SelectedObservation,
    SelectedPair,
    prepare_case,
    write_prepared_case,
)
from appraisal_review.adapters.local.document_manifest import InputManifest, InputSpec
from appraisal_review.adapters.local.native_candidates import native_candidates
from appraisal_review.adapters.local.pdf_parser import DocumentInput, LocalPDFParser
from appraisal_review.domain.case_review import CaseReviewer
from appraisal_review.domain.confidence import confirm_side, confirmation_digest
from appraisal_review.domain.document_models import SourceCitation, SourceRegistry
from appraisal_review.domain.factor_models import NormalizedValue
from appraisal_review.domain.review_contracts import (
    ArithmeticCheck,
    CaseIdentity,
    ComparisonContext,
    InventoryContext,
    ReviewSlot,
    content_digest,
)
from appraisal_review.domain.rule_sources import CatalogRuleSource, RuleCatalog


def _matrix(path):
    labels = ["優", "稍優", "普通", "稍劣", "劣"]
    descriptions = [
        "優:40m以上",
        "稍優:25m以上未滿40m",
        "普通:12m以上未滿25m",
        "稍劣:3m以上未滿12m",
        "劣:未滿3m",
    ]
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=800, height=420)
        page.insert_text((20, 40), "合成區域因素", fontname="china-t", fontsize=12)
        xs = [20, 120, 180, 240, 300, 360, 420, 480, 780]
        ys = [90 + row * 40 for row in range(7)]
        for x in xs:
            page.draw_line((x, ys[0]), (x, ys[-1]))
        for y in ys:
            page.draw_line((xs[0], y), (xs[-1], y))
        for col, label in enumerate(labels):
            page.insert_text((xs[col + 2] + 8, 115), label, fontname="china-t", fontsize=10)
        for row, label in enumerate(labels):
            y = 155 + row * 40
            page.insert_text((30, y), "合成道路", fontname="china-t", fontsize=10)
            page.insert_text((130, y), label, fontname="china-t", fontsize=10)
            for col in range(5):
                page.insert_text((xs[col + 2] + 8, y), str(col - row), fontsize=10)
            page.insert_text((490, y), descriptions[row], fontname="china-t", fontsize=10)
        pdf.save(path)


@pytest.fixture
def inputs(tmp_path):
    originals = tmp_path / "sources"
    originals.mkdir()
    for id in ["forms", "manual"]:
        with pymupdf.open() as pdf:
            page = pdf.new_page()
            texts = (
                ["Synthetic case", "12", "2", "m", "2%", "0%", "0%", "\u25cf \u25cb", "km"]
                if id == "forms"
                else ["Synthetic total copy procedure. Not approved."]
            )
            for i, text in enumerate(texts):
                page.insert_text(
                    (40, 50 + i * 35),
                    text,
                    fontname="china-t" if text == "\u25cf \u25cb" else "helv",
                )
            if id == "manual":
                pdf.new_page().insert_text((40, 50), "Synthetic second page, not reviewed.")
            pdf.save(originals / f"{id}.pdf")
    _matrix(originals / "criteria.pdf")
    identity = CaseIdentity(
        case_id="fixture",
        version="v1",
        district="Fixture District",
        zone="Fixture Zone",
        land_use_category="Fixture Use",
        effective_date=date(2025, 9, 1),
    )
    specs = [
        InputSpec(
            path=originals / f"{id}.pdf",
            document_id=id,
            version="v1",
            role=role,
            expected_hash=hashlib.sha256((originals / f"{id}.pdf").read_bytes()).hexdigest(),
        )
        for id, role in [("forms", "forms"), ("criteria", "criteria"), ("manual", "reference")]
    ]
    parser = LocalPDFParser([])
    registry = SourceRegistry(
        documents=[
            parser.parse_bytes(
                s.path.read_bytes(), DocumentInput(**s.model_dump()), uri=s.path.as_uri()
            ).source
            for s in specs
        ]
    )

    def cite(id, text, occurrence=0):
        doc = next(d for d in registry.documents if d.document_id == id)
        region = [r for r in doc.pages[0].regions if r.text.strip() == text][occurrence]
        return SourceCitation(
            document_id=id,
            content_hash=doc.content_hash,
            version=doc.version,
            page=1,
            region_id=region.id,
            bbox=region.bbox,
            excerpt=region.text,
        )

    procedure = cite("manual", "Synthetic total copy procedure. Not approved.")
    candidate = next(c.candidate for c in native_candidates(registry.documents[1]) if c.candidate)
    catalog = RuleCatalog(
        version="fixture-catalog-v1",
        entries=[
            CatalogRuleSource(
                entry_id=id,
                entry_version="v1",
                document_id=id,
                version="v1",
                content_hash=next(
                    d.content_hash for d in registry.documents if d.document_id == id
                ),
                pages=[1],
                role=role,
                use=use,
                district=identity.district,
                zone=identity.zone,
                land_use_category=identity.land_use_category,
                scopes=["regional"],
                effective_from=None,
                effective_to=None,
                evidence=[ref],
            )
            for id, role, use, ref in [
                ("manual", "general_rules", "procedure", procedure),
                ("criteria", "district_basis", "factor_rules", candidate.evidence[0]),
            ]
        ],
    )
    context = ComparisonContext(
        scope="regional", target_id="fixture-target", comparable_id="fixture-comparable"
    )
    factor = candidate.rule.factor_id
    selections = CandidateSelections(
        version="fixture-selection-v1",
        registry_digest=content_digest(registry),
        catalog_digest=content_digest(catalog),
        conditions=[],
        contexts=[
            InventoryContext(
                context=context, factor_ids=[factor], evidence=[cite("forms", "Synthetic case")]
            )
        ],
        native_rules=[
            NativeRuleSelection(
                document_id="criteria",
                factor_id=factor,
                candidate_digest=content_digest(candidate),
                evidence=[candidate.evidence[0]],
            )
        ],
        pairs=[
            SelectedPair(
                context=context,
                factor_id=factor,
                **{
                    side: SelectedFact(
                        value=NormalizedValue(type="number", value=value, unit="m"),
                        source=cite("forms", str(value)),
                        unit_evidence=[cite("forms", "m")],
                        method="native_proposed",
                        interpretation="Synthetic raw selection",
                    )
                    for side, value in [("target", 12), ("comparable", 2)]
                },
            )
        ],
        observations=[
            SelectedObservation(
                slot=ReviewSlot(
                    id=id,
                    context=context,
                    value=kind,
                    factor_id=factor if id == "adjustment" else None,
                    evidence=[ref],
                ),
                source=ref,
                unit="percent_points",
            )
            for id, kind, ref in [
                ("adjustment", "adjustment_percent", cite("forms", "2%")),
                ("source-total", "total", cite("forms", "0%")),
                ("copied-total", "subtotal", cite("forms", "0%", 1)),
            ]
        ],
        checks=[
            ArithmeticCheck(
                id="manual-copy",
                kind="equals",
                inputs=["source-total"],
                target="copied-total",
                evidence=[procedure],
            )
        ],
        unresolved=["Synthetic fixtures have no source or material approval"],
    )
    return {
        "manifest": InputManifest(identity=identity, documents=specs),
        "registry": registry,
        "catalog": catalog,
        "selections": selections,
        "source_roots": [originals],
    }


def test_candidate_assembly_keeps_independent_sources_and_zero_confidence(inputs):
    hashes = {s.path: s.expected_hash for s in inputs["manifest"].documents}
    prepared = prepare_case(**inputs)
    material = prepared.material
    assert material is not None
    assert {s.role for s in prepared.resolution.bundle.sources} == {
        "general_rules",
        "district_basis",
    }
    assert prepared.resolution.status == "needs_review"
    assert not prepared.resolution.bundle.conditions_confirmed
    assert all(s.rules.status == "candidate" for s in material.policy.rule_sets)
    assert material.policy.inventory.checks[0].evidence[0].document_id == "manual"
    assert material.policy.rule_sets[0].evidence[0].document_id == "criteria"
    for side in ["target", "comparable"]:
        observation = getattr(material.facts.pairs[0].pair, side)
        reliability = getattr(material.facts.pairs[0], side + "_reliability")
        assert observation.confidence == observation.evidence[0].confidence == 0
        selected = getattr(inputs["selections"].pairs[0], side)
        assert observation.raw_text == "\n".join(
            ref.excerpt for ref in [selected.source, *selected.unit_evidence]
        )
        assert len(observation.evidence) == 2
        assert all(e.confidence == 0 for e in observation.evidence)
        assert reliability.method == "native_proposed"
        assert reliability.confidence_kind == "localization_only"
        assert reliability.provenance == "parser_registry"
        assert reliability.producer == "controlled-selection:fixture-selection-v1"
        assert reliability.confirmation is None and reliability.model_confidence is None
        assert reliability.unresolved == []
    assert all(
        e.origin in {"native", "manual"} and e.model_id is None for e in prepared.extractions
    )
    assert prepared.arithmetic_observations[0]["agrees"] is True
    assert prepared.arithmetic_observations[0]["computed"] == "0.00"
    assert prepared.arithmetic_observations[0]["status"] == "needs_review"
    assert (
        CaseReviewer(None).review(material.policy, material.facts, inputs["registry"]).status
        == "needs_review"
    )
    assert "district" in prepared.condition_evidence["missing_fields"]
    assert prepared.coverage["semantic_inventory_complete"] is False
    assert {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in hashes} == hashes


def test_manual_proposal_is_not_a_human_confirmation(inputs):
    inputs["selections"].pairs[0].target.method = "manual_proposed"
    result = prepare_case(**inputs).material.facts.pairs[0]
    assert result.target_reliability.method == "manual_proposed"
    assert result.target_reliability.confirmation is None
    assert result.target_reliability.confidence_kind == "localization_only"
    assert result.target_reliability.model_confidence is None
    assert result.pair.target.confidence == 0


@pytest.mark.parametrize("method", ["native_proposed", "manual_proposed"])
def test_explicit_review_confirmation_preserves_raw_zero_and_never_approves(inputs, method):
    inputs["selections"].pairs[0].target.method = method
    material = prepare_case(**inputs).material
    pair = material.facts.pairs[0]
    raw_before = pair.pair.model_dump()
    sources_before = [ref.model_dump() for ref in pair.target_sources]
    assert pair.target_reliability.method == method
    expected_binding = confirmation_digest(pair, "target")
    # Explicit post-review operation on synthetic regression data only.
    confirm_side(pair, "target", reviewer="synthetic-regression-reviewer")
    reliability = pair.target_reliability
    assert reliability.method == "reviewer_confirmed"
    assert reliability.confirmation.input_digest == expected_binding
    assert reliability.confidence_kind == "localization_only"
    assert reliability.provenance == "parser_registry" and reliability.model_confidence is None
    assert pair.pair.model_dump() == raw_before
    assert [ref.model_dump() for ref in pair.target_sources] == sources_before
    assert pair.pair.target.confidence == 0
    assert all(ref.confidence == 0 for ref in pair.pair.target.evidence)
    assert pair.comparable_reliability.method == "native_proposed"
    assert all(s.rules.status == "candidate" for s in material.policy.rule_sets)
    assert material.policy.rule_bundle.conditions_confirmed is False
    reviewed = CaseReviewer(None).review(material.policy, material.facts, inputs["registry"])
    assert reviewed.status == "needs_review"
    assert next(f for f in reviewed.findings if f.id == "trust").status == "needs_review"


def _forms_anchor(inputs, text):
    document = next(d for d in inputs["registry"].documents if d.role == "forms")
    region = next(r for r in document.pages[0].regions if r.text.strip() == text)
    return SourceCitation(
        document_id=document.document_id,
        version=document.version,
        content_hash=document.content_hash,
        page=1,
        region_id=region.id,
        bbox=region.bbox,
        excerpt=region.text,
    )


@pytest.mark.parametrize("method", ["native_proposed", "manual_proposed"])
def test_actual_ambiguous_native_marks_block_confirmation(inputs, method):
    selected = inputs["selections"].pairs[0].target
    selected.source = _forms_anchor(inputs, "\u25cf \u25cb")
    selected.value = NormalizedValue(type="category", value=selected.source.excerpt.strip())
    selected.unit_evidence = []
    selected.method = method
    pair = prepare_case(**inputs).material.facts.pairs[0]
    assert pair.target_reliability.confidence_kind == "unknown"
    assert pair.target_reliability.selection == "ambiguous"
    assert "Source selection marks are ambiguous" in pair.target_reliability.unresolved
    assert pair.pair.target.confidence == 0
    with pytest.raises(ValueError, match="ambiguity"):
        confirm_side(pair, "target", reviewer="synthetic-regression-reviewer")
    assert pair.target_reliability.method == method and pair.target_reliability.confirmation is None


@pytest.mark.parametrize(
    "issue", ["missing_value", "missing_unit", "declared_ambiguity", "conflicting_units"]
)
def test_actual_fact_issues_remain_unresolved_and_unconfirmable(inputs, issue):
    selected = inputs["selections"].pairs[0].target
    if issue == "missing_value":
        selected.value = None
        expected = "Proposed fact value is missing"
    elif issue == "missing_unit":
        selected.value.unit = None
        selected.unit_evidence = []
        expected = "Required fact unit is missing"
    elif issue == "declared_ambiguity":
        expected = "Two source readings conflict"
        selected.unresolved = [expected]
    else:
        selected.unit_evidence.append(_forms_anchor(inputs, "km"))
        expected = "Source unit anchors conflict"
    pair = prepare_case(**inputs).material.facts.pairs[0]
    assert pair.target_reliability.unresolved == [expected]
    assert pair.target_reliability.confidence_kind == "unknown"
    assert pair.target_reliability.model_confidence is None
    assert pair.pair.target.confidence == 0
    with pytest.raises(ValueError, match="Resolve missing facts"):
        confirm_side(pair, "target", reviewer="synthetic-regression-reviewer")
    assert pair.target_reliability.confirmation is None


@pytest.mark.parametrize("field", ["confidence_kind", "provenance"])
def test_confirmation_still_rejects_unknown_provenance_or_confidence_kind(inputs, field):
    pair = prepare_case(**inputs).material.facts.pairs[0]
    setattr(pair.target_reliability, field, "unknown")
    with pytest.raises(ValueError, match="provenance"):
        confirm_side(pair, "target", reviewer="synthetic-regression-reviewer")
    assert pair.target_reliability.confirmation is None


@pytest.mark.parametrize(
    "field,value", [("confidence", 1), ("confirmation", {}), ("method", "reviewer_confirmed")]
)
def test_configuration_cannot_forge_measured_confidence_or_confirmation(inputs, field, value):
    data = inputs["selections"].pairs[0].target.model_dump()
    data[field] = value
    with pytest.raises(ValidationError):
        SelectedFact.model_validate(data)


def test_configuration_cannot_confirm_conditions(inputs):
    data = inputs["selections"].model_dump()
    data["conditions_confirmed"] = True
    with pytest.raises(ValidationError):
        CandidateSelections.model_validate(data)


def test_registry_tamper_rejected_even_when_selection_digest_is_updated(inputs):
    inputs["registry"].documents[0].pages[0].regions[0].text = "Forged text"
    inputs["selections"].registry_digest = content_digest(inputs["registry"])
    with pytest.raises(ValueError, match="current native parser"):
        prepare_case(**inputs)


@pytest.mark.parametrize("change", ["hash", "root", "link", "size", "pages"])
def test_original_admission_checks(inputs, tmp_path, change):
    if change == "hash":
        inputs["manifest"].documents[0].path.write_bytes(b"%PDF-changed")
    elif change == "root":
        empty = tmp_path / "unauthorized"
        empty.mkdir()
        inputs["source_roots"] = [empty]
    elif change == "link":
        path = inputs["manifest"].documents[0].path
        new = path.with_name("linked.pdf")
        path.rename(new)
        path.symlink_to(new)
    elif change == "size":
        inputs["max_bytes"] = 10
    else:
        inputs["max_pages"] = 1
    with pytest.raises(ValueError):
        prepare_case(**inputs)


@pytest.mark.parametrize("field", ["registry_digest", "catalog_digest"])
def test_configuration_digest_pins(inputs, field):
    setattr(inputs["selections"], field, "0" * 64)
    with pytest.raises(ValueError, match="bound to another"):
        prepare_case(**inputs)


@pytest.mark.parametrize("change", ["missing", "ambiguous", "expired"])
def test_rule_resolution_has_no_default_or_approval_fallback(inputs, change):
    catalog = inputs["catalog"]
    if change == "missing":
        catalog.entries = catalog.entries[:1]
    elif change == "ambiguous":
        entry = catalog.entries[-1].model_copy(deep=True)
        entry.entry_id = "other-entry"
        catalog.entries.append(entry)
    else:
        catalog.entries[-1].effective_to = date(2024, 1, 1)
    inputs["selections"].catalog_digest = content_digest(catalog)
    result = prepare_case(**inputs)
    assert result.material is None and result.resolution.bundle is None
    assert result.resolution.status == ("ambiguous" if change == "ambiguous" else "missing")


@pytest.mark.parametrize(
    "change",
    [
        "candidate_digest",
        "unknown_factor",
        "partial_excerpt",
        "native_number",
        "unit",
        "duplicate_slot",
        "wrong_fact_role",
        "unknown_dependency",
        "cycle",
        "blank_derivation",
    ],
)
def test_controlled_selections_reject_unbound_values_and_dependencies(inputs, change):
    selections = inputs["selections"]
    if change == "candidate_digest":
        selections.native_rules[0].candidate_digest = "0" * 64
    elif change == "unknown_factor":
        selections.native_rules[0].factor_id = "unsupported"
    elif change == "partial_excerpt":
        selections.contexts[0].evidence[0].excerpt = "Synthetic"
    elif change == "native_number":
        selections.pairs[0].target.value.value = 999.0
    elif change == "unit":
        selections.pairs[0].target.unit_evidence = []
    elif change == "duplicate_slot":
        selections.observations.append(selections.observations[0])
    elif change == "wrong_fact_role":
        selections.pairs[0].target.source = selections.checks[0].evidence[0]
    elif change == "unknown_dependency":
        selections.checks[0].inputs = ["absent-slot"]
    elif change == "cycle":
        selections.checks.append(
            ArithmeticCheck(
                id="backwards",
                kind="equals",
                inputs=["copied-total"],
                target="source-total",
                evidence=selections.checks[0].evidence,
            )
        )
    else:
        selections.observations[0].slot.derivable_blank = True
    with pytest.raises(ValueError):
        prepare_case(**inputs)


def test_candidate_disagreement_is_preserved_without_case_acceptance(inputs):
    inputs["selections"].checks[0].inputs = ["adjustment"]
    result = prepare_case(**inputs)
    diagnostic = result.arithmetic_observations[0]
    assert diagnostic["computed"] == "2.00" and diagnostic["observed"] == "0"
    assert diagnostic["agrees"] is False and diagnostic["status"] == "needs_review"


def test_private_output_is_exclusive_confined_and_hashed(inputs, tmp_path):
    result = prepare_case(**inputs)
    output = tmp_path / "artifacts" / "prepared"
    kwargs = {k: inputs[k] for k in ["manifest", "registry", "catalog", "selections"]}
    hashes = write_prepared_case(output, workspace=tmp_path, prepared=result, **kwargs)
    assert output.stat().st_mode & 0o777 == 0o700
    assert json.loads((output / "hashes.json").read_text()) == hashes
    for path in output.iterdir():
        assert path.stat().st_mode & 0o777 == 0o600
        if path.name != "hashes.json":
            assert hashlib.sha256(path.read_bytes()).hexdigest() == hashes[path.name]
    assert json.loads((output / "template-gap.json").read_text())["output_pdf"] is None
    assert json.loads((output / "review.json").read_text())["status"] == "needs_review"
    binding = json.loads((output / "preparation-manifest.json").read_text())
    assert binding["material_digest"] == content_digest(result.material)
    assert binding["selections_digest"] == content_digest(inputs["selections"])
    assert binding["bundle_id"] == result.resolution.bundle_id
    assert binding["approval_issued"] is False and binding["model_executed"] is False
    with pytest.raises(ValueError, match="already exists"):
        write_prepared_case(output, workspace=tmp_path, prepared=result, **kwargs)
    with pytest.raises(ValueError, match="inside this workspace"):
        write_prepared_case(tmp_path / "outside", workspace=tmp_path, prepared=result, **kwargs)
    link = tmp_path / "artifacts" / "link"
    link.symlink_to(inputs["source_roots"][0], target_is_directory=True)
    with pytest.raises(ValueError, match="without symlinks"):
        write_prepared_case(link / "output", workspace=tmp_path, prepared=result, **kwargs)


def test_output_rejects_changed_inputs_without_repreparation(inputs, tmp_path):
    result = prepare_case(**inputs)
    inputs["selections"].version = "changed-after-preparation"
    kwargs = {k: inputs[k] for k in ["manifest", "registry", "catalog", "selections"]}
    with pytest.raises(ValueError, match="inputs changed"):
        write_prepared_case(
            tmp_path / "artifacts" / "changed", workspace=tmp_path, prepared=result, **kwargs
        )


def test_cli_requires_all_configuration_and_runs_current_code(inputs, tmp_path):
    project = Path(__file__).resolve().parents[2]
    command = [sys.executable, str(project / "scripts" / "prepare_local_case.py")]
    env = {**os.environ, "PYTHONPATH": str(project / "src")}
    missing = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, timeout=20)
    assert missing.returncode == 2 and b"required" in missing.stderr
    for flag in ["manifest", "registry", "catalog", "selections"]:
        path = tmp_path / f"{flag}.json"
        path.write_text(inputs[flag].model_dump_json())
        command += [f"--{flag}", str(path)]
    output = tmp_path / "artifacts" / "cli-prepared"
    command += ["--source-root", str(inputs["source_roots"][0]), "--output", str(output)]
    result = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr.decode()
    assert b"needs_review" in result.stdout
    assert (
        json.loads((output / "preparation-manifest.json").read_text())["approval_issued"] is False
    )
