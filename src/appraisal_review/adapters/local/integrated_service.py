"""Configured loopback service, durable jobs and the canonical human-task API.

Authentication and execution are explicit constructor dependencies. A synthetic
launcher supplies its own isolated assets; this module never invents approvals.
"""

from __future__ import annotations

import asyncio
import hmac
import json
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager, closing, suppress
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import RequestResponseEndpoint

from appraisal_review.adapters.local.artifact_publication import CommittedResultResolver
from appraisal_review.adapters.local.sqlite_review_store import SQLiteReviewStore
from appraisal_review.api.app import create_app
from appraisal_review.api.dependencies import get_principal
from appraisal_review.application.document_transfer import DocumentTransferService
from appraisal_review.application.human_tasks import HumanTaskService
from appraisal_review.application.outbox import DispatchMessage, JobReconciler, OutboxDispatcher
from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.application.runtime_sources import SnapshotJobService, source_fault
from appraisal_review.application.runtime_worker import (
    ExecutedReview,
    ReviewExecution,
    RuntimeWorker,
)
from appraisal_review.application.service_guards import Principal, ServiceFault
from appraisal_review.domain.artifact_publication import PublicationError
from appraisal_review.domain.document_transfer import DocumentFault
from appraisal_review.domain.service_contracts import (
    DocumentReference,
    MaterialRevision,
    Permission,
    RevisionReference,
    ServiceErrorCode,
)
from appraisal_review.ports.jobs import ClaimedAttempt, JobRecord

_request_principal: ContextVar[Principal | None] = ContextVar("review_principal", default=None)


class LocalDirectory:
    """Trusted session configuration; requests cannot supply actor or permission fields."""

    def __init__(self, sessions: Mapping[str, Principal]) -> None:
        if not sessions or any(len(token) < 32 for token in sessions):
            raise ValueError("Explicit server-issued local sessions are required")
        self._sessions = dict(sessions)

    def authenticate(self, header: str) -> Principal:
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer":
            raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
        for expected, principal in self._sessions.items():
            if hmac.compare_digest(expected, token):
                return principal
        raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)

    async def read(self, principal_id: str, case_id: str) -> Principal:
        for principal in self._sessions.values():
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
        if snapshot.revision.reference != reference:
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        return snapshot

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
class CurrentSourceExecution:
    execution: ReviewExecution
    directory: LocalDirectory
    catalog: LocalMaterialCatalog
    documents: DocumentTransferService
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


def create_integrated_service(
    *,
    authority: str,
    directory: LocalDirectory,
    catalog: LocalMaterialCatalog,
    documents: DocumentTransferService,
    execution: ReviewExecution,
    resolver: CommittedResultResolver,
    worker_enabled: bool = True,
    worker_timeout: float = 60,
) -> FastAPI:
    if not authority.startswith("127.0.0.1:"):
        raise ValueError("This configured local service requires a numeric loopback authority")
    store = catalog.store
    service = SnapshotJobService(store, store.results, policy=store.policy)
    service.bind_sources(documents, catalog)
    worker = RuntimeWorker(
        service,
        CurrentSourceExecution(execution, directory, catalog, documents, store),
        timeout_seconds=worker_timeout,
    )
    app = create_app(
        job_service=service,
        human_task_service=HumanTaskService(store, new_revision_id=lambda: str(uuid4())),
        principal_resolver=directory,
    )
    app.state.material_catalog = catalog
    app.state.runtime_worker = worker
    app.state.worker_problem = None
    queue = SQLiteDispatchQueue(store)
    app.state.dispatch_queue = queue

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
                    application.state.worker_problem = None
                except Exception:
                    application.state.worker_problem = "worker_unavailable"
                with suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=0.1)

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
        try:
            principal = directory.authenticate(request.headers.get("authorization", ""))
        except ServiceFault as fault:
            return JSONResponse(status_code=403, content=fault.problem.model_dump(mode="json"))
        token = _request_principal.set(principal)
        try:
            response: Response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
            return response
        finally:
            _request_principal.reset(token)

    @app.get("/v1/documents/{document_id}/content")
    async def source_pdf(
        document_id: UUID,
        version: str,
        content_hash: str,
        principal: Annotated[Principal, Depends(get_principal)],
    ) -> Response:
        reference = catalog.source(str(document_id), version, content_hash)
        principal.require(reference.case_id, Permission.REVIEW)
        try:
            data = await asyncio.to_thread(documents.read, principal, reference)
        except DocumentFault as error:
            raise source_fault(error) from None
        return Response(data.content, media_type="application/pdf")

    @app.get("/v1/review-jobs/{job_id}/artifacts/{artifact_id}/content")
    async def artifact_pdf(
        job_id: UUID,
        artifact_id: UUID,
        principal: Annotated[Principal, Depends(get_principal)],
    ) -> Response:
        status = await service.status(principal, job_id)
        result = await service.result(principal, job_id)
        if status.current_run is None:
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        if artifact_id not in {artifact.artifact_id for artifact in result.artifacts}:
            raise ServiceFault(ServiceErrorCode.NOT_FOUND)
        try:
            artifact, data = await asyncio.to_thread(
                resolver.verified_bytes,
                principal,
                status.job.case_id,
                status.current_run.run_id,
                artifact_id,
            )
        except PublicationError:
            raise ServiceFault(ServiceErrorCode.UNAUTHORIZED) from None
        return Response(
            data,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{artifact.artifact_id}.pdf"'},
        )

    return app
