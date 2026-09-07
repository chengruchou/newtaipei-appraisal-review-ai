"""Explicit local service composition. No document, font or cloud I/O on import."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass, replace
from io import BytesIO
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from appraisal_review.adapters.local.approval import LocalApprovalStore
from appraisal_review.adapters.local.document_manifest import InputManifest
from appraisal_review.adapters.local.pdf_config import (
    PDFRenderConfig,
    PDFTemplatePolicy,
    field_map_sha256,
)
from appraisal_review.application.bootstrap import ConfigurationError, build_controller
from appraisal_review.application.controller import ReviewAgentController
from appraisal_review.application.document_review import document_adapters
from appraisal_review.application.entrypoint import EntryError, execute_review
from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.config import Settings
from appraisal_review.domain.document_models import Digest, DocumentModel
from appraisal_review.domain.factor_models import (
    AgentReviewRequest,
    AgentReviewRun,
    ReviewMaterial,
    VerificationReport,
    WorkflowStatus,
)
from appraisal_review.domain.pdf_models import PDFFieldMap, PDFWriteRequest, PDFWriteResult
from appraisal_review.domain.pdf_types import PDFWriteError
from appraisal_review.domain.review_contracts import content_digest
from appraisal_review.domain.service_contracts import (
    ArtifactManifest,
    ExecutionStatus,
    OpaqueID,
    RunReference,
    ServiceErrorCode,
    ServiceProblem,
    ServiceResult,
    ServiceVerification,
    VerificationDiagnostic,
)
from appraisal_review.ports.pdf import PDFWriter


def public_verification(report: VerificationReport | None) -> ServiceVerification | None:
    """Allowlist known reasons; unknown internal text never crosses this boundary."""
    if report is None:
        return None
    known = {
        "source_binding: requested and reviewed source must match": VerificationDiagnostic(
            code="source_binding",
            message="Requested documents must match the configured review sources.",
        ),
        "A typed current source registry is required": VerificationDiagnostic(
            code="source_registry_required",
            message="A current source registry is required to review the material.",
        ),
    }
    blocker = VerificationDiagnostic(
        code="verification_blocker",
        message="Verification could not pass; inspect review findings or request human review.",
    )
    warning = VerificationDiagnostic(
        code="verification_warning",
        message="Verification reported a warning; request human review before proceeding.",
    )
    return ServiceVerification(
        status=report.status,
        critical_errors=tuple(known.get(reason, blocker) for reason in report.critical_errors),
        warnings=tuple(known.get(reason, warning) for reason in report.warnings),
    )


class LocalWriterConfiguration(DocumentModel):
    template_path: Path
    output_directory: Path
    field_map: PDFFieldMap
    template_policy: PDFTemplatePolicy
    render: PDFRenderConfig

    @model_validator(mode="after")
    def explicit_policy(self) -> LocalWriterConfiguration:
        if not self.template_path.is_absolute() or not self.output_directory.is_absolute():
            raise ValueError("Local writer paths must be absolute")
        if (
            self.field_map.template_id != self.template_policy.template_id
            or field_map_sha256(self.field_map) != self.template_policy.field_map_sha256
            or not self.field_map.fields
        ):
            raise ValueError("Configured field map differs from trusted template policy")
        return self


class LocalServiceConfiguration(DocumentModel):
    schema_version: Literal["local-service-v1"] = "local-service-v1"
    inputs: InputManifest
    material_path: Path
    expected_material_digest: Digest
    revision_id: OpaqueID
    approval_store: Path | None = None
    writer: LocalWriterConfiguration | None = None
    minimum_confidence: float = Field(default=0.85, ge=0, le=1)

    @model_validator(mode="after")
    def absolute_paths(self) -> LocalServiceConfiguration:
        paths = [self.material_path, *(d.path for d in self.inputs.documents)]
        if self.approval_store is not None:
            paths.append(self.approval_store)
        if not all(p.is_absolute() for p in paths):
            raise ValueError("Local service paths must be explicitly absolute")
        return self


@dataclass(frozen=True)
class LocalArtifactEvidence:
    """Per-call evidence from the configured writer, never a durable authorization."""

    run_id: UUID
    destination: Path
    file_identity: tuple[int, int]
    content_hash: str
    result_digest: str
    review_digest: str
    field_map_hash: str


def read_artifact(path: Path) -> tuple[bytes, tuple[int, int]]:
    with path.open("rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise PDFWriteError("Artifact must be a regular file")
        return stream.read(), (info.st_dev, info.st_ino)


class ConfinedWriter:
    """Local request bodies cannot select another template/map or output namespace."""

    def __init__(self, delegate: PDFWriter, configuration: LocalWriterConfiguration) -> None:
        self.delegate, self.configuration = delegate, configuration
        self.run_id = uuid4()
        self.evidence: LocalArtifactEvidence | None = None

    async def write_pdf(self, request: PDFWriteRequest) -> PDFWriteResult:
        from pypdf import PdfReader

        from appraisal_review.document_cli import _file_path

        self.evidence = None
        run_id = self.run_id
        config = self.configuration
        source, output = _file_path(request.source_uri), _file_path(request.destination_uri)
        if (
            source.resolve() != config.template_path.resolve()
            or not output.resolve().is_relative_to(config.output_directory.resolve())
            or field_map_sha256(request.field_map) != config.template_policy.field_map_sha256
        ):
            raise PDFWriteError("Write request is outside configured local policy")
        try:
            previous = output.stat()
            previous_identity = (previous.st_dev, previous.st_ino)
        except FileNotFoundError:
            previous_identity = None
        result = await self.delegate.write_pdf(request)
        result = PDFWriteResult.model_validate_json(result.model_dump_json())
        if not result.artifact_created:
            return result
        data, identity = read_artifact(output)
        if (
            identity == previous_identity
            or _file_path(result.output_uri).resolve() != output.resolve()
            or set(result.written_field_ids) != {f.field_id for f in request.field_map.fields}
            or len(PdfReader(BytesIO(data)).pages) != result.page_count
        ):
            raise PDFWriteError("Writer did not publish the requested artifact")
        # The existing writer publishes a new staged inode, including explicit overwrite.
        # Pin its observed bytes before returning to the Controller/facade.
        self.evidence = LocalArtifactEvidence(
            run_id=run_id,
            destination=output.resolve(),
            file_identity=identity,
            content_hash=hashlib.sha256(data).hexdigest(),
            result_digest=content_digest(result),
            review_digest=content_digest(request.result),
            field_map_hash=field_map_sha256(request.field_map),
        )
        return result


class LocalReviewService:
    """One pinned local material, one existing Controller; not a multi-user server."""

    def __init__(self, configuration: LocalServiceConfiguration) -> None:
        self.configuration = LocalServiceConfiguration.model_validate_json(
            configuration.model_dump_json()
        )
        config = self.configuration
        material = ReviewMaterial.model_validate_json(config.material_path.read_bytes())
        if content_digest(material) != config.expected_material_digest:
            raise ValueError("Configured material changed")
        if material.policy.identity != config.inputs.identity:
            raise ValueError("Configured case identity changed")
        expected = {
            d.document_id: (d.path.resolve().as_uri(), d.version, d.expected_hash, d.role)
            for d in config.inputs.documents
        }
        actual = {
            d.document_id: (d.uri, d.version, d.content_hash, d.role)
            for d in material.policy.registry.documents
        }
        if expected != actual or len(expected) != len(config.inputs.documents):
            raise ValueError("Configured source identities differ from material")
        self.snapshot = RevisionSnapshot.capture(material, config.revision_id)
        self.parser = config.inputs.parser()

    def controller_factory(self) -> ReviewAgentController:
        config = self.configuration
        authority = LocalApprovalStore(config.approval_store) if config.approval_store else None
        adapters = document_adapters(self.parser, self.snapshot.material, authority)
        if config.writer is not None:
            from appraisal_review.adapters.local.pdf_writer import LocalPDFWriter

            writer = LocalPDFWriter(
                render_config=config.writer.render, template_policy=config.writer.template_policy
            )
            adapters = replace(adapters, pdf_writer=ConfinedWriter(writer, config.writer))
        return build_controller(
            Settings.model_construct(
                runtime_mode="local",
                synthetic_demo=False,
                min_extraction_confidence=config.minimum_confidence,
            ),
            adapters=adapters,
        )

    async def run(self, request: AgentReviewRequest) -> ServiceResult:
        """Additional local envelope; legacy HTTP/invoke still return AgentReviewRun."""
        run = RunReference(run_id=uuid4(), revision=self.snapshot.revision.reference)
        writer: ConfinedWriter | None = None

        def factory() -> ReviewAgentController:
            nonlocal writer
            controller = self.controller_factory()
            if isinstance(controller.pdf_writer, ConfinedWriter):
                writer = controller.pdf_writer
                writer.run_id = run.run_id
                writer.evidence = None  # A reused factory cannot replay a previous write.
            return controller

        try:
            request = AgentReviewRequest.model_validate_json(request.model_dump_json())
            if request.case_id != run.revision.case_id:
                raise ValueError("Case mismatch")
            result = await execute_review(request, factory)
        except ValueError:
            return ServiceResult(
                run=run,
                result_version=1,
                execution_status=ExecutionStatus.FAILED,
                problem=ServiceProblem(code=ServiceErrorCode.VALIDATION),
            )
        except EntryError as error:
            return ServiceResult(
                run=run,
                result_version=1,
                execution_status=ExecutionStatus.FAILED,
                problem=ServiceProblem(
                    code=ServiceErrorCode.CAPABILITY
                    if error.status_code == 503
                    else ServiceErrorCode.EXECUTION
                ),
            )
        findings = tuple(result.case_review.findings) if result.case_review else ()
        verification = public_verification(result.verification)
        try:
            artifacts = self._manifest(result, request, run, writer.evidence if writer else None)
        except Exception:
            # A local file can exist, but an unverified manifest must never advertise it.
            return ServiceResult(
                run=run,
                result_version=1,
                execution_status=ExecutionStatus.FAILED,
                business_status=WorkflowStatus.FAILED,
                findings=findings,
                verification=verification,
                problem=ServiceProblem(code=ServiceErrorCode.EXECUTION),
            )
        return ServiceResult(
            run=run,
            result_version=1,
            execution_status=ExecutionStatus.FAILED
            if result.pdf_error
            else ExecutionStatus.SUCCEEDED,
            business_status=result.status,
            artifact_status=result.artifact_status,
            findings=findings,
            verification=verification,
            artifacts=artifacts,
            problem=ServiceProblem(code=ServiceErrorCode.EXECUTION) if result.pdf_error else None,
        )

    def _manifest(
        self,
        result: AgentReviewRun,
        request: AgentReviewRequest,
        run: RunReference,
        evidence: LocalArtifactEvidence | None,
    ) -> tuple[ArtifactManifest, ...]:
        if result.artifact_status != "written":
            return ()
        from pypdf import PdfReader

        from appraisal_review.document_cli import _file_path

        config = self.configuration.writer
        if (
            config is None
            or result.output_pdf_uri is None
            or result.pdf_result is None
            or result.review is None
            or result.review.context is None
            or request.field_map is None
            or evidence is None
        ):
            raise ValueError("Incomplete artifact evidence")
        path = _file_path(result.output_pdf_uri)
        data, identity = read_artifact(path)
        if (
            evidence.run_id != run.run_id
            or path.resolve() != evidence.destination
            or identity != evidence.file_identity
            or hashlib.sha256(data).hexdigest() != evidence.content_hash
            or content_digest(result.pdf_result) != evidence.result_digest
            or content_digest(result.review) != evidence.review_digest
            or field_map_sha256(request.field_map) != evidence.field_map_hash
            or len(PdfReader(BytesIO(data)).pages) != result.pdf_result.page_count
        ):
            raise ValueError("Artifact differs from this call's verified write")
        return (
            ArtifactManifest(
                artifact_id=uuid4(),
                content_hash=hashlib.sha256(data).hexdigest(),
                context=result.review.context,
                field_ids=tuple(result.pdf_result.written_field_ids),
                page_count=result.pdf_result.page_count,
                template_hash=config.template_policy.template_sha256,
                field_map_hash=field_map_sha256(request.field_map),
            ),
        )


def load_service(path: Path) -> LocalReviewService:
    try:
        return LocalReviewService(LocalServiceConfiguration.model_validate_json(path.read_bytes()))
    except Exception as error:
        raise ConfigurationError("invalid_local_service_configuration") from error
