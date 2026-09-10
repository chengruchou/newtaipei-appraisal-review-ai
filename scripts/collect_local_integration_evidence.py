"""Read-only local observation collector; never an acceptance gate or authorization."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import subprocess
import sys
import time
from contextlib import ExitStack, closing, redirect_stderr, redirect_stdout
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any
from uuid import UUID

from check_rehearsal_evidence import EvidenceError, SafeParser, _unique_pairs
from pypdf import PdfReader

from appraisal_review.adapters.local.pdf_config import field_map_sha256
from appraisal_review.adapters.local.service import LocalWriterConfiguration
from appraisal_review.adapters.local.sqlite_review_store import _State
from appraisal_review.adapters.local.workflow_runtime import SQLiteDecisionTrace
from appraisal_review.domain.artifact_publication import CommittedManifest
from appraisal_review.domain.document_transfer import (
    ObjectLabels,
    RunSourceSnapshot,
    StoredDocument,
    canonical_bytes,
)
from appraisal_review.domain.privacy_refill import FinalLocalManifest
from appraisal_review.domain.review_contracts import content_digest
from appraisal_review.domain.service_contracts import (
    FencedArtifactManifest,
    RunReference,
    ServiceResult,
)

MAX_FILE = 64 * 1024 * 1024
MAX_DATABASE = 512 * 1024 * 1024


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def decode(raw: bytes | str) -> Any:
    return json.loads(raw, object_pairs_hook=_unique_pairs)


def opaque(value: str) -> str:
    """Non-UUID internal labels never become public case names or paths."""
    try:
        return str(UUID(value))
    except ValueError:
        return "sha256:" + digest(value.encode())


def confined(path: Path, root: Path) -> Path:
    path = path.absolute()
    if not path.is_relative_to(root) or path.resolve() != path:
        raise EvidenceError("path_outside_repository_or_symlink")
    return path


def read_file(path: Path, root: Path) -> bytes:
    path = confined(path, root)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 <= info.st_size <= MAX_FILE:
            raise EvidenceError("invalid_evidence_file")
        raw = stream.read(MAX_FILE + 1)
        if len(raw) > MAX_FILE:
            raise EvidenceError("invalid_evidence_file")
        return raw


def snapshot(path: Path, root: Path, stack: ExitStack) -> sqlite3.Connection:
    path = confined(path, root)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_DATABASE or info.st_mode & 0o077:
        raise EvidenceError("invalid_private_database")
    # mode=ro prevents application writes. Backup includes the current committed
    # WAL state; immutable=1 would silently ignore a live WAL and is not used.
    memory = stack.enter_context(closing(sqlite3.connect(":memory:")))
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)) as source:
        source.execute("PRAGMA query_only=ON")
        source.backup(memory)
    memory.execute("PRAGMA query_only=ON")
    if memory.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
        raise EvidenceError("database_integrity_failure")
    return memory


def has_table(db: sqlite3.Connection, table: str) -> bool:
    return (
        db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        is not None
    )


def _pdf_facts(raw: bytes) -> dict[str, Any]:
    reader = PdfReader(BytesIO(raw), strict=True)
    if reader.is_encrypted or not reader.pages:
        raise EvidenceError("pdf_unreadable")
    # Force decompression without exposing any extracted source/original text.
    for page in reader.pages:
        page.extract_text()
    return {"sha256": digest(raw), "byte_size": len(raw), "page_count": len(reader.pages)}


def pdf_facts(raw: bytes) -> dict[str, Any]:
    with redirect_stderr(StringIO()), redirect_stdout(StringIO()):
        return _pdf_facts(raw)


def source_observations(
    dbs: list[sqlite3.Connection],
) -> tuple[dict[tuple[str, str, str], Any], dict[str, Any]]:
    documents: dict[tuple[str, str, str], Any] = {}
    snapshots: dict[str, Any] = {}
    for db in dbs:
        rows = db.execute(
            "SELECT key,version,content,labels FROM documents ORDER BY key"
        ).fetchall()
        stored = {}
        for key, version, raw, labels in rows:
            checked = ObjectLabels.model_validate_json(labels)
            if digest(raw) != checked.content_hash or len(raw) != checked.byte_size:
                raise EvidenceError("source_storage_digest_mismatch")
            stored[key] = (version, raw)
        for key, (_, raw) in stored.items():
            if key.startswith("documents/"):
                document = StoredDocument.model_validate_json(raw)
                ref = document.metadata.reference
                suffix = f"{ref.document_id}/{ref.version}"
                version, content = stored["content/" + suffix]
                if key != "documents/" + suffix or version != document.storage_version:
                    raise EvidenceError("source_version_mismatch")
                measured = pdf_facts(content)
                if (
                    measured["sha256"] != ref.content_hash
                    or len(content) != document.metadata.byte_size
                ):
                    raise EvidenceError("source_content_mismatch")
                identity = (ref.document_id, ref.version, ref.content_hash)
                entry = (document, digest(raw), measured)
                if identity in documents and documents[identity] != entry:
                    raise EvidenceError("ambiguous_source_identity")
                documents[identity] = entry
            elif key.startswith("snapshots/"):
                pinned = RunSourceSnapshot.model_validate_json(raw)
                expected = f"snapshots/{pinned.revision.case_id}/{pinned.run_id}"
                if key != expected:
                    raise EvidenceError("source_snapshot_key_mismatch")
                if str(pinned.run_id) in snapshots and snapshots[str(pinned.run_id)] != pinned:
                    raise EvidenceError("ambiguous_source_snapshot")
                snapshots[str(pinned.run_id)] = pinned
    return documents, snapshots


def repository_state(root: Path) -> dict[str, Any]:
    def git(*args: str) -> bytes:
        return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True).stdout

    head = git("rev-parse", "HEAD").decode().strip()
    status = git("status", "--porcelain=v1", "-z")
    paths = sorted(
        set(git("ls-files", "--cached", "--others", "--exclude-standard", "-z").split(b"\0"))
        - {b""}
    )
    tree = hashlib.sha256()
    for name in paths:
        path = root / os.fsdecode(name)
        tree.update(name + b"\0")
        tree.update(bytes.fromhex(digest(read_file(path, root))) if path.exists() else b"deleted")
    return {"head": head, "dirty": bool(status), "working_tree_sha256": tree.hexdigest()}


def collect_jobs(
    db: sqlite3.Connection, sources: dict[Any, Any], snapshots: dict[str, Any], root: Path
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw_state = db.execute("SELECT payload FROM review_state WHERE singleton=1").fetchone()[0]
    decode(raw_state)
    state = _State.model_validate_json(raw_state)
    state.stores()  # Validate duplicate identities and recomputed material/revision bindings.
    observations = []
    artifacts: dict[str, Any] = {}
    for job in state.jobs.values():
        runs = [
            row.value
            for row in state.runs
            if row.job_id == job.job_id and row.value.run_id == job.current_run_id
        ]
        if len(runs) != 1:
            raise EvidenceError("job_run_binding_mismatch")
        run = runs[0]
        item: dict[str, Any] = {
            "job_id": str(job.job_id),
            "case_id": opaque(job.case_id),
            "run_id": str(run.run_id),
            "revision_id": opaque(run.revision.revision_id),
            "material_sha256": run.revision.material_digest,
            "attempt_id": str(run.attempt_id) if run.attempt_id else None,
            "fencing_token": run.fencing_token,
            "result_version": run.result_version,
            "job_status": job.status.value,
            "cancel_requested": job.cancel_requested,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
            "open_task_count": len(job.open_task_ids),
            "sources": [],
            "artifacts": [],
            "current_service_authorization": "not_checked",
        }
        pinned = snapshots.get(str(run.run_id))
        if pinned is not None and (
            pinned.revision != run.revision
            or set(e.document for e in pinned.documents) != set(run.documents)
        ):
            raise EvidenceError("run_source_snapshot_mismatch")
        item["c2_snapshot_observed"] = pinned is not None
        for ref in run.documents:
            entry = sources.get((ref.document_id, ref.version, ref.content_hash))
            value = {
                "document_id": opaque(ref.document_id),
                "version": opaque(ref.version),
                "sha256": ref.content_hash,
                "bytes_observed": entry is not None,
            }
            if entry is not None:
                document, stored_digest, measured = entry
                if document.metadata.reference != ref:
                    raise EvidenceError("source_case_binding_mismatch")
                if pinned is not None:
                    e = next(e for e in pinned.documents if e.document == ref)
                    if (
                        e.stored_document_digest != stored_digest
                        or e.page_count != measured["page_count"]
                        or e.privacy_manifest_digest
                        != digest(canonical_bytes(document.metadata.attestation.claims.manifest))
                    ):
                        raise EvidenceError("source_snapshot_digest_mismatch")
                value.update(measured)
            item["sources"].append(value)
        if has_table(db, "workflow_trace"):
            events = SQLiteDecisionTrace._load(db, run.run_id)
            if any(SQLiteDecisionTrace._run(e).revision != run.revision for e in events):
                raise EvidenceError("trace_revision_mismatch")
            item["causal_event_count"] = len(events)
            item["trace_sha256"] = digest(b"\n".join(e.model_dump_json().encode() for e in events))
        if run.result_version:
            refs = [
                r
                for r in state.results
                if r.run_id == run.run_id and r.result_version == run.result_version
            ]
            if len(refs) != 1 or refs[0].fencing_token != run.fencing_token:
                raise EvidenceError("result_reference_mismatch")
            rows = db.execute(
                "SELECT attempt_id,digest,payload FROM review_results "
                "WHERE run_id=? AND version=? AND digest=?",
                (str(run.run_id), run.result_version, refs[0].result_digest),
            ).fetchall()
            if len(rows) != 1:
                raise EvidenceError("result_body_unavailable")
            result = ServiceResult.model_validate_json(rows[0][2])
            if (
                rows[0][0] != str(result.run.attempt_id)
                or not any(
                    a.job_id == job.job_id
                    and a.run_id == run.run_id
                    and a.attempt_id == result.run.attempt_id
                    and a.fencing_token == run.fencing_token
                    for a in state.attempts
                )
                or result.result_version != run.result_version
                or result.execution_status != refs[0].execution_status
                or result.business_status != refs[0].business_status
                or result.artifact_status != refs[0].artifact_status
                or tuple(v.artifact_id for v in result.artifacts) != refs[0].artifact_ids
                or len(result.findings) != refs[0].finding_count
                or result.run.revision != run.revision
                or result.run.run_id != run.run_id
                or content_digest(result) != refs[0].result_digest
            ):
                raise EvidenceError("result_body_binding_mismatch")
            item["attempt_id"] = str(result.run.attempt_id)
            item["result_sha256"] = refs[0].result_digest
            item["business_status"] = (
                result.business_status.value if result.business_status else None
            )
            item["verification_status"] = (
                result.verification.status.value if result.verification else None
            )
            item["finding_count"] = len(result.findings)
            for view in result.artifacts:
                row = db.execute(
                    "SELECT fencing_token,digest,payload FROM publication_manifests "
                    "WHERE case_id=? AND run_id=?",
                    (job.case_id, str(run.run_id)),
                ).fetchone()
                if row is None:
                    raise EvidenceError("manifest_missing")
                manifest = CommittedManifest.model_validate_json(row[2])
                if (
                    manifest.fencing_token != run.fencing_token
                    or row[:2] != (manifest.fencing_token, manifest.manifest_digest)
                    or manifest.candidate.run != result.run
                    or manifest.candidate.result_version != run.result_version
                ):
                    raise EvidenceError("manifest_run_binding_mismatch")
                matches = [
                    a for a in manifest.candidate.artifacts if a.artifact_id == view.artifact_id
                ]
                if len(matches) != 1:
                    raise EvidenceError("manifest_artifact_mismatch")
                artifact = matches[0]
                object_row = db.execute(
                    "SELECT version,digest,size,body FROM publication_objects WHERE object_key=?",
                    (artifact.key,),
                ).fetchone()
                if object_row is None:
                    raise EvidenceError("artifact_bytes_missing")
                version, hashed, size, content = object_row
                measured = pdf_facts(content)
                if (
                    (version, hashed, size)
                    != (artifact.object_version, artifact.content_hash, artifact.size_bytes)
                    or measured["sha256"] != artifact.content_hash
                    or measured["page_count"] != artifact.page_count
                    or digest(artifact.key.encode() + b"\0" + content) != version
                    or view.content_hash != artifact.content_hash
                ):
                    raise EvidenceError("artifact_content_binding_mismatch")
                if {
                    (s.document_id, s.version, s.content_hash) for s in artifact.source_versions
                } != {(d.document_id, d.version, d.content_hash) for d in run.documents}:
                    raise EvidenceError("artifact_source_binding_mismatch")
                if (
                    not isinstance(view, FencedArtifactManifest)
                    or any(
                        getattr(view, name) != getattr(artifact, name)
                        for name in (
                            "contexts",
                            "field_ids",
                            "page_count",
                            "template_hash",
                            "field_map_hash",
                            "font_hash",
                            "writer_version",
                        )
                    )
                    or view.manifest_digest != manifest.manifest_digest
                ):
                    raise EvidenceError("public_manifest_projection_mismatch")
                reader = PdfReader(BytesIO(content))
                metadata = reader.metadata
                if (
                    metadata is None
                    or metadata.get("/AppraisalReviewWriterVersion") != artifact.writer_version
                    or decode(metadata.get("/AppraisalReviewFieldIds", "null"))
                    != list(artifact.field_ids)
                ):
                    raise EvidenceError("writer_metadata_mismatch")
                facts = measured | {
                    "artifact_id": str(artifact.artifact_id),
                    "manifest_sha256": manifest.manifest_digest,
                    "template_sha256": artifact.template_hash,
                    "field_map_sha256": artifact.field_map_hash,
                    "font_sha256": artifact.font_hash,
                    "context_count": len(artifact.contexts),
                    "field_count": len(artifact.field_ids),
                }
                item["artifacts"].append(facts)
                artifacts[str(artifact.artifact_id)] = (item, artifact, facts)
        item["writer_inputs"] = {"status": "not_observed"}
        if has_table(db, "synthetic_run_assets"):
            row = db.execute(
                "SELECT payload FROM synthetic_run_assets WHERE run_id=?", (str(run.run_id),)
            ).fetchone()
            if row is not None:
                assets = decode(row[0])
                asset_run = RunReference.model_validate(assets["run"])
                if asset_run.run_id != run.run_id or asset_run.revision != run.revision:
                    raise EvidenceError("writer_input_run_mismatch")
                config = LocalWriterConfiguration.model_validate_json(
                    json.dumps(assets["configuration"])
                )
                template = read_file(config.template_path, root)
                font = read_file(config.render.font_path, root)
                if (
                    digest(template) != config.template_policy.template_sha256
                    or digest(font) != config.render.approved_font_sha256
                ):
                    raise EvidenceError("writer_asset_policy_mismatch")
                evidence = assets.get("evidence")
                item["writer_inputs"] = {
                    "status": "stored_inputs_and_assets_observed",
                    "configuration_sha256": digest(
                        json.dumps(
                            assets["configuration"], sort_keys=True, separators=(",", ":")
                        ).encode()
                    ),
                    "request_sha256": digest(
                        json.dumps(
                            assets["request"], sort_keys=True, separators=(",", ":")
                        ).encode()
                    ),
                    "template_sha256": digest(template),
                    "font_sha256": digest(font),
                    "field_map_sha256": field_map_sha256(config.field_map),
                    "writer_evidence_observed": evidence is not None,
                }
                if evidence is not None:
                    output = read_file(Path(evidence["destination"]), root)
                    if (
                        str(asset_run.run_id) != evidence["run_id"]
                        or digest(output) != evidence["content_hash"]
                    ):
                        raise EvidenceError("writer_evidence_bytes_mismatch")
                    if any(
                        f["sha256"] != digest(output)
                        or f["template_sha256"] != digest(template)
                        or f["font_sha256"] != digest(font)
                        or f["field_map_sha256"] != evidence["field_map_hash"]
                        for f in item["artifacts"]
                    ):
                        raise EvidenceError("writer_evidence_publication_mismatch")
                    item["writer_inputs"]["output_sha256"] = digest(output)
        observations.append(item)
    return observations, artifacts


def collect_captures(
    path: Path | None,
    root: Path,
    jobs: list[dict[str, Any]],
    artifacts: dict[str, Any],
    code: dict[str, Any],
    configuration: str,
) -> dict[str, Any]:
    if path is None:
        return {"status": "not_observed", "records": []}
    raw = read_file(path, root)
    index = decode(raw)
    if index.get("schema_version") != "local-integration-captures-v1":
        raise EvidenceError("unsupported_capture_index")
    records = []
    for entry in index["records"]:
        body = read_file(path.parent / entry["body_file"], root)
        if digest(body) != entry["body_sha256"]:
            raise EvidenceError("capture_file_digest_mismatch")
        binding = next((j for j in jobs if j["job_id"] == entry["job_id"]), None)
        if binding is None or any(
            entry.get(k) != binding[k] for k in ("case_id", "run_id", "revision_id", "attempt_id")
        ):
            raise EvidenceError("capture_run_binding_mismatch")
        observed_at = entry["observed_at"]
        if type(observed_at) is not int or not 0 < observed_at <= int(time.time()) + 300:
            raise EvidenceError("capture_time_invalid")
        kind = entry["kind"]
        fact: dict[str, Any] = {
            "kind": kind,
            "job_id": binding["job_id"],
            "run_id": binding["run_id"],
            "body_sha256": digest(body),
            "byte_size": len(body),
            "declared_observed_at": observed_at,
            "capture_producer_authenticated": False,
            "declared_head_matches_collection": entry.get("source_head") == code["head"],
            "declared_config_matches_collection": entry.get("configuration_sha256")
            == configuration,
        }
        if "http_status" in entry or "http_method" in entry:
            status, method = entry.get("http_status"), entry.get("http_method")
            if (
                type(status) is not int
                or not 100 <= status <= 599
                or method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"}
            ):
                raise EvidenceError("capture_http_metadata_invalid")
            fact.update(declared_http_status=status, declared_http_method=method)
        if kind == "source_pdf":
            expected = entry["source_sha256"]
            if digest(body) != expected or not any(
                s["sha256"] == expected and s["bytes_observed"] for s in binding["sources"]
            ):
                raise EvidenceError("captured_source_mismatch")
            fact.update(pdf_facts(body))
        elif kind == "publication_pdf":
            owner, artifact, _ = artifacts[entry["artifact_id"]]
            if owner["job_id"] != binding["job_id"]:
                raise EvidenceError("capture_artifact_job_mismatch")
            if artifact.content_hash != digest(body):
                raise EvidenceError("captured_publication_mismatch")
            fact.update(pdf_facts(body))
        elif kind == "result_json":
            result = ServiceResult.model_validate_json(body)
            if content_digest(result) != binding.get("result_sha256"):
                raise EvidenceError("captured_result_mismatch")
        elif kind == "restore_json":
            manifest = FinalLocalManifest.model_validate(decode(body)["manifest"])
            owner, artifact, _ = artifacts[entry["artifact_id"]]
            if owner["job_id"] != binding["job_id"]:
                raise EvidenceError("capture_artifact_job_mismatch")
            restored = read_file(path.parent / entry["restored_pdf_file"], root)
            measured = pdf_facts(restored)
            if (
                str(manifest.case_id) != binding["case_id"]
                or str(manifest.run_id) != binding["run_id"]
                or str(manifest.revision_id) != binding["revision_id"]
                or manifest.input_artifact_digest != artifact.content_hash
                or manifest.final_digest != measured["sha256"]
                or manifest.byte_size != len(restored)
            ):
                raise EvidenceError("restore_binding_mismatch")
            fact.update(
                final_pdf=measured,
                restored_fields_count=len(manifest.restored_fields),
                omitted_fields_count=len(manifest.omitted_fields),
                original_values_independently_checked=False,
            )
        elif kind == "browser_trace":
            fact["content_interpreted"] = False
        else:
            raise EvidenceError("unsupported_capture_kind")
        records.append(fact)
    return {
        "status": "files_observed_producer_unattested",
        "index_sha256": digest(raw),
        "records": records,
    }


def main() -> int:
    try:
        parser = SafeParser(description=__doc__)
        parser.add_argument("--repository", type=Path, default=Path.cwd())
        parser.add_argument("--review-db", type=Path, required=True)
        parser.add_argument("--source-db", type=Path, action="append", default=[])
        parser.add_argument("--config", type=Path, action="append", default=[])
        parser.add_argument("--capture-index", type=Path)
        parser.add_argument("--output", type=Path, required=True)
        args = parser.parse_args()
        root = args.repository.absolute()
        confined(root, root)
        started = int(time.time())
        code = repository_state(root)
        configuration = digest(
            b"\n".join(sorted(bytes.fromhex(digest(read_file(p, root))) for p in args.config))
        )
        with ExitStack() as stack:
            review = snapshot(args.review_db, root, stack)
            source_dbs = [snapshot(p, root, stack) for p in args.source_db]
            sources, pinned = source_observations(source_dbs)
            jobs, artifacts = collect_jobs(review, sources, pinned, root)
            report = {
                "schema_version": "local-integration-evidence-v1",
                "assessment": "observations_only",
                "live_acceptance": False,
                "publication_gate_evaluated": False,
                "collection_started_at": started,
                "collection_finished_at": int(time.time()),
                "collector_sha256": digest(read_file(Path(__file__).absolute(), root)),
                "repository_at_collection": code,
                "runtime_source_head_attested": False,
                "configuration_sha256": configuration,
                "configuration_file_count": len(args.config),
                "review_database_snapshot_sha256": digest(review.serialize()),
                "source_database_snapshot_sha256": [digest(d.serialize()) for d in source_dbs],
                "jobs": jobs,
                "captures": collect_captures(
                    args.capture_index, root, jobs, artifacts, code, configuration
                ),
            }
        if repository_state(root) != code:
            raise EvidenceError("repository_changed_during_collection")
        report["collection_finished_at"] = int(time.time())
        output = confined(args.output, root)
        if "artifacts" not in output.relative_to(root).parts or output.parent.is_symlink():
            raise EvidenceError("output_must_be_under_artifacts")
        data = (json.dumps(report, sort_keys=True, indent=2) + "\n").encode()
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        print(
            json.dumps(
                {
                    "status": "collected",
                    "report_sha256": digest(data),
                    "job_count": len(jobs),
                    "live_acceptance": False,
                }
            )
        )
        return 0
    except EvidenceError as error:
        print(json.dumps({"status": "unavailable", "reason": str(error), "live_acceptance": False}))
    except Exception:
        print(
            json.dumps(
                {"status": "unavailable", "reason": "collection_failed", "live_acceptance": False}
            )
        )
    return 2


if __name__ == "__main__":
    sys.exit(main())
