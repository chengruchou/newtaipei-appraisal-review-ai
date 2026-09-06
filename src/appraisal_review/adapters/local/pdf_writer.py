"""Production local implementation of the shared PDF writer port."""

from __future__ import annotations

from appraisal_review.adapters.local.object_access import LocalObjectAccess
from appraisal_review.adapters.local.pdf_config import PDFRenderConfig, PDFTemplatePolicy
from appraisal_review.adapters.local.pdf_preflight import PDFPreflightValidator
from appraisal_review.adapters.local.pdf_render import PDFMutationExecutor
from appraisal_review.adapters.local.pdf_verify import PDFArtifactVerifier
from appraisal_review.domain.pdf_models import PDFWriteRequest, PDFWriteResult


class LocalPDFWriter:
    """Validate, mutate, verify and atomically publish one local PDF copy."""

    def __init__(
        self,
        *,
        render_config: PDFRenderConfig,
        template_policy: PDFTemplatePolicy,
    ) -> None:
        self.object_access = LocalObjectAccess(overwrite_existing=render_config.overwrite_existing)
        self.preflight = PDFPreflightValidator(
            render_config=render_config,
            template_policy=template_policy,
        )
        self.mutation = PDFMutationExecutor(render_config)
        self.verifier = PDFArtifactVerifier(template_policy)

    async def write_pdf(self, request: PDFWriteRequest) -> PDFWriteResult:
        """Return success only after verified atomic destination publication."""
        with self.object_access.staged_write(
            request.source_uri, request.destination_uri
        ) as session:
            plan = self.preflight.validate(request, session.source_path)
            mutation_result = self.mutation.write_temporary(
                source_path=session.source_path,
                output_path=session.temporary_path,
                plan=plan,
            )
            self.verifier.verify(
                source_path=session.source_path,
                output_path=session.temporary_path,
                plan=plan,
                mutation_result=mutation_result,
            )
            result = PDFWriteResult(
                output_uri=request.destination_uri,
                page_count=mutation_result.page_count,
                written_field_ids=list(mutation_result.written_field_ids),
                warnings=[],
            )
            session.publish()
            return result
