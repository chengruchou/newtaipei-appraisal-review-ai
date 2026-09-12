"""Private SQLite publication using the existing review store's transaction domain.

The source callback runs outside transactions. All authority, pinned-source and
revocation checks run again under BEGIN IMMEDIATE with the manifest write.
Only explicitly configured synthetic composition may create exact approvals.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel

from appraisal_review.application.service_guards import Principal
from appraisal_review.domain.artifact_publication import (
    ArtifactKey,
    CommittedManifest,
    ManifestCandidate,
    PublicationError,
    PublishedArtifact,
    SourceVersion,
)
from appraisal_review.domain.factor_models import WorkflowStatus
from appraisal_review.domain.service_contracts import ActorReference, Permission, RunReference
from appraisal_review.ports.artifact_publication import PublicationAttempt


class ReviewDatabase(Protocol):
    """Coordinated integration-only seam; preserve the store's file identity checks."""

    def _connect(self) -> sqlite3.Connection: ...

    def _decode(self, payload: str) -> BaseModel: ...


SourceAuthorizer = Callable[[Principal, RunReference, tuple[SourceVersion, ...]], None]


@contextmanager
def _transaction(database: ReviewDatabase) -> Iterator[sqlite3.Connection]:
    connection = database._connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class _Pinned:
    job: dict[str, Any]
    run: dict[str, Any]
    attempt: dict[str, Any] | None
    digest: str
    epoch: int
    results: tuple[dict[str, Any], ...]


class SQLiteArtifactObjectStore:
    """Immutable local PDF BLOBs in the same private SQLite file, never local paths."""

    def __init__(self, review_store: ReviewDatabase, *, max_bytes: int = 67_108_864) -> None:
        if type(max_bytes) is not int or max_bytes < 1:
            raise ValueError("A positive output size limit is required")
        self.database, self.max_bytes = review_store, max_bytes
        with _transaction(self.database) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS publication_objects ("
                "object_key TEXT PRIMARY KEY, version TEXT NOT NULL, digest TEXT NOT NULL, "
                "size INTEGER NOT NULL, body BLOB NOT NULL)"
            )

    def create(self, key: str, data: bytes) -> str:
        ArtifactKey.parse(key)
        if not isinstance(data, bytes) or not 0 < len(data) <= self.max_bytes:
            raise PublicationError("artifact_mismatch")
        digest = hashlib.sha256(data).hexdigest()
        version = hashlib.sha256(key.encode() + b"\0" + data).hexdigest()
        with _transaction(self.database) as connection:
            row = connection.execute(
                "SELECT version,digest,size,body FROM publication_objects WHERE object_key=?",
                (key,),
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO publication_objects VALUES(?,?,?,?,?)",
                    (key, version, digest, len(data), data),
                )
            elif row != (version, digest, len(data), data):
                raise PublicationError("artifact_mismatch")
        return version

    def read(self, artifact: PublishedArtifact) -> bytes:
        artifact = PublishedArtifact.model_validate_json(artifact.model_dump_json())
        if artifact.size_bytes > self.max_bytes:
            raise PublicationError("artifact_mismatch")
        connection = self.database._connect()
        try:
            # Filter the stored byte length before reading a potentially corrupt large BLOB.
            row = connection.execute(
                "SELECT version,digest,size,body FROM publication_objects "
                "WHERE object_key=? AND length(body)<=?",
                (artifact.key, self.max_bytes),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise PublicationError("artifact_mismatch")
        version, digest, size, data = row
        if (
            not isinstance(data, bytes)
            or len(data) != size
            or size != artifact.size_bytes
            or version != artifact.object_version
            or digest != artifact.content_hash
            or hashlib.sha256(data).hexdigest() != digest
            or hashlib.sha256(artifact.key.encode() + b"\0" + data).hexdigest() != version
        ):
            raise PublicationError("artifact_mismatch")
        return data


class SQLiteManifestRepository:
    """Current review-state authority plus exact grants in one SQLite transaction.

    Grant methods are trusted composition methods, never public HTTP/model tools.
    No role or human approval is inferred from a candidate or a constructor flag.
    The flag only enables synthetic grant issuance for an already authorized owner.
    """

    def __init__(
        self,
        review_store: ReviewDatabase,
        *,
        source_authorizer: SourceAuthorizer,
        trusted_synthetic_approval: bool = False,
        approval_ttl_seconds: int = 900,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if type(approval_ttl_seconds) is not int or not 1 <= approval_ttl_seconds <= 900:
            raise ValueError("Approval lifetime must be between 1 and 900 seconds")
        self.database = review_store
        self.source_authorizer = source_authorizer
        self.trusted_synthetic_approval = trusted_synthetic_approval is True
        self.approval_ttl_seconds, self.clock = approval_ttl_seconds, clock
        with _transaction(self.database) as connection:
            self._state(connection)
            connection.execute(
                "CREATE TABLE IF NOT EXISTS publication_epochs ("
                "case_id TEXT PRIMARY KEY, epoch INTEGER NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS publication_access ("
                "case_id TEXT NOT NULL, actor_id TEXT NOT NULL, actor TEXT NOT NULL, "
                "permissions TEXT NOT NULL, expires_at INTEGER NOT NULL, active INTEGER NOT NULL, "
                "PRIMARY KEY(case_id,actor_id))"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS publication_grants ("
                "case_id TEXT NOT NULL, run_id TEXT NOT NULL, actor_id TEXT NOT NULL, "
                "digest TEXT NOT NULL, candidate TEXT NOT NULL, pin TEXT NOT NULL, "
                "epoch INTEGER NOT NULL, expires_at INTEGER NOT NULL, active INTEGER NOT NULL, "
                "PRIMARY KEY(case_id,run_id,actor_id))"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS publication_manifests ("
                "case_id TEXT NOT NULL, run_id TEXT NOT NULL, actor_id TEXT NOT NULL, "
                "fencing_token INTEGER NOT NULL, digest TEXT NOT NULL, payload TEXT NOT NULL, "
                "PRIMARY KEY(case_id,run_id))"
            )

    def _state(self, connection: sqlite3.Connection) -> dict[str, Any]:
        row = connection.execute("SELECT payload FROM review_state WHERE singleton=1").fetchone()
        if row is None:
            raise PublicationError("manifest_store_unavailable")
        # Reuse canonical typed decoding without importing or duplicating shared schemas.
        state = self.database._decode(row[0]).model_dump(mode="json")
        if state.get("schema_version") != "sqlite-review-v1":
            raise PublicationError("manifest_store_unavailable")
        return state

    @staticmethod
    def _epoch(connection: sqlite3.Connection, case_id: str) -> int:
        row = connection.execute(
            "SELECT epoch FROM publication_epochs WHERE case_id=?", (case_id,)
        ).fetchone()
        return 0 if row is None else int(row[0])

    def _pin(self, connection: sqlite3.Connection, candidate: ManifestCandidate) -> _Pinned:
        state = self._state(connection)
        matches = [r for r in state["runs"] if r["value"]["run_id"] == str(candidate.run.run_id)]
        if len(matches) != 1:
            raise PublicationError("stale_publication")
        job_id, run = matches[0]["job_id"], matches[0]["value"]
        job = state["jobs"].get(job_id)
        case_id = candidate.run.revision.case_id
        if (
            job is None
            or job["job_id"] != job_id
            or job["case_id"] != case_id
            or job["current_run_id"] != str(candidate.run.run_id)
            or run["revision"] != candidate.run.revision.model_dump(mode="json")
        ):
            raise PublicationError("stale_publication")
        sources = {(d["document_id"], d["version"], d["content_hash"]) for d in run["documents"]}
        if (
            not sources
            or len(sources) != len(run["documents"])
            or any(d["case_id"] != case_id for d in run["documents"])
            or any(
                len(a.source_versions) != len(sources)
                or {(s.document_id, s.version, s.content_hash) for s in a.source_versions}
                != sources
                for a in candidate.artifacts
            )
        ):
            raise PublicationError("artifact_evidence_missing")
        attempts = [
            a
            for a in state["attempts"]
            if a["job_id"] == job_id
            and a["run_id"] == str(candidate.run.run_id)
            and a["attempt_id"] == str(candidate.run.attempt_id)
        ]
        if len(attempts) > 1:
            raise PublicationError("stale_publication")
        pin = hashlib.sha256(
            _json(
                {
                    "job_id": job_id,
                    "principal_id": job["principal_id"],
                    "current_run_id": job["current_run_id"],
                    "revision": run["revision"],
                    "documents": run["documents"],
                    "payload_digest": run["payload_digest"],
                }
            ).encode()
        ).hexdigest()
        return _Pinned(
            job,
            run,
            attempts[0] if attempts else None,
            pin,
            self._epoch(connection, case_id),
            tuple(r for r in state["results"] if r["run_id"] == str(candidate.run.run_id)),
        )

    def _before(self, candidate: ManifestCandidate) -> _Pinned:
        connection = self.database._connect()
        try:
            connection.execute("BEGIN")
            return self._pin(connection, candidate)
        finally:
            connection.rollback()
            connection.close()

    def _sources(self, principal: Principal, candidate: ManifestCandidate) -> None:
        try:
            result = self.source_authorizer(
                principal, candidate.run, candidate.artifacts[0].source_versions
            )
            if result is not None:
                raise PublicationError("publication_unauthorized")
        except Exception:
            raise PublicationError("publication_unauthorized") from None

    @staticmethod
    def _owner(principal: Principal, pin: _Pinned) -> None:
        if principal.actor.kind == "model" or principal.actor.actor_id != pin.job["principal_id"]:
            raise PublicationError("publication_unauthorized")

    @staticmethod
    def _candidate(candidate: ManifestCandidate) -> ManifestCandidate:
        candidate = ManifestCandidate.model_validate_json(candidate.model_dump_json())
        if candidate.review_status != WorkflowStatus.COMPLETED:
            raise PublicationError("review_not_publishable")
        if any(
            not a.font_hash
            or not a.object_version
            or a.object_version == "null"
            or not a.source_versions
            for a in candidate.artifacts
        ):
            raise PublicationError("artifact_evidence_missing")
        return candidate

    def _live(
        self, pin: _Pinned, candidate: ManifestCandidate, attempt: PublicationAttempt, token: int
    ) -> None:
        run, job, stored = pin.run, pin.job, pin.attempt
        if (
            type(token) is not int
            or token < 1
            or type(attempt.expected_result_version) is not int
            or attempt.expected_result_version < 0
            or job["job_id"] != str(attempt.job_id)
            or job["status"] != "running"
            or job["cancel_requested"] is not False
            or job["open_task_ids"]
            or run["run_status"] != "running"
            or run["attempt_id"] != str(candidate.run.attempt_id)
            or run["lease_owner"] != str(attempt.owner)
            or run["lease_expires_at"] is None
            or run["lease_expires_at"] <= int(self.clock())
            or run["fencing_token"] != token
            or run["result_version"] != attempt.expected_result_version
            or candidate.result_version != attempt.expected_result_version + 1
            or stored is None
            or stored["owner"] != str(attempt.owner)
            or stored["fencing_token"] != token
            or stored["outcome"] != "running"
        ):
            raise PublicationError("stale_publication")

    def _readable(self, pin: _Pinned, result: CommittedManifest) -> None:
        candidate = result.candidate
        if pin.job["status"] == "running":
            if not isinstance(pin.run["lease_owner"], str):
                raise PublicationError("stale_publication")
            self._live(
                pin,
                candidate,
                PublicationAttempt(
                    job_id=UUID(pin.job["job_id"]),
                    owner=UUID(pin.run["lease_owner"]),
                    expected_result_version=candidate.result_version - 1,
                ),
                result.fencing_token,
            )
            return
        references = [r for r in pin.results if r["result_version"] == candidate.result_version]
        if (
            pin.job["status"] != "succeeded"
            or pin.job["cancel_requested"] is not False
            or pin.run["run_status"] != "succeeded"
            or pin.job["open_task_ids"]
            or pin.run["result_version"] != candidate.result_version
            or pin.run["fencing_token"] != result.fencing_token
            or pin.attempt is None
            or pin.attempt["outcome"] != "publish_result"
            or pin.attempt["fencing_token"] != result.fencing_token
            or len(references) != 1
            or references[0]["fencing_token"] != result.fencing_token
            or references[0]["execution_status"] != "succeeded"
            or not {str(a.artifact_id) for a in candidate.artifacts}.issubset(
                references[0]["artifact_ids"]
            )
        ):
            raise PublicationError("stale_publication")

    def _access(
        self,
        connection: sqlite3.Connection,
        principal: Principal,
        case_id: str,
        permission: Permission,
    ) -> int:
        principal.require(case_id, permission)
        if principal.actor.kind == "model":
            raise PublicationError("publication_unauthorized")
        row = connection.execute(
            "SELECT permissions,expires_at,active FROM publication_access "
            "WHERE case_id=? AND actor_id=?",
            (case_id, principal.actor.actor_id),
        ).fetchone()
        if (
            row is None
            or row[2] != 1
            or row[1] <= int(self.clock())
            or permission.value not in json.loads(row[0])
        ):
            raise PublicationError("publication_unauthorized")
        return int(row[1])

    def authorize(self, principal: Principal, case_id: str, permission: Permission) -> int:
        connection = self.database._connect()
        snapshots: list[tuple[ManifestCandidate, _Pinned]] = []
        try:
            connection.execute("BEGIN")
            expires = self._access(connection, principal, case_id, permission)
            if permission == Permission.REVIEW:
                state = self._state(connection)
                current_runs = {
                    job["current_run_id"]
                    for job in state["jobs"].values()
                    if job["case_id"] == case_id and job["principal_id"] == principal.actor.actor_id
                }
                rows = connection.execute(
                    "SELECT fencing_token,digest,payload FROM publication_manifests "
                    "WHERE case_id=? AND actor_id=?",
                    (case_id, principal.actor.actor_id),
                ).fetchall()
                for row in rows:
                    manifest = self._manifest(row)
                    if manifest is None or str(manifest.candidate.run.run_id) not in current_runs:
                        continue
                    pin = self._pin(connection, manifest.candidate)
                    self._owner(principal, pin)
                    self._grant(connection, manifest.candidate, principal, pin)
                    snapshots.append((manifest.candidate, pin))
        finally:
            connection.rollback()
            connection.close()
        # The principal is the current authenticated request principal. Do not
        # reconstruct old permissions from the stored publisher/approval identity.
        for candidate, _ in snapshots:
            self._sources(principal, candidate)
        if snapshots:
            with _transaction(self.database) as connection:
                expires = self._access(connection, principal, case_id, permission)
                for candidate, before in snapshots:
                    current = self._pin(connection, candidate)
                    if (before.digest, before.epoch) != (current.digest, current.epoch):
                        raise PublicationError("stale_publication")
                    self._owner(principal, current)
                    expires = min(expires, self._grant(connection, candidate, principal, current))
        return expires

    def approve_exact(self, candidate: ManifestCandidate, principal: Principal) -> str:
        """Trusted synthetic grant only; caller already verified actual Controller/assets.

        Never expose as a request/model action. This creates no material approval,
        confirmation, human receipt or increased confidence.
        """
        if not self.trusted_synthetic_approval:
            raise PublicationError("approval_disabled")
        candidate = self._candidate(candidate)
        principal.require(candidate.run.revision.case_id, Permission.PUBLISH)
        before = self._before(candidate)
        if before.epoch != 0:
            raise PublicationError("publication_unauthorized")
        self._owner(principal, before)
        self._sources(principal, candidate)
        with _transaction(self.database) as connection:
            current = self._pin(connection, candidate)
            if (before.digest, before.epoch) != (current.digest, current.epoch):
                raise PublicationError("stale_publication")
            self._owner(principal, current)
            if not isinstance(current.run["lease_owner"], str):
                raise PublicationError("stale_publication")
            self._live(
                current,
                candidate,
                PublicationAttempt(
                    job_id=UUID(current.job["job_id"]),
                    owner=UUID(current.run["lease_owner"]),
                    expected_result_version=current.run["result_version"],
                ),
                current.run["fencing_token"],
            )
            case_id = candidate.run.revision.case_id
            existing = self._manifest(
                connection.execute(
                    "SELECT fencing_token,digest,payload FROM publication_manifests "
                    "WHERE case_id=? AND run_id=?",
                    (case_id, str(candidate.run.run_id)),
                ).fetchone()
            )
            if existing is not None:
                if existing.fencing_token > current.run["fencing_token"]:
                    raise PublicationError("stale_publication")
                if (
                    existing.fencing_token == current.run["fencing_token"]
                    and existing.candidate != candidate
                ):
                    raise PublicationError("manifest_conflict")
            self._issue(connection, candidate, principal, current)
        return candidate.digest()

    def _issue(
        self,
        connection: sqlite3.Connection,
        candidate: ManifestCandidate,
        principal: Principal,
        pin: _Pinned,
    ) -> int:
        """Write one bounded access and download window. The cap is never exceeded."""
        case_id = candidate.run.revision.case_id
        expires = int(self.clock()) + self.approval_ttl_seconds
        connection.execute(
            "INSERT INTO publication_access VALUES(?,?,?,?,?,1) ON CONFLICT(case_id,actor_id) "
            "DO UPDATE SET actor=excluded.actor,permissions=excluded.permissions,"
            "expires_at=excluded.expires_at,active=1",
            (
                case_id,
                principal.actor.actor_id,
                principal.actor.model_dump_json(),
                _json(sorted(p.value for p in principal.permissions)),
                expires,
            ),
        )
        connection.execute(
            "INSERT INTO publication_grants VALUES(?,?,?,?,?,?,?,?,1) "
            "ON CONFLICT(case_id,run_id,actor_id) DO UPDATE SET digest=excluded.digest,"
            "candidate=excluded.candidate,pin=excluded.pin,epoch=excluded.epoch,"
            "expires_at=excluded.expires_at,active=1",
            (
                case_id,
                str(candidate.run.run_id),
                principal.actor.actor_id,
                candidate.digest(),
                candidate.model_dump_json(),
                pin.digest,
                pin.epoch,
                expires,
            ),
        )
        return expires

    def _renewable(
        self,
        connection: sqlite3.Connection,
        candidate: ManifestCandidate,
        principal: Principal,
        pin: _Pinned,
    ) -> None:
        """Accept a lapsed window, refuse a revoked or superseded one.

        This is deliberately not _grant: the expiry is the one thing being renewed. Every
        other condition still has to hold, and `active` is what keeps revoke() final -
        a revoked grant row stays revoked and can never be renewed back into existence.
        """
        row = connection.execute(
            "SELECT digest,pin,epoch,active FROM publication_grants "
            "WHERE case_id=? AND run_id=? AND actor_id=?",
            (candidate.run.revision.case_id, str(candidate.run.run_id), principal.actor.actor_id),
        ).fetchone()
        if row is None:
            raise PublicationError("unpublished")
        if row[3] != 1:
            raise PublicationError("publication_revoked")
        if tuple(row[:3]) != (candidate.digest(), pin.digest, pin.epoch):
            raise PublicationError("stale_publication")

    def reauthorize_download(self, principal: Principal, case_id: str) -> int:
        """Renew a lapsed download window for output that is already committed.

        A window closing is not a change of authority, so a reviewer should not have to
        re-run a case to fetch the same verified bytes again. Nothing is regenerated and no
        synthetic approval is involved: the committed manifest, the run's current ownership,
        the pinned sources and the case permission are all rechecked against the current
        principal, and the new window is bounded by the same lifetime as the original.
        """
        principal.require(case_id, Permission.REVIEW)
        if principal.actor.kind == "model":
            raise PublicationError("publication_unauthorized")
        pending: list[tuple[ManifestCandidate, _Pinned]] = []
        connection = self.database._connect()
        try:
            connection.execute("BEGIN")
            state = self._state(connection)
            current_runs = {
                job["current_run_id"]
                for job in state["jobs"].values()
                if job["case_id"] == case_id and job["principal_id"] == principal.actor.actor_id
            }
            rows = connection.execute(
                "SELECT fencing_token,digest,payload FROM publication_manifests "
                "WHERE case_id=? AND actor_id=?",
                (case_id, principal.actor.actor_id),
            ).fetchall()
            for row in rows:
                manifest = self._manifest(row)
                if manifest is None or str(manifest.candidate.run.run_id) not in current_runs:
                    continue
                pin = self._pin(connection, manifest.candidate)
                self._owner(principal, pin)
                self._renewable(connection, manifest.candidate, principal, pin)
                pending.append((manifest.candidate, pin))
        finally:
            connection.rollback()
            connection.close()
        if not pending:
            raise PublicationError("unpublished")
        # Source admission is rechecked outside the read transaction, exactly as authorize
        # does, so a withdrawn source still blocks a renewal.
        for candidate, _ in pending:
            self._sources(principal, candidate)
        renewed: list[int] = []
        with _transaction(self.database) as connection:
            for candidate, before in pending:
                current = self._pin(connection, candidate)
                if (before.digest, before.epoch) != (current.digest, current.epoch):
                    raise PublicationError("stale_publication")
                self._owner(principal, current)
                self._renewable(connection, candidate, principal, current)
                renewed.append(self._issue(connection, candidate, principal, current))
        return min(renewed)

    def revoke(self, case_id: str) -> None:
        """Trusted source/access revocation; invalidate in-flight callbacks durably."""
        with _transaction(self.database) as connection:
            connection.execute(
                "INSERT INTO publication_epochs VALUES(?,1) ON CONFLICT(case_id) "
                "DO UPDATE SET epoch=publication_epochs.epoch+1",
                (case_id,),
            )
            connection.execute("UPDATE publication_grants SET active=0 WHERE case_id=?", (case_id,))
            connection.execute("UPDATE publication_access SET active=0 WHERE case_id=?", (case_id,))

    def _grant(
        self,
        connection: sqlite3.Connection,
        candidate: ManifestCandidate,
        principal: Principal,
        pin: _Pinned,
    ) -> int:
        row = connection.execute(
            "SELECT digest,pin,epoch,expires_at,active FROM publication_grants "
            "WHERE case_id=? AND run_id=? AND actor_id=?",
            (candidate.run.revision.case_id, str(candidate.run.run_id), principal.actor.actor_id),
        ).fetchone()
        if (
            row is None
            or row[:3] != (candidate.digest(), pin.digest, pin.epoch)
            or row[3] <= int(self.clock())
            or row[4] != 1
        ):
            raise PublicationError("publication_unauthorized")
        return int(row[3])

    @staticmethod
    def _manifest(row: tuple[Any, ...] | None) -> CommittedManifest | None:
        if row is None:
            return None
        try:
            result = CommittedManifest.model_validate_json(row[2])
            if (result.fencing_token, result.manifest_digest) != row[:2]:
                raise ValueError("Manifest binding mismatch")
            return result
        except (ValueError, TypeError):
            raise PublicationError("manifest_corrupt") from None

    def commit(
        self,
        candidate: ManifestCandidate,
        *,
        fencing_token: int,
        principal: Principal,
        attempt: PublicationAttempt,
    ) -> CommittedManifest:
        candidate = self._candidate(candidate)
        case_id, run_id = candidate.run.revision.case_id, str(candidate.run.run_id)
        self.authorize(principal, case_id, Permission.PUBLISH)
        before = self._before(candidate)
        self._owner(principal, before)
        self._sources(principal, candidate)
        with _transaction(self.database) as connection:
            current = self._pin(connection, candidate)
            if (before.digest, before.epoch) != (current.digest, current.epoch):
                raise PublicationError("stale_publication")
            self._owner(principal, current)
            self._live(current, candidate, attempt, fencing_token)
            self._access(connection, principal, case_id, Permission.PUBLISH)
            existing = self._manifest(
                connection.execute(
                    "SELECT fencing_token,digest,payload FROM publication_manifests "
                    "WHERE case_id=? AND run_id=?",
                    (case_id, run_id),
                ).fetchone()
            )
            if existing is not None:
                if existing.fencing_token > fencing_token:
                    raise PublicationError("stale_publication")
                if existing.fencing_token == fencing_token:
                    if existing.candidate != candidate:
                        raise PublicationError("manifest_conflict")
                    self._grant(connection, candidate, principal, current)
                    return existing
            self._grant(connection, candidate, principal, current)
            result = CommittedManifest(
                candidate=candidate, fencing_token=fencing_token, manifest_digest=candidate.digest()
            )
            connection.execute(
                "INSERT INTO publication_manifests VALUES(?,?,?,?,?,?) ON CONFLICT(case_id,run_id) "
                "DO UPDATE SET actor_id=excluded.actor_id,fencing_token=excluded.fencing_token,"
                "digest=excluded.digest,payload=excluded.payload",
                (
                    case_id,
                    run_id,
                    principal.actor.actor_id,
                    fencing_token,
                    result.manifest_digest,
                    result.model_dump_json(),
                ),
            )
            return result

    def read(self, case_id: str, run_id: UUID) -> CommittedManifest | None:
        connection = self.database._connect()
        try:
            connection.execute("BEGIN")
            row = connection.execute(
                "SELECT fencing_token,digest,payload,actor_id FROM publication_manifests "
                "WHERE case_id=? AND run_id=?",
                (case_id, str(run_id)),
            ).fetchone()
            result = self._manifest(None if row is None else row[:3])
            if result is None:
                return None
            if (
                result.candidate.run.run_id != run_id
                or result.candidate.run.revision.case_id != case_id
            ):
                raise PublicationError("manifest_corrupt")
            actor_row = connection.execute(
                "SELECT actor,permissions FROM publication_access WHERE case_id=? AND actor_id=?",
                (case_id, row[3]),
            ).fetchone()
            if actor_row is None:
                raise PublicationError("publication_unauthorized")
            principal = Principal(
                actor=ActorReference.model_validate_json(actor_row[0]),
                case_ids=frozenset({case_id}),
                permissions=frozenset(Permission(p) for p in json.loads(actor_row[1])),
            )
            before = self._pin(connection, result.candidate)
            self._grant(connection, result.candidate, principal, before)
            self._readable(before, result)
        finally:
            connection.rollback()
            connection.close()
        with _transaction(self.database) as connection:
            current = self._pin(connection, result.candidate)
            if (before.digest, before.epoch) != (current.digest, current.epoch):
                raise PublicationError("stale_publication")
            self._owner(principal, current)
            self._access(connection, principal, case_id, Permission.REVIEW)
            self._grant(connection, result.candidate, principal, current)
            self._readable(current, result)
            latest = connection.execute(
                "SELECT fencing_token,digest,payload FROM publication_manifests "
                "WHERE case_id=? AND run_id=?",
                (case_id, str(run_id)),
            ).fetchone()
            if self._manifest(latest) != result:
                raise PublicationError("stale_publication")
            return result
