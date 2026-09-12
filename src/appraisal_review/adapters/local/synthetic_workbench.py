"""Explicit synthetic local assembly with durable jobs, decisions and real PDFs.

The injected Converse client is a fixed test model, not measured model quality.
Only server-authored fixtures can acquire synthetic material/publication authority.
Private bootstrap and per-run writer evidence survive process restart.
"""

from __future__ import annotations

import json
import os
import secrets
from contextlib import closing
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict

from appraisal_review.adapters.aws.action_selector import BedrockActionSelector, ModelSelectorConfig
from appraisal_review.adapters.local.artifact_publication import (
    AttemptArtifactPublisher,
    CommittedResultResolver,
)
from appraisal_review.adapters.local.document_authority import (
    ConfiguredDocumentAuthorization,
    DocumentGrant,
    Ed25519ExportVerifier,
)
from appraisal_review.adapters.local.document_storage import SQLiteDocumentStorage
from appraisal_review.adapters.local.export_composition import (
    discover_converter,
    discover_export_assets,
)
from appraisal_review.adapters.local.integrated_publication import (
    IntegratedResultProjection,
    PublicationEvidenceWriter,
    PublicationInputs,
)
from appraisal_review.adapters.local.integrated_service import (
    LocalDirectory,
    LocalMaterialCatalog,
    create_integrated_service,
)
from appraisal_review.adapters.local.pdf_writer import LocalPDFWriter
from appraisal_review.adapters.local.service import (
    LocalArtifactEvidence,
    LocalReviewService,
    LocalServiceConfiguration,
    LocalWriterConfiguration,
)
from appraisal_review.adapters.local.snapshot_registry import RegisteredSnapshots
from appraisal_review.adapters.local.sqlite_publication import (
    SQLiteArtifactObjectStore,
    SQLiteManifestRepository,
)
from appraisal_review.adapters.local.sqlite_review_store import SQLiteReviewStore
from appraisal_review.adapters.local.sqlite_workflow_run_ledger import SqliteWorkflowRunLedger
from appraisal_review.adapters.local.workbook_writer import fill_workbook
from appraisal_review.adapters.local.workflow_runtime import (
    SQLiteDecisionTrace,
    SQLiteExecutionAuthority,
    SQLiteWorkflowReviews,
)
from appraisal_review.application.document_transfer import DocumentTransferService
from appraisal_review.application.human_tasks import HumanTaskService
from appraisal_review.application.integrated_workflow import (
    ControllerInvocation,
    IntegratedTaskBinding,
    IntegratedWorkflowExecution,
)
from appraisal_review.application.outbox import JobReconciler, OutboxDispatcher
from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.application.runtime_sources import SnapshotJobService
from appraisal_review.application.service_guards import Principal
from appraisal_review.domain.artifact_publication import ManifestCandidate, SourceVersion
from appraisal_review.domain.document_transfer import DocumentOperation, Purpose
from appraisal_review.domain.factor_models import AgentReviewRequest, ReviewMaterial
from appraisal_review.domain.review_contracts import content_digest
from appraisal_review.domain.service_contracts import (
    ActorReference,
    Budget,
    DeterministicReviewArguments,
    DocumentReference,
    MaterialRevision,
    Permission,
    RequestHumanReviewArguments,
    ReviewSubmission,
    RunReference,
    SelectorInput,
    TaskKind,
)
from appraisal_review.ports.approval import ReviewAuthorization
from appraisal_review.ports.jobs import ClaimedAttempt, JobRecord
from appraisal_review.testing.integration_fixture import (
    BUNDLED_CJK_SHA256,
    IntegrationFixture,
    create_integration_fixture,
)

SCENARIOS = ("empty", "completed", "confirm", "correct", "reject", "conflict", "lost_response")


def _private_json(path: Path, value: Any) -> None:
    """Atomically replace an owned private report/configuration within the data directory."""
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


class RunAssets(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    run: RunReference
    configuration: LocalWriterConfiguration
    request: AgentReviewRequest
    evidence: LocalArtifactEvidence | None = None


@dataclass(frozen=True)
class SyntheticMaterialGrant:
    """Exact synthetic-only capability; never an OS-signed or formal approval receipt."""

    store: SQLiteReviewStore
    run: RunReference
    capability: ReviewAuthorization

    def permits(self, material: ReviewMaterial) -> bool:
        if content_digest(material) != self.run.revision.material_digest:
            return False
        with closing(self.store._connect()) as db:
            row = db.execute(
                "SELECT run_json,material_digest,scope FROM synthetic_material_grants "
                "WHERE run_id=?",
                (str(self.run.run_id),),
            ).fetchone()
        return bool(
            row
            and tuple(row)
            == (self.run.model_dump_json(), self.run.revision.material_digest, "synthetic-only")
            and self.capability.permits(material)
        )


class SyntheticConverseClient:
    """Fixed mock-model selection from typed advertised input; no network client."""

    def __init__(self, store: SQLiteReviewStore) -> None:
        self.store = store

    def converse(self, **kwargs: Any) -> dict[str, Any]:
        payload = json.loads(kwargs["messages"][0]["content"][0]["text"])
        selected = SelectorInput.model_validate(
            {key: payload[key] for key in ("snapshot", "allowed_actions", "evidence", "budget")}
        )
        action = selected.allowed_actions.actions[0]
        arguments: DeterministicReviewArguments | RequestHumanReviewArguments
        if action.action.value == "deterministic_review":
            arguments = DeterministicReviewArguments(
                revision=selected.snapshot.revision.reference,
                rules=selected.snapshot.revision.rules,
            )
        elif action.action.value == "request_human_review":
            blocker = selected.snapshot.unresolved_blockers[0]
            arguments = RequestHumanReviewArguments(
                question="Inspect the exact synthetic observation before responding.",
                reason_code=blocker.reason_code,
                affected_subject_ids=blocker.affected_subject_ids,
                evidence=blocker.evidence,
            )
        else:
            raise ValueError("Synthetic selector only supports review and human handoff")
        answer = dict(
            action=action.action.value,
            action_id=action.action_id,
            arguments=arguments.model_dump(mode="json"),
        )
        with closing(self.store._connect()) as db:
            db.execute(
                "INSERT INTO synthetic_model_calls(run_id,request,response) VALUES(?,?,?)",
                (str(selected.snapshot.run.run_id), selected.model_dump_json(), json.dumps(answer)),
            )
        return {
            "stopReason": "end_turn",
            "output": {"message": {"content": [{"text": json.dumps(answer)}]}},
        }


class CaseDocuments(DocumentTransferService):
    """Closed case routing over existing C2 services, never request-selected URLs."""

    def __init__(self, cases: dict[str, Any]) -> None:
        self.cases = cases
        self.authorization = self

    def require(
        self, principal: Principal, case_id: str, purpose: Purpose, operation: DocumentOperation
    ) -> None:
        self._case(case_id).authorization.require(principal, case_id, purpose, operation)

    def _case(self, case_id: str) -> DocumentTransferService:
        if case_id not in self.cases:
            raise ValueError("Unknown configured synthetic case")
        return cast(DocumentTransferService, self.cases[case_id].documents)

    def read(self, principal: Principal, reference: DocumentReference) -> Any:
        return self._case(reference.case_id).read(principal, reference)

    def create_snapshot(
        self, principal: Principal, run: RunReference, revision: MaterialRevision
    ) -> Any:
        return self._case(run.revision.case_id).create_snapshot(principal, run, revision)

    def read_snapshot(
        self, principal: Principal, run: RunReference, reference: DocumentReference
    ) -> Any:
        return self._case(run.revision.case_id).read_snapshot(principal, run, reference)


class SyntheticWorkbench:
    def __init__(self, root: Path, port: int, state: dict[str, Any], fixtures: dict[str, Any]):
        self.root, self.port, self.state, self.fixtures = root, port, state, fixtures
        self.case_ids = {
            name: f.snapshot.revision.reference.case_id for name, f in fixtures.items()
        }
        self.principal = next(iter(fixtures.values())).principal
        self.directory = LocalDirectory({state["session_token"]: self.principal})
        self.store = SQLiteReviewStore(root / "state/review.sqlite")
        with closing(self.store._connect()) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS synthetic_run_assets "
                "(run_id TEXT PRIMARY KEY,payload TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS synthetic_model_calls "
                "(seq INTEGER PRIMARY KEY,run_id TEXT,request TEXT,response TEXT)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS synthetic_material_grants "
                "(run_id TEXT PRIMARY KEY,run_json TEXT NOT NULL,material_digest TEXT NOT NULL,"
                "scope TEXT NOT NULL CHECK(scope='synthetic-only'))"
            )
        self.catalog = LocalMaterialCatalog(self.store)
        self.by_case = {f.snapshot.revision.reference.case_id: f for f in fixtures.values()}
        self.documents = CaseDocuments(self.by_case)
        for fixture in fixtures.values():
            self.catalog.register(self.principal, fixture.snapshot)
        self.jobs = SnapshotJobService(self.store, self.store.results, policy=self.store.policy)
        self.jobs.bind_sources(self.documents, self.catalog)
        self.human_tasks = HumanTaskService(self.store, new_revision_id=lambda: str(uuid4()))
        self.objects = SQLiteArtifactObjectStore(self.store)
        self.manifests = SQLiteManifestRepository(
            self.store, source_authorizer=self.authorize_sources, trusted_synthetic_approval=True
        )
        self.resolver = CommittedResultResolver(objects=self.objects, manifests=self.manifests)
        projection = IntegratedResultProjection(
            publisher=AttemptArtifactPublisher(objects=self.objects, manifests=self.manifests),
            manifests=self.manifests,
            catalog=self.catalog,
            principal_getter=self.directory.read,
            inputs_getter=self.publication_inputs,
            synthetic_authorizer=self.authorize_publication,
        )
        self.projection = projection
        self.guard = SQLiteExecutionAuthority(self.store, self.documents, self.directory)
        self.executions = {
            self.case_ids[name]: self._execution(name, fixture)
            for name, fixture in fixtures.items()
        }
        export_assets = discover_export_assets()
        self.snapshots = RegisteredSnapshots(self.root / "snapshots")
        self.app = create_integrated_service(
            authority=f"127.0.0.1:{port}",
            directory=self.directory,
            catalog=self.catalog,
            documents=self.documents,
            execution=self,
            resolver=self.resolver,
            export_assets=export_assets,
            export_filler=fill_workbook if export_assets is not None else None,
            export_converter=discover_converter(),
            snapshot_provider=self.snapshots,
        )
        self.app.state.workbench_data_mode = "synthetic"
        self.app.state.configured_workbench_jobs = lambda: tuple(
            self.state.get("job_ids", {}).values()
        )

    def _execution(self, name: str, fixture: Any) -> IntegratedWorkflowExecution:
        bindings = []
        for number, pair in enumerate(fixture.snapshot.material.facts.pairs, 1):
            for side in ("target", "comparable"):
                bindings.append(
                    IntegratedTaskBinding(
                        binding_id=f"{name}.c{number}.{side}",
                        kind=TaskKind.CORRECTION if name == "correct" else TaskKind.FACT,
                        context=pair.context,
                        factor_id=pair.pair.factor_id,
                        side=side,
                        finding_ids=(
                            pair.context.key() + "/factor/" + pair.pair.factor_id,
                            f"observed/c{number}-rate",
                        ),
                        question=f"Inspect synthetic comparison {number} {side} road width.",
                    )
                )
        return IntegratedWorkflowExecution(
            human_tasks=self.human_tasks,
            principals=self.directory,
            guard=self.guard,
            registration=self.guard,
            controllers=self.controller,
            reviews=SQLiteWorkflowReviews(self.store),
            selector=BedrockActionSelector(
                SyntheticConverseClient(self.store),
                ModelSelectorConfig(model_id="fixed-synthetic-converse-v1", attempts=1),
            ),
            ledger=SqliteWorkflowRunLedger(self.root / "state/workflow.sqlite"),
            trace=SQLiteDecisionTrace(self.store),
            bindings=tuple(bindings),
            budget=Budget(steps_remaining=8, model_calls_remaining=8, retries_remaining=0),
            project_result=self.projection,
        )

    async def register_synthetic_case(
        self,
        fixture: Any,
        *,
        name: str | None = None,
        raster_source_versions: tuple[SourceVersion, ...] | None = None,
    ) -> dict[str, str]:
        """Trusted setup for a freshly admitted authored fixture, never an HTTP import.

        Return after durable admission, before worker execution or human answers.
        The fixture supplies its NEW C2 sources, snapshot and exact writer policy;
        existing native evidence and authorizations are never transplanted.
        """
        case_id = fixture.snapshot.revision.reference.case_id
        name = name or "privacy-" + case_id
        if fixture.principal.actor != self.principal.actor or name in self.fixtures:
            raise ValueError("Explicit same-actor fresh synthetic case required")
        if case_id in self.by_case or not callable(fixture.authorize):
            raise ValueError("Synthetic case identity must be fresh and authorizable")
        config = LocalServiceConfiguration.model_validate_json(
            fixture.configuration.model_dump_json()
        )
        snapshot = fixture.snapshot
        if raster_source_versions is not None:
            expected_sources = tuple(
                SourceVersion(
                    document_id=d.document_id, version=d.version, content_hash=d.content_hash
                )
                for d in snapshot.revision.documents
            )
            forms = [d for d in snapshot.revision.documents if d.purpose == "forms"]
            if (
                tuple(raster_source_versions) != expected_sources
                or len({d.document_id for d in raster_source_versions}) != len(expected_sources)
                or any(
                    p.has_text for d in snapshot.material.policy.registry.documents for p in d.pages
                )
                or len(forms) != 1
                or config.writer is None
                or config.writer.template_policy.template_sha256 != forms[0].content_hash
            ):
                raise ValueError("Exact authored raster source and template pins required")
        if (
            config.inputs.identity != snapshot.material.policy.identity
            or config.expected_material_digest != snapshot.revision.reference.material_digest
            or config.revision_id != snapshot.revision.reference.revision_id
            or config.writer is None
        ):
            raise ValueError("New fixture configuration must bind its exact prepared snapshot")
        principal = self.directory.grant_case(self.principal.actor.actor_id, case_id)
        # Authoritative grants are explicit for this C2 case and the shared human.
        authorization = fixture.documents.authorization
        if not isinstance(authorization, ConfiguredDocumentAuthorization):
            raise ValueError("Synthetic registration requires explicit configured C2 grants")
        grant = DocumentGrant(
            UUID(principal.actor.actor_id),
            UUID(case_id),
            frozenset({"criteria", "forms"}),
            frozenset(DocumentOperation),
        )
        if grant not in authorization.grants:
            authorization.grants += (grant,)
        for reference in snapshot.revision.documents:
            fixture.documents.read(principal, reference)
        # These private authored files make restart rehydration independent of
        # in-memory objects. No private signing key is copied or reported.
        path = self.root / "registered" / case_id
        path.mkdir(mode=0o700, parents=True, exist_ok=False)
        for filename, model in (
            ("config", config),
            ("material", snapshot.material),
            ("revision", snapshot.revision),
            ("run", fixture.run),
            ("request", fixture.request),
        ):
            _private_json(path / (filename + ".json"), model.model_dump(mode="json"))
        storage_path = Path(fixture.documents.storage.database).resolve()
        descriptor: dict[str, Any] = {
            "path": str(path),
            "storage_path": str(storage_path),
            "font_sha256": fixture.font_sha256,
        }
        if raster_source_versions is not None:
            descriptor["raster_source_versions"] = [
                source.model_dump(mode="json") for source in raster_source_versions
            ]
        self.principal = principal
        fixture = replace(fixture, principal=principal)
        self.fixtures[name] = fixture
        self.case_ids[name] = case_id
        self.by_case[case_id] = fixture
        self.catalog.register(principal, snapshot)
        self.executions[case_id] = self._execution(name, fixture)
        self.state.setdefault("registered", {})[name] = descriptor
        # Persist reconstruction data before a durable queue can expose the job.
        _private_json(self.root / "bootstrap.json", self.state)
        outcome = await self.jobs.submit(
            principal,
            ReviewSubmission(
                revision=snapshot.revision.reference,
                documents=snapshot.revision.documents,
                idempotency_key="synthetic-workbench-" + name,
            ),
        )
        job_id = str(outcome.status.job.job_id)
        self.state["job_ids"][name] = job_id
        _private_json(self.root / "bootstrap.json", self.state)
        return {"case_id": case_id, "review_job_id": job_id}

    async def execute(self, record: JobRecord, attempt: ClaimedAttempt) -> Any:
        return await self.executions[record.case_id].execute(record, attempt)

    def authorize_sources(
        self, principal: Principal, run: RunReference, sources: tuple[SourceVersion, ...]
    ) -> None:
        for source in sources:
            reference = self.catalog.source(source.document_id, source.version, source.content_hash)
            self.documents.read_snapshot(principal, run, reference)

    def authorize_publication(self, candidate: ManifestCandidate, principal: Principal) -> None:
        fixture = self.by_case[candidate.run.revision.case_id]
        if principal != self.principal:
            raise ValueError("Synthetic publication principal differs")
        snapshot = self._snapshot(candidate.run)
        fixture.authorize(snapshot)
        self.authorize_sources(principal, candidate.run, candidate.artifacts[0].source_versions)

    def _snapshot(self, run: RunReference) -> RevisionSnapshot:
        # Synchronous publication callback; typed state is freshly read, not cached.
        with closing(self.store._connect()) as db:
            state = self.store._decode(
                db.execute("SELECT payload FROM review_state WHERE singleton=1").fetchone()[0]
            )
        for snapshot in state.snapshots:
            parsed = snapshot.snapshot()
            if parsed.revision.reference == run.revision:
                return parsed
        raise ValueError("Exact persisted snapshot required")

    def _save_assets(self, assets: RunAssets) -> None:
        with closing(self.store._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute(
                "SELECT payload FROM synthetic_run_assets WHERE run_id=?", (str(assets.run.run_id),)
            ).fetchone()
            if prior:
                old = RunAssets.model_validate_json(prior[0])
                if old.model_copy(update={"evidence": None}) != assets.model_copy(
                    update={"evidence": None}
                ):
                    raise ValueError("Run writer configuration is immutable")
                if old.evidence is not None and old.evidence != assets.evidence:
                    raise ValueError("Run writer evidence is immutable")
            db.execute(
                "INSERT OR REPLACE INTO synthetic_run_assets VALUES(?,?)",
                (str(assets.run.run_id), assets.model_dump_json()),
            )
            db.commit()

    @staticmethod
    def _writer(assets: RunAssets, on_evidence: Any = None) -> PublicationEvidenceWriter:
        config = assets.configuration
        writer = PublicationEvidenceWriter(
            LocalPDFWriter(render_config=config.render, template_policy=config.template_policy),
            config,
            on_evidence=on_evidence,
        )
        writer.run_id, writer.evidence = assets.run.run_id, assets.evidence
        return writer

    async def controller(
        self, principal: Principal, run: RunReference, snapshot: RevisionSnapshot
    ) -> ControllerInvocation:
        fixture = self.by_case[run.revision.case_id]
        config = fixture.configuration
        if config.writer is None:
            raise ValueError("Actual synthetic writer required")
        request = fixture.request.model_copy(
            update={
                "output_pdf_uri": (
                    config.writer.output_directory / f"{run.run_id}-{run.attempt_id}.pdf"
                ).as_uri()
            }
        )
        assets = RunAssets(run=run, configuration=config.writer, request=request)
        self._save_assets(assets)
        writer = self._writer(
            assets,
            lambda evidence: self._save_assets(assets.model_copy(update={"evidence": evidence})),
        )
        controller = LocalReviewService(config).controller_factory()
        controller.pdf_writer = writer
        try:
            authorization = fixture.authorize(snapshot)
        except ValueError:
            authorization = None
        if authorization is None:
            controller.authorization = None
        elif controller.authorization is None or not controller.authorization.permits(
            snapshot.material
        ):
            # Eligible UUID confirmations retain their original identity and raw score.
            # Existing completed-fixture OS receipts remain on their original path.
            with closing(self.store._connect()) as db:
                values = (run.model_dump_json(), run.revision.material_digest, "synthetic-only")
                db.execute("BEGIN IMMEDIATE")
                old = db.execute(
                    "SELECT run_json,material_digest,scope FROM synthetic_material_grants "
                    "WHERE run_id=?",
                    (str(run.run_id),),
                ).fetchone()
                if old is not None and tuple(old) != values:
                    raise ValueError("Synthetic material grant cannot change identity")
                db.execute(
                    "INSERT OR IGNORE INTO synthetic_material_grants VALUES(?,?,?,?)",
                    (str(run.run_id), *values),
                )
                db.commit()
            controller.authorization = SyntheticMaterialGrant(self.store, run, authorization)
        return ControllerInvocation(controller, request)

    def publication_inputs(self, record: JobRecord, attempt: ClaimedAttempt) -> PublicationInputs:
        with closing(self.store._connect()) as db:
            row = db.execute(
                "SELECT payload FROM synthetic_run_assets WHERE run_id=?",
                (str(record.current_run.run_id),),
            ).fetchone()
        if row is None:
            raise ValueError("Durable writer evidence missing")
        assets = RunAssets.model_validate_json(row[0])
        if assets.run != record.current_run or assets.run.attempt_id != attempt.attempt_id:
            raise ValueError("Durable evidence belongs to another attempt")
        raster_assets = None
        name = next(n for n, case_id in self.case_ids.items() if case_id == record.case_id)
        descriptor = self.state.get("registered", {}).get(name, {})
        if "raster_source_versions" in descriptor:
            from appraisal_review.adapters.local.integrated_publication import (
                TrustedRasterPublication,
            )

            fixture = self.fixtures[name]
            snapshot = self._snapshot(assets.run)
            raster_assets = TrustedRasterPublication(
                source_versions=tuple(
                    SourceVersion.model_validate(source)
                    for source in descriptor["raster_source_versions"]
                ),
                template_hash=assets.configuration.template_policy.template_sha256,
                authorization=fixture.authorize(snapshot),
            )
        inputs = PublicationInputs(
            configuration=assets.configuration,
            request=assets.request,
            writer=self._writer(assets),
            template_version="synthetic-v1",
            fixed_synthetic_assets=True,
        )
        return replace(inputs, raster_assets=raster_assets) if raster_assets is not None else inputs

    async def submit_cases(self) -> None:
        ids = dict(self.state.get("job_ids", {}))
        for name, fixture in self.fixtures.items():
            if name in ids:
                record = await self.store.read_job(job_id=UUID(ids[name]))
                if record is None or record.case_id != self.case_ids[name]:
                    raise ValueError("Persisted scenario job identity differs")
                continue
            outcome = await self.jobs.submit(
                self.principal,
                ReviewSubmission(
                    revision=fixture.snapshot.revision.reference,
                    documents=fixture.snapshot.revision.documents,
                    idempotency_key="synthetic-workbench-" + name,
                ),
            )
            ids[name] = str(outcome.status.job.job_id)
        self.state["job_ids"] = ids
        _private_json(self.root / "bootstrap.json", self.state)

    async def drain(self) -> None:
        """Process acknowledged durable dispatches without inventing completion."""
        queue = self.app.state.dispatch_queue
        await JobReconciler(OutboxDispatcher(self.jobs, queue.send)).run_once(limit=20)
        for message in queue.pending():
            await self.app.state.runtime_worker.process(message)
            queue.acknowledge(message)

    async def settle(self) -> None:
        await self.drain()
        self._tasks = dict(self.state.get("task_ids", {}))
        previous = self.root / "fixture.json"
        if not self._tasks and previous.exists():
            self._tasks = dict(json.loads(previous.read_text())["tasks"])
        for name, job_id in self.state["job_ids"].items():
            if name not in SCENARIOS or name in self._tasks:
                continue
            record = await self.store.read_job(job_id=UUID(job_id))
            if record is None or record.status.value not in {"succeeded", "waiting_for_human"}:
                raise RuntimeError(f"Synthetic scenario {name} did not reach its required state")
            tasks = await self.human_tasks.list_tasks(self.principal, UUID(job_id))
            if name in {"empty", "completed"}:
                if record.status.value != "succeeded" or tasks.tasks:
                    raise RuntimeError("Completed synthetic case must succeed with empty tasks")
            else:
                opened = [v.task for v in tasks.tasks if v.task.state == "open"]
                if not opened:
                    raise RuntimeError("Synthetic response case must expose actual open tasks")
                self._tasks[name] = str(opened[0].task_id)
        self.state["task_ids"] = self._tasks
        _private_json(self.root / "bootstrap.json", self.state)

    async def case_status(self, case_id: str) -> dict[str, Any]:
        """Private callback projection for admitted privacy jobs, with no bearer secrets."""
        name = next((n for n, c in self.case_ids.items() if c == case_id), None)
        if name is None:
            raise ValueError("Unknown registered synthetic case")
        job_id = self.state["job_ids"][name]
        record = await self.store.read_job(job_id=UUID(job_id))
        if record is None:
            raise ValueError("Registered job disappeared")
        tasks = await self.human_tasks.list_tasks(self.principal, UUID(job_id))
        view: dict[str, Any] = {
            "case_id": case_id,
            "review_job_id": job_id,
            "job_status": record.status.value,
            "task_ids": [str(v.task.task_id) for v in tasks.tasks if v.task.state == "open"],
        }
        if record.status.value == "succeeded":
            result = await self.jobs.result(self.principal, UUID(job_id))
            if result.business_status == "completed" and result.artifacts:
                view.update(
                    completed_job_id=job_id,
                    run_id=str(result.run.run_id),
                    artifact_ids=[str(a.artifact_id) for a in result.artifacts],
                )
        return view

    def manifest(self) -> dict[str, Any]:
        return dict(
            api_base_url=f"http://127.0.0.1:{self.port}",
            session_token=self.state["session_token"],
            empty_job_id=self.state["job_ids"]["empty"],
            completed_job_id=self.state["job_ids"]["completed"],
            tasks=self._tasks,
        )


async def prepare_workbench(directory: Path, *, port: int = 8765) -> SyntheticWorkbench:
    if not 1 <= port <= 65535:
        raise ValueError("A valid local port is required")
    directory = directory.absolute()
    if directory.is_symlink():
        raise ValueError("Workbench directory cannot be a symlink")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.stat().st_mode & 0o077:
        raise ValueError("Workbench directory must be private")
    bootstrap = directory / "bootstrap.json"
    fixtures: dict[str, Any] = {}
    if bootstrap.exists():
        state = json.loads(bootstrap.read_text())
        if state["dataset_kind"] != "synthetic-workbench-v1":
            raise ValueError("Unknown bootstrap format")
        locations = {name: {"path": str(directory / name)} for name in SCENARIOS}
        locations.update(state.get("registered", {}))
        for name, location in locations.items():
            path = Path(location["path"])
            config = LocalServiceConfiguration.model_validate_json(
                (path / "config.json").read_bytes()
            )
            revision = MaterialRevision.model_validate_json((path / "revision.json").read_bytes())
            fixtures[name] = (path, config, revision)
        principal = Principal(
            ActorReference(actor_id=state["actor_id"], kind="human"),
            frozenset(v[2].reference.case_id for v in fixtures.values()),
            frozenset(Permission),
        )
        for name, (path, config, revision) in list(fixtures.items()):
            location = locations[name]
            storage = SQLiteDocumentStorage(
                Path(location.get("storage_path", path / "documents.sqlite"))
            )
            authority = ConfiguredDocumentAuthorization(
                (
                    DocumentGrant(
                        UUID(state["actor_id"]),
                        UUID(revision.reference.case_id),
                        frozenset({"criteria", "forms"}),
                        frozenset(DocumentOperation),
                    ),
                )
            )
            documents = DocumentTransferService(
                storage, authority, Ed25519ExportVerifier(()), storage
            )
            fixtures[name] = IntegrationFixture(
                path,
                principal,
                documents,
                RevisionSnapshot((path / "material.json").read_text(), revision.model_dump_json()),
                RunReference.model_validate_json((path / "run.json").read_bytes()),
                config,
                AgentReviewRequest.model_validate_json((path / "request.json").read_bytes()),
                location.get("font_sha256", BUNDLED_CJK_SHA256),
            )
    else:
        state = dict(
            dataset_kind="synthetic-workbench-v1",
            actor_id=str(uuid4()),
            session_token=secrets.token_urlsafe(36),
            model_mode="fixed_mock_converse",
        )
        for name in SCENARIOS:
            fixtures[name] = await create_integration_fixture(
                directory / name, approve_synthetic=name in {"empty", "completed"}
            )
        principal = Principal(
            ActorReference(actor_id=state["actor_id"], kind="human"),
            frozenset(f.snapshot.revision.reference.case_id for f in fixtures.values()),
            frozenset(Permission),
        )
        for name, fixture in list(fixtures.items()):
            authority = fixture.documents.authorization
            authority.grants += (
                DocumentGrant(
                    UUID(state["actor_id"]),
                    UUID(fixture.snapshot.revision.reference.case_id),
                    frozenset({"criteria", "forms"}),
                    frozenset(DocumentOperation),
                ),
            )
            fixtures[name] = replace(fixture, principal=principal)
        _private_json(bootstrap, state)
    workbench = SyntheticWorkbench(directory, port, state, fixtures)
    await workbench.submit_cases()
    return workbench
