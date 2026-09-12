"""Controlled local-original workbench, using real parsers and canonical transactions.

This composition intentionally performs deterministic review and human handoff;
it does not claim model planning, C2 admission, rule approval or PDF publication.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from uuid import uuid4, uuid5

from fastapi import FastAPI
from pydantic import TypeAdapter

from appraisal_review.adapters.local.approval import current_reviewer
from appraisal_review.adapters.local.document_manifest import InputManifest
from appraisal_review.adapters.local.integrated_service import (
    LocalDirectory,
    LocalMaterialCatalog,
    create_integrated_service,
)
from appraisal_review.adapters.local.original_documents import (
    AuthorizedOriginalParser,
    LocalOriginalAuthorization,
    LocalOriginalDocuments,
)
from appraisal_review.adapters.local.service import (
    LocalReviewService,
    LocalServiceConfiguration,
    public_verification,
)
from appraisal_review.adapters.local.sqlite_review_store import SQLiteReviewStore
from appraisal_review.adapters.local.workflow_runtime import (
    SQLiteExecutionAuthority,
    SQLiteWorkflowReviews,
)
from appraisal_review.application.controller import ReviewAgentController
from appraisal_review.application.document_review import MaterialProvider
from appraisal_review.application.runtime_sources import source_fault
from appraisal_review.application.runtime_worker import ExecutedReview
from appraisal_review.application.service_guards import Principal, ServiceFault
from appraisal_review.domain.confidence import Side, confirmation_digest
from appraisal_review.domain.document_transfer import DocumentFault, Purpose
from appraisal_review.domain.factor_models import AgentReviewRequest, EvidencedPair, ReviewMaterial
from appraisal_review.domain.review_contracts import content_digest
from appraisal_review.domain.service_contracts import (
    AcceptedResponse,
    ActorReference,
    ExecutionStatus,
    FactSideReference,
    HumanTask,
    Permission,
    ResponseAction,
    ReviewSubmission,
    ServiceErrorCode,
    ServiceResult,
    TaskKind,
)
from appraisal_review.ports.jobs import ClaimedAttempt, JobRecord


class ExpiringLocalDirectory(LocalDirectory):
    """No external sign-in or arbitrary password acceptance; OS-controlled sessions."""

    def __init__(self, sessions: dict[str, Principal], grant: LocalOriginalAuthorization) -> None:
        super().__init__(sessions)
        self.grant = grant

    def _active(self) -> None:
        if self.grant.revoked or self.grant.clock() >= self.grant.expires_at:
            raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)

    def authenticate(self, header: str) -> Principal:
        self._active()
        return super().authenticate(header)

    async def read(self, principal_id: str, case_id: str) -> Principal:
        self._active()
        return await super().read(principal_id, case_id)


class LocalCandidateExecution:
    """Actual Controller output and located source-confirmation tasks; no model stub."""

    def __init__(
        self,
        local: LocalReviewService,
        documents: LocalOriginalDocuments,
        directory: LocalDirectory,
        catalog: LocalMaterialCatalog,
    ) -> None:
        self.local, self.documents, self.directory, self.catalog = (
            local,
            documents,
            directory,
            catalog,
        )
        self.reviews = SQLiteWorkflowReviews(catalog.store, attempt_scoped=True)
        self.authority = SQLiteExecutionAuthority(catalog.store, documents, directory)

    async def execute(self, record: JobRecord, attempt: ClaimedAttempt) -> ExecutedReview:
        principal = await self.directory.read(record.principal_id, record.case_id)
        snapshot = await self.catalog.snapshot(principal, record.current_run.revision)
        await self.authority.require_current(principal, record, attempt, snapshot)
        review = await self.reviews.read(record.current_run)
        if review is None:
            configured = self.local.controller_factory()
            provider = MaterialProvider(snapshot.material)
            controller = ReviewAgentController(
                parser=AuthorizedOriginalParser(
                    self.documents, self.directory, principal, record.current_run
                ),
                fact_extractor=provider,
                rule_provider=provider,
                verifier=configured.verifier,
                minimum_confidence=configured.minimum_confidence,
                audit_logger=configured.audit_logger,
                # A configured local source session cannot approve rules/material.
                authorization=None,
                pdf_writer=None,
            )
            material = snapshot.material
            forms = [d for d in material.policy.registry.documents if d.role == "forms"]
            criteria_id = (
                material.policy.rule_bundle.primary_criteria_document_id
                if material.policy.rule_bundle
                else None
            )
            criteria = [
                d
                for d in material.policy.registry.documents
                if d.role == "criteria" and (criteria_id is None or d.document_id == criteria_id)
            ]
            if len(forms) != 1 or len(criteria) != 1:
                raise ServiceFault(ServiceErrorCode.VALIDATION)
            review = await controller.review(
                AgentReviewRequest(
                    case_id=record.case_id,
                    case_document_uri=forms[0].uri,
                    criteria_document_uri=criteria[0].uri,
                )
            )
            await self.authority.require_current(principal, record, attempt, snapshot)
            await self.reviews.put(record.current_run, review)
        tasks: list[HumanTask] = []
        if review.case_review is not None:
            findings = {
                f.id: f
                for f in review.case_review.findings
                if f.status != "verified" and f.kind == "evidence_reliability"
            }
            for pair in snapshot.material.facts.pairs:
                identity = f"{pair.context.key()}/factor/{pair.pair.factor_id}"
                finding = findings.get(identity)
                if (
                    finding is None
                    or finding.context != pair.context
                    or finding.factor_id != pair.pair.factor_id
                ):
                    continue
                for side in ("target", "comparable"):
                    tasks.extend(self._side_tasks(record, pair, side, identity))
        await self.authority.require_current(principal, record, attempt, snapshot)
        if tasks:
            await self.authority.register(record, attempt, snapshot, tuple(tasks))
            return ExecutedReview(persisted_task_ids=tuple(task.task_id for task in tasks))
        return ExecutedReview(
            result=ServiceResult(
                run=record.current_run,
                result_version=attempt.expected_result_version + 1,
                execution_status=ExecutionStatus.SUCCEEDED,
                business_status=review.status,
                artifact_status=review.artifact_status,
                findings=tuple(review.case_review.findings) if review.case_review else (),
                verification=public_verification(review.verification),
            )
        )

    @staticmethod
    def _side_tasks(
        record: JobRecord, pair: EvidencedPair, side: Side, finding_id: str
    ) -> list[HumanTask]:
        reliability = getattr(pair, f"{side}_reliability")
        digest = confirmation_digest(pair, side)
        if (
            reliability.method == "reviewer_confirmed"
            and reliability.confirmation is not None
            and reliability.confirmation.input_digest == digest
            and reliability.confirmation.reviewer == record.principal_id
        ):
            return []
        observation = getattr(pair.pair, side)
        if (
            observation.value is None
            or not observation.evidence
            or not getattr(pair, f"{side}_sources")
            or reliability.unresolved
            or reliability.selection == "ambiguous"
            or reliability.confidence_kind == "unknown"
            or reliability.provenance == "unknown"
        ):
            return []
        reference = FactSideReference(
            context=pair.context, factor_id=pair.pair.factor_id, side=side, input_digest=digest
        )
        subject = "source-" + content_digest(reference)
        return [
            HumanTask(
                task_id=uuid5(record.current_run.attempt_id or record.current_run.run_id, subject),
                run=record.current_run,
                version=1,
                kind=TaskKind.FACT,
                required_permission=Permission.CONFIRM,
                side=reference,
                question=(
                    "Compare this exact observation with its cited original before confirming. "
                    "Rules and full material approval remain separate."
                ),
                evidence=tuple(getattr(pair, f"{side}_sources")),
                finding_ids=(finding_id,),
                allowed_responses=(ResponseAction.CONFIRM, ResponseAction.REJECT),
                reason_code="local-source-observation",
                affected_subject_ids=(subject,),
            )
        ]


def private_state(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with os.fdopen(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)


async def prepare_workbench(
    *,
    manifest_path: Path,
    material_path: Path,
    data_directory: Path,
    port: int,
    session_hours: float = 4,
) -> FastAPI:
    """Trusted CLI setup; no caller-selected file or identity fields are mounted."""
    if not 1024 <= port <= 65535 or not 0 < session_hours <= 8:
        raise ValueError("A loopback port and bounded local session are required")
    manifest = InputManifest.model_validate_json(manifest_path.read_bytes())
    material = ReviewMaterial.model_validate_json(material_path.read_bytes())
    root = data_directory.resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    state_file = root / "session.json"
    digest = content_digest(material)
    reviewer = current_reviewer()
    owner = f"{reviewer.uid}:{reviewer.name}"
    if not state_file.exists():
        private_state(
            state_file,
            {
                "mode": "local-original-v1",
                "actor_id": owner,
                "token": secrets.token_urlsafe(48),
                "case_id": manifest.identity.case_id,
                "material_digest": digest,
                "revision_id": f"prepared-{digest[:24]}",
                "request_key": str(uuid4()),
                "expires_at": time.time() + session_hours * 3600,
            },
        )
    if state_file.is_symlink() or state_file.stat().st_mode & 0o077:
        raise ValueError("Local session configuration must remain private")
    state = json.loads(state_file.read_text())
    if (state["mode"], state["actor_id"], state["case_id"], state["material_digest"]) != (
        "local-original-v1",
        owner,
        manifest.identity.case_id,
        digest,
    ):
        raise ValueError("Use a new isolated directory for another owner or initial material")
    principal = Principal(
        actor=ActorReference(actor_id=owner, kind="human"),
        case_ids=frozenset({manifest.identity.case_id}),
        permissions=frozenset({Permission.REVIEW, Permission.CONFIRM, Permission.CORRECT}),
    )
    purpose_type: TypeAdapter[Purpose] = TypeAdapter(Purpose)
    purposes = frozenset(
        purpose_type.validate_python(document.role) for document in manifest.documents
    )
    grant = LocalOriginalAuthorization(principal, purposes, state["expires_at"])
    directory = ExpiringLocalDirectory({state["token"]: principal}, grant)
    directory.authenticate("Bearer " + state["token"])

    def response_authority(accepted: AcceptedResponse, task: HumanTask) -> None:
        current = directory.authenticate("Bearer " + state["token"])
        current.require(task.run.revision.case_id, task.required_permission)
        if accepted.actor != current.actor:
            raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
        from appraisal_review.domain.document_transfer import DocumentOperation

        try:
            for purpose in purposes:
                grant.require(current, task.run.revision.case_id, purpose, DocumentOperation.READ)
        except DocumentFault as error:
            raise source_fault(error) from None

    store = SQLiteReviewStore(root / "reviews.sqlite3", response_authority=response_authority)
    documents = LocalOriginalDocuments(manifest, store, grant)
    local = LocalReviewService(
        LocalServiceConfiguration(
            inputs=manifest,
            material_path=material_path.resolve(),
            expected_material_digest=digest,
            revision_id=state["revision_id"],
        )
    )
    catalog = LocalMaterialCatalog(store)
    catalog.register(principal, local.snapshot)
    execution = LocalCandidateExecution(local, documents, directory, catalog)
    app = create_integrated_service(
        authority=f"127.0.0.1:{port}",
        directory=directory,
        catalog=catalog,
        documents=documents,
        execution=execution,
        resolver=None,
        worker_timeout=120,
        source_delivery_enabled=False,
        legacy_review_enabled=False,
        poll_interval=1,
        attempt_scoped_reviews=True,
    )
    app.state.workbench_data_mode = "local_original"
    app.state.local_original_grant = grant
    app.state.local_original_directory = directory
    app.state.local_original_documents = documents
    acceptance = await app.state.job_service.submit(
        principal,
        ReviewSubmission(
            revision=local.snapshot.revision.reference,
            documents=local.snapshot.revision.documents,
            idempotency_key=state["request_key"],
        ),
    )
    job_id = acceptance.status.job.job_id
    app.state.configured_workbench_jobs = lambda: (job_id,)
    if not (root / "configured-job.json").exists():
        private_state(
            root / "configured-job.json",
            {
                "job_id": str(job_id),
                "revision": local.snapshot.revision.reference.model_dump(mode="json"),
                "mode": "local_original",
            },
        )
    return app
