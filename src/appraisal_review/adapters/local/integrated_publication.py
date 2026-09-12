"""Publish an existing verified Controller output through the fenced local store.

Constructor capabilities and per-call writer evidence are trusted server inputs.
This adapter never creates material approvals or invokes a PDF writer in projection.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from io import BytesIO
from typing import Protocol
from uuid import uuid5

from pypdf import PdfReader

from appraisal_review.adapters.local.artifact_publication import AttemptArtifactPublisher
from appraisal_review.adapters.local.pdf_config import field_map_sha256
from appraisal_review.adapters.local.pdf_preflight import PDFPreflightValidator
from appraisal_review.adapters.local.pdf_render import PDFMutationResult
from appraisal_review.adapters.local.pdf_verify import PDFArtifactVerifier
from appraisal_review.adapters.local.service import (
    ConfinedWriter,
    LocalArtifactEvidence,
    LocalWriterConfiguration,
    public_verification,
    read_artifact,
)
from appraisal_review.adapters.local.sqlite_publication import SQLiteManifestRepository
from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.application.service_guards import Principal
from appraisal_review.document_cli import _file_path
from appraisal_review.domain.artifact_publication import (
    ArtifactKey,
    ManifestCandidate,
    PublicationError,
    PublishedArtifact,
    SourceVersion,
)
from appraisal_review.domain.factor_models import (
    AgentReviewRequest,
    AgentReviewRun,
    EvaluationStatus,
    WorkflowStatus,
)
from appraisal_review.domain.pdf_models import PDFWriteRequest, PDFWriteResult
from appraisal_review.domain.review_contracts import content_digest
from appraisal_review.domain.service_contracts import (
    ExecutionStatus,
    FencedArtifactManifest,
    Permission,
    RevisionReference,
    ServiceResult,
)
from appraisal_review.ports.approval import ReviewAuthorization
from appraisal_review.ports.artifact_publication import PublicationAttempt
from appraisal_review.ports.jobs import ClaimedAttempt, JobRecord
from appraisal_review.ports.pdf import PDFWriter


class PublicationEvidenceWriter(ConfinedWriter):
    """Capture verified write evidence before returning to the Controller.

    Bind run_id before execution. An optional durable evidence sink is synchronous;
    its failure prevents a completed Controller result. Rehydration is a trusted
    server operation, never request deserialization.
    """

    def __init__(
        self,
        delegate: PDFWriter,
        configuration: LocalWriterConfiguration,
        *,
        on_evidence: Callable[[LocalArtifactEvidence], None] | None = None,
    ) -> None:
        super().__init__(delegate, configuration)
        self.on_evidence = on_evidence

    @property
    def supports_multiple_contexts(self) -> bool:
        return getattr(self.delegate, "supports_multiple_contexts", False) is True

    @property
    def reveals_placeholders(self) -> bool:
        return getattr(self.delegate, "reveals_placeholders", None) is not False

    async def write_pdf(self, request: PDFWriteRequest) -> PDFWriteResult:
        result = await super().write_pdf(request)
        if self.evidence is not None and self.on_evidence is not None:
            self.on_evidence(self.evidence)
        return result


@dataclass(frozen=True)
class TrustedRasterPublication:
    """Server-owned exact authored assets, never a request flag or model assertion.

    The independent fixture authority must validate current material and its
    original pinned configuration/assets. Hashes alone do not grant this scope.
    """

    source_versions: tuple[SourceVersion, ...]
    template_hash: str
    authorization: ReviewAuthorization


@dataclass(frozen=True)
class PublicationInputs:
    """Fixed synthetic assets selected by server configuration, not model arguments."""

    configuration: LocalWriterConfiguration
    request: AgentReviewRequest
    writer: ConfinedWriter
    template_version: str
    fixed_synthetic_assets: bool = False
    expected_placeholder_tokens: tuple[str, ...] = ()
    forbidden_originals: tuple[str, ...] = ()
    raster_assets: TrustedRasterPublication | None = None


class PublicationCatalog(Protocol):
    async def snapshot(
        self, principal: Principal, reference: RevisionReference
    ) -> RevisionSnapshot: ...


class IntegratedResultProjection:
    def __init__(
        self,
        *,
        publisher: AttemptArtifactPublisher,
        manifests: SQLiteManifestRepository,
        catalog: PublicationCatalog,
        principal_getter: Callable[[str, str], Awaitable[Principal]],
        inputs_getter: Callable[[JobRecord, ClaimedAttempt], PublicationInputs],
        synthetic_authorizer: Callable[[ManifestCandidate, Principal], None],
    ) -> None:
        if publisher.manifests is not manifests:
            raise ValueError("Publication and approval must use the same repository")
        self.publisher, self.manifests, self.catalog = publisher, manifests, catalog
        self.principal_getter, self.inputs_getter = principal_getter, inputs_getter
        self.synthetic_authorizer = synthetic_authorizer

    async def __call__(
        self, record: JobRecord, attempt: ClaimedAttempt, review: AgentReviewRun
    ) -> ServiceResult:
        review = AgentReviewRun.model_validate_json(review.model_dump_json())
        run = record.current_run
        if (
            review.case_id != record.case_id
            or run.revision.case_id != record.case_id
            or (attempt.job_id, attempt.run_id, attempt.attempt_id)
            != (record.job_id, run.run_id, run.attempt_id)
            or attempt.expected_result_version != record.result_version
        ):
            raise PublicationError("stale_publication")
        findings = tuple(review.case_review.findings) if review.case_review is not None else ()
        verification = public_verification(review.verification)
        if review.artifact_status != "written":
            return ServiceResult(
                run=run,
                result_version=attempt.expected_result_version + 1,
                execution_status=ExecutionStatus.SUCCEEDED,
                business_status=review.status,
                artifact_status=review.artifact_status,
                findings=findings,
                verification=verification,
            )
        if (
            review.status != WorkflowStatus.COMPLETED
            or review.verification is None
            or not review.verification.can_complete
            or review.case_review is None
            or review.case_review.status != EvaluationStatus.VERIFIED
            or review.case_review.identity.case_id != record.case_id
            or review.case_review.coverage.missing
            or review.case_review.coverage.unsupported
            or any(f.status != "verified" for f in findings)
            or not set(review.case_review.coverage.required).issubset(
                review.case_review.coverage.verified
            )
        ):
            raise PublicationError("review_not_publishable")
        principal = await self.principal_getter(record.principal_id, record.case_id)
        principal.require(record.case_id, Permission.PUBLISH)
        if principal.actor.actor_id != record.principal_id or principal.actor.kind == "model":
            raise PublicationError("publication_unauthorized")
        snapshot = await self.catalog.snapshot(principal, run.revision)
        if (
            snapshot.revision.reference != run.revision
            or content_digest(snapshot.material) != run.revision.material_digest
            or snapshot.material.policy.identity != review.case_review.identity
        ):
            raise PublicationError("artifact_evidence_missing")
        inputs = self.inputs_getter(record, attempt)
        try:
            artifact = self._verified_artifact(record, review, inputs, snapshot)
        except PublicationError:
            raise
        except Exception:
            raise PublicationError("artifact_evidence_missing") from None
        evidence = inputs.writer.evidence
        if evidence is None or artifact.font_hash is None:
            raise PublicationError("artifact_evidence_missing")
        font_hash = artifact.font_hash
        artifact = self.publisher.stage(
            evidence.destination,
            run=run,
            artifact=artifact,
            writer=inputs.writer.delegate,
        )
        candidate = ManifestCandidate(
            run=run,
            result_version=attempt.expected_result_version + 1,
            review_status=review.status,
            artifacts=(artifact,),
        )
        # The callback is an independent, trusted synthetic service authorization,
        # never a model-selected action or a substitute for Controller verification.
        if self.synthetic_authorizer(candidate, principal) is not None:
            raise PublicationError("publication_unauthorized")
        self.manifests.approve_exact(candidate, principal)
        committed = self.publisher.publish(
            candidate,
            fencing_token=attempt.fencing_token,
            principal=principal,
            attempt=PublicationAttempt(
                job_id=attempt.job_id,
                owner=attempt.owner,
                expected_result_version=attempt.expected_result_version,
            ),
        )
        return ServiceResult(
            run=run,
            result_version=candidate.result_version,
            execution_status=ExecutionStatus.SUCCEEDED,
            business_status=review.status,
            artifact_status=review.artifact_status,
            findings=findings,
            verification=verification,
            artifacts=(
                FencedArtifactManifest(
                    artifact_id=artifact.artifact_id,
                    content_hash=artifact.content_hash,
                    context=artifact.contexts[0],
                    contexts=artifact.contexts,
                    field_ids=artifact.field_ids,
                    page_count=artifact.page_count,
                    template_hash=artifact.template_hash,
                    field_map_hash=artifact.field_map_hash,
                    font_hash=font_hash,
                    writer_version=artifact.writer_version,
                    manifest_digest=committed.manifest_digest,
                ),
            ),
        )

    @staticmethod
    def _verified_artifact(
        record: JobRecord,
        review: AgentReviewRun,
        inputs: PublicationInputs,
        snapshot: RevisionSnapshot,
    ) -> PublishedArtifact:
        config, request, writer = inputs.configuration, inputs.request, inputs.writer
        evidence, pdf, case = writer.evidence, review.pdf_result, review.case_review
        if (
            inputs.fixed_synthetic_assets is not True
            or getattr(writer.delegate, "reveals_placeholders", None) is not False
            or writer.configuration != config
            or evidence is None
            or pdf is None
            or not pdf.artifact_created
            or pdf.warnings
            or case is None
            or not case.comparisons
            or request.case_id != record.case_id
            or request.field_map != config.field_map
            or request.pdf_template_uri is None
            or request.output_pdf_uri is None
            or review.output_pdf_uri != request.output_pdf_uri
            or pdf.output_uri != request.output_pdf_uri
            or evidence.run_id != record.current_run.run_id
            or writer.run_id != record.current_run.run_id
        ):
            raise PublicationError("artifact_evidence_missing")
        output, template = _file_path(pdf.output_uri), config.template_path
        data, identity = read_artifact(output)
        content_hash = hashlib.sha256(data).hexdigest()
        comparisons = case.comparisons
        contexts = tuple(c.context for c in comparisons if c.context is not None)
        registry = snapshot.material.policy.registry
        sources = tuple(
            SourceVersion(document_id=d.document_id, version=d.version, content_hash=d.content_hash)
            for d in snapshot.revision.documents
        )
        registry_versions = {(d.document_id, d.version, d.content_hash) for d in registry.documents}
        expected_hashes = {d.document_id: d.content_hash for d in registry.documents}
        if (
            _file_path(request.pdf_template_uri).resolve() != template.resolve()
            or not output.resolve().is_relative_to(config.output_directory.resolve())
            or output.resolve() == template.resolve()
            or output.resolve() != evidence.destination
            or identity != evidence.file_identity
            or content_hash != evidence.content_hash
            or content_digest(pdf) != evidence.result_digest
            or content_digest(comparisons[0]) != evidence.review_digest
            or field_map_sha256(config.field_map) != evidence.field_map_hash
            or evidence.field_map_hash != config.template_policy.field_map_sha256
            or len(contexts) != len(comparisons)
            or len({c.key() for c in contexts}) != len(contexts)
            or any(
                c.case_id != record.case_id
                or c.source_hashes != expected_hashes
                or c.summary.status != EvaluationStatus.VERIFIED
                for c in comparisons
            )
            or {c.key() for c in contexts}
            != {c.context.key() for c in snapshot.material.policy.rule_sets}
            or {(s.document_id, s.version, s.content_hash) for s in sources} != registry_versions
            or (
                len(comparisons) > 1
                and getattr(writer, "supports_multiple_contexts", False) is not True
            )
        ):
            raise PublicationError("artifact_evidence_missing")
        requested_sources = {
            "criteria": _file_path(request.criteria_document_uri).resolve(),
            "forms": _file_path(request.case_document_uri).resolve(),
        }
        for role, requested_path in requested_sources.items():
            matches = [d for d in registry.documents if d.role == role]
            if len(matches) != 1 or _file_path(matches[0].uri).resolve() != requested_path:
                raise PublicationError("artifact_evidence_missing")
        protected = []
        raster = inputs.raster_assets
        if raster is not None:
            IntegratedResultProjection._check_raster_assets(raster, snapshot, config, sources)
        for document in registry.documents:
            path = _file_path(document.uri)
            raw, source_identity = read_artifact(path)
            if (
                identity == source_identity
                or output.resolve() == path.resolve()
                or hashlib.sha256(raw).hexdigest() != document.content_hash
                or (
                    raster is None
                    and "SYNTHETIC SOURCE"
                    not in "\n".join(p.extract_text() for p in PdfReader(BytesIO(raw)).pages)
                )
            ):
                raise PublicationError("artifact_evidence_missing")
            protected.append(document.uri)
        reader = PdfReader(BytesIO(data), strict=True)
        text = "\n".join(p.extract_text() for p in reader.pages)
        template_text = "\n".join(p.extract_text() for p in PdfReader(template).pages)
        tokens = tuple(f.placeholder_token for f in config.field_map.fields if f.placeholder_token)
        if (
            set(tokens) != set(inputs.expected_placeholder_tokens)
            or any(token not in text for token in tokens)
            or any(
                not original or original in text or original.encode() in data
                for original in inputs.forbidden_originals
            )
            or (raster is None and "SYNTHETIC OUTPUT" not in template_text)
            or (raster is None and "SYNTHETIC OUTPUT" not in text)
            or reader.metadata is None
            or reader.metadata.get("/AppraisalReviewWriterVersion") != "2"
            or json.loads(reader.metadata.get("/AppraisalReviewFieldIds", "null"))
            != pdf.written_field_ids
            or pdf.written_field_ids != [f.field_id for f in config.field_map.fields]
            or len(reader.pages) != pdf.page_count
        ):
            raise PublicationError("artifact_evidence_missing")
        pdf_request = PDFWriteRequest(
            source_uri=template.as_uri(),
            destination_uri=pdf.output_uri,
            protected_source_uris=protected,
            result=comparisons[0],
            additional_results=comparisons[1:],
            field_map=config.field_map,
        )
        plan = PDFPreflightValidator(
            render_config=config.render, template_policy=config.template_policy
        ).validate(pdf_request, template)
        font_hash = hashlib.sha256(plan.font_bytes).hexdigest()
        if font_hash != config.render.approved_font_sha256:
            raise PublicationError("artifact_evidence_missing")
        PDFArtifactVerifier(config.template_policy).verify(
            source_path=template,
            output_path=output,
            plan=plan,
            mutation_result=PDFMutationResult(pdf.page_count, tuple(pdf.written_field_ids)),
        )
        if raster is not None:
            IntegratedResultProjection._check_raster_assets(raster, snapshot, config, sources)
        # A stable attempt/version identity makes post-commit crash replay exact.
        attempt_id = record.current_run.attempt_id
        if attempt_id is None:
            raise PublicationError("stale_publication")
        artifact_id = uuid5(attempt_id, "completed-pdf")
        return PublishedArtifact(
            artifact_id=artifact_id,
            key=ArtifactKey.for_run(record.current_run, artifact_id).key(),
            content_hash=content_hash,
            size_bytes=len(data),
            writer_version="2",
            template_id=config.template_policy.template_id,
            template_version=inputs.template_version,
            template_hash=config.template_policy.template_sha256,
            field_map_hash=evidence.field_map_hash,
            font_hash=font_hash,
            contexts=contexts,
            field_ids=tuple(pdf.written_field_ids),
            page_count=pdf.page_count,
            source_versions=sources,
            placeholder_only=True,
        )

    @staticmethod
    def _check_raster_assets(
        assets: TrustedRasterPublication,
        snapshot: RevisionSnapshot,
        configuration: LocalWriterConfiguration,
        sources: tuple[SourceVersion, ...],
    ) -> None:
        try:
            if type(assets) is not TrustedRasterPublication:
                raise ValueError("Explicit trusted raster authority required")
            expected = {(s.document_id, s.version, s.content_hash) for s in assets.source_versions}
            actual = {(s.document_id, s.version, s.content_hash) for s in sources}
            template, _ = read_artifact(configuration.template_path)
            registry = snapshot.material.policy.registry
            forms = [document for document in registry.documents if document.role == "forms"]
            if (
                not actual
                or expected != actual
                or len(assets.source_versions) != len(sources)
                or len(forms) != 1
                or forms[0].content_hash != assets.template_hash
                or any(page.has_text for document in registry.documents for page in document.pages)
                or assets.template_hash != configuration.template_policy.template_sha256
                or hashlib.sha256(template).hexdigest() != assets.template_hash
                or assets.authorization.permits(snapshot.material) is not True
            ):
                raise ValueError("Authored raster assets or approval changed")
        except Exception:
            raise PublicationError("artifact_evidence_missing") from None
