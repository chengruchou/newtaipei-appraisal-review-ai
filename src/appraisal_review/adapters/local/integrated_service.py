"""Configured loopback service, durable jobs and the canonical human-task API.

Authentication and execution are explicit constructor dependencies. A synthetic
launcher supplies its own isolated assets; this module never invents approvals.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import time
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager, closing, suppress
from contextvars import ContextVar
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast, get_args
from uuid import UUID, uuid4

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import RequestResponseEndpoint

from appraisal_review.adapters.local.approval_store import SQLiteApprovalStore
from appraisal_review.adapters.local.artifact_publication import CommittedResultResolver
from appraisal_review.adapters.local.export_store import SQLiteExportStore
from appraisal_review.adapters.local.sqlite_review_store import SQLiteReviewStore
from appraisal_review.api.app import create_app
from appraisal_review.application.exports import (
    ExportAssets,
    ExportService,
    SnapshotProvider,
    WorkbookConverter,
    WorkbookFiller,
)
from appraisal_review.application.human_tasks import HumanTaskService
from appraisal_review.application.outbox import DispatchMessage, JobReconciler, OutboxDispatcher
from appraisal_review.application.report_approvals import ReportApprovalService
from appraisal_review.application.report_readiness import ReadinessPolicy
from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.application.runtime_sources import SnapshotJobService, source_fault
from appraisal_review.application.runtime_worker import (
    ExecutedReview,
    ReviewExecution,
    RuntimeWorker,
)
from appraisal_review.application.service_guards import Principal, ServiceFault
from appraisal_review.domain.artifact_publication import PublicationError
from appraisal_review.domain.document_transfer import DocumentFault, DocumentOperation
from appraisal_review.domain.official_export import ExportOperation
from appraisal_review.domain.service_contracts import (
    DocumentReference,
    MaterialRevision,
    Permission,
    RevisionReference,
    ServiceErrorCode,
    ServiceResult,
)
from appraisal_review.ports.content import ContentMediaType, DeliveredContent
from appraisal_review.ports.jobs import ClaimedAttempt, JobRecord
from appraisal_review.ports.runtime_documents import RuntimeDocuments

_request_principal: ContextVar[Principal | None] = ContextVar("review_principal", default=None)
_request_token: ContextVar[str | None] = ContextVar("review_session_token", default=None)


def _write_private(path: Path, value: object) -> None:
    """Atomically replace an owned private file inside the private data directory."""
    temporary = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class LocalDirectory:
    """Trusted session configuration; requests cannot supply actor or permission fields.

    A session value may carry an expiry (epoch seconds); ``None`` keeps the legacy
    non-expiring behavior, so baked fixture tokens continue to authenticate. Revoked
    token digests optionally persist to ``revocations_path`` and survive a reload of
    the directory from bootstrap state; without a path revocation is process-lifetime.
    """

    def __init__(
        self,
        sessions: Mapping[str, Principal | tuple[Principal, int | None]],
        *,
        clock: Callable[[], float] = time.time,
        revocations_path: Path | None = None,
    ) -> None:
        if not sessions or any(len(token) < 32 for token in sessions):
            raise ValueError("Explicit server-issued local sessions are required")
        self._sessions: dict[str, Principal] = {}
        self._expiries: dict[str, int | None] = {}
        self._clock = clock
        self._revocations_path = revocations_path
        self._revoked: set[str] = set()
        if revocations_path is not None and revocations_path.exists():
            loaded = json.loads(revocations_path.read_text())
            if not isinstance(loaded, list) or any(type(item) is not str for item in loaded):
                raise ValueError("The revocation list must be a JSON array of token digests")
            self._revoked = set(loaded)
        for token, value in sessions.items():
            principal, expires_at = value if isinstance(value, tuple) else (value, None)
            self.add_session(token, principal, expires_at)

    def add_session(self, token: str, principal: Principal, expires_at: int | None) -> None:
        """Trusted local setup only; tokens are server-minted, never request-chosen."""
        if len(token) < 32:
            raise ValueError("Explicit server-issued local sessions are required")
        if expires_at is not None and expires_at <= 0:
            raise ValueError("A session expiry must be a positive epoch second")
        self._sessions[token] = principal
        self._expiries[token] = expires_at

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def _inactive(self, token: str) -> bool:
        if self._digest(token) in self._revoked:
            return True
        expires_at = self._expiries.get(token)
        return expires_at is not None and self._clock() >= expires_at

    def authenticate(self, header: str) -> Principal:
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer":
            raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
        for expected, principal in self._sessions.items():
            if hmac.compare_digest(expected, token):
                # Expired or revoked sessions answer exactly like unknown tokens.
                if self._inactive(expected):
                    break
                return principal
        raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)

    def session_view(self, token: str) -> tuple[Principal, int | None]:
        """Post-authentication projection of the presented token; never echoes it."""
        for expected, principal in self._sessions.items():
            if hmac.compare_digest(expected, token) and not self._inactive(expected):
                return principal, self._expiries.get(expected)
        raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)

    def revoke_token(self, token: str) -> None:
        """Revoke the exact presented bearer token, durably when a path is configured."""
        self._revoked.add(self._digest(token))
        self._sessions.pop(token, None)
        self._expiries.pop(token, None)
        if self._revocations_path is not None:
            _write_private(self._revocations_path, sorted(self._revoked))

    async def read(self, principal_id: str, case_id: str) -> Principal:
        for token, principal in self._sessions.items():
            if self._inactive(token):
                continue
            if principal.actor.actor_id == principal_id and case_id in principal.case_ids:
                principal.require(case_id, Permission.REVIEW)
                return principal
        raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)

    def revoke(self, actor_id: str, before: Callable[[], None]) -> None:
        """Trusted administration only: fence publications before removing sessions."""
        before()
        self._sessions = {
            token: p for token, p in self._sessions.items() if p.actor.actor_id != actor_id
        }
        self._expiries = {
            token: expiry for token, expiry in self._expiries.items() if token in self._sessions
        }

    def grant_case(self, actor_id: str, case_id: str) -> Principal:
        """Trusted local setup only; retain existing permissions and actor identity."""
        if str(UUID(case_id)) != case_id or UUID(case_id).version != 4:
            raise ValueError("A canonical case identifier is required")
        updated = None
        for token, principal in tuple(self._sessions.items()):
            if principal.actor.actor_id == actor_id:
                if principal.actor.kind == "model":
                    raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
                updated = replace(principal, case_ids=principal.case_ids | {case_id})
                self._sessions[token] = updated
        if updated is None:
            raise ServiceFault(ServiceErrorCode.NOT_FOUND)
        return updated

    async def current_principal(self) -> Principal:
        principal = _request_principal.get()
        if principal is None:
            raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
        return principal


class LocalMaterialCatalog:
    """Immutable prepared inputs; subsequent revisions belong to the task transaction."""

    def __init__(self, store: SQLiteReviewStore) -> None:
        self.store = store
        with closing(store._connect()) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS prepared_materials "
                "(case_id TEXT NOT NULL, revision_id TEXT NOT NULL, principal_id TEXT NOT NULL, "
                "material TEXT NOT NULL, revision TEXT NOT NULL, "
                "PRIMARY KEY(case_id, revision_id))"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS admitted_documents "
                "(document_id TEXT NOT NULL, version TEXT NOT NULL, reference TEXT NOT NULL, "
                "PRIMARY KEY(document_id, version))"
            )

    def register(self, principal: Principal, snapshot: RevisionSnapshot) -> None:
        revision = snapshot.revision
        principal.require(revision.reference.case_id, Permission.REVIEW)
        with closing(self.store._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT principal_id, material, revision FROM prepared_materials "
                    "WHERE case_id=? AND revision_id=?",
                    (revision.reference.case_id, revision.reference.revision_id),
                ).fetchone()
                expected = (
                    principal.actor.actor_id,
                    snapshot.material.model_dump_json(),
                    revision.model_dump_json(),
                )
                if row is not None and tuple(row) != expected:
                    raise ServiceFault(ServiceErrorCode.CONFLICT)
                connection.execute(
                    "INSERT OR IGNORE INTO prepared_materials VALUES(?,?,?,?,?)",
                    (revision.reference.case_id, revision.reference.revision_id, *expected),
                )
                for reference in revision.documents:
                    existing = connection.execute(
                        "SELECT reference FROM admitted_documents "
                        "WHERE document_id=? AND version=?",
                        (reference.document_id, reference.version),
                    ).fetchone()
                    if existing and DocumentReference.model_validate_json(existing[0]) != reference:
                        raise ServiceFault(ServiceErrorCode.CONFLICT)
                    connection.execute(
                        "INSERT OR IGNORE INTO admitted_documents VALUES(?,?,?)",
                        (reference.document_id, reference.version, reference.model_dump_json()),
                    )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    async def snapshot(
        self, principal: Principal, reference: RevisionReference
    ) -> RevisionSnapshot:
        principal.require(reference.case_id, Permission.REVIEW)
        stored = await self.store.read_snapshot(revision=reference)
        if stored is not None:
            return stored
        with closing(self.store._connect()) as connection:
            row = connection.execute(
                "SELECT principal_id, material, revision FROM prepared_materials "
                "WHERE case_id=? AND revision_id=?",
                (reference.case_id, reference.revision_id),
            ).fetchone()
        if row is None or row[0] != principal.actor.actor_id:
            raise ServiceFault(ServiceErrorCode.NOT_FOUND)
        snapshot = RevisionSnapshot(row[1], row[2])
        revision = snapshot.revision
        rebuilt = RevisionSnapshot.capture(
            snapshot.material,
            revision.reference.revision_id,
            parent=revision.parent,
            changes=revision.changes,
        )
        if revision.reference != reference or rebuilt.revision != revision:
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        return rebuilt

    async def read(self, principal: Principal, reference: RevisionReference) -> MaterialRevision:
        return (await self.snapshot(principal, reference)).revision

    def source(self, document_id: str, version: str, content_hash: str) -> DocumentReference:
        with closing(self.store._connect()) as connection:
            row = connection.execute(
                "SELECT reference FROM admitted_documents WHERE document_id=? AND version=?",
                (document_id, version),
            ).fetchone()
        if row is None:
            raise ServiceFault(ServiceErrorCode.NOT_FOUND)
        reference = DocumentReference.model_validate_json(row[0])
        if reference.content_hash != content_hash:
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        return reference


@dataclass
class LocalWorkbenchReadAccess:
    directory: LocalDirectory
    catalog: LocalMaterialCatalog
    documents: RuntimeDocuments

    async def read(self, principal: Principal, reference: RevisionReference) -> RevisionSnapshot:
        current = await self.directory.read(principal.actor.actor_id, reference.case_id)
        if current.actor != principal.actor:
            raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
        snapshot = await self.catalog.snapshot(current, reference)
        try:
            for document in snapshot.revision.documents:
                await asyncio.to_thread(self.documents.read, current, document)
        except DocumentFault as error:
            raise source_fault(error) from None
        latest = await self.directory.read(principal.actor.actor_id, reference.case_id)
        if latest != current:
            raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
        return snapshot

    async def require_current(
        self, principal: Principal, references: tuple[RevisionReference, ...]
    ) -> None:
        snapshots = [await self.catalog.snapshot(principal, reference) for reference in references]
        for case_id in {reference.case_id for reference in references}:
            latest = await self.directory.read(principal.actor.actor_id, case_id)
            if latest != principal:
                raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
        # No await after the last principal refresh: independent purpose revocation
        # must also fence the response, even when case membership remains unchanged.
        try:
            for snapshot in snapshots:
                for document in snapshot.revision.documents:
                    self.documents.authorization.require(
                        principal, document.case_id, document.purpose, DocumentOperation.READ
                    )
        except DocumentFault as error:
            raise source_fault(error) from None


class LocalWorkbenchJobService(SnapshotJobService):
    workbench_access: LocalWorkbenchReadAccess

    async def result(self, principal: Principal, job_id: UUID) -> ServiceResult:
        record = await self._authorized(principal, job_id)
        reference = record.current_run.revision
        await self.workbench_access.read(principal, reference)
        result = await super().result(principal, job_id)
        if (
            result.run.revision != reference
            or result.run.run_id != record.current_run.run_id
            or await self._authorized(principal, job_id) != record
        ):
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        await self.workbench_access.require_current(principal, (reference,))
        return result


@dataclass
class CurrentSourceExecution:
    execution: ReviewExecution
    directory: LocalDirectory
    catalog: LocalMaterialCatalog
    documents: RuntimeDocuments
    store: SQLiteReviewStore

    async def execute(self, record: JobRecord, attempt: ClaimedAttempt) -> ExecutedReview:
        principal = await self.directory.read(record.principal_id, record.case_id)
        snapshot = await self.catalog.snapshot(principal, record.current_run.revision)
        # A response transaction schedules a new run. Pin its new exact document set
        # before execution; never reuse the old run's C2 snapshot or approvals.
        try:
            await asyncio.to_thread(
                self.documents.create_snapshot, principal, record.current_run, snapshot.revision
            )
            for reference in snapshot.revision.documents:
                await asyncio.to_thread(
                    self.documents.read_snapshot, principal, record.current_run, reference
                )
            from appraisal_review.adapters.local.workflow_runtime import SQLiteExecutionAuthority

            execution_authority = SQLiteExecutionAuthority(
                self.store, self.documents, self.directory
            )
            await execution_authority.register(record, attempt, snapshot, ())
            result = await self.execution.execute(record, attempt)
            principal = await self.directory.read(record.principal_id, record.case_id)
            await execution_authority.require_current(principal, record, attempt, snapshot)
            return result
        except DocumentFault as error:
            raise source_fault(error) from None


class SQLiteDispatchQueue:
    """Durable local queue: handoff survives a crash after outbox acknowledgment."""

    def __init__(self, store: SQLiteReviewStore) -> None:
        self.store = store
        with closing(store._connect()) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS local_dispatch_queue "
                "(token TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )

    @staticmethod
    def _payload(message: DispatchMessage) -> str:
        return json.dumps(
            {
                "job_id": str(message.job_id),
                "run_id": str(message.run_id),
                "outbox_seq": message.outbox_seq,
                "dispatch_token": str(message.dispatch_token),
                "enqueued_at": message.enqueued_at,
                "schema_version": message.schema_version,
            },
            sort_keys=True,
        )

    @staticmethod
    def _decode(token: str, payload: str) -> DispatchMessage:
        try:
            item = json.loads(payload)
            if (
                not isinstance(item, dict)
                or set(item)
                != {
                    "job_id",
                    "run_id",
                    "outbox_seq",
                    "dispatch_token",
                    "enqueued_at",
                    "schema_version",
                }
                or item["schema_version"] != "job-dispatch-v1"
                or item["dispatch_token"] != token
                or type(item["outbox_seq"]) is not int
                or item["outbox_seq"] < 1
                or type(item["enqueued_at"]) is not int
                or item["enqueued_at"] < 0
            ):
                raise ServiceFault(ServiceErrorCode.CONFLICT)
            for key in ("job_id", "run_id", "dispatch_token"):
                identity = UUID(item[key])
                if str(identity) != item[key]:
                    raise ServiceFault(ServiceErrorCode.CONFLICT)
                item[key] = identity
            return DispatchMessage(**item)
        except (ValueError, TypeError, AttributeError):
            raise ServiceFault(ServiceErrorCode.CONFLICT) from None

    async def send(self, message: DispatchMessage) -> None:
        token, payload = str(message.dispatch_token), self._payload(message)
        checked = self._decode(token, payload)
        with closing(self.store._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT payload FROM local_dispatch_queue WHERE token=?", (token,)
                ).fetchone()
                if row is not None:
                    existing = self._decode(token, row[0])
                    # Outbox redelivery regenerates its send time. Keep the first
                    # durable timestamp while requiring exact dispatch identity.
                    if replace(existing, enqueued_at=checked.enqueued_at) != checked:
                        raise ServiceFault(ServiceErrorCode.CONFLICT)
                else:
                    connection.execute(
                        "INSERT INTO local_dispatch_queue VALUES(?,?)", (token, payload)
                    )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def pending(self) -> tuple[DispatchMessage, ...]:
        with closing(self.store._connect()) as connection:
            rows = connection.execute(
                "SELECT token,payload FROM local_dispatch_queue ORDER BY rowid LIMIT 10"
            ).fetchall()
        return tuple(self._decode(token, payload) for token, payload in rows)

    def acknowledge(self, message: DispatchMessage) -> None:
        token = str(message.dispatch_token)
        checked = self._decode(token, self._payload(message))
        with closing(self.store._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT payload FROM local_dispatch_queue WHERE token=?", (token,)
                ).fetchone()
                if row is not None:
                    if self._decode(token, row[0]) != checked:
                        raise ServiceFault(ServiceErrorCode.CONFLICT)
                    connection.execute("DELETE FROM local_dispatch_queue WHERE token=?", (token,))
                connection.commit()
            except BaseException:
                connection.rollback()
                raise


def formal_delivery_allowed(
    *,
    operation: ExportOperation,
    approval_status: str | None,
    current_revision_id: str,
) -> bool:
    """Whether a formal operation's bytes may travel RIGHT NOW.

    Three live facts must all hold at read time: the operation actually committed a
    delivery, its approval is still approved, and its revision is still the case's
    current one. Stale bytes from before a correction are refused rather than served
    as the current formal result; no historical-download semantics exist this round.
    """
    if operation.status not in {"succeeded", "partial"}:
        return False
    if operation.effective_mode != "formal":
        return True
    if approval_status != "approved":
        return False
    return bool(operation.run.revision.revision_id == current_revision_id)


class LocalContentPlane:
    """Reads authorized bytes for the shared content routes.

    Every check the inline routes used to perform stays here: the source lookup is pinned to
    an exact version and hash, an artifact must belong to the job's current run, and the
    publication resolver still verifies the bytes against the manifest before returning
    them. A lapsed download grant raises PublicationError and surfaces as 403, so the
    fifteen-minute window is enforced rather than reported as a missing artifact.
    """

    def __init__(
        self,
        *,
        catalog: LocalMaterialCatalog,
        documents: RuntimeDocuments,
        jobs: SnapshotJobService,
        resolver: CommittedResultResolver | None,
        source_delivery_enabled: bool = True,
        exports: SQLiteExportStore | None = None,
        approvals: SQLiteApprovalStore | None = None,
    ) -> None:
        self.catalog, self.documents, self.jobs = catalog, documents, jobs
        self.exports = exports
        self.approvals = approvals
        # A composition that publishes nothing, or deliberately withholds source bytes,
        # keeps answering capability_unavailable. Moving the routes behind this port must
        # not quietly re-enable delivery the local original stack chose to switch off.
        self.resolver = resolver
        self.source_delivery_enabled = source_delivery_enabled

    async def read_source(
        self,
        principal: Principal,
        *,
        document_id: UUID,
        version: str,
        content_hash: str,
    ) -> DeliveredContent:
        if not self.source_delivery_enabled:
            raise ServiceFault(ServiceErrorCode.CAPABILITY)
        reference = self.catalog.source(str(document_id), version, content_hash)
        principal.require(reference.case_id, Permission.REVIEW)
        try:
            data = await asyncio.to_thread(self.documents.read, principal, reference)
        except DocumentFault as error:
            raise source_fault(error) from None
        return DeliveredContent(data=data.content, media_type="application/pdf")

    async def read_artifact(
        self,
        principal: Principal,
        *,
        job_id: UUID,
        artifact_id: UUID,
    ) -> DeliveredContent:
        status = await self.jobs.status(principal, job_id)
        principal.require(status.job.case_id, Permission.REVIEW)
        if self.exports is not None:
            # A committed export record is its own authority for the bytes it delivered;
            # the operation already pins snapshot, template bundle and per-table hashes.
            found = await asyncio.to_thread(self.exports.find_delivered, job_id, artifact_id)
            if found is not None:
                operation, body = found
                if operation.status not in {"succeeded", "partial"}:
                    raise ServiceFault(ServiceErrorCode.NOT_FOUND)
                live_status = (
                    None
                    if self.approvals is None or operation.approval_id is None
                    else self.approvals.current_status(job_id, operation.approval_id)
                )
                current = status.current_run.revision.revision_id if status.current_run else ""
                if not formal_delivery_allowed(
                    operation=operation,
                    approval_status=live_status,
                    current_revision_id=current,
                ):
                    # Withdrawn approval or superseded revision: the staged bytes stay
                    # for history, but they no longer travel as current formal output.
                    raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
                delivered = next(a for a in operation.artifacts if a.artifact_id == artifact_id)
                if hashlib.sha256(body).hexdigest() != delivered.content_hash:
                    raise ServiceFault(ServiceErrorCode.EXECUTION)
                media = (
                    "application/pdf"
                    if delivered.kind == "converted_pdf"
                    else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
                return DeliveredContent(
                    data=body,
                    media_type=cast(ContentMediaType, media),
                    filename=delivered.filename,
                )
        if self.resolver is None:
            raise ServiceFault(ServiceErrorCode.CAPABILITY)
        result = await self.jobs.result(principal, job_id)
        if status.current_run is None:
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        if artifact_id not in {artifact.artifact_id for artifact in result.artifacts}:
            raise ServiceFault(ServiceErrorCode.NOT_FOUND)
        try:
            artifact, data = await asyncio.to_thread(
                self.resolver.verified_bytes,
                principal,
                status.job.case_id,
                status.current_run.run_id,
                artifact_id,
            )
        except PublicationError:
            raise ServiceFault(ServiceErrorCode.UNAUTHORIZED) from None
        # The published manifest records what the writer produced; transport never guesses.
        media_type = artifact.content_type or "application/pdf"
        if media_type not in get_args(ContentMediaType):
            raise ServiceFault(ServiceErrorCode.CAPABILITY)
        suffix = Path(artifact.key).suffix or ".pdf"
        return DeliveredContent(
            data=data,
            media_type=cast(ContentMediaType, media_type),
            filename=f"{artifact.artifact_id}{suffix}",
        )


def create_integrated_service(
    *,
    authority: str,
    directory: LocalDirectory,
    catalog: LocalMaterialCatalog,
    documents: RuntimeDocuments,
    execution: ReviewExecution,
    resolver: CommittedResultResolver | None,
    worker_enabled: bool = True,
    worker_timeout: float = 60,
    source_delivery_enabled: bool = True,
    export_assets: ExportAssets | None = None,
    export_filler: WorkbookFiller | None = None,
    export_converter: WorkbookConverter | None = None,
    snapshot_provider: SnapshotProvider | None = None,
    legacy_review_enabled: bool = True,
    poll_interval: float = 0.1,
    attempt_scoped_reviews: bool = False,
) -> FastAPI:
    if not authority.startswith("127.0.0.1:"):
        raise ValueError("This configured local service requires a numeric loopback authority")
    if not 0.05 <= poll_interval <= 5:
        raise ValueError("Local polling requires a bounded interval")
    store = catalog.store
    access = LocalWorkbenchReadAccess(directory, catalog, documents)
    service = LocalWorkbenchJobService(store, store.results, policy=store.policy)
    service.workbench_access = access
    service.bind_sources(documents, catalog)
    worker = RuntimeWorker(
        service,
        CurrentSourceExecution(execution, directory, catalog, documents, store),
        timeout_seconds=worker_timeout,
    )
    from appraisal_review.adapters.local.service import public_verification
    from appraisal_review.adapters.local.workflow_runtime import SQLiteWorkflowReviews
    from appraisal_review.domain.service_contracts import RunReference
    from appraisal_review.domain.workbench_contracts import PausedReviewView

    review_store = SQLiteWorkflowReviews(store, attempt_scoped=attempt_scoped_reviews)

    async def assessment(run: RunReference, snapshot: RevisionSnapshot) -> PausedReviewView | None:
        review = await review_store.read(run)
        if review is None or review.case_review is None:
            return None
        if review.case_review.identity != snapshot.material.policy.identity:
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        return PausedReviewView(
            run=run,
            status=review.case_review.status,
            findings=tuple(review.case_review.findings),
            coverage=review.case_review.coverage,
            verification=public_verification(review.verification),
        )

    export_store: SQLiteExportStore | None = None
    export_service: ExportService | None = None
    approval_store: SQLiteApprovalStore | None = None
    approval_service: ReportApprovalService | None = None
    if export_assets is not None and export_filler is not None and snapshot_provider is not None:
        export_store = SQLiteExportStore(store)
        approval_store = SQLiteApprovalStore(store)

        async def read_confirmed_references(job_id: UUID) -> frozenset[str]:
            # Absence claims must cite a confirmation a human actually committed:
            # the answered task ids of this job are the only recognized references.
            # Awaited by the approval service on the request loop - asyncio.run here
            # would abort every basis/readiness call with a nested-event-loop error.
            records = await store.list_tasks(job_id=job_id)
            return frozenset(
                str(record.task.task_id) for record in records if record.task.state == "answered"
            )

        approval_service = ReportApprovalService(
            jobs=service,
            store=approval_store,
            assets=export_assets,
            filler=export_filler,
            snapshots=snapshot_provider,
            policy=ReadinessPolicy.default(),
            confirmed_references=read_confirmed_references,
        )

        def read_current_revision(job_id: UUID) -> RevisionReference | None:
            record = asyncio.run(store.read_job(job_id=job_id))
            if record is None or record.current_run is None:
                return None
            return record.current_run.revision

        export_service = ExportService(
            jobs=service,
            store=export_store,
            assets=export_assets,
            filler=export_filler,
            converter=export_converter,
            snapshots=snapshot_provider,
            approvals=approval_store,
            current_revision=read_current_revision,
        )
    app = create_app(
        job_service=service,
        human_task_service=HumanTaskService(
            store,
            new_revision_id=lambda: str(uuid4()),
            assessment_reader=assessment,
            workbench_access=access,
        ),
        principal_resolver=directory,
        content_plane=LocalContentPlane(
            catalog=catalog,
            documents=documents,
            jobs=service,
            resolver=resolver,
            source_delivery_enabled=source_delivery_enabled,
            exports=export_store,
            approvals=approval_store,
        ),
        export_operations=export_service,
        report_approvals=approval_service,
    )
    app.state.material_catalog = catalog
    app.state.runtime_worker = worker
    app.state.worker_problem = None
    queue = SQLiteDispatchQueue(store)
    app.state.dispatch_queue = queue

    def _current_session() -> tuple[Principal, str]:
        principal = _request_principal.get()
        bearer = _request_token.get()
        if principal is None or bearer is None:
            raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
        return principal, bearer

    async def read_session() -> JSONResponse:
        """Describe the authenticated session; the token itself never travels back."""
        principal, bearer = _current_session()
        _, expires_at = directory.session_view(bearer)
        return JSONResponse(
            {
                "actor_id": principal.actor.actor_id,
                "kind": principal.actor.kind,
                "expires_at": expires_at,
                "permissions_summary": {
                    "case_count": len(principal.case_ids),
                    "permission_count": len(principal.permissions),
                },
            }
        )

    async def delete_session() -> Response:
        """Revoke the CURRENT bearer token; no route mints or rotates tokens."""
        _, bearer = _current_session()
        directory.revoke_token(bearer)
        return Response(status_code=204)

    app.add_api_route("/v1/session", read_session, methods=["GET"])
    app.add_api_route("/v1/session", delete_session, methods=["DELETE"], status_code=204)
    if not legacy_review_enabled:
        # The local-original composition admits only configured revision references.
        # It must not expose the legacy caller-URI execution entry point.
        app.router.routes = [
            route
            for route in app.router.routes
            if getattr(route, "path", None) not in {"/v1/reviews", "/v1/validate"}
        ]

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        stop = asyncio.Event()

        async def tick() -> None:
            reconciler = JobReconciler(OutboxDispatcher(service, queue.send))
            while not stop.is_set():
                try:
                    await reconciler.run_once(limit=10)
                    for message in queue.pending():
                        await worker.process(message)
                        queue.acknowledge(message)
                    if export_service is not None:
                        await export_service.run_pending()
                    application.state.worker_problem = None
                except Exception:
                    application.state.worker_problem = "worker_unavailable"
                with suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=poll_interval)

        task = asyncio.create_task(tick()) if worker_enabled else None
        try:
            yield
        finally:
            stop.set()
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    app.router.lifespan_context = lifespan

    @app.middleware("http")
    async def authenticate(request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.headers.get("host") != authority:
            return Response(status_code=403)
        if not request.url.path.startswith("/v1/"):
            return await call_next(request)
        header = request.headers.get("authorization", "")
        try:
            principal = directory.authenticate(header)
        except ServiceFault as fault:
            return JSONResponse(status_code=403, content=fault.problem.model_dump(mode="json"))
        token = _request_principal.set(principal)
        bearer = _request_token.set(header.partition(" ")[2])
        try:
            response: Response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
            return response
        finally:
            _request_token.reset(bearer)
            _request_principal.reset(token)

    return app
