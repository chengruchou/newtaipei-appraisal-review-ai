"""Direct non-sensitive source wiring tests. Synthetic PDFs only; no organizer files."""

import asyncio
import hashlib
from pathlib import Path

import pytest

from appraisal_review.adapters.local.case_sources import (
    DirectSourceError,
    document_inputs,
    parse_case_sources,
)
from appraisal_review.domain.source_manifest import CaseSourceManifest
from appraisal_review.domain.source_purpose import SourcePurposes

DIRECT = {
    "handling": "direct_non_sensitive",
    "handling_reference": "Synthetic reviewer decision",
}


def make_pdf(path: Path, pages: int, label: str) -> Path:
    pymupdf = pytest.importorskip("pymupdf")
    with pymupdf.open() as document:
        for index in range(pages):
            page = document.new_page(width=595, height=842)
            page.insert_text((72, 96), f"{label} page {index + 1}", fontsize=14)
        document.save(str(path))
    return path


def payload(brief: Path, basis: Path, *, direct=True, **changes):
    sources = [
        {
            "source_id": "brief-case",
            "title": "Synthetic brief",
            "medium": "pdf",
            "usage": "case_source",
            "registry_role": "forms",
            "purposes": ["case"],
            "content_sha256": hashlib.sha256(brief.read_bytes()).hexdigest(),
        },
        {
            "source_id": "evaluation-basis",
            "title": "Synthetic basis",
            "medium": "pdf",
            "usage": "rule_source",
            "registry_role": "criteria",
            "purposes": ["rule"],
            "content_sha256": hashlib.sha256(basis.read_bytes()).hexdigest(),
        },
        {
            "source_id": "form-table-3",
            "title": "Synthetic workbook",
            "medium": "workbook",
            "usage": "output_template",
            "content_sha256": "d" * 64,
        },
    ]
    if direct:
        for source in sources:
            source.update(DIRECT)
    return {
        "manifest_id": "synthetic-case",
        "case": {
            "case_label": "Synthetic case",
            "district": "Synthetic district",
            "land_use_description": "Synthetic use",
            "effective_date": "2022-09-01",
            "effective_date_as_written": "1110901",
            "subjects": [
                {"label": "P001", "role": "subject"},
                {"label": "P002", "role": "comparable"},
            ],
        },
        "sources": sources,
    } | changes


def manifest(brief, basis, **changes):
    return CaseSourceManifest.model_validate(payload(brief, basis, **changes))


def sources(tmp_path):
    brief = make_pdf(tmp_path / "brief.pdf", 2, "BRIEF")
    basis = make_pdf(tmp_path / "basis.pdf", 3, "BASIS")
    return brief, basis


def bindings(brief, basis):
    return {"brief-case": brief, "evaluation-basis": basis}


def test_only_registry_sources_are_wired_and_each_is_pinned(tmp_path):
    brief, basis = sources(tmp_path)
    inputs = document_inputs(manifest(brief, basis), bindings(brief, basis))
    assert [entry.document_id for entry in inputs] == ["brief-case", "evaluation-basis"]
    assert [entry.role for entry in inputs] == ["forms", "criteria"]
    assert all(entry.expected_hash for entry in inputs)
    digest = hashlib.sha256(brief.read_bytes()).hexdigest()
    assert inputs[0].version == f"sha256:{digest[:16]}"


def test_a_declared_version_overrides_the_digest_version(tmp_path):
    brief, basis = sources(tmp_path)
    data = payload(brief, basis)
    data["sources"][0]["declared_version"] = "2022-09 edition"
    inputs = document_inputs(CaseSourceManifest.model_validate(data), bindings(brief, basis))
    assert inputs[0].version == "2022-09 edition"


def test_parsing_produces_a_registry_that_selects_one_document_per_role(tmp_path):
    brief, basis = sources(tmp_path)
    registry, parsed = asyncio.run(
        parse_case_sources(manifest(brief, basis), bindings(brief, basis))
    )
    purposes = SourcePurposes.selected(registry)
    assert purposes.forms.document_id == "brief-case"
    assert purposes.criteria.document_id == "evaluation-basis"
    assert [document.page_count for document in parsed] == [2, 3]
    assert all(page.has_text for document in registry.documents for page in document.pages)
    assert registry.documents[0].content_hash == hashlib.sha256(brief.read_bytes()).hexdigest()


def test_a_citation_from_the_parsed_registry_resolves(tmp_path):
    from appraisal_review.domain.document_models import SourceCitation

    brief, basis = sources(tmp_path)
    registry, _parsed = asyncio.run(
        parse_case_sources(manifest(brief, basis), bindings(brief, basis))
    )
    document = registry.documents[0]
    region = next(r for r in document.pages[0].regions if r.kind == "text" and r.text.strip())
    citation = SourceCitation(
        document_id=document.document_id,
        content_hash=document.content_hash,
        version=document.version,
        page=1,
        region_id=region.id,
        bbox=region.bbox,
        excerpt=region.text.strip()[:5],
    )
    assert registry.resolves(citation)
    assert not registry.resolves(citation.model_copy(update={"excerpt": "ABSENT-TEXT"}))


def test_a_source_that_still_needs_the_privacy_route_is_refused(tmp_path):
    brief, basis = sources(tmp_path)
    with pytest.raises(DirectSourceError, match="privacy_route"):
        document_inputs(manifest(brief, basis, direct=False), bindings(brief, basis))


def test_an_unpinned_source_is_refused(tmp_path):
    brief, basis = sources(tmp_path)
    data = payload(brief, basis)
    data["sources"][0]["content_sha256"] = None
    with pytest.raises(DirectSourceError, match="not_pinned"):
        document_inputs(CaseSourceManifest.model_validate(data), bindings(brief, basis))


def test_a_registry_source_without_a_supplied_original_is_refused(tmp_path):
    brief, basis = sources(tmp_path)
    with pytest.raises(DirectSourceError, match="not_supplied"):
        document_inputs(manifest(brief, basis), {"brief-case": brief})


def test_a_binding_for_an_undeclared_source_is_refused(tmp_path):
    brief, basis = sources(tmp_path)
    supplied = bindings(brief, basis) | {"other": brief}
    with pytest.raises(DirectSourceError, match="undeclared"):
        document_inputs(manifest(brief, basis), supplied)


def test_a_missing_file_is_refused_before_parsing(tmp_path):
    brief, basis = sources(tmp_path)
    supplied = {"brief-case": tmp_path / "absent.pdf", "evaluation-basis": basis}
    with pytest.raises(DirectSourceError, match="not_a_file"):
        document_inputs(manifest(brief, basis), supplied)


def test_a_changed_original_fails_instead_of_being_parsed(tmp_path):
    brief, basis = sources(tmp_path)
    case = manifest(brief, basis)
    make_pdf(brief, 2, "REPLACED")
    with pytest.raises(DirectSourceError, match="could_not_be_parsed"):
        asyncio.run(parse_case_sources(case, bindings(brief, basis)))


def test_a_template_is_never_wired_even_when_bound(tmp_path):
    brief, basis = sources(tmp_path)
    workbook = tmp_path / "table3.xlsx"
    workbook.write_bytes(b"PK\x03\x04synthetic")
    supplied = bindings(brief, basis) | {"form-table-3": workbook}
    inputs = document_inputs(manifest(brief, basis), supplied)
    assert "form-table-3" not in {entry.document_id for entry in inputs}


def test_two_documents_in_the_same_role_cannot_be_wired(tmp_path):
    brief, basis = sources(tmp_path)
    data = payload(brief, basis)
    # Bypass the manifest validator to prove the wiring re-checks role selection.
    case = CaseSourceManifest.model_validate(data)
    swapped = case.sources[1].model_copy(update={"registry_role": "forms", "usage": "case_source"})
    broken = case.model_copy(update={"sources": (case.sources[0], swapped, case.sources[2])})
    with pytest.raises(DirectSourceError, match="one_document_per_role"):
        asyncio.run(parse_case_sources(broken, bindings(brief, basis)))
