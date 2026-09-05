"""Explicit contract test double. Does not read, render, upload or create PDFs."""

from appraisal_review.domain.pdf_models import PDFWriteRequest, PDFWriteResult


class FakePDFWriter:
    def __init__(self, *, page_count: int = 1) -> None:
        self.calls: list[PDFWriteRequest] = []
        self.page_count = page_count

    async def write_pdf(self, request: PDFWriteRequest) -> PDFWriteResult:
        self.calls.append(request)
        return PDFWriteResult(
            output_uri=request.destination_uri,
            page_count=self.page_count,
            written_field_ids=[field.field_id for field in request.field_map.fields],
            warnings=["Synthetic PDF writer: no file was created."],
        )
