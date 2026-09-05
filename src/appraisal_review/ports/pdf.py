"""The single provider-neutral PDF writer protocol."""

from typing import Protocol

from appraisal_review.domain.pdf_models import PDFWriteRequest, PDFWriteResult


class PDFWriter(Protocol):
    async def write_pdf(self, request: PDFWriteRequest) -> PDFWriteResult: ...
