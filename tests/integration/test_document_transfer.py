"""The same service contract runs against durable local and version-aware S3 storage."""

from __future__ import annotations

import base64
import io
import json
import runpy
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from types import SimpleNamespace
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError
from pypdf import PdfWriter

from appraisal_review.adapters.aws.document_storage import (
    S3DocumentConfiguration,
    S3DocumentStorage,
)
from appraisal_review.adapters.document_audit import ImmutableDocumentAudit
from appraisal_review.adapters.local.document_authority import (
    ConfiguredDocumentAuthorization,
    DocumentGrant,
    Ed25519ExportVerifier,
    TrustedExportKey,
)
from appraisal_review.adapters.local.document_export import ConfirmedDocumentExport
from appraisal_review.adapters.local.document_storage import SQLiteDocumentStorage
from appraisal_review.application.document_transfer import DocumentTransferService
from appraisal_review.application.service_guards import Principal
from appraisal_review.domain.document_transfer import (
    DocumentErrorCode,
    DocumentFault,
    DocumentOperation,
    ExportClaims,
    PrivacyAttestation,
    StoredBytes,
    canonical_bytes,
    digest_bytes,
)
from appraisal_review.domain.privacy_export import PrivacyExportPayload
from appraisal_review.domain.privacy_models import PrivacyManifest, PrivacyPage
from appraisal_review.domain.service_contracts import (
    ActorReference,
    MaterialRevision,
    Permission,
    RevisionReference,
    RunReference,
)

NOW = datetime(2026, 9, 10, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[2]
exercise_storage = runpy.run_path(str(ROOT / "cloud_tests/document_smoke.py"))["exercise_storage"]


class S3Error(Exception):
    def __init__(self, code):
        self.response = {"Error": {"Code": code, "Message": "private SDK diagnostic"}}


class VersionedS3:
    """An independent observable SDK double; never supplies default credentials or network."""

    def __init__(self):
        self.calls = []
        self.objects = {}
        self.latest = {}
        self.versioning = "Enabled"
        self.public = False
        self._lock = Lock()

    def get_bucket_versioning(self, **kw):
        self.calls.append(("versioning", kw))
        return {"Status": self.versioning}

    def get_bucket_encryption(self, **kw):
        return {
            "ServerSideEncryptionConfiguration": {
                "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
            }
        }

    def get_public_access_block(self, **kw):
        return {
            "PublicAccessBlockConfiguration": {
                k: True
                for k in (
                    "BlockPublicAcls",
                    "IgnorePublicAcls",
                    "BlockPublicPolicy",
                    "RestrictPublicBuckets",
                )
            }
        }

    def get_bucket_ownership_controls(self, **kw):
        return {"OwnershipControls": {"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]}}

    def get_bucket_policy_status(self, **kw):
        return {"PolicyStatus": {"IsPublic": self.public}}

    def put_object(self, **kw):
        self.calls.append(("put", kw))
        assert kw["IfNoneMatch"] == "*"
        assert kw["ServerSideEncryption"] == "AES256"
        assert kw["ExpectedBucketOwner"] == "111122223333"
        assert (
            kw["ChecksumSHA256"]
            == base64.b64encode(bytes.fromhex(digest_bytes(kw["Body"]))).decode()
        )
        with self._lock:
            if kw["Key"] in self.latest:
                raise S3Error("PreconditionFailed")
            version = str(uuid4())
            self.objects[kw["Key"], version] = dict(kw)
            self.latest[kw["Key"]] = version
        return {"VersionId": version}

    def head_object(self, **kw):
        self.calls.append(("head", kw))
        if kw["Key"] not in self.latest:
            raise S3Error("NoSuchKey")
        return {"VersionId": self.latest[kw["Key"]]}

    def get_object(self, **kw):
        self.calls.append(("get", kw))
        assert kw["VersionId"] and kw["VersionId"] != "null"
        assert kw["ChecksumMode"] == "ENABLED"
        obj = self.objects.get((kw["Key"], kw["VersionId"]))
        if obj is None:
            raise S3Error("NoSuchVersion")
        return {
            "VersionId": kw["VersionId"],
            "Body": io.BytesIO(obj["Body"]),
            "ContentLength": len(obj["Body"]),
            "ChecksumSHA256": obj["ChecksumSHA256"],
            "ServerSideEncryption": obj["ServerSideEncryption"],
        }


def pdf_bytes(width=100):
    writer = PdfWriter()
    writer.add_blank_page(width=width, height=100)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.fixture(params=["local", "s3"])
def env(request, tmp_path):
    case, actor, key_id = uuid4(), uuid4(), uuid4()
    key = Ed25519PrivateKey.generate()
    principal = Principal(
        ActorReference(actor_id=str(actor), kind="human"),
        frozenset({str(case)}),
        frozenset({Permission.REVIEW}),
    )
    grants = ConfiguredDocumentAuthorization(
        (
            DocumentGrant(
                actor,
                case,
                frozenset({"criteria", "forms", "reference", "brief", "template"}),
                frozenset(DocumentOperation),
            ),
        )
    )
    verifier = Ed25519ExportVerifier(
        (TrustedExportKey(key_id, key.public_key(), actor, frozenset({case})),), clock=lambda: NOW
    )
    audit = SQLiteDocumentStorage(tmp_path / "audit.sqlite")
    sdk = VersionedS3()
    storage = (
        SQLiteDocumentStorage(tmp_path / "documents.sqlite")
        if request.param == "local"
        else (
            S3DocumentStorage(
                sdk, S3DocumentConfiguration("synthetic-documents", "111122223333", uuid4())
            )
        )
    )
    service = DocumentTransferService(storage, grants, verifier, audit, clock=lambda: NOW)
    return SimpleNamespace(
        case=case,
        actor=actor,
        key_id=key_id,
        key=key,
        principal=principal,
        grants=grants,
        verifier=verifier,
        audit=audit,
        storage=storage,
        sdk=sdk,
        service=service,
        mode=request.param,
    )


def signed(env, content=None, purpose="forms", previous=None, **changes):
    content = pdf_bytes() if content is None else content
    manifest = PrivacyManifest(
        case_id=env.case,
        document_id=uuid4(),
        sanitized_digest=digest_bytes(content),
        byte_size=len(content),
        pages=(PrivacyPage(number=1, width=100.0, height=100.0),),
        occurrences=(),
    )
    claims = ExportClaims(
        key_id=env.key_id,
        export_id=uuid4(),
        principal_id=env.actor,
        manifest=manifest,
        purpose=purpose,
        confirmed_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
        previous=previous,
        **changes,
    )
    return content, PrivacyAttestation(
        claims=claims, signature_hex=env.key.sign(canonical_bytes(claims)).hex()
    )


def ingest(env, **kwargs):
    content, attestation = signed(env, **kwargs)
    return env.service.ingest(env.principal, content, attestation)


def material_run(env, documents):
    data = json.loads((ROOT / "examples/service-v1/revision.json").read_text())
    data["reference"]["case_id"] = str(env.case)
    data["reference"]["revision_id"] = str(uuid4())
    data["documents"] = [d.model_dump(mode="json") for d in documents]
    data["parent"] = None
    revision = MaterialRevision.model_validate(data)
    return revision, RunReference(run_id=uuid4(), revision=revision.reference)


def expect_fault(code, call):
    with pytest.raises(DocumentFault) as raised:
        call()
    assert raised.value.problem.code == code
    assert str(raised.value) == code.value
    assert "private" not in raised.value.problem.model_dump_json()
    return raised.value


def test_ingestion_identity_persistence_exact_hash_and_replay(env):
    content, attestation = signed(env)
    result = env.service.ingest(env.principal, content, attestation)
    assert result.reference.document_id != str(attestation.claims.manifest.document_id)
    assert result.classification == "sanitized" and result.content_type == "application/pdf"
    assert result.reference.content_hash == digest_bytes(content)
    assert env.service.ingest(env.principal, content, attestation) == result
    reopened = DocumentTransferService(env.storage, env.grants, env.verifier, env.audit)
    assert reopened.read(env.principal, result.reference).content == content
    assert "storage_version" not in result.model_dump_json()
    assert not any(uri in result.model_dump_json() for uri in ("s3://", "file://", "https://"))


@pytest.mark.parametrize("change", ["actor", "case", "permission", "model", "purpose"])
def test_unauthorized_ingestion_never_writes_storage(env, change):
    content, attestation = signed(env)
    principal = env.principal
    if change == "actor":
        principal = replace(principal, actor=ActorReference(actor_id=str(uuid4()), kind="human"))
    elif change == "case":
        principal = replace(principal, case_ids=frozenset())
    elif change == "permission":
        principal = replace(principal, permissions=frozenset())
    elif change == "model":
        principal = replace(principal, actor=principal.actor.model_copy(update={"kind": "model"}))
    else:
        env.service.authorization = ConfiguredDocumentAuthorization(())
    expect_fault(
        DocumentErrorCode.UNAUTHORIZED, lambda: env.service.ingest(principal, content, attestation)
    )
    assert not env.sdk.objects
    if env.mode == "local":
        with sqlite3.connect(env.storage.database) as connection:
            assert connection.execute("SELECT count(*) FROM documents").fetchone() == (0,)


@pytest.mark.parametrize("change", ["signature", "key", "expired", "future", "too_long", "actor"])
def test_invalid_attestation_is_rejected_before_storage(env, change):
    content, attestation = signed(env)
    if change == "signature":
        attestation = attestation.model_copy(update={"signature_hex": "0" * 128})
    else:
        changed = {
            "key": {"key_id": uuid4()},
            "actor": {"principal_id": uuid4()},
            "expired": {
                "confirmed_at": NOW - timedelta(minutes=11),
                "expires_at": NOW - timedelta(minutes=1),
            },
            "future": {"confirmed_at": NOW + timedelta(seconds=1)},
            "too_long": {"expires_at": NOW + timedelta(days=1)},
        }[change]
        claims = attestation.claims.model_copy(update=changed)
        attestation = PrivacyAttestation(
            claims=claims, signature_hex=env.key.sign(canonical_bytes(claims)).hex()
        )
    expect_fault(
        DocumentErrorCode.PRIVACY, lambda: env.service.ingest(env.principal, content, attestation)
    )
    assert not env.sdk.objects


@pytest.mark.parametrize(
    "change,code",
    [
        ("hash", DocumentErrorCode.INTEGRITY),
        ("size", DocumentErrorCode.INTEGRITY),
        ("non_pdf", DocumentErrorCode.NOT_PDF),
        ("oversize", DocumentErrorCode.TOO_LARGE),
    ],
)
def test_pdf_admission(env, change, code):
    content, attestation = signed(env, content=b"plain text" if change == "non_pdf" else None)
    if change == "hash":
        content = b"X" + content[1:]
    if change == "size":
        content += b"x"
    if change == "oversize":
        env.service.max_pdf_bytes = len(content) - 1
    expect_fault(code, lambda: env.service.ingest(env.principal, content, attestation))


@pytest.mark.parametrize(
    "change,code",
    [
        ("case", DocumentErrorCode.UNAUTHORIZED),
        ("hash", DocumentErrorCode.INTEGRITY),
        ("version", DocumentErrorCode.NOT_FOUND),
        ("purpose", DocumentErrorCode.INTEGRITY),
        ("file", DocumentErrorCode.INVALID),
        ("url", DocumentErrorCode.INVALID),
        ("bucket", DocumentErrorCode.INVALID),
    ],
)
def test_resolution_rejects_substitution(env, change, code):
    metadata = ingest(env)
    changes = {
        "case": {"case_id": str(uuid4())},
        "hash": {"content_hash": "a" * 64},
        "version": {"version": str(uuid4())},
        "purpose": {"purpose": "criteria"},
        "file": {"document_id": "file:///private/canary.pdf"},
        "url": {"document_id": "https://example.invalid/canary.pdf"},
        "bucket": {"document_id": "s3://arbitrary-bucket/key"},
    }
    reference = metadata.reference.model_copy(update=changes[change])
    expect_fault(code, lambda: env.service.read(env.principal, reference))


def test_original_run_remains_pinned_after_update_and_reopen(env):
    forms, criteria = ingest(env), ingest(env, purpose="criteria")
    revision, run = material_run(env, (forms.reference, criteria.reference))
    first = env.service.create_snapshot(env.principal, run, revision)
    assert env.service.create_snapshot(env.principal, run, revision) == first
    updated = ingest(env, content=pdf_bytes(200), previous=forms.reference)
    assert updated.reference.document_id == forms.reference.document_id
    assert updated.reference.version != forms.reference.version
    reopened = DocumentTransferService(env.storage, env.grants, env.verifier, env.audit)
    assert reopened.read_snapshot(env.principal, run, forms.reference).content == pdf_bytes()
    expect_fault(
        DocumentErrorCode.UNAUTHORIZED,
        lambda: reopened.read_snapshot(env.principal, run, updated.reference),
    )
    changed_revision = revision.model_copy(
        update={"documents": (updated.reference, criteria.reference)}
    )
    expect_fault(
        DocumentErrorCode.CONFLICT,
        lambda: reopened.create_snapshot(env.principal, run, changed_revision),
    )
    different_run = run.model_copy(
        update={
            "revision": RevisionReference(
                case_id=str(env.case), revision_id="other", material_digest="a" * 64
            )
        }
    )
    expect_fault(
        DocumentErrorCode.CONFLICT,
        lambda: reopened.read_snapshot(env.principal, different_run, forms.reference),
    )


def test_s3_latest_version_substitution_cannot_change_started_run(env):
    if env.mode != "s3":
        # Local storage is create-only; the equivalent overwrite attempt must fail atomically.
        doc = ingest(env)
        stored = env.service._resolve(env.principal, doc.reference, DocumentOperation.READ)
        key = env.service._key(doc.reference, "content")
        expect_fault(
            DocumentErrorCode.CONFLICT,
            lambda: env.storage.create(
                key,
                b"replacement",
                env.service._labels(env.principal, str(env.case), b"replacement"),
            ),
        )
        assert env.service._read(stored).content == pdf_bytes()
        return
    forms, criteria = ingest(env), ingest(env, purpose="criteria")
    revision, run = material_run(env, (forms.reference, criteria.reference))
    env.service.create_snapshot(env.principal, run, revision)
    object_key = env.storage._key(env.service._key(forms.reference, "content"))
    new_version = str(uuid4())
    old = env.sdk.objects[object_key, env.sdk.latest[object_key]]
    env.sdk.objects[object_key, new_version] = {**old, "Body": pdf_bytes(200)}
    env.sdk.latest[object_key] = new_version
    assert env.service.read_snapshot(env.principal, run, forms.reference).content == pdf_bytes()


def test_read_audit_and_failure_are_mandatory(env):
    doc = ingest(env)
    env.service.read(env.principal, doc.reference)
    with sqlite3.connect(env.audit.database) as connection:
        events = [
            json.loads(row[0]) for row in connection.execute("SELECT event FROM document_audit")
        ]
    assert [event["operation"] for event in events] == ["ingest", "read"]
    assert all(event["outcome"] == "succeeded" for event in events)

    class UnavailableAudit:
        def append(self, event):
            raise RuntimeError("private audit connection information")

    env.service.audit = UnavailableAudit()
    expect_fault(
        DocumentErrorCode.UNAVAILABLE, lambda: env.service.read(env.principal, doc.reference)
    )


def test_canaries_remain_local_across_confirmation_sdk_records_and_public_results(
    env, caplog, capsys
):
    # Source and re-identification records deliberately never become export-carrier fields.
    originals = {"source": "SYNTHETIC_PERSON_8a62", "map": "SYNTHETIC_ADDRESS_e311"}
    content, attestation = signed(env)
    payload = PrivacyExportPayload(content, attestation.claims.manifest.model_dump_json())
    shown = []

    class HumanConfirmation:
        def confirm(self, actual):
            shown.append(actual)
            return True

    bridge = ConfirmedDocumentExport(
        HumanConfirmation(),
        env.service,
        env.principal,
        "forms",
        env.key,
        env.key_id,
        clock=lambda: NOW,
    )
    assert bridge.confirm(payload)
    bridge.accept(payload)
    assert shown == [payload] and shown[0] is payload
    assert bridge.result is not None
    assert env.service.read(env.principal, bridge.result.reference).content == content
    expect_fault(DocumentErrorCode.PRIVACY, lambda: bridge.accept(payload))
    with sqlite3.connect(env.audit.database) as connection:
        audit = str(connection.execute("SELECT event FROM document_audit").fetchall())
    records = repr(env.sdk.calls)
    if env.mode == "local":
        with sqlite3.connect(env.storage.database) as connection:
            records += str(
                connection.execute("SELECT key, content, labels FROM documents").fetchall()
            )
    captured = capsys.readouterr()
    surfaces = (
        records,
        audit,
        caplog.text,
        captured.out,
        captured.err,
        bridge.result.model_dump_json(),
        repr(payload),
        repr(bridge),
    )
    for canary in originals.values():
        assert all(canary not in surface for surface in surfaces)
    assert "reviewer_text" not in bridge.result.model_dump_json()


@pytest.mark.parametrize("change", ["unconfirmed", "denied", "copied", "changed", "extra_text"])
def test_confirmation_cannot_be_reused_or_self_asserted(env, change):
    content, attestation = signed(env)
    payload = PrivacyExportPayload(content, attestation.claims.manifest.model_dump_json())
    bridge = ConfirmedDocumentExport(
        SimpleNamespace(confirm=lambda actual: change != "denied"),
        env.service,
        env.principal,
        "forms",
        env.key,
        env.key_id,
        clock=lambda: NOW,
    )
    if change == "extra_text":
        bad = PrivacyExportPayload(content, payload.manifest_json, "SYNTHETIC_PRIVATE_TEXT")
        expect_fault(DocumentErrorCode.INVALID, lambda: bridge.confirm(bad))
        return
    if change != "unconfirmed":
        assert bridge.confirm(payload) is (change != "denied")
    if change == "copied":
        payload = PrivacyExportPayload(payload.pdf, payload.manifest_json)
    if change == "changed":
        object.__setattr__(payload, "pdf", pdf_bytes(200))
    expect_fault(
        DocumentErrorCode.INTEGRITY if change == "changed" else DocumentErrorCode.PRIVACY,
        lambda: bridge.accept(payload),
    )
    assert bridge.result is None and not env.sdk.objects


@pytest.mark.parametrize(
    "extra",
    [
        "original_filename",
        "mapping",
        "source_uri",
        "bucket",
        "key",
        "human_confirmed",
        "original_digest",
    ],
)
def test_privacy_manifest_rejects_nonallowlisted_fields(extra):
    manifest = {
        "case_id": str(uuid4()),
        "document_id": str(uuid4()),
        "sanitized_digest": "a" * 64,
        "byte_size": 1,
        "pages": [{"number": 1, "width": 100.0, "height": 100.0}],
        "occurrences": [],
        extra: "SYNTHETIC_PRIVATE_VALUE",
    }
    with pytest.raises(ValidationError):
        PrivacyManifest.model_validate_json(json.dumps(manifest))


def test_storage_live_probe_contract_uses_both_adapters(env):
    exercise_storage(env.storage)


def test_concurrent_replay_allocates_one_document_version(env):
    content, attestation = signed(env)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(
            executor.map(
                lambda _: env.service.ingest(env.principal, content, attestation), range(2)
            )
        )
    assert results[0] == results[1]
    assert env.service.read(env.principal, results[0].reference).content == content


def test_concurrent_snapshot_conflict_cannot_overwrite_winner(env):
    forms, criteria = ingest(env), ingest(env, purpose="criteria")
    newer = ingest(env, content=pdf_bytes(200), previous=forms.reference)
    revision, run = material_run(env, (forms.reference, criteria.reference))
    changed = revision.model_copy(update={"documents": (newer.reference, criteria.reference)})

    def attempt(material):
        try:
            return env.service.create_snapshot(env.principal, run, material)
        except DocumentFault as error:
            assert error.problem.code == DocumentErrorCode.CONFLICT
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(attempt, (revision, changed)))
    winners = [r for r in results if r is not None]
    assert len(winners) == 1
    for entry in winners[0].documents:
        resolved = env.service.read_snapshot(env.principal, run, entry.document)
        assert digest_bytes(resolved.content) == entry.document.content_hash


def test_snapshot_commitment_includes_actual_storage_version(env):
    forms, criteria = ingest(env), ingest(env, purpose="criteria")
    revision, run = material_run(env, (forms.reference, criteria.reference))
    env.service.create_snapshot(env.principal, run, revision)
    original = env.storage

    class CorruptedCatalog:
        def read(self, key, *, version, limit):
            found = original.read(key, version=version, limit=limit)
            if key.kind == "documents":
                data = json.loads(found.content)
                data["storage_version"] = "replacement-with-identical-pdf-bytes"
                return StoredBytes(json.dumps(data).encode(), found.version)
            return found

    env.service.storage = CorruptedCatalog()
    expect_fault(
        DocumentErrorCode.INTEGRITY,
        lambda: env.service.read_snapshot(env.principal, run, forms.reference),
    )


def test_source_byte_corruption_is_rejected(env):
    document = ingest(env)
    key = env.service._key(document.reference, "content")
    if env.mode == "local":
        with sqlite3.connect(env.storage.database) as connection:
            connection.execute(
                "UPDATE documents SET content = ? WHERE key = ?",
                (pdf_bytes(200), key.relative_key()),
            )
    else:
        full_key = env.storage._key(key)
        obj = env.sdk.objects[full_key, env.sdk.latest[full_key]]
        obj["Body"] = pdf_bytes(200)
        obj["ChecksumSHA256"] = base64.b64encode(bytes.fromhex(digest_bytes(obj["Body"]))).decode()
    expect_fault(
        DocumentErrorCode.INTEGRITY, lambda: env.service.read(env.principal, document.reference)
    )


def test_document_grant_revocation_blocks_already_pinned_source(env):
    forms, criteria = ingest(env), ingest(env, purpose="criteria")
    revision, run = material_run(env, (forms.reference, criteria.reference))
    env.service.create_snapshot(env.principal, run, revision)
    env.service.authorization = ConfiguredDocumentAuthorization(())
    expect_fault(
        DocumentErrorCode.UNAUTHORIZED,
        lambda: env.service.read_snapshot(env.principal, run, forms.reference),
    )


def test_expired_export_does_not_expire_accepted_evidence(env):
    content, attestation = signed(env)
    document = env.service.ingest(env.principal, content, attestation)
    env.verifier.clock = lambda: NOW + timedelta(days=1)
    expect_fault(
        DocumentErrorCode.PRIVACY, lambda: env.service.ingest(env.principal, content, attestation)
    )
    assert env.service.read(env.principal, document.reference).content == content


def test_audit_can_use_durable_immutable_storage(env):
    env.service.audit = ImmutableDocumentAudit(env.storage)
    document = ingest(env)
    env.service.read(env.principal, document.reference)
    if env.mode == "s3":
        events = [
            json.loads(obj["Body"]) for (key, _), obj in env.sdk.objects.items() if "/audit/" in key
        ]
    else:
        with sqlite3.connect(env.storage.database) as connection:
            events = [
                json.loads(row[0])
                for row in connection.execute(
                    "SELECT content FROM documents WHERE key LIKE 'audit/%'"
                )
            ]
    assert len(events) == 2
    read_event = next(event for event in events if event["operation"] == "read")
    assert read_event["document_id"] == document.reference.document_id
    assert "Body" not in read_event and "storage_version" not in read_event


@pytest.mark.parametrize("field,value", [("versioning", "Suspended"), ("public", True)])
def test_s3_configuration_fails_closed_before_write(field, value):
    sdk = VersionedS3()
    setattr(sdk, field, value)
    storage = S3DocumentStorage(
        sdk, S3DocumentConfiguration("synthetic-documents", "111122223333", uuid4())
    )
    expect_fault(DocumentErrorCode.UNAVAILABLE, lambda: exercise_storage(storage))
    assert not sdk.objects


def test_private_local_database_required(tmp_path):
    path = tmp_path / "world-readable.sqlite"
    path.touch(mode=0o644)
    expect_fault(DocumentErrorCode.UNAVAILABLE, lambda: SQLiteDocumentStorage(path))
    link = tmp_path / "alias.sqlite"
    link.symlink_to(path)
    expect_fault(DocumentErrorCode.UNAVAILABLE, lambda: SQLiteDocumentStorage(link))


def test_public_schema_matches_generated_contracts_and_actual_json(env, tmp_path):
    import jsonschema

    exported = runpy.run_path(str(ROOT / "scripts/export_document_contracts.py"))
    exported["export"](tmp_path)
    assert (tmp_path / "schemas/document-v1.json").read_bytes() == (
        ROOT / "schemas/document-v1.json"
    ).read_bytes()
    schema = json.loads((tmp_path / "schemas/document-v1.json").read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    forms, criteria = ingest(env), ingest(env, purpose="criteria")
    revision, run = material_run(env, (forms.reference, criteria.reference))
    snapshot = env.service.create_snapshot(env.principal, run, revision)
    for value in (
        forms,
        forms.attestation,
        forms.attestation.claims.manifest,
        snapshot,
        DocumentFault(DocumentErrorCode.PRIVACY).problem,
    ):
        jsonschema.validate(
            value.model_dump(mode="json"), {**schema, "$ref": f"#/$defs/{type(value).__name__}"}
        )


def test_substituted_original_canary_never_reaches_ingestion_transport(env):
    content, attestation = signed(env)
    payload = PrivacyExportPayload(content, attestation.claims.manifest.model_dump_json())
    received = []

    class Transport:
        def ingest(self, principal, pdf, proof):
            received.append((pdf, proof))
            return env.service.ingest(principal, pdf, proof)

    bridge = ConfirmedDocumentExport(
        SimpleNamespace(confirm=lambda exact: True),
        Transport(),
        env.principal,
        "forms",
        env.key,
        env.key_id,
        clock=lambda: NOW,
    )
    assert bridge.confirm(payload)
    object.__setattr__(payload, "pdf", b"SYNTHETIC_ORIGINAL_PERSON_AND_MAPPING")
    expect_fault(DocumentErrorCode.INTEGRITY, lambda: bridge.accept(payload))
    assert received == [] and bridge.result is None and not env.sdk.objects


@pytest.mark.parametrize("missing", [None, {}, "file:///private/original.pdf"])
def test_missing_attestation_has_safe_error_and_no_storage(env, missing):
    expect_fault(
        DocumentErrorCode.PRIVACY, lambda: env.service.ingest(env.principal, pdf_bytes(), missing)
    )
    assert not env.sdk.objects


def test_caller_revision_label_cannot_leak_into_snapshot_storage(env):
    forms, criteria = ingest(env), ingest(env, purpose="criteria")
    revision, run = material_run(env, (forms.reference, criteria.reference))
    reference = revision.reference.model_copy(
        update={"revision_id": "SYNTHETIC_PRIVATE_FILENAME.pdf"}
    )
    bad_revision = revision.model_copy(update={"reference": reference})
    bad_run = run.model_copy(update={"revision": reference})
    expect_fault(
        DocumentErrorCode.INVALID,
        lambda: env.service.create_snapshot(env.principal, bad_run, bad_revision),
    )
    if env.mode == "s3":
        assert "SYNTHETIC_PRIVATE_FILENAME" not in repr(env.sdk.calls)
    else:
        with sqlite3.connect(env.storage.database) as connection:
            assert connection.execute(
                "SELECT count(*) FROM documents WHERE key LIKE 'snapshots/%'"
            ).fetchone() == (0,)


def test_invalid_constructed_model_cannot_echo_private_input_in_serializer_warnings(env, recwarn):
    content, attestation = signed(env)
    manifest = attestation.claims.manifest.model_copy(update={"case_id": "SYNTHETIC_PRIVATE_CASE"})
    claims = attestation.claims.model_copy(update={"manifest": manifest})
    malformed = attestation.model_copy(update={"claims": claims})
    expect_fault(
        DocumentErrorCode.PRIVACY, lambda: env.service.ingest(env.principal, content, malformed)
    )
    assert not recwarn.list and not env.sdk.objects
