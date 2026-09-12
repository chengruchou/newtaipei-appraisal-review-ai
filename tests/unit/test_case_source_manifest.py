"""Case source manifest and checker tests. Synthetic files only; no organizer material."""

import argparse
import hashlib
import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from appraisal_review.domain.source_manifest import CaseSourceManifest, compare

ROOT = Path(__file__).resolve().parents[2]
SHIPPED = ROOT / "configs/sources/newtaipei-shulin-1110901.json"
SHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def load():
    spec = importlib.util.spec_from_file_location(
        "case_source_cli", ROOT / "scripts/check_case_sources.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CLI = load()


DIRECT = {
    "handling": "direct_non_sensitive",
    "handling_reference": "Synthetic reviewer decision",
}


def manifest_payload(**changes):
    return {
        "manifest_id": "synthetic-case",
        "case": {
            "case_label": "Synthetic case",
            "district": "Synthetic district",
            "land_use_description": "Synthetic use",
            "effective_date": "2022-09-01",
            "effective_date_as_written": "1110901",
            "subjects": [
                {"label": "P001", "role": "subject", "brief_page": 4},
                {"label": "P002", "role": "comparable", "brief_page": 1},
            ],
        },
        "sources": [
            {
                "source_id": "brief-case",
                "title": "Synthetic brief",
                "medium": "pdf",
                "usage": "case_source",
                "registry_role": "forms",
                "purposes": ["case"],
                "page_count": 2,
            },
            {
                "source_id": "evaluation-basis",
                "title": "Synthetic basis",
                "medium": "pdf",
                "usage": "rule_source",
                "registry_role": "criteria",
                "purposes": ["rule"],
                "page_count": 3,
            },
            {
                "source_id": "form-table-3",
                "title": "Synthetic workbook",
                "medium": "workbook",
                "usage": "output_template",
                "sheet_count": 3,
                "hidden_sheet_count": 2,
            },
        ],
    } | changes


def make_pdf(path: Path, pages: int) -> Path:
    pymupdf = pytest.importorskip("pymupdf")
    with pymupdf.open() as document:
        for _ in range(pages):
            document.new_page(width=595, height=842)
        document.save(str(path))
    return path


def make_workbook(path: Path, sheets: int, hidden: int) -> Path:
    entries = "".join(
        f'<sheet name="S{index}" sheetId="{index}" r:id="rId{index}"'
        + (' state="hidden"' if index <= hidden else "")
        + "/>"
        for index in range(1, sheets + 1)
    )
    document = (
        f'<?xml version="1.0"?><workbook xmlns="{SHEET_NS}" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<sheets>{entries}</sheets></workbook>"
    )
    with zipfile.ZipFile(path, "w") as package:
        package.writestr("xl/workbook.xml", document)
    return path


def write_manifest(tmp_path: Path, payload) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def run(tmp_path, payload, files=()):
    manifest = write_manifest(tmp_path, payload)
    argv = ["--manifest", str(manifest)]
    for source_id, path in files:
        argv += ["--file", f"{source_id}={path}"]
    return CLI.main(argv)


def report(capsys):
    return json.loads(capsys.readouterr().out)


def states(payload):
    return {entry["source_id"]: entry["state"] for entry in payload["sources"]}


def test_shipped_shulin_manifest_is_valid_and_declares_the_expected_roles():
    manifest = CaseSourceManifest.model_validate_json(SHIPPED.read_bytes())
    assert manifest.case.effective_date == "2022-09-01"
    assert manifest.case.role_of("P001") == "subject"
    assert all(manifest.case.role_of(label) == "comparable" for label in ("P002", "P003", "P004"))
    assert manifest.source("evaluation-basis").registry_role == "criteria"
    assert manifest.source("brief-case").registry_role == "forms"
    assert [s.source_id for s in manifest.sources if s.usage == "output_template"] == [
        "form-table-3",
        "form-table-5",
        "form-table-4",
    ]


def test_shipped_manifest_is_pinned_and_keeps_its_blockers_visible():
    manifest = CaseSourceManifest.model_validate_json(SHIPPED.read_bytes())
    assert manifest.unpinned() == ()
    blocking = {q.question_id: q.blocks for q in manifest.blocking_questions()}
    assert blocking["p001-zoning-conflict"] == "rule_selection"
    assert blocking["missing-individual-factors"] == "calculation"
    assert blocking["factor-weights"] == "calculation"
    assert blocking["floor-area-ratio-basis"] == "calculation"
    assert blocking["matrix-axis-orientation"] == "calculation"
    # Pinning bytes resolves none of the questions the reviewer still owns.
    assert len(manifest.blocking_questions()) >= 5


def test_shipped_batch_is_recorded_as_direct_with_a_named_decision():
    manifest = CaseSourceManifest.model_validate_json(SHIPPED.read_bytes())
    assert all(source.handling == "direct_non_sensitive" for source in manifest.sources)
    assert all(source.handling_reference for source in manifest.sources)
    # Only registry sources can be wired; the three templates stay out.
    assert {s.source_id for s in manifest.direct_sources()} == {
        "brief-case",
        "evaluation-basis",
    }


def test_shipped_manifest_versions_each_source_by_its_pinned_digest():
    manifest = CaseSourceManifest.model_validate_json(SHIPPED.read_bytes())
    for source in manifest.sources:
        assert source.declared_version is None
        assert source.content_sha256 is not None
        assert source.document_version() == f"sha256:{source.content_sha256[:16]}"


def test_shipped_workbooks_declare_their_single_visible_worksheet():
    manifest = CaseSourceManifest.model_validate_json(SHIPPED.read_bytes())
    templates = [s for s in manifest.sources if s.usage == "output_template"]
    assert len(templates) == 3
    assert all(s.sheet_count == 24 and s.hidden_sheet_count == 23 for s in templates)
    assert all(s.visible_worksheet for s in templates)
    assert len({s.visible_worksheet for s in templates}) == 3


def test_a_brief_page_order_never_decides_the_subject_role():
    manifest = CaseSourceManifest.model_validate_json(SHIPPED.read_bytes())
    ordered = sorted(manifest.case.subjects, key=lambda s: s.brief_page or 0)
    assert [s.label for s in ordered] == ["P002", "P003", "P004", "P001"]
    assert ordered[-1].role == "subject"


@pytest.mark.parametrize(
    "changes",
    [
        {"subjects": [{"label": "P001", "role": "comparable"}]},
        {
            "subjects": [
                {"label": "P001", "role": "subject"},
                {"label": "P001", "role": "comparable"},
            ]
        },
        {
            "subjects": [
                {"label": "P001", "role": "subject"},
                {"label": "P002", "role": "subject"},
                {"label": "P003", "role": "comparable"},
            ]
        },
    ],
)
def test_a_case_needs_exactly_one_subject_and_unique_labels(changes):
    payload = manifest_payload()
    payload["case"] = payload["case"] | changes
    with pytest.raises(ValidationError):
        CaseSourceManifest.model_validate(payload)


def test_a_template_is_never_declared_as_a_registry_source():
    payload = manifest_payload()
    payload["sources"][2]["registry_role"] = "forms"
    with pytest.raises(ValidationError):
        CaseSourceManifest.model_validate(payload)
    payload = manifest_payload()
    payload["sources"][0]["registry_role"] = None
    with pytest.raises(ValidationError):
        CaseSourceManifest.model_validate(payload)


def test_two_criteria_or_two_forms_sources_are_refused():
    for role, index in (("criteria", 0), ("forms", 1)):
        payload = manifest_payload()
        payload["sources"][index]["registry_role"] = role
        payload["sources"][index]["usage"] = "rule_source" if role == "criteria" else "case_source"
        with pytest.raises(ValidationError):
            CaseSourceManifest.model_validate(payload)


def test_rule_evidence_requires_the_criteria_role():
    payload = manifest_payload()
    payload["sources"][1]["registry_role"] = "reference"
    with pytest.raises(ValidationError):
        CaseSourceManifest.model_validate(payload)


def test_structure_fields_must_match_the_medium():
    payload = manifest_payload()
    payload["sources"][0]["sheet_count"] = 24
    with pytest.raises(ValidationError):
        CaseSourceManifest.model_validate(payload)
    payload = manifest_payload()
    payload["sources"][2]["page_count"] = 6
    with pytest.raises(ValidationError):
        CaseSourceManifest.model_validate(payload)


def test_a_workbook_needs_at_least_one_visible_worksheet():
    payload = manifest_payload()
    payload["sources"][2]["hidden_sheet_count"] = 3
    with pytest.raises(ValidationError):
        CaseSourceManifest.model_validate(payload)


def test_an_observed_digest_is_never_treated_as_a_reviewer_pin():
    manifest = CaseSourceManifest.model_validate(manifest_payload())
    expected = manifest.source("brief-case")
    state, differences = compare(
        expected, content_sha256="a" * 64, byte_size=10, structure={"page_count": 2}
    )
    assert (state, differences) == ("unpinned", ())
    pinned = expected.model_copy(update={"content_sha256": "b" * 64})
    assert compare(pinned, content_sha256="b" * 64, byte_size=10, structure={"page_count": 2}) == (
        "satisfied",
        (),
    )
    assert compare(pinned, content_sha256="a" * 64, byte_size=10, structure={"page_count": 2}) == (
        "changed",
        ("content_sha256",),
    )


def test_supplied_originals_are_measured_and_reported(tmp_path, capsys):
    brief = make_pdf(tmp_path / "brief.pdf", 2)
    basis = make_pdf(tmp_path / "basis.pdf", 3)
    workbook = make_workbook(tmp_path / "table3.xlsx", 3, 2)
    code = run(
        tmp_path,
        manifest_payload(),
        (("brief-case", brief), ("evaluation-basis", basis), ("form-table-3", workbook)),
    )
    payload = report(capsys)
    assert code == 1 and payload["status"] == "blocked"
    assert states(payload) == {
        "brief-case": "unpinned",
        "evaluation-basis": "unpinned",
        "form-table-3": "unpinned",
    }
    entry = next(s for s in payload["sources"] if s["source_id"] == "form-table-3")
    assert entry["structure"] == {
        "sheet_count": 3,
        "hidden_sheet_count": 2,
        "visible_worksheet": "S3",
    }
    assert entry["content_sha256"] == hashlib.sha256(workbook.read_bytes()).hexdigest()
    assert payload["ready_for_registry"] == []


def pinned_payload(paths, *, direct=True):
    payload = manifest_payload()
    for index, path in enumerate(paths):
        content = path.read_bytes()
        payload["sources"][index]["content_sha256"] = hashlib.sha256(content).hexdigest()
        payload["sources"][index]["byte_size"] = len(content)
        if direct:
            payload["sources"][index].update(DIRECT)
    return payload


def test_pinned_originals_that_match_are_reported_as_satisfied(tmp_path, capsys):
    brief = make_pdf(tmp_path / "brief.pdf", 2)
    basis = make_pdf(tmp_path / "basis.pdf", 3)
    workbook = make_workbook(tmp_path / "table3.xlsx", 3, 2)
    code = run(
        tmp_path,
        pinned_payload((brief, basis, workbook)),
        (("brief-case", brief), ("evaluation-basis", basis), ("form-table-3", workbook)),
    )
    result = report(capsys)
    assert code == 0 and result["status"] == "sources_pinned"
    # Templates are pinned and satisfied, but never wired as registry sources.
    assert sorted(result["ready_for_registry"]) == ["brief-case", "evaluation-basis"]
    assert result["handling"]["form-table-3"] == "direct_non_sensitive"


def test_a_pinned_source_still_needs_a_recorded_direct_decision(tmp_path, capsys):
    brief = make_pdf(tmp_path / "brief.pdf", 2)
    basis = make_pdf(tmp_path / "basis.pdf", 3)
    workbook = make_workbook(tmp_path / "table3.xlsx", 3, 2)
    code = run(
        tmp_path,
        pinned_payload((brief, basis, workbook), direct=False),
        (("brief-case", brief), ("evaluation-basis", basis), ("form-table-3", workbook)),
    )
    result = report(capsys)
    assert code == 0 and result["ready_for_registry"] == []
    assert set(result["handling"].values()) == {"privacy_required"}


def test_direct_handling_without_a_recorded_decision_is_refused():
    payload = manifest_payload()
    payload["sources"][0]["handling"] = "direct_non_sensitive"
    with pytest.raises(ValidationError):
        CaseSourceManifest.model_validate(payload)


def test_a_changed_original_is_reported_instead_of_being_accepted(tmp_path, capsys):
    brief = make_pdf(tmp_path / "brief.pdf", 2)
    payload = manifest_payload()
    payload["sources"][0]["content_sha256"] = "c" * 64
    run(tmp_path, payload, (("brief-case", brief),))
    entry = next(s for s in report(capsys)["sources"] if s["source_id"] == "brief-case")
    assert entry["state"] == "changed" and entry["differences"] == ["content_sha256"]


def test_a_structural_mismatch_is_reported(tmp_path, capsys):
    brief = make_pdf(tmp_path / "brief.pdf", 5)
    run(tmp_path, manifest_payload(), (("brief-case", brief),))
    entry = next(s for s in report(capsys)["sources"] if s["source_id"] == "brief-case")
    assert entry["state"] == "structure_differs" and entry["differences"] == ["page_count"]


def test_unreadable_and_unknown_inputs_are_reported_without_paths(tmp_path, capsys):
    broken = tmp_path / "broken.xlsx"
    broken.write_bytes(b"not a package")
    code = run(
        tmp_path,
        manifest_payload(),
        (("form-table-3", broken), ("other-source", tmp_path / "missing.pdf")),
    )
    payload = report(capsys)
    assert code == 1 and payload["unknown_source_ids"] == ["other-source"]
    entry = next(s for s in payload["sources"] if s["source_id"] == "form-table-3")
    assert entry["state"] == "unreadable"
    assert str(tmp_path) not in json.dumps(payload)


def test_an_invalid_manifest_is_refused_without_echoing_its_content(tmp_path, capsys):
    path = tmp_path / "manifest.json"
    path.write_text('{"manifest_id":"PRIVATE-CANARY","case":{}}', encoding="utf-8")
    code = CLI.main(["--manifest", str(path)])
    output = capsys.readouterr().out
    assert code == 1 and json.loads(output)["status"] == "invalid_manifest"
    assert "PRIVATE-CANARY" not in output


@pytest.mark.parametrize("value", ["brief-case", "=path", "brief-case="])
def test_file_arguments_require_a_source_id_and_a_path(value):
    with pytest.raises(argparse.ArgumentTypeError):
        CLI.parse_file(value)
