"""Synthetic unit fixtures for local source authority; not real-case acceptance."""

import asyncio
import hashlib
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pymupdf
import pytest
from fastapi.testclient import TestClient

from appraisal_review.adapters.local.document_manifest import InputManifest, InputSpec
from appraisal_review.adapters.local.original_documents import (
    AuthorizedOriginalParser,
    LocalOriginalAuthorization,
    LocalOriginalDocuments,
)
from appraisal_review.adapters.local.original_preview import create_original_preview
from appraisal_review.adapters.local.original_workbench import ExpiringLocalDirectory
from appraisal_review.adapters.local.sqlite_review_store import SQLiteReviewStore
from appraisal_review.application.revisions import source_preparation_revision
from appraisal_review.application.service_guards import Principal, ServiceFault
from appraisal_review.domain.document_transfer import DocumentErrorCode, DocumentFault
from appraisal_review.domain.review_contracts import CaseIdentity
from appraisal_review.domain.service_contracts import ActorReference, Permission, RunReference


@pytest.fixture
def original_fixture(tmp_path):
    tmp_path.chmod(0o700)
    specs = []
    for role in ("forms", "criteria", "reference"):
        path = tmp_path / f"{role}.pdf"
        with pymupdf.open() as document:
            page = document.new_page()
            page.insert_text((50, 60), f"Unit fixture {role}")
            document.save(path)
        specs.append(
            InputSpec(
                path=path,
                document_id=role,
                version="unit-v1",
                role=role,
                expected_hash=hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        )
    identity = CaseIdentity(
        case_id="unit-case",
        version="1",
        district="unit-district",
        zone="unit-zone",
        land_use_category="unit-use",
        effective_date="2026-01-01",
    )
    manifest = InputManifest(identity=identity, documents=specs)
    principal = Principal(
        ActorReference(actor_id="unit-operator", kind="human"),
        frozenset({"unit-case"}),
        frozenset({Permission.REVIEW, Permission.CONFIRM}),
    )
    now = [100.0]
    grant = LocalOriginalAuthorization(
        principal, frozenset({"forms", "criteria", "reference"}), 200.0, clock=lambda: now[0]
    )
    directory = ExpiringLocalDirectory({"session-" + "s" * 32: principal}, grant)
    store = SQLiteReviewStore(tmp_path / "state.sqlite")
    documents = LocalOriginalDocuments(manifest, store, grant)
    references = tuple(documents._reference(role) for role in ("forms", "criteria", "reference"))
    revision = source_preparation_revision(references, "input-v1")
    run = RunReference(run_id=uuid4(), revision=revision.reference)
    return SimpleNamespace(
        manifest=manifest,
        principal=principal,
        now=now,
        grant=grant,
        directory=directory,
        store=store,
        documents=documents,
        references=references,
        revision=revision,
        run=run,
    )


def test_original_snapshot_has_no_c2_attestation_and_rechecks_actual_bytes(original_fixture):
    h = original_fixture
    assert h.documents.create_snapshot(h.principal, h.run, h.revision) == h.revision
    data = h.documents.read_snapshot(h.principal, h.run, h.references[0])
    assert data.boundary == "local-original-v1"
    assert not hasattr(data, "metadata")
    assert data.content == h.manifest.documents[0].path.read_bytes()
    changed = data.content.replace(b"%%EOF", b"% changed unit fixture\n%%EOF")
    assert changed != data.content
    h.manifest.documents[0].path.write_bytes(changed)
    with pytest.raises(DocumentFault) as failure:
        h.documents.read_snapshot(h.principal, h.run, h.references[0])
    assert failure.value.problem.code == DocumentErrorCode.INTEGRITY


@pytest.mark.parametrize("change", ["case", "version", "hash", "purpose", "actor"])
def test_source_reference_and_actor_cannot_select_other_material(original_fixture, change):
    h = original_fixture
    raw = h.references[0].model_dump()
    actor = h.principal
    if change == "actor":
        actor = replace(actor, actor=ActorReference(actor_id="other", kind="human"))
    else:
        key, value = {
            "case": ("case_id", "other"),
            "version": ("version", "other"),
            "hash": ("content_hash", "f" * 64),
            "purpose": ("purpose", "criteria"),
        }[change]
        raw[key] = value
    with pytest.raises(DocumentFault):
        h.documents.read(actor, type(h.references[0]).model_validate(raw))


def test_run_binding_cannot_be_replayed_for_another_run_or_revision(original_fixture):
    h = original_fixture
    h.documents.create_snapshot(h.principal, h.run, h.revision)
    other = h.run.model_copy(update={"run_id": uuid4()})
    with pytest.raises(DocumentFault):
        h.documents.read_snapshot(h.principal, other, h.references[0])
    changed = h.run.model_copy(
        update={"revision": h.run.revision.model_copy(update={"revision_id": "other"})}
    )
    with pytest.raises(DocumentFault):
        h.documents.read_snapshot(h.principal, changed, h.references[0])


@pytest.mark.parametrize("deny", ["expiration", "purpose", "revocation"])
def test_sources_expire_or_revoke_independently_of_case_membership(original_fixture, deny):
    h = original_fixture
    h.documents.create_snapshot(h.principal, h.run, h.revision)
    if deny == "expiration":
        h.now[0] = 200
    elif deny == "purpose":
        h.grant.purposes = frozenset({"criteria"})
    else:
        h.grant.revoked = True
    assert "unit-case" in h.principal.case_ids
    with pytest.raises(DocumentFault):
        h.documents.read_snapshot(h.principal, h.run, h.references[0])


def test_current_authorized_parser_returns_actual_native_source(original_fixture):
    h = original_fixture
    h.documents.create_snapshot(h.principal, h.run, h.revision)
    parser = AuthorizedOriginalParser(h.documents, h.directory, h.principal, h.run)
    actual = asyncio.run(parser.parse_document(h.manifest.documents[0].path.as_uri()))
    assert actual.source.content_hash == h.references[0].content_hash
    assert any("Unit fixture forms" in region.text for region in actual.source.pages[0].regions)
    with pytest.raises(ValueError):
        asyncio.run(parser.parse_document("file:///private/not-configured.pdf"))


def test_parser_refreshes_purpose_after_native_parse(original_fixture):
    h = original_fixture
    h.documents.create_snapshot(h.principal, h.run, h.revision)
    parse = h.documents.parser.parse_bytes

    def revoked_after_parse(*args, **kwargs):
        result = parse(*args, **kwargs)
        h.grant.revoked = True
        return result

    h.documents.parser.parse_bytes = revoked_after_parse
    parser = AuthorizedOriginalParser(h.documents, h.directory, h.principal, h.run)
    with pytest.raises((ServiceFault, DocumentFault)):
        asyncio.run(parser.parse_document(h.manifest.documents[0].path.as_uri()))


def test_original_preview_requires_origin_pairing_and_current_case_session(original_fixture):
    h = original_fixture
    app = create_original_preview(
        authority="127.0.0.1:8769",
        origin="http://127.0.0.1:4176",
        pairing_token="p" * 32,
        directory=h.directory,
        documents=h.documents,
    )
    reference = h.references[0]
    path = (
        f"/local-original/documents/{reference.document_id}?version={reference.version}"
        f"&content_hash={reference.content_hash}"
    )
    headers = {
        "Origin": "http://127.0.0.1:4176",
        "Authorization": "Bearer " + "p" * 32,
        "X-Review-Session": "Bearer session-" + "s" * 32,
    }
    with TestClient(app, base_url="http://127.0.0.1:8769") as client:
        for key in headers:
            assert (
                client.get(path, headers={k: v for k, v in headers.items() if k != key}).status_code
                == 403
            )
        assert (
            client.get(path, headers=headers | {"Origin": "https://example.org"}).status_code == 403
        )
        actual = client.get(path, headers=headers)
        assert (
            actual.status_code == 200
            and actual.content == h.manifest.documents[0].path.read_bytes()
        )
        assert actual.headers["cache-control"] == "no-store"
        assert client.post(path, headers=headers).status_code == 403
        h.grant.revoked = True
        assert client.get(path, headers=headers).status_code == 403
